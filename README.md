# GlowAuth Discord Bot

GlowAuth Discord bot with standard Discord embeds and interactive buttons.

## Command

Run `/create script` and leave `script_id` empty to send a preview panel. To create a panel for an uploaded script, provide the 32-character script ID shown in the GlowAuth dashboard.

The panel includes Open Raw, Get Loader, and Refresh controls. Real script panels are fetched from the GlowAuth registry; if the registry cannot verify the ID, the command returns an error rather than presenting an unverified script as verified. Deleted scripts are rejected.

## Render environment variables

- `DISCORD_TOKEN` — your Discord bot token, stored as a secret in Render.
- `GLOWAUTH_BASE_URL` — optional; defaults to `https://glowauth.pages.dev`.

The service exposes `/health`. Render should use `pip install -r requirements.txt` as the build command and `python bot.py` as the start command.
