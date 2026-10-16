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
