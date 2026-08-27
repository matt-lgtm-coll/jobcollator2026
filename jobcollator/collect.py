"""Fetch current job postings for every company in companies.json, store
them in data/jobs.db, and regenerate data/report.html.

Usage:
    python -m jobcollator.collect [--companies path/to/companies.json]
"""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from . import db, report
from .detect import detect
from .engines import ENGINES
from .engines.base import FetchError
from .http import new_session

DEFAULT_COMPANIES_PATH = Path(__file__).resolve().parent.parent / "companies.json"
REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "report.html"
SUMMARY_PATH = Path(__file__).resolve().parent.parent / "data" / "last_run_summary.json"


def load_companies(path: Path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["companies"]


def save_companies(path: Path, companies):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["companies"] = companies
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", type=Path, default=DEFAULT_COMPANIES_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    companies = load_companies(args.companies)
    session = new_session()
    conn = db.connect()
    run_date = date.today().isoformat()

    companies_dirty = False
    exit_code = 0
    failed_companies = []

    for company in companies:
        name = company["name"]
        start_url = company["start_url"]
        engine_name = company.get("engine")

        if not engine_name:
            print(f"[{name}] no engine set — attempting auto-detection...")
            engine_name, start_url = detect(start_url, session=session)
            if engine_name == "unknown":
                print(f"[{name}] could not auto-detect an engine, skipping. "
                      f"Set 'engine' manually in {args.companies.name}.")
                db.record_failure(conn, name, "engine auto-detection failed", run_date=run_date)
                exit_code = 1
                failed_companies.append(name)
                continue
            company["engine"], company["start_url"] = engine_name, start_url
            companies_dirty = True
            print(f"[{name}] detected engine: {engine_name}")

        engine = ENGINES.get(engine_name)
        if engine is None:
            print(f"[{name}] unknown engine '{engine_name}', skipping.")
            db.record_failure(conn, name, f"unknown engine '{engine_name}'", run_date=run_date)
            exit_code = 1
            failed_companies.append(name)
            continue

        try:
            postings = engine.fetch(name, start_url, session, location_filter=company.get("location_filter"))
        except FetchError as e:
            print(f"[{name}] FAILED: {e}")
            db.record_failure(conn, name, str(e), run_date=run_date)
            exit_code = 1
            failed_companies.append(name)
            continue
        except Exception as e:  # noqa: BLE001 - keep collecting other companies
            print(f"[{name}] FAILED (unexpected {type(e).__name__}): {e}")
            db.record_failure(conn, name, f"{type(e).__name__}: {e}", run_date=run_date)
            exit_code = 1
            failed_companies.append(name)
            continue

        stats = db.record_success(conn, name, postings, run_date=run_date)
        print(f"[{name}] {stats['total']} postings "
              f"(+{stats['new']} new, {stats['updated']} updated, {stats['removed']} removed)")

        time.sleep(1)  # be polite between companies

    if companies_dirty:
        save_companies(args.companies, companies)

    out_path = report.generate(conn, args.report)
    print(f"\nReport written to {out_path}")

    new_jobs = db.new_since(conn, run_date)
    summary = {
        "run_date": run_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_active": len(db.active_jobs(conn)),
        "new_count": len(new_jobs),
        "new_jobs": new_jobs,
        "failed_companies": failed_companies,
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Summary written to {SUMMARY_PATH} ({summary['new_count']} new)")

    conn.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
