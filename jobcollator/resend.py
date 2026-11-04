"""Re-send the Telegram notification for an earlier run's new jobs, without
collecting again. Rebuilds the list from data/jobs.db.

Usage:
    python -m jobcollator.resend [--date YYYY-MM-DD]   (default: today, UTC)
"""
import argparse
import sys
from datetime import date, datetime

from . import db, notify_telegram
from .collect import ARTIFACT_URL_PATH


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="run date whose new jobs to re-send (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    run_date = date.fromisoformat(args.date).isoformat()

    conn = db.connect()
    new_jobs = db.new_since(conn, run_date)
    conn.close()
    summary = {
        "run_date": run_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "new_count": len(new_jobs),
        "new_jobs": new_jobs,
    }
    print(f"{len(new_jobs)} new job(s) first seen on {run_date}")
    if not new_jobs:
        return 0

    dashboard_url = None
    if ARTIFACT_URL_PATH.exists():
        dashboard_url = ARTIFACT_URL_PATH.read_text(encoding="utf-8").strip() or None
    return 0 if notify_telegram.notify(summary, dashboard_url) else 1


if __name__ == "__main__":
    sys.exit(main())
