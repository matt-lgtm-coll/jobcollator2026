"""Engine for Workday-hosted career sites (the "cxs" search API).

Workday career sites live at
    https://<tenant>.wd<N>.myworkdayjobs.com/[<locale>/]<site>
and back their search box with a stable, undocumented-but-widely-relied-on
JSON API at
    POST https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

This is used by a huge share of large enterprises (verified here against
Novartis's career site), so a single connector like this covers many
companies just by pointing it at their career site's start URL.
"""
import re
from datetime import date, timedelta
from urllib.parse import urlparse

from .base import FetchError, JobPosting

PAGE_LIMIT = 20
MAX_PAGES = 300  # safety valve: 300 * 20 = 6000 postings

_LOCALE_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


def _parse_tenant_site(start_url: str):
    parsed = urlparse(start_url)
    host_parts = parsed.netloc.split(".")
    if len(host_parts) < 2 or "myworkdayjobs" not in parsed.netloc:
        raise FetchError(f"not a myworkdayjobs.com URL: {start_url}")
    tenant, wd_part = host_parts[0], host_parts[1]  # e.g. 'novartis', 'wd3'

    segments = [s for s in parsed.path.split("/") if s]
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    if not segments:
        raise FetchError(f"could not find a site path in {start_url}")
    site = segments[0]

    return tenant, wd_part, site


def _parse_posted_on(text: str):
    if not text:
        return None
    text = text.strip().lower()
    today = date.today()
    if text == "posted today":
        return today.isoformat()
    if text == "posted yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = re.match(r"posted (\d+)\+? days? ago", text)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return None  # e.g. "Posted 30+ Days Ago" — not precise enough to convert


def _find_facets(data: dict, param_name: str) -> list[dict]:
    """Collect every facet node in the tree whose facetParameter matches
    `param_name` (case/underscore-insensitive — tenants name these
    differently, e.g. 'locationCountry' vs 'Location_Country')."""
    target = param_name.lower().replace("_", "")
    found = []

    def walk(node):
        if isinstance(node, dict):
            if (node.get("facetParameter") or "").lower().replace("_", "") == target:
                found.append(node)
            for v in node.get("values", []):
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk({"values": data.get("facets", [])})
    return found


def _location_facet_selection(data: dict, location_filter: str):
    """Resolve `location_filter` (e.g. "Denmark") to (facetParameter, ids) —
    the exact key Workday expects back in `appliedFacets`, which varies by
    tenant (seen so far: 'locationCountry', 'Location_Country') and must be
    echoed back verbatim and case-exact or the API 400s.

    Prefer a country-level facet when the tenant exposes one (exact match on
    descriptor — reliable). Not every tenant does, though: some only expose
    per-city/region ids under 'locations', and on those the descriptor is
    sometimes just the bare city name with no country in it at all (e.g.
    Bavarian Nordic's Workday lists "Kvistgaard", not "Kvistgaard, Denmark") —
    a substring match against those descriptors would silently miss every
    posting there. So the city-facet fallback only applies when a country
    name *is* present in at least one descriptor; otherwise we deliberately
    return nothing rather than pretend server-side filtering worked.
    """
    needle = location_filter.lower()

    for country_facet in _find_facets(data, "locationCountry"):
        ids = [v["id"] for v in country_facet.get("values", [])
               if v.get("descriptor", "").lower() == needle]
        if ids:
            return country_facet["facetParameter"], ids

    for locations_facet in _find_facets(data, "locations"):
        ids = [v["id"] for v in locations_facet.get("values", [])
               if needle in v.get("descriptor", "").lower()]
        if ids:
            return locations_facet["facetParameter"], ids

    return None, []


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    tenant, wd_part, site = _parse_tenant_site(start_url)
    base = f"https://{tenant}.{wd_part}.myworkdayjobs.com"
    api_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"

    # Workday pins a session to a specific backend instance via Set-Cookie
    # (its VPS/session cookies literally name the instance). Reusing that
    # across the many different tenants this tool visits in one run has been
    # observed to route a later request to an instance that 502s for no
    # reason a fresh connection wouldn't — so this engine never carries
    # cookies between requests, here or across companies.
    session.cookies.clear()

    applied_facets = {}
    if location_filter:
        # One cheap request to discover which location-facet ids match the
        # filter, then apply those server-side so we don't paginate through
        # postings we're going to throw away anyway.
        probe = session.post(
            api_url,
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if probe.status_code != 200:
            raise FetchError(f"{name}: POST {api_url} -> HTTP {probe.status_code}")
        facet_key, location_ids = _location_facet_selection(probe.json(), location_filter)
        if not location_ids:
            return []  # legitimately zero postings match the filter
        applied_facets[facet_key] = location_ids
        session.cookies.clear()

    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    offset = 0
    total = None

    for _ in range(MAX_PAGES):
        resp = session.post(
            api_url,
            json={"appliedFacets": applied_facets, "limit": PAGE_LIMIT, "offset": offset, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        session.cookies.clear()
        if resp.status_code != 200:
            if offset == 0:
                raise FetchError(f"{name}: POST {api_url} -> HTTP {resp.status_code}")
            break

        data = resp.json()
        if total is None:
            total = data.get("total", 0)
            if total == 0:
                break
        job_postings = data.get("jobPostings", [])
        if not job_postings:
            break

        for job in job_postings:
            external_path = job.get("externalPath", "")
            if not external_path:
                continue
            bullet_fields = job.get("bulletFields") or []
            external_id = bullet_fields[0] if bullet_fields else external_path
            if external_id in seen_ids:
                continue  # Workday's default ordering can drift slightly across pages
            seen_ids.add(external_id)

            postings.append(JobPosting(
                company=name,
                external_id=external_id,
                title=job.get("title", "").strip(),
                location=job.get("locationsText", "") or "",
                category="",  # not returned in the base listing payload
                url=f"{base}/{site}{external_path}",
                posted_date=_parse_posted_on(job.get("postedOn", "")),
            ))

        offset += PAGE_LIMIT
        if offset >= total:
            break

    if total is None:
        raise FetchError(f"{name}: no response from {api_url}")

    return postings
