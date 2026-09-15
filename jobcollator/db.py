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
    soft_match INTEGER NOT NULL DEFAULT 0,
    jd_text TEXT,
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
    # Migrations for databases created before these columns existed.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "soft_match" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN soft_match INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "jd_text" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN jd_text TEXT")
        conn.commit()
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
                "posted_date, first_seen, last_seen, active, soft_match) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                (company, p.external_id, p.title, p.location, p.category, p.url,
                 p.posted_date, run_date, run_date, int(p.soft_match)),
            )
            new_count += 1
        else:
            cur.execute(
                "UPDATE jobs SET title=?, location=?, category=?, url=?, posted_date=?, "
                "last_seen=?, active=1, soft_match=? WHERE company=? AND external_id=?",
                (p.title, p.location, p.category, p.url, p.posted_date, run_date,
                 int(p.soft_match), company, p.external_id),
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
        "first_seen, last_seen, soft_match, jd_text FROM jobs WHERE active=1 "
        "ORDER BY company, COALESCE(posted_date, '') DESC, title"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def jobs_needing_jd_text(conn):
    """Active postings whose description hasn't been fetched yet (see
    tagging.backfill_descriptions) — jd_text is set exactly once per
    posting and never re-fetched, so this naturally shrinks to just
    whatever's newly discovered each run once the initial backfill across
    the existing active set is done."""
    cur = conn.execute(
        "SELECT company, external_id, url FROM jobs WHERE active=1 AND jd_text IS NULL"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def set_jd_text(conn, company, external_id, text):
    conn.execute(
        "UPDATE jobs SET jd_text=? WHERE company=? AND external_id=?",
        (text, company, external_id),
    )
    conn.commit()


def new_since(conn, run_date):
    """Confirmed active postings first seen on `run_date` — i.e. new in the
    most recent run. Excludes soft matches (see JobPosting.soft_match):
    those are a hint, not a confirmed opening, so they don't drive the "new"
    count/list a push notification would otherwise treat as reliable."""
    cur = conn.execute(
        "SELECT company, title, location, url FROM jobs "
        "WHERE active=1 AND first_seen=? AND soft_match=0 ORDER BY company, title",
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
