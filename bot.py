import os
import asyncio
import re
from urllib.parse import quote

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands, tasks

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GLOWAUTH_BASE_URL = os.getenv("GLOWAUTH_BASE_URL", "https://glowauth.pages.dev").strip().rstrip("/")
PORT = int(os.getenv("PORT", "10000"))
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
CACHE_INTERVAL = 60
SCRIPT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
SCRIPT_CACHE = {}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def raw_url(script):
    return f"{GLOWAUTH_BASE_URL}/raw/{quote(str(script['id']), safe='')}.{quote(str(script.get('extension') or 'lua').lower(), safe='')}"


def loader(script):
    return f'loadstring(game:HttpGet("{raw_url(script)}"))()'


def preview_script():
    return {
        "id": "00000000000000000000000000000000",
        "name": "GlowAuth Panel Preview",
        "extension": "lua",
        "size": 0,
        "deleted": False,
        "preview": True,
        "verified": False,
    }


async def fetch_json(url):
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(
                url,
                headers={"Accept": "application/json", "User-Agent": "GlowAuth-Discord-Bot/1.0"},
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                if response.status != 200:
                    return None, payload.get("error") or f"GlowAuth returned HTTP {response.status}."
                return payload, None
    except asyncio.TimeoutError:
        return None, "GlowAuth request timed out."
    except aiohttp.ClientError:
        return None, "Couldn't connect to GlowAuth."


async def fetch_all_scripts():
    payload, error = await fetch_json(f"{GLOWAUTH_BASE_URL}/api/bot/scripts")
    if error:
        raise RuntimeError(error)
    scripts = payload.get("scripts")
    if not isinstance(scripts, list):
        raise RuntimeError("GlowAuth returned an invalid script list.")
    return {
        str(item.get("id")): {**item, "verified": True}
        for item in scripts
        if isinstance(item, dict) and item.get("id")
    }


async def fetch_script(script_id):
    cached = SCRIPT_CACHE.get(script_id)
    if cached is not None:
        return cached, None
    payload, error = await fetch_json(f"{GLOWAUTH_BASE_URL}/api/bot/scripts/{quote(script_id, safe='')}")
    if error:
        return None, error
    script = payload.get("script")
    if not isinstance(script, dict) or str(script.get("id", "")).lower() != script_id:
        return None, "GlowAuth returned an invalid script record."
    script = {**script, "verified": True}
    SCRIPT_CACHE[script_id] = script
    return script, None


class ScriptPanel(discord.ui.LayoutView):
    def __init__(self, script, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.script = script
        self.is_preview = bool(script.get("preview"))
        self.is_verified = bool(script.get("verified")) and not self.is_preview
        safe_name = discord.utils.escape_markdown(str(script.get("name") or "script"))

        if self.is_preview:
            status = "PREVIEW ONLY"
            note = "This is a layout preview. No script ID or API key is needed. Upload a script and use its ID to create a verified panel."
            panel_items = [
                discord.ui.TextDisplay(content="## ✦ GlowAuth · Script Panel"),
                discord.ui.TextDisplay(content=f"**{safe_name}**\n`{status}` · Testing mode"),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content="**Script ID**\n`Not set`\n\n**Type**\n`.lua` · Preview"),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=note),
                discord.ui.ActionRow(
                    discord.ui.Button(label="Preview Mode", style=discord.ButtonStyle.secondary, emoji="🧪", disabled=True)
                ),
            ]
            self.add_item(discord.ui.Container(*panel_items, accent_colour=0x8B5CF6))
            return

        status = "DELETED" if script.get("deleted") else ("ACTIVE" if self.is_verified else "UNVERIFIED")
        verification = "Verified from GlowAuth" if self.is_verified else "Not verified · panel test"
        raw_button = discord.ui.Button(
            label="Open Raw",
            style=discord.ButtonStyle.link,
            emoji="🔗",
            url=raw_url(script),
        )
        loader_button = discord.ui.Button(
            label="Get Loader",
            style=discord.ButtonStyle.primary,
            emoji="📋",
        )
        refresh_button = discord.ui.Button(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            emoji="↻",
        )
        loader_button.callback = self.get_loader
        refresh_button.callback = self.refresh
        if self.is_verified:
            note = "Script status is checked against the GlowAuth registry. Deleted scripts are marked and cannot be presented as active."
        else:
            note = "This panel was sent in test mode because GlowAuth could not verify the ID. Check that the ID exists before sharing its raw URL or loader."
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content="## ✦ GlowAuth · Script Panel"),
                discord.ui.TextDisplay(content=f"**{safe_name}**\n`{status}` · {verification}"),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    content=(
                        f"**Script ID**\n`{discord.utils.escape_markdown(str(script['id']))}`\n\n"
                        f"**Type**\n`.{discord.utils.escape_markdown(str(script.get('extension') or 'lua'))}` · "
                        f"`{int(script.get('size') or 0):,} bytes`"
                    )
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f"**Raw URL**\n<{raw_url(script)}>"),
                discord.ui.ActionRow(raw_button, loader_button, refresh_button),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=note),
                accent_colour=0x8B5CF6 if self.is_verified else 0xF0B35A,
            )
        )

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the user who created this panel can use these controls.",
                ephemeral=True,
            )
            return False
        return True

    async def get_loader(self, interaction):
        if self.is_preview:
            await interaction.response.send_message("This is a preview panel. Upload a script and create a panel with its ID to get a loader.", ephemeral=True)
            return
        if self.script.get("deleted"):
            await interaction.response.send_message("This script has been deleted by its owner.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**GlowAuth Loader**\n```lua\n{loader(self.script)}\n```",
            ephemeral=True,
        )

    async def refresh(self, interaction):
        if self.is_preview:
            await interaction.response.send_message("Preview panels do not have a live script to refresh.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        fresh, error = await fetch_script(str(self.script["id"]))
        if error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return
        if fresh.get("deleted"):
            await interaction.followup.send("🗑️ This script has been deleted by its owner.", ephemeral=True)
            return
        await interaction.followup.send(view=ScriptPanel(fresh, interaction.user.id), ephemeral=True)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user} | synced {len(synced)} commands")
    except Exception as exc:
        print(f"Command sync failed: {type(exc).__name__}: {exc}")
    if not sync_glowauth_scripts.is_running():
        sync_glowauth_scripts.start()


@tasks.loop(seconds=CACHE_INTERVAL)
async def sync_glowauth_scripts():
    global SCRIPT_CACHE
    try:
        SCRIPT_CACHE = await fetch_all_scripts()
        active = sum(1 for script in SCRIPT_CACHE.values() if not script.get("deleted"))
        deleted = sum(1 for script in SCRIPT_CACHE.values() if script.get("deleted"))
        print(f"GlowAuth sync: {len(SCRIPT_CACHE)} total | {active} active | {deleted} deleted")
    except Exception as exc:
        print(f"GlowAuth sync failed: {type(exc).__name__}: {exc}")


@sync_glowauth_scripts.before_loop
async def before_sync():
    await bot.wait_until_ready()


create_group = app_commands.Group(name="create", description="Create GlowAuth panels")
bot.tree.add_command(create_group)


@create_group.command(name="script", description="Create a GlowAuth panel or preview its layout")
@app_commands.describe(script_id="Optional: the 32-character ID shown after uploading a script")
@app_commands.guild_only()
async def create_script(interaction: discord.Interaction, script_id: str | None = None):
    if script_id is None or not script_id.strip():
        await interaction.response.send_message(view=ScriptPanel(preview_script(), interaction.user.id))
        return

    script_id = script_id.strip().lower()
    if not SCRIPT_ID_PATTERN.fullmatch(script_id):
        await interaction.response.send_message(
            "That isn't a valid GlowAuth script ID. Use the 32-character ID shown in the Dashboard, or leave the option empty to preview the panel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    script, error = await fetch_script(script_id)
    if script and script.get("deleted"):
        await interaction.followup.send(
            "## 🗑️ Script deleted\nThis GlowAuth script was deleted by its owner and can no longer be used.",
            ephemeral=True,
        )
        return

    if error or script is None:
        script = {
            "id": script_id,
            "name": "Unverified GlowAuth Script",
            "extension": "lua",
            "size": 0,
            "deleted": False,
            "verified": False,
        }
        print(f"Panel test mode for {script_id}: {error or 'script record unavailable'}")
    else:
        SCRIPT_CACHE[script_id] = script

    await interaction.followup.send(view=ScriptPanel(script, interaction.user.id))


async def health(request):
    return web.json_response(
        {
            "ok": True,
            "service": "glowauth-discord-bot",
            "discord_ready": bot.is_ready(),
            "cached_scripts": len(SCRIPT_CACHE),
        }
    )


async def start_http():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"Health server listening on {PORT}")
    return runner


async def main():
    if not DISCORD_TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN environment variable.")
    if not GLOWAUTH_BASE_URL.startswith("https://"):
        raise RuntimeError("GLOWAUTH_BASE_URL must use HTTPS.")
    runner = await start_http()
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        if sync_glowauth_scripts.is_running():
            sync_glowauth_scripts.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
