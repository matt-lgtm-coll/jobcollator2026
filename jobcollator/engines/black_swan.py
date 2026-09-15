"""Engine for Black Swans Exist (blackswansexist.com), a life-science
recruitment agency covering Denmark and Germany.

Unlike every other engine here, this isn't one employer's own career site —
it's a boutique recruiter's mandate board: postings are for *their clients*
(often anonymised — "a growing biologics CDMO" rather than a named company),
not for "Black Swans Exist" itself. `collect.py` still stores every posting
under the companies.json entry name (see db.record_success — the `company`
column always comes from the caller, not from JobPosting.company), so there's
nowhere else to put a hint about which mandate this is; the tile/detail-page
text itself is the only signal, same as any recruiter board.

The site is WordPress + WP Job Manager. Its REST API exposes the full list
cleanly:

    GET {origin}/wp-json/wp/v2/job-listings?per_page=100

...but the location taxonomy (`job_listing_region`) is never actually used
(confirmed: empty on every posting) — location only exists as a plain-text
field ("Denmark", "Germany", "UK", ...) rendered next to a flag icon in the
`.job-location` div, and that div is *not* exposed anywhere in the REST
response (checked `meta`, `acf`, embedded terms — none of them carry it). So
this engine fetches each posting's own detail page (`link` from the REST
item) to read that field — genuinely one extra request per posting, but the
whole site only lists ~20-30 jobs total, so it's cheap.
"""
import html
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

PAGE_SIZE = 100  # generous — comfortably covers this site's whole listing in one page
MAX_PAGES = 10  # safety valve: 10 * 100 = 1000 postings


def _origin(start_url: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _job_location(session, url: str) -> str:
    """The `.job-location` div's visible text (e.g. "Denmark") — the flag
    <img>'s alt text is a second, redundant source of the same value but
    get_text() already ignores it, so no need to fall back to alt."""
    resp = session.get(url, timeout=30)
    if resp.status_code != 200:
        return ""
    soup = BeautifulSoup(resp.text, "lxml")
    el = soup.select_one(".job-location")
    return el.get_text(strip=True) if el else ""


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    origin = _origin(start_url)
    api_url = f"{origin}/wp-json/wp/v2/job-listings"

    listings = []
    for page in range(1, MAX_PAGES + 1):
        resp = session.get(api_url, params={"per_page": PAGE_SIZE, "page": page, "_embed": 1}, timeout=30)
        if resp.status_code == 400:
            break  # WP's own "you've paged past the last page" response
        if resp.status_code != 200:
            if page == 1:
                raise FetchError(f"{name}: GET {api_url} -> HTTP {resp.status_code}")
            break
        try:
            batch = resp.json()
        except ValueError:
            raise FetchError(f"{name}: non-JSON response from {api_url}")
        if not batch:
            break
        listings.extend(batch)
        if len(batch) < PAGE_SIZE:
            break

    postings: list[JobPosting] = []
    for item in listings:
        job_id = str(item.get("id"))
        # WP's REST API entity-encodes rendered titles and term names (e.g.
        # "R&amp;D", "Founding Director &#8211; AU Cyber") rather than
        # returning plain text, so both need unescaping.
        raw_title = (item.get("title") or {}).get("rendered", "")
        title = html.unescape(re.sub(r"\s+", " ", raw_title)).strip()
        link = item.get("link")
        if not job_id or not title or not link:
            continue

        # Second slot of the embedded taxonomy terms is job_listing_category
        # (see the taxonomies order declared on the job_listing post type);
        # the first (job_listing_region) is always empty on this site.
        embedded_terms = (item.get("_embedded") or {}).get("wp:term") or []
        categories = embedded_terms[1] if len(embedded_terms) > 1 else []
        category = ", ".join(html.unescape(t["name"]) for t in categories if t.get("name"))

        location = _job_location(session, link)
        if location_filter and location_filter.lower() not in location.lower():
            continue

        postings.append(JobPosting(
            company=name,
            external_id=job_id,
            title=title,
            location=location,
            category=category,
            url=link,
            posted_date=(item.get("date") or "")[:10] or None,
        ))

    return postings
