"""Engine for the HiTalento "career theme" WordPress platform.

Confirmed on Hemab's career site (career.hitalento.com/hemab/). HiTalento is
a white-label vendor: every client site is a WordPress install running the
same `hitalento-career-theme`, so this engine should work unmodified for any
other company hosted at `career.hitalento.com/<slug>/`. Tell-tale sign:
`hitalento-career-theme` appears in the page's own asset URLs.

Job postings are a custom `job` post type queried through WordPress's
generic AJAX endpoint:

    POST {start_url}wp-admin/admin-ajax.php
    body: action=get_additional_jobads&page=<N>&size=<page size>&increment=0
          &terms[]=<value of filter select #1>&terms[]=<value of filter select #2>...

`terms[]` must carry exactly one value per `<select class="category-filter">`
on the listing page, in DOM order — the theme's own JS builds it the same
way ($('.category-filter').each -> push .val()). Sending more than one value
for a single filter (e.g. trying to OR two location ids together) makes the
endpoint return zero results, so a location filter that matches multiple
option ids requires one request per id, merged and de-duped afterward.

The response is JSON: {"content": "<...job card HTML...>", "max_num_pages": N}.
Job cards don't carry a numeric id, so the trailing URL slug (e.g.
"director-clinical-pharmacology") is used as the stable external_id.
"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

PAGE_SIZE = 100  # generous — large enough to get everything in one page for a
                  # single-location career site; pagination still applies if not.
MAX_PAGES = 50  # safety valve: 50 * 100 = 5000 postings per filter value


def _ajax_url(start_url: str) -> str:
    return start_url.rstrip("/") + "/wp-admin/admin-ajax.php"


def _location_filter_index_and_ids(soup: BeautifulSoup, location_filter: str):
    """Find every `.category-filter` select (in DOM order) and, among them,
    the one holding location options plus which of its option values match
    `location_filter` (substring, case-insensitive, against the option
    label). Returns (index_of_location_select_or_None, matching_ids)."""
    selects = soup.select("select.category-filter")
    needle = location_filter.lower()

    for idx, select in enumerate(selects):
        name_id = f"{select.get('name', '')} {select.get('id', '')}".lower()
        if "location" not in name_id:
            continue
        ids = [
            opt.get("value") for opt in select.select("option")
            if opt.get("value") and needle in opt.get_text(strip=True).lower()
        ]
        if ids:
            return idx, ids

    return None, []


def _parse_cards(html: str, base: str) -> list[JobPosting]:
    soup = BeautifulSoup(html, "lxml")
    postings = []

    for card in soup.select("div.position-card-wrapper"):
        link = card.select_one("a[href]")
        title_el = card.select_one("h4")
        if not link or not title_el:
            continue

        href = link["href"]
        slug = [s for s in href.rstrip("/").split("/") if s][-1] if href else None
        if not slug:
            continue

        # These spans are an unordered set of taxonomy term labels attached
        # to the post (confirmed: sometimes a plain location, sometimes a
        # location plus a work-mode tag like "Hybrid / remote", in no fixed
        # order) — not a fixed (location, category) pair. Join them rather
        # than guess a position, so nothing gets silently misclassified.
        spans = card.select(".position-card--content--departments span.title")
        texts = [s.get_text(strip=True) for s in spans if s.get_text(strip=True)]

        postings.append(JobPosting(
            company="",  # filled in by caller
            external_id=slug,
            title=title_el.get_text(strip=True),
            location=", ".join(texts),
            category="",  # not reliably distinguishable from location on the listing card
            url=urljoin(base, href),
            posted_date=None,  # not exposed on the listing card
        ))

    return postings


def _fetch_terms(name, ajax_url, base, session, terms: list) -> list[JobPosting]:
    postings = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        resp = session.post(
            ajax_url,
            data={
                "action": "get_additional_jobads",
                "page": page,
                "size": PAGE_SIZE,
                "increment": 0,
                "terms[]": terms,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            if page == 1:
                raise FetchError(f"{name}: POST {ajax_url} -> HTTP {resp.status_code}")
            break

        try:
            data = resp.json()
        except ValueError:
            raise FetchError(f"{name}: non-JSON response from {ajax_url}")

        for posting in _parse_cards(data.get("content", ""), base):
            if posting.external_id in seen_ids:
                continue
            seen_ids.add(posting.external_id)
            posting.company = name
            postings.append(posting)

        max_pages = data.get("max_num_pages") or 1
        if page >= max_pages:
            break

    return postings


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    ajax_url = _ajax_url(start_url)

    resp = session.get(start_url, timeout=30)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")
    if "hitalento-career-theme" not in resp.text.lower():
        raise FetchError(f"{name}: {start_url} doesn't look like a HiTalento career page")

    soup = BeautifulSoup(resp.text, "lxml")
    n_filters = len(soup.select("select.category-filter"))

    if location_filter:
        loc_idx, location_ids = _location_filter_index_and_ids(soup, location_filter)
        if not location_ids:
            return []  # legitimately zero postings match the filter

        postings: list[JobPosting] = []
        seen_ids = set()
        for location_id in location_ids:
            terms = [""] * n_filters
            terms[loc_idx] = location_id
            for posting in _fetch_terms(name, ajax_url, start_url, session, terms):
                if posting.external_id in seen_ids:
                    continue
                seen_ids.add(posting.external_id)
                postings.append(posting)
        return postings

    terms = [""] * n_filters
    return _fetch_terms(name, ajax_url, start_url, session, terms)
