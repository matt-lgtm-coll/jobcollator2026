"""Engine for Moveup (moveup.se), a Swedish life-science/medtech recruiter.

A recruiter's mandate board (postings are for Moveup's clients), on
WordPress + Elementor. Two sources, deliberately combined:

* The live listing at /lediga-jobb/ is the authority on what is *open*: it
  shows ~15 current mandates, each with a title link to /jobs/<slug>/ and a
  location line ("South of Sweden", "Göteborg (preferred)", "Oslo").
* The WP REST API (`/wp-json/wp/v2/jobb`) holds every mandate Moveup has
  ever posted (548 and counting, back to 2024), so listing it directly would
  report hundreds of long-filled roles as open. It is used only to enrich the
  ~15 live slugs with a posting date and category, in one request.

Locations are free-text regions and sometimes outside Sweden, so
`location_filter` is ignored and the whole board is tracked.
"""
import html
import re

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting
from urllib.parse import urlparse

_SLUG_RE = re.compile(r"/jobs/([^/]+)/?$")


def _origin(start_url: str) -> str:
    p = urlparse(start_url)
    return f"{p.scheme}://{p.netloc}"


def _enrich(session, origin: str, slugs: list[str]) -> dict[str, dict]:
    """slug -> {'date': 'YYYY-MM-DD', 'category': str}. Best-effort: a failure
    here costs dates/categories, not the postings themselves."""
    try:
        resp = session.get(
            f"{origin}/wp-json/wp/v2/jobb",
            params={"slug": ",".join(slugs), "per_page": 100, "_embed": 1},
            timeout=30,
        )
        if resp.status_code != 200:
            return {}
        items = resp.json()
    except (ValueError, OSError):
        return {}

    out = {}
    for item in items:
        terms = [t for group in (item.get("_embedded") or {}).get("wp:term") or [] for t in group]
        cats = [html.unescape(t["name"]) for t in terms if t.get("taxonomy") == "jobb-categories"]
        out[item.get("slug")] = {
            "date": (item.get("date") or "")[:10] or None,
            "category": ", ".join(cats),
        }
    return out


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    resp = session.get(start_url, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "lxml")

    cards = []
    seen = set()
    for a in soup.select("h4.elementor-heading-title a[href]"):
        m = _SLUG_RE.search(a["href"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        container = a.find_parent(class_="elementor-container")
        loc_el = container.select_one(".elementor-icon-list-text") if container else None
        cards.append((m.group(1), a.get_text(" ", strip=True), a["href"],
                      loc_el.get_text(" ", strip=True) if loc_el else ""))

    extra = _enrich(session, _origin(start_url), [c[0] for c in cards]) if cards else {}

    return [
        JobPosting(
            company=name,
            external_id=slug,
            title=title,
            location=location,
            category=extra.get(slug, {}).get("category", ""),
            url=href,
            posted_date=extra.get(slug, {}).get("date"),
        )
        for slug, title, href, location in cards
    ]
