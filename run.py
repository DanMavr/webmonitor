"""
run.py — WebMonitor entry point

  python run.py start   — start monitoring + dashboard (default)
  python run.py check   — run one immediate check of all sites, then exit
"""
import sys
import logging
import threading
import warnings
from pathlib import Path

import yaml
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

# ── Paths (anchored to this file so cwd doesn't matter) ──────────────────────
ROOT     = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    handlers=[logging.FileHandler(DATA_DIR / "monitor.log")]
)

class _ActivityHandler(logging.Handler):
    _ANSI = __import__("re").compile(r"\x1b\[[0-9;]*m")
    def emit(self, record):
        try:
            msg = self._ANSI.sub("", self.format(record))
            with open(DATA_DIR / "activity.log", "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

_ah = _ActivityHandler()
_ah.setLevel(logging.INFO)
_ah.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger = logging.getLogger("monitor")
logger.setLevel(logging.INFO)
logger.addHandler(_ah)
logger.propagate = False


# ── Config ────────────────────────────────────────────────────────────────────
def load_sites() -> list:
    cfg = ROOT / "config" / "sites.yaml"
    with open(cfg) as f:
        return yaml.safe_load(f).get("sites", [])


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_check():
    from monitor.storage  import init_db
    from monitor.core     import check_site
    init_db()
    for site in load_sites():
        check_site(site)
    print("One-shot check complete.")


def cmd_start():
    from monitor.storage  import init_db
    from monitor.scheduler import build_scheduler
    from dashboard.app    import start_dashboard, set_scheduler

    init_db()
    sites = load_sites()

    scheduler = build_scheduler(sites)
    set_scheduler(scheduler)
    scheduler.start()

    dash_thread = threading.Thread(
        target=start_dashboard,
        kwargs={"host": "0.0.0.0", "port": 5000, "debug": False},
        daemon=True,
    )
    dash_thread.start()
    print("Dashboard running at http://localhost:5000")
    print("Monitoring started. Press Ctrl+C to stop.")

    try:
        threading.Event().wait()      # block main thread until Ctrl+C
    except KeyboardInterrupt:
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
