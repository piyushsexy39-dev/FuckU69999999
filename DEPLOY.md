# Deploy on any VPS (Ubuntu, Debian, CentOS/Rocky/Alma, Fedora, Arch, ...)

1. In @BotFather: /revoke the old token and create a new one.
2. Upload this folder to the VPS, then:

       cd <folder>
       sudo ./install.sh          # first run creates .env -> edit it (BOT_TOKEN, OWNER_IDS)
       nano .env
       sudo ./install.sh          # installs and starts the bot

   Auto-picks systemd (needs Python >= 3.10) or Docker. Force one: `sudo ./install.sh docker` / `systemd`.

Always on: systemd/Docker restart the bot on crash and on reboot; the supervisor in server.py
restarts it with backoff. Health: http://127.0.0.1:3000/health (503 if the bot process is down) -
point UptimeRobot/Healthchecks at it through a reverse proxy if you want alerts.

Update: copy new files over /opt/cricket-bot (keep .env and *.db), then `sudo systemctl restart cricketbot`
(or `docker compose up -d --build`).

Backup: copy gullycric.db, gullycric.db-wal, gullycric.db-shm and .backup_secret together.
Tests (optional): pip install -r requirements-dev.txt && pytest

## Keeping stats across redeploys / new hosts

The bot sends a database snapshot to the owner's Telegram DM every 60 minutes (when something changed), pins it and deletes the previous one so only one backup stays in the chat.
On start-up with an empty database it downloads the pinned snapshot and restores it automatically, so moving to a new
host or redeploying without a volume does not reset stats. The owner must have pressed /start on the bot in DM once.

- `/backupdb` (owner) - send and pin a backup right now
- `/restoredb` (owner) - reply to a backup `.db` file (or an old `gullycric.db`) to restore it
- Optional env vars: `BACKUP_INTERVAL_MINUTES` (default 60), `BACKUP_CHAT_ID` (default: first OWNER_IDS id)
