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
  - `q`      — free-text search that also matches on location, so passing a
               place name here filters server-side the same way `sf_j2w`'s
               `locationsearch` and `workday`'s location facets do
"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

PAGE_SIZE = 48
MAX_PAGES = 60  # safety valve: 60 * 48 = 2880 postings


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    params = {"size": PAGE_SIZE}
    if location_filter:
        params["q"] = location_filter

    postings: list[JobPosting] = []
    seen_ids: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        resp = session.get(start_url, params={**params, "page": page}, timeout=30)
        if resp.status_code != 200:
            if page == 1:
                raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
            break

        if page == 1 and "attrax" not in resp.text.lower():
            raise FetchError(f"{name}: {start_url} doesn't look like an Attrax career page")

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

            location_el = tile.select_one(".attrax-vacancy-tile__location-freetext .attrax-vacancy-tile__item-value")
            category_el = tile.select_one(".attrax-vacancy-tile__option-business-area .attrax-vacancy-tile__item-value")

            postings.append(JobPosting(
                company=name,
                external_id=job_id,
                title=title_el.get_text(strip=True),
                location=location_el.get_text(strip=True) if location_el else "",
                category=category_el.get_text(strip=True) if category_el else "",
                url=urljoin(start_url, title_el["href"]),
                posted_date=None,  # not exposed on the listing tile
            ))

        if len(tiles) < PAGE_SIZE:
            break

    return postings
