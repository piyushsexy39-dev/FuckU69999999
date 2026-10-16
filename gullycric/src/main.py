import os
import sys
import asyncio
import fcntl
import signal
import time
import logging

from .config import config
from .db.session import init_db
from .bot import create_bot_application
from .sweeper import sweeper

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("gullycric.main")

LOCK_FILE = "/tmp/gullycric_bot.lock"
_lock_fd = None

def acquire_single_instance_lock() -> bool:
    global _lock_fd
    try:
        _lock_fd = open(LOCK_FILE, "a+")
        for attempt in range(3):
            try:
                fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _lock_fd.seek(0)
                _lock_fd.truncate()
                _lock_fd.write(str(os.getpid()))
                _lock_fd.flush()
                logger.info(f"Acquired single-instance lock for PID {os.getpid()}.")
                return True
            except (BlockingIOError, IOError):
                try:
                    _lock_fd.seek(0)
                    pid_str = _lock_fd.read().strip()
                    if pid_str and pid_str.isdigit():
                        old_pid = int(pid_str)
                        if old_pid != os.getpid():
                            logger.warning(f"Existing bot instance PID {old_pid} found. Terminating to take over...")
                            try:
                                os.kill(old_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                except Exception as e:
                    logger.warning(f"Error checking old PID: {e}")
                time.sleep(1.0)

        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.seek(0)
        _lock_fd.truncate()
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except Exception as e:
        logger.error(f"Cannot acquire lock ({e}). Exiting to prevent Telegram polling conflict.")
        return False

async def main():
    if not acquire_single_instance_lock():
        return

    from .services import db_backup
    restore_status = await db_backup.restore_latest_if_empty()
    logger.info("Database restore check: %s", restore_status)

    logger.info("Initializing database...")
    await init_db()

    sweeper_task = asyncio.create_task(sweeper.run_loop())

    logger.info("Starting Telegram Bot with configured credentials...")
    from telegram.error import InvalidToken
    app = create_bot_application()
    sweeper.bot = app.bot  # lets the sweeper tidy up pinned lobby messages
    backup_task = asyncio.create_task(db_backup.run_backup_loop(app.bot))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal():
        logger.info("Termination signal received. Shutting down gracefully...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    from telegram.error import InvalidToken

    while not stop_event.is_set():
        try:
            async with app:
                await app.start()
                await app.updater.start_polling(
                    drop_pending_updates=True,
                    poll_interval=0.0,
                    timeout=20,
                    bootstrap_retries=-1,
                )
                logger.info("Ultimate Cricket Bot is now polling for Telegram updates!")
                try:
                    await stop_event.wait()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    break
                finally:
                    logger.info("Stopping updater...")
                    try:
                        await app.updater.stop()
                    except Exception:
                        pass
                    try:
                        await app.stop()
                    except Exception:
                        pass
                break
        except InvalidToken:
            logger.error("Telegram rejected BOT_TOKEN. Rotate it with @BotFather and update .env.")
            raise SystemExit(1)
        except Exception as e:
            logger.warning(f"Transient polling error: {e}. Reconnecting in 1s...")
            await asyncio.sleep(1)

    sweeper.stop()
    sweeper_task.cancel()
    backup_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
