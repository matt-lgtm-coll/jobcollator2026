"""Engine for Teamtailor career sites (TFS HealthScience, Haeger & Carlsson).

Every Teamtailor career site publishes its full open-job list as an RSS feed
at `{origin}/jobs.rss`, with structured per-job locations in a `tt:locations`
extension (one `tt:location` per site, each with `city` and `country`). That
feed is a documented, public integration surface, so it's used instead of
scraping the JS-rendered HTML listing (TFS's `/jobs` page contains no job
links in its raw HTML at all, while the RSS has all 38). For Haeger & Carlsson
the RSS and the HTML listing were confirmed to carry the identical 18 ids.

A posting can list several locations across several countries (TFS routinely
tags one req with e.g. Barcelona + Gothenburg + Warszawa). `location_filter`
matches if *any* of them is the given country or city, and the other
countries are appended to the displayed location so the multi-site nature
isn't hidden. The feed has no server-side filter, but it is one small request
for the whole company, so there is nothing to save by filtering earlier.

The RSS descriptions of some H&C postings contain literal U+FFFD characters
(mojibake from the source data, not a decoding bug here); the description is
only used for interest-tag matching, where it doesn't matter.
"""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from .base import FetchError, JobPosting

NS = "{https://teamtailor.com/locations}"
_ID_RE = re.compile(r"/jobs/(\d+)")


def _origin(start_url: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _locations(item) -> list[tuple[str, str]]:
    container = item.find(NS + "locations")
    if container is None:
        return []
    out = []
    for loc in container:
        city = (loc.findtext(NS + "city") or "").strip()
        country = (loc.findtext(NS + "country") or "").strip()
        if city or country:
            out.append((city, country))
    return out


def _unique(seq):
    return list(dict.fromkeys(x for x in seq if x))


def _matches(city: str, country: str, wanted: str) -> bool:
    wanted = wanted.lower()
    return wanted in (city.lower(), country.lower())


def _format_location(locs: list[tuple[str, str]], wanted: str | None) -> str:
    if not wanted:
        return "; ".join(_unique(f"{c}, {k}" if c and k else (c or k) for c, k in locs))
    hit = [(c, k) for c, k in locs if _matches(c, k, wanted)]
    text = ", ".join(_unique([c for c, _ in hit]) + _unique([k for _, k in hit]))
    others = _unique(k for c, k in locs if not _matches(c, k, wanted))
    others = [k for k in others if k not in {k2 for _, k2 in hit}]
    return f"{text} (also {', '.join(others)})" if others else text


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    feed_url = f"{_origin(start_url)}/jobs.rss"
    resp = session.get(feed_url, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {feed_url} -> HTTP {resp.status_code}")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise FetchError(f"{name}: {feed_url} is not valid RSS ({e})")

    postings: list[JobPosting] = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        m = _ID_RE.search(link)
        if not (m and title):
            continue

        locs = _locations(item)
        if location_filter and not any(_matches(c, k, location_filter) for c, k in locs):
            continue

        posted = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                posted = parsedate_to_datetime(pub).date().isoformat()
            except (TypeError, ValueError):
                pass

        category = (item.findtext(NS + "department") or item.findtext(NS + "role") or "").strip()

        postings.append(JobPosting(
            company=name,
            external_id=m.group(1),
            title=title,
            location=_format_location(locs, location_filter),
            category=category,
            url=link,
            posted_date=posted,
        ))
    return postings
