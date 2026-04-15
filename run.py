"""
run.py — entry point

  python run.py start     ← start monitoring + dashboard
  python run.py check     ← run one immediate check of all sites, then exit
"""
import sys
import asyncio
import logging
import threading
import warnings
import urllib3
from pathlib import Path

import yaml
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.WARNING,
    handlers=[logging.FileHandler("data/monitor.log")]
)

class _ActivityHandler(logging.Handler):
    _ANSI = __import__("re").compile(r"\x1b\[[0-9;]*m")
    def emit(self, record):
        try:
            msg = self._ANSI.sub("", self.format(record))
            with open("data/activity.log", "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

_ah = _ActivityHandler()
_ah.setLevel(logging.INFO)
_ah.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
monitor_logger = logging.getLogger("monitor")
monitor_logger.setLevel(logging.INFO)
monitor_logger.addHandler(_ah)
monitor_logger.propagate = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_sites() -> list:
    with open("config/sites.yaml") as f:
        return yaml.safe_load(f).get("sites", [])


# ── Commands ──────────────────────────────────────────────────────────────────

async def _run_all_once(sites):
    from monitor.core import check_site
    await asyncio.gather(*[check_site(s) for s in sites])


def cmd_check():
    from monitor.storage import init_db
    init_db()
    sites = load_sites()
    asyncio.run(_run_all_once(sites))
    print("One-shot check complete.")


def cmd_start():
    from monitor.storage  import init_db
    from monitor.scheduler import build_scheduler
    from dashboard.app    import start_dashboard, set_scheduler

    init_db()
    sites = load_sites()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    scheduler = build_scheduler(sites)
    set_scheduler(scheduler)

    scheduler.start()

    # Run dashboard in a background thread
    dash_thread = threading.Thread(
        target=start_dashboard,
        kwargs={"host": "0.0.0.0", "port": 5000, "debug": False},
        daemon=True,
    )
    dash_thread.start()
    print("Dashboard running at http://localhost:5000")
    print("Monitoring started. Press Ctrl+C to stop.")

    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("Stopped.")


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "check":
        cmd_check()
    elif cmd == "start":
        cmd_start()
    else:
        print(__doc__)
        sys.exit(1)
