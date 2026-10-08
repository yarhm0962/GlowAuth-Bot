import os
import asyncio
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
SCRIPT_CACHE = {}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def raw_url(script):
    extension = str(script.get("extension") or "lua").lower()
    return f"{GLOWAUTH_BASE_URL}/raw/{quote(str(script['id']))}.{quote(extension)}"


def loader(script):
    return f'loadstring(game:HttpGet("{raw_url(script)}"))()'


async def fetch_json(url):
    try:
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            async with session.get(
                url,
                headers={"Accept": "application/json", "User-Agent": "GlowAuth-Discord-Bot/1.0"},
            ) as response:
                payload = await response.json(content_type=None)
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
    return {str(item.get("id")): item for item in scripts if item.get("id")}


async def fetch_script(script_id):
    cached = SCRIPT_CACHE.get(script_id)
    if cached is not None:
        return cached, None
    payload, error = await fetch_json(f"{GLOWAUTH_BASE_URL}/api/bot/scripts/{quote(script_id, safe='')}")
    if error:
        return None, error
    script = payload.get("script")
    if not isinstance(script, dict):
        return None, "GlowAuth returned an invalid script record."
    SCRIPT_CACHE[script_id] = script
    return script, None


class ScriptPanel(discord.ui.LayoutView):
    def __init__(self, script, owner_id):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.script = script

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

        status = "ACTIVE" if not script.get("deleted") else "DELETED"
        safe_name = discord.utils.escape_markdown(str(script.get("name") or "script"))

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content="## ✦ GlowAuth · Script Panel"),
                discord.ui.TextDisplay(
                    content=f"**{safe_name}**\n`{status}` · Verified from GlowAuth"
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    content=(
                        f"**Script ID**\n`{script['id']}`\n\n"
                        f"**Type**\n`.{script.get('extension', 'lua')}` · "
                        f"`{int(script.get('size', 0)):,} bytes`"
                    )
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f"**Raw URL**\n<{raw_url(script)}>"),
                discord.ui.ActionRow(raw_button, loader_button, refresh_button),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    content="Status is checked through the GlowAuth script registry. Deleted scripts are never presented as active."
                ),
                accent_colour=0x8B5CF6 if not script.get("deleted") else 0xFF647C,
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
        if self.script.get("deleted"):
            await interaction.response.send_message(
                "This script has been deleted by its owner.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"**GlowAuth Loader**\n```lua\n{loader(self.script)}\n```",
            ephemeral=True,
        )

    async def refresh(self, interaction):
        await interaction.response.defer(ephemeral=True)
        fresh, error = await fetch_script(str(self.script["id"]))
        if error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
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


@create_group.command(name="script", description="Create a GlowAuth panel for a script ID")
@app_commands.describe(script_id="The 32-character script ID shown after uploading to GlowAuth")
@app_commands.guild_only()
async def create_script(interaction, script_id: str):
    script_id = script_id.strip().lower()

    if len(script_id) != 32 or any(ch not in "0123456789abcdef" for ch in script_id):
        await interaction.response.send_message(
            "That isn't a valid GlowAuth script ID. Copy the 32-character ID from the GlowAuth Dashboard.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    script, error = await fetch_script(script_id)
    if error:
        await interaction.followup.send(f"## ⚠️ GlowAuth\n{error}")
        return

    if script.get("deleted"):
        await interaction.followup.send(
            "## 🗑️ Script deleted\nThis GlowAuth script was deleted by its owner and can no longer be used."
        )
        return

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
