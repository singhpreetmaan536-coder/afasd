# Telegram Instagram Monitor — Railway

## Environment variables

Set these in Railway:

- `BOT_TOKEN` = Telegram bot token from @BotFather
- `ADMIN_ID` = your numeric Telegram user ID

Optional:
- `PORT` = Railway-provided port; do not hard-code it.

## Deploy

1. Push this folder to GitHub.
2. Create a Railway service from the repository.
3. Add `BOT_TOKEN` and `ADMIN_ID` under Variables.
4. Railway uses `nixpacks.toml` automatically.
5. Start command is `node server.js`.
6. Health endpoint: `/health`.

## Telegram commands

User:
- `/start`
- `/help`
- `/monitor instagram cristiano`
- `/stop`
- `/status`

Admin:
- `/adduser 123456789`
- `/removeuser 123456789`
- `/listusers`

The monitor checks every 2 seconds and sends a screenshot + status only on the first check and when the status changes.

## Important

Do not put your real Telegram bot token in the source code or GitHub.
