"""Engine for PeakSearch (peaksearch.se), a Swedish life-science recruiter.

A recruiter's mandate board (postings are for PeakSearch's clients). Their
/rekryteringsuppdrag/ page lists mandates as cards: an `<h3>` title followed
by a description and a link to the mandate in their ATS
(`peaksearch.positionett.se/assignment.php?id=N`, whose robots.txt only
blocks a few named SEO bots).

Two quirks worth knowing:

* Filled mandates stay on the page, prefixed "TILLSATT" ("filled"). They are
  skipped — reporting them would pad the open-roles count with dead ones.
* Neither the card nor the ATS page has a location or a posting date field,
  so both are left blank (the report shows "—"). `location_filter` is
  ignored; the board is Swedish by nature and the text sometimes names
  Denmark or the Nordics.
"""
import re

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

_ID_RE = re.compile(r"assignment\.php\?id=(\d+)")


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    resp = session.get(start_url, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
    soup = BeautifulSoup(resp.text, "lxml")

    postings: list[JobPosting] = []
    seen = set()
    for h3 in soup.find_all("h3", attrs={"data-fontsize": True}):
        title = h3.get_text(" ", strip=True)
        link = h3.find_next("a", href=_ID_RE)
        if not (title and link):
            continue
        m = _ID_RE.search(link["href"])
        if m.group(1) in seen or title.upper().startswith("TILLSATT"):
            continue
        seen.add(m.group(1))
        postings.append(JobPosting(
            company=name,
            external_id=m.group(1),
            title=title,
            location="",
            category="",
            url=link["href"],
            posted_date=None,
        ))
    return postings
