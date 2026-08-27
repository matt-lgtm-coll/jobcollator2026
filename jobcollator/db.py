"""SQLite storage: keeps history across runs so the report can show what's
new since last time and what's been filled/pulled since it last appeared."""
import sqlite3
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    category TEXT,
    url TEXT NOT NULL,
    posted_date TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (company, external_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    run_date TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    postings_found INTEGER
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def record_success(conn, company, postings, run_date=None):
    """Upsert this run's postings for `company` and mark anything that
    dropped out of the listing (i.e. filled/removed) as inactive."""
    run_date = run_date or date.today().isoformat()
    cur = conn.cursor()
    new_count = updated_count = 0
    seen_ids = [p.external_id for p in postings]

    for p in postings:
        cur.execute(
            "SELECT 1 FROM jobs WHERE company=? AND external_id=?",
            (company, p.external_id),
        )
        if cur.fetchone() is None:
            cur.execute(
                "INSERT INTO jobs (company, external_id, title, location, category, url, "
                "posted_date, first_seen, last_seen, active) VALUES (?,?,?,?,?,?,?,?,?,1)",
                (company, p.external_id, p.title, p.location, p.category, p.url,
                 p.posted_date, run_date, run_date),
            )
            new_count += 1
        else:
            cur.execute(
                "UPDATE jobs SET title=?, location=?, category=?, url=?, posted_date=?, "
                "last_seen=?, active=1 WHERE company=? AND external_id=?",
                (p.title, p.location, p.category, p.url, p.posted_date, run_date,
                 company, p.external_id),
            )
            updated_count += 1

    if seen_ids:
        placeholders = ",".join("?" * len(seen_ids))
        cur.execute(
            f"SELECT external_id FROM jobs WHERE company=? AND active=1 "
            f"AND external_id NOT IN ({placeholders})",
            (company, *seen_ids),
        )
    else:
        cur.execute("SELECT external_id FROM jobs WHERE company=? AND active=1", (company,))
    removed_ids = [row[0] for row in cur.fetchall()]
    if removed_ids:
        placeholders = ",".join("?" * len(removed_ids))
        cur.execute(
            f"UPDATE jobs SET active=0 WHERE company=? AND external_id IN ({placeholders})",
            (company, *removed_ids),
        )

    cur.execute(
        "INSERT INTO runs (company, run_date, status, detail, postings_found) VALUES (?,?,?,?,?)",
        (company, run_date, "ok", None, len(postings)),
    )
    conn.commit()
    return {"new": new_count, "updated": updated_count, "removed": len(removed_ids), "total": len(postings)}


def record_failure(conn, company, error, run_date=None):
    run_date = run_date or date.today().isoformat()
    conn.execute(
        "INSERT INTO runs (company, run_date, status, detail, postings_found) VALUES (?,?,?,?,?)",
        (company, run_date, "error", str(error), None),
    )
    conn.commit()


def active_jobs(conn):
    cur = conn.execute(
        "SELECT company, external_id, title, location, category, url, posted_date, "
        "first_seen, last_seen FROM jobs WHERE active=1 "
        "ORDER BY company, COALESCE(posted_date, '') DESC, title"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def new_since(conn, run_date):
    """Active postings first seen on `run_date` — i.e. new in the most recent run."""
    cur = conn.execute(
        "SELECT company, title, location, url FROM jobs "
        "WHERE active=1 AND first_seen=? ORDER BY company, title",
        (run_date,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def recent_runs(conn, limit=50):
    cur = conn.execute(
        "SELECT company, run_date, status, detail, postings_found FROM runs "
        "ORDER BY id DESC LIMIT ?", (limit,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
