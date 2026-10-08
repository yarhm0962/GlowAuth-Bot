import os
import asyncio
import re
from urllib.parse import quote

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GLOWAUTH_BASE_URL = os.getenv("GLOWAUTH_BASE_URL", "https://glowauth.pages.dev").strip().rstrip("/")
PORT = int(os.getenv("PORT", "10000"))
TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)
SCRIPT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
SCRIPT_CACHE = {}

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())


def raw_url(script):
    extension = str(script.get("extension") or "lua").lower().lstrip(".")
    return f"{GLOWAUTH_BASE_URL}/raw/{quote(str(script['id']), safe='')}.{quote(extension, safe='')}"


def make_loader(script):
    return f'loadstring(game:HttpGet("{raw_url(script)}"))()'


async def get_json(url):
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url, headers={"Accept": "application/json", "User-Agent": "GlowAuth-Discord-Bot/1.1"}) as response:
                try:
                    data = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = {}
                if not isinstance(data, dict):
                    return None, "GlowAuth returned an invalid response."
                if response.status != 200:
                    return None, str(data.get("error") or f"GlowAuth returned HTTP {response.status}.")
                return data, None
    except asyncio.TimeoutError:
        return None, "GlowAuth took too long to respond. The panel is shown, but script details could not be verified."
    except aiohttp.ClientError:
        return None, "Couldn't connect to GlowAuth. The panel is shown, but script details could not be verified."
    except Exception as exc:
        print(f"GlowAuth request error: {type(exc).__name__}: {exc}")
        return None, "An unexpected error occurred while checking GlowAuth."


async def fetch_script(script_id, force=False):
    if not force and script_id in SCRIPT_CACHE:
        return SCRIPT_CACHE[script_id], None
    data, error = await get_json(f"{GLOWAUTH_BASE_URL}/api/bot/scripts/{quote(script_id, safe='')}")
    if error:
        return None, error
    script = data.get("script")
    if not isinstance(script, dict) or str(script.get("id", "")).lower() != script_id:
        return None, "GlowAuth returned an invalid script record."
    script = {**script, "verified": True}
    SCRIPT_CACHE[script_id] = script
    return script, None


def script_embed(script, note=None):
    preview = bool(script.get("preview"))
    verified = bool(script.get("verified")) and not preview
    name = discord.utils.escape_markdown(str(script.get("name") or "GlowAuth Script"))
    extension = discord.utils.escape_markdown(str(script.get("extension") or "lua").lstrip("."))
    script_id = str(script.get("id") or "Not set")
    size = int(script.get("size") or 0)
    tick = chr(96)
    if preview:
        status = "PREVIEW ONLY"
    elif script.get("deleted"):
        status = "DELETED"
    elif verified:
        status = "VERIFIED"
    else:
        status = "UNVERIFIED"
    description = f"**{name}**\n{tick}{status}{tick}"
    if note:
        description += f"\n\n{note}"
    color = 0x8B5CF6 if verified or preview else (0xED4245 if script.get("deleted") else 0xF0B35A)
    embed = discord.Embed(title="✦ GlowAuth · Script Panel", description=description, color=color)
    embed.add_field(name="Script ID", value=f"{tick}{script_id if not preview else 'Not set'}{tick}", inline=False)
    embed.add_field(name="Type", value=f"{tick}.{extension}{tick}", inline=True)
    if not preview:
        embed.add_field(name="Size", value=f"{tick}{size:,} bytes{tick}", inline=True)
        if verified and not script.get("deleted"):
            embed.add_field(name="Raw URL", value=f"[Open raw script]({raw_url(script)})", inline=False)
        embed.add_field(name="Status", value=note or ("Verified from GlowAuth." if verified else "This ID could not be verified against GlowAuth."), inline=False)
    else:
        embed.add_field(name="Preview", value="This confirms the panel UI works. Use a real script ID to load its details.", inline=False)
    embed.set_footer(text="GlowAuth • Script management")
    return embed


class ScriptPanel(discord.ui.View):
    def __init__(self, script, owner_id):
        super().__init__(timeout=600)
        self.script = script
        self.owner_id = owner_id
        self.preview = bool(script.get("preview"))
        if self.preview:
            self.add_item(discord.ui.Button(label="Preview Mode", emoji="🧪", style=discord.ButtonStyle.secondary, disabled=True))
        elif script.get("verified") and not script.get("deleted"):
            self.add_item(discord.ui.Button(label="Open Raw", emoji="🔗", style=discord.ButtonStyle.link, url=raw_url(script)))
        else:
            self.add_item(discord.ui.Button(label="Raw Unavailable", emoji="🔒", style=discord.ButtonStyle.secondary, disabled=True))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the person who created this panel can use its buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Get Loader", emoji="📋", style=discord.ButtonStyle.primary)
    async def get_loader(self, interaction, button):
        if self.preview:
            await interaction.response.send_message("Preview mode only. Use a real script ID to get its loader.", ephemeral=True)
        elif self.script.get("deleted"):
            await interaction.response.send_message("This script has been deleted.", ephemeral=True)
        elif not self.script.get("verified"):
            await interaction.response.send_message("GlowAuth could not verify this script ID, so the loader is disabled. Check the ID and try again.", ephemeral=True)
        else:
            tick = chr(96)
            await interaction.response.send_message(f"**GlowAuth Loader**\n{tick * 3}lua\n{make_loader(self.script)}\n{tick * 3}", ephemeral=True)

    @discord.ui.button(label="Refresh", emoji="↻", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        if self.preview:
            await interaction.response.send_message("Preview panels cannot be refreshed.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            script, error = await fetch_script(str(self.script["id"]), force=True)
            if error or not script:
                await interaction.followup.send(f"⚠️ {error or 'Could not load this script.'}", ephemeral=True)
                return
            if script.get("deleted"):
                await interaction.followup.send("This script has been deleted.", ephemeral=True)
                return
            await interaction.followup.send(embed=script_embed(script), view=ScriptPanel(script, interaction.user.id), ephemeral=True)
        except Exception as exc:
            print(f"Refresh button error: {type(exc).__name__}: {exc}")
            await interaction.followup.send("⚠️ Refresh failed unexpectedly. Check the bot's Render logs.", ephemeral=True)


group = app_commands.Group(name="create", description="Create GlowAuth panels")
bot.tree.add_command(group)


@group.command(name="script", description="Create a GlowAuth script panel or preview")
@app_commands.describe(script_id="Optional 32-character script ID from your GlowAuth dashboard")
@app_commands.guild_only()
async def create_script(interaction: discord.Interaction, script_id: str = None):
    if not script_id or not script_id.strip():
        preview = {"id": "0" * 32, "name": "GlowAuth Panel Preview", "extension": "lua", "size": 0, "preview": True}
        await interaction.response.send_message(embed=script_embed(preview), view=ScriptPanel(preview, interaction.user.id))
        return

    script_id = script_id.strip().lower()
    if not SCRIPT_ID_PATTERN.fullmatch(script_id):
        await interaction.response.send_message("Enter the 32-character script ID from your GlowAuth dashboard, or leave it empty to preview the panel.", ephemeral=True)
        return

    placeholder = {
        "id": script_id, "name": "Checking GlowAuth…", "extension": "lua",
        "size": 0, "verified": False,
    }
    view = ScriptPanel(placeholder, interaction.user.id)
    await interaction.response.send_message(
        embed=script_embed(placeholder, "Checking the GlowAuth registry. This panel will update automatically."),
        view=view,
    )
    try:
        script, error = await fetch_script(script_id, force=True)
        if script and script.get("deleted"):
            await interaction.edit_original_response(
                embed=discord.Embed(title="🗑️ Script deleted", description="This script is no longer available.", color=0xED4245),
                view=None,
            )
            return
        if error or not script:
            fallback = {
                "id": script_id, "name": "GlowAuth Script", "extension": "lua",
                "size": 0, "verified": False,
            }
            await interaction.edit_original_response(
                embed=script_embed(fallback, error or "GlowAuth could not verify this ID."),
                view=ScriptPanel(fallback, interaction.user.id),
            )
            return
        await interaction.edit_original_response(
            embed=script_embed(script),
            view=ScriptPanel(script, interaction.user.id),
        )
    except Exception as exc:
        print(f"/create script handler error: {type(exc).__name__}: {exc}")
        fallback = {
            "id": script_id, "name": "GlowAuth Script", "extension": "lua",
            "size": 0, "verified": False,
        }
        try:
            await interaction.edit_original_response(
                embed=script_embed(fallback, "The panel was created, but script lookup failed. Check the bot's Render logs."),
                view=ScriptPanel(fallback, interaction.user.id),
            )
        except Exception as edit_exc:
            print(f"Could not update script panel: {type(edit_exc).__name__}: {edit_exc}")


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}; synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Slash command sync failed: {type(exc).__name__}: {exc}")


async def health(request):
    return web.json_response({"ok": True, "service": "glowauth-discord-bot", "discord_ready": bot.is_ready()})


async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN environment variable.")
    if not GLOWAUTH_BASE_URL.startswith("https://"):
        raise RuntimeError("GLOWAUTH_BASE_URL must use HTTPS.")
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
