# GlowAuth Discord Bot

Discord Components V2 panels for GlowAuth.

## Command

Run `/create script` and leave `script_id` empty to send a preview panel. This preview does not need a GlowAuth API key or an uploaded script. To make a panel for an actual upload, supply the 32-character script ID shown in the GlowAuth dashboard.

The bot attempts to verify real IDs against the public GlowAuth script registry. If verification is unavailable, it still sends an explicitly marked unverified test panel, instead of refusing to send anything. A script marked as deleted by the registry is rejected.

## Render environment variables

- `DISCORD_TOKEN` — your Discord bot token, stored as a secret in Render.
- `GLOWAUTH_BASE_URL` — `https://glowauth.pages.dev`.

No `GLOWAUTH_API_KEY` is required. Render provides `PORT`; the service exposes `/health`.
