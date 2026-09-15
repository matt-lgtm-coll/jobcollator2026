"""Engine for the Attrax career-site platform.

Confirmed on ICON plc's career site (careers.iconplc.com/jobs). Tell-tale
signs: `attraxbundle.js`, and job listings rendered server-side as
`<div class="attrax-vacancy-tile" data-jobid="...">` cards under a
`ListWidget`/`ListDataPaginationWidget` pair.

The listing page takes plain query-string params (confirmed by watching the
site's own pagination control do a full navigation, not an AJAX call):
  - `page`   — 1-indexed page number
  - `size`   — results per page (48 is the platform's own max; requesting
               more is silently capped back down to 48)
  - `q`      — free-text search

`q` is NOT used for location filtering here despite looking like it should
be (and despite genuinely narrowing the result count) — confirmed on ICON
that it's an unreliable index: `q=Denmark` missed a real EMEA-wide "Medical
Director" posting that `q=Copenhagen` happened to catch, because that
posting's *displayed* location text only shows one of its several valid
locations ("UK, Reading"), and `q`'s indexing apparently doesn't always
cover the full set either. What *is* complete and authoritative is each
tile's CSS class list: Attrax tags every valid location as a
`attrax-vacancy-tile--<slug>` class (confirmed: the Medical Director tile's
classes include `attrax-vacancy-tile--copenhagen attrax-vacancy-tile--denmark`
right alongside `--sofia --bulgaria`, `--paris --france`, etc. — one
city/country pair per valid location, always adjacent in that order). So
this engine always fetches the full unfiltered listing and filters on that
class list instead of trusting any server-side search param.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

PAGE_SIZE = 48
MAX_PAGES = 60  # safety valve: 60 * 48 = 2880 postings

_TOTAL_RESULTS_RE = re.compile(r'res-number">\s*(\d+)')


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _location_match(tile_classes: list[str], location_filter: str):
    """Returns the derived "City, Country" label if `location_filter` is
    among this tile's location classes, else None. The class immediately
    before the matching country class is that location's city (confirmed
    pattern: city class then country class, repeated per valid location)."""
    marker = f"attrax-vacancy-tile--{_slug(location_filter)}"
    if marker not in tile_classes:
        return None
    idx = tile_classes.index(marker)
    if idx > 0:
        city_token = tile_classes[idx - 1]
        prefix = "attrax-vacancy-tile--"
        if city_token.startswith(prefix):
            city = city_token[len(prefix):].replace("-", " ").title()
            return f"{city}, {location_filter.title()}"
    return location_filter.title()


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    reported_total = None

    for page in range(1, MAX_PAGES + 1):
        resp = session.get(start_url, params={"size": PAGE_SIZE, "page": page}, timeout=30)
        if resp.status_code != 200:
            if page == 1:
                raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
            break

        if page == 1 and "attrax" not in resp.text.lower():
            raise FetchError(f"{name}: {start_url} doesn't look like an Attrax career page")
        if reported_total is None:
            m = _TOTAL_RESULTS_RE.search(resp.text)
            if m:
                reported_total = int(m.group(1))

        soup = BeautifulSoup(resp.text, "lxml")
        tiles = soup.select("div.attrax-vacancy-tile[data-jobid]")
        if not tiles:
            break

        for tile in tiles:
            job_id = tile.get("data-jobid")
            title_el = tile.select_one("a.attrax-vacancy-tile__title")
            if not job_id or not title_el or not title_el.get("href"):
                continue
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            tile_classes = (tile.get("class") or [])
            if location_filter:
                derived_location = _location_match(tile_classes, location_filter)
                if derived_location is None:
                    continue
                location = derived_location
            else:
                location_el = tile.select_one(".attrax-vacancy-tile__location-freetext .attrax-vacancy-tile__item-value")
                location = location_el.get_text(strip=True) if location_el else ""

            category_el = tile.select_one(".attrax-vacancy-tile__option-business-area .attrax-vacancy-tile__item-value")

            postings.append(JobPosting(
                company=name,
                external_id=job_id,
                title=title_el.get_text(strip=True),
                location=location,
                category=category_el.get_text(strip=True) if category_el else "",
                url=urljoin(start_url, title_el["href"]),
                posted_date=None,  # not exposed on the listing tile
            ))

        if reported_total is not None:
            if page * PAGE_SIZE >= reported_total:
                break
        elif len(tiles) < PAGE_SIZE:
            break

    return postings
