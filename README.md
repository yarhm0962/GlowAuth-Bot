# GlowAuth Discord Bot

Components V2 Discord bot for GlowAuth.

## Command

`/create script` with required `script_id`.

The bot validates script IDs against GlowAuth's script registry and refreshes the full registry every 60 seconds. The registry contains metadata and deletion state, not uploaded source code.

## Render

Environment variables:
- `DISCORD_TOKEN` — your Discord bot token
- `GLOWAUTH_BASE_URL` — `https://glowauth.pages.dev`

Render supplies `PORT`. The bot exposes `/health` so it can run on the existing Render Web Service.

Requires discord.py 2.6+ for LayoutView and other Components V2 classes.
