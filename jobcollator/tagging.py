"""Keyword-based interest tags for postings — e.g. flagging roles relevant
to a particular clinical specialty so they're easy to spot on the
dashboard.

Matching itself (match_tags) runs against title + category + a cached full
job-description text, and is computed fresh at report render time (see
report.py) — nothing tag-related is stored, so editing interest_tags.json
changes what's flagged on the very next render with no other changes
needed. Editing the title/category of a posting was always "free" this
way, but a posting's *description* text often carries the only signal for
what it's actually about (confirmed concretely: an ICON "Medical Director"
posting only mentions "Cardiology" and "GLP1" in its qualifications
section, nowhere in the title) — and fetching that text is a real network
call, not free. So the *fetching* is a separate, cached-forever step
(backfill_descriptions, called once from collect.py after each run): once
a posting's description has been fetched, it's stored in jobs.jd_text and
never re-fetched, keeping the ongoing daily cost bounded to whatever's
newly discovered that day rather than growing with the whole active set.
"""
import json
import re
import time
from pathlib import Path

from . import db
from .engines import ENGINES
from .engines.base import fetch_description_default

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "interest_tags.json"

# A JD-only match needs at least this many *distinct* keywords, not title/
# category matching (see match_tags) — confirmed necessary: a single
# incidental mention (Novo Nordisk's standard "we improve the lives of 30
# million people living with diabetes" boilerplate, present in essentially
# every one of their postings regardless of role) would otherwise flag
# roles with no real connection to the tag, like "Automation Supporter".
# A genuinely relevant JD tends to mention several terms together (the ICON
# "Medical Director" case that motivated JD matching in the first place hit
# four: cardiology, cardiovascular, diabetes, obesity).
MIN_DISTINCT_JD_KEYWORDS = 2


def load_tags(path: Path = DEFAULT_PATH) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tags", [])


def _keyword_matches(keyword: str, text: str, text_lower: str) -> bool:
    """A short ALL-CAPS keyword (a disease abbreviation like "NASH") is
    matched case-sensitively as a whole word — case-insensitive substring
    matching collides with ordinary words (confirmed: "NASH" matched inside
    "Nashville" in a list of office locations). Anything else stays a
    case-insensitive substring match, since some keywords are deliberately
    partial word-stems (e.g. "echocardiogra" to catch both
    "echocardiogram" and "echocardiography" without listing both)."""
    if keyword.isupper():
        return re.search(rf"\b{re.escape(keyword)}\b", text) is not None
    return keyword.lower() in text_lower


def match_tags(title: str, category: str, tags_config: list[dict], jd_text: str = "") -> list[dict]:
    """Which configured tags does this posting mention? Title/category is a
    short, deliberately-written field, so a single keyword hit there is
    trusted outright. The cached job-description text is longer and full of
    boilerplate a title never has, so a JD-only match needs corroborating
    evidence — see MIN_DISTINCT_JD_KEYWORDS — rather than trusting one
    incidental mention. Tight, specific keywords in interest_tags.json still
    matter most; this is a second line of defense, not a replacement."""
    title_cat, title_cat_lower = f"{title} {category}", f"{title} {category}".lower()
    jd_text, jd_lower = jd_text or "", (jd_text or "").lower()

    matched = []
    for tag in tags_config:
        keywords = tag.get("keywords", [])
        if any(_keyword_matches(kw, title_cat, title_cat_lower) for kw in keywords):
            matched.append({"id": tag["id"], "label": tag["label"]})
            continue
        jd_hits = {kw for kw in keywords if _keyword_matches(kw, jd_text, jd_lower)}
        if len(jd_hits) >= MIN_DISTINCT_JD_KEYWORDS:
            matched.append({"id": tag["id"], "label": tag["label"]})
    return matched


def backfill_descriptions(conn, session, companies: list[dict], delay: float = 0.3) -> int:
    """Fetch and cache jd_text for every active posting that doesn't have
    one yet. Each engine may define its own `fetch_description(session,
    url) -> str` when the generic "fetch the URL, strip tags" approach
    doesn't work (confirmed needed for Workday and Workable, both of which
    serve their job detail pages as JS-rendered shells with no
    server-rendered description — see workday.py / workable.py); everything
    else falls back to the shared default. Never raises on a single
    posting's failure — the point is best-effort backfill, not blocking the
    whole run over one bad URL.

    Returns how many postings were newly fetched (for logging)."""
    engine_by_company = {c["name"]: c.get("engine") for c in companies}
    pending = db.jobs_needing_jd_text(conn)
    fetched = 0

    for job in pending:
        engine_name = engine_by_company.get(job["company"])
        engine = ENGINES.get(engine_name) if engine_name else None
        fetch_fn = getattr(engine, "fetch_description", None) or fetch_description_default

        try:
            text = fetch_fn(session, job["url"])
        except Exception as e:  # noqa: BLE001 - best-effort; one bad URL shouldn't stop the backfill
            print(f"  [tagging] couldn't fetch description for {job['company']} {job['url']}: {e}")
            time.sleep(delay)
            continue  # leave jd_text NULL so this one's retried on a future run, not stuck forever

        db.set_jd_text(conn, job["company"], job["external_id"], text)
        fetched += 1
        time.sleep(delay)

    return fetched
