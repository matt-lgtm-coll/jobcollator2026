"""Engine for Brice (brice.se), a Swedish life-science/medtech recruiter.

Like Black Swans Exist, this is a recruiter's mandate board rather than one
employer's career site, so postings are for Brice's clients (usually
anonymised). The listing at /lediga-jobb/ is server-rendered: one card per
mandate, each an `<a href="/lediga-jobb/?id=N&titleSlug=...">` containing an
`<h3>` title and a small table of (region, engagement type, industry).

The region is a Swedish county/region name ("Skåne", "Stockholm") or a
Nordic-wide label ("The Nordics"), never a country, and mandate text
sometimes says "Denmark or Sweden". There is nothing to filter on
structurally, so `location_filter` is ignored and everything on the board is
tracked — which is the right call for a board that is Swedish by nature.
Cards carry no posting date.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

_ID_RE = re.compile(r"[?&]id=(\d+)")


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    resp = session.get(start_url, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "lxml")

    postings: list[JobPosting] = []
    seen = set()
    for a in soup.select('a[href*="id="]'):
        m = _ID_RE.search(a["href"])
        h3 = a.find("h3")
        if not (m and h3) or m.group(1) in seen:
            continue
        seen.add(m.group(1))

        cells = [td.get_text(" ", strip=True) for td in a.select("footer td")]
        region = cells[0] if cells else ""
        engagement = cells[1] if len(cells) > 1 else ""  # Rekrytering / Interim
        industry = cells[2] if len(cells) > 2 else ""    # Pharma / Medtech / Life Science
        category = " · ".join(x for x in (industry, engagement) if x)

        postings.append(JobPosting(
            company=name,
            external_id=m.group(1),
            title=h3.get_text(" ", strip=True),
            location=region,
            category=category,
            url=urljoin(start_url, a["href"]),
            posted_date=None,
        ))
    return postings
