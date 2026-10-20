"""Engine for Bondi Executive Search (bondi.se), a Stockholm/Malmö recruiter.

A recruiter's public mandate board (/sv/publika-uppdrag): a short list of
Brizy-built cards, each just a title linking to /sv/<slug>. There is no
location, category or date field on the board, and the detail pages are a few
sentences of prose, so location and date are left blank.

The board covers all of Bondi's sectors, not only life science (it currently
includes a real-estate franchise mandate), and there's no sector marker to
filter on, so everything is tracked. `location_filter` is ignored.
"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    resp = session.get(start_url, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "lxml")

    postings: list[JobPosting] = []
    seen = set()
    for a in soup.select("div.brz-posts__item a[href]"):
        h3 = a.find("h3")
        slug = a["href"].rstrip("/").rsplit("/", 1)[-1]
        if not h3 or not slug or slug in seen:
            continue
        seen.add(slug)
        postings.append(JobPosting(
            company=name,
            external_id=slug,
            title=h3.get_text(" ", strip=True),
            location="",
            category="",
            url=urljoin(start_url, a["href"]),
            posted_date=None,
        ))
    return postings
