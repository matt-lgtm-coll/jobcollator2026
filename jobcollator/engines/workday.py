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

from .base import COUNTRY_ABBREVIATIONS, FetchError, JobPosting, strip_html, title_mentions_location

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


def _check_not_maintenance(name: str, resp):
    """Workday occasionally takes a whole shard down for infrastructure
    maintenance (confirmed: the entire wd3 cluster — Ferring, Zealand
    Pharma, FUJIFILM Diosynth, GN, all unrelated companies, all down at
    once) and 303-redirects every request to a Workday status page instead
    of erroring cleanly. `requests` follows that redirect automatically, so
    what would otherwise surface is a confusing "JSONDecodeError: Expecting
    value" from trying to parse that HTML page as JSON — this gives the
    real reason instead, so a future run (or a human looking at the logs)
    doesn't have to re-diagnose it from scratch. Nothing to fix on our end:
    this always resolves on its own once Workday's maintenance ends."""
    if "community.workday.com" in resp.url:
        raise FetchError(f"{name}: Workday maintenance in progress on this tenant's shard "
                          f"(redirected to {resp.url}) — not an error on our end, try again later")


def fetch_description(session, url: str) -> str:
    """Workday's job detail page (JobPosting.url) is a JS-rendered shell with
    no description in its raw HTML at all, so the generic
    fetch_description_default doesn't work here — this hits the same 'cxs'
    JSON API fetch() uses, just the per-job endpoint instead of the search
    one, which does carry the full description (confirmed: `jobPostingInfo
    .jobDescription`, HTML-formatted)."""
    tenant, wd_part, site = _parse_tenant_site(url)
    base = f"https://{tenant}.{wd_part}.myworkdayjobs.com"
    prefix = f"{base}/{site}"
    if not url.startswith(prefix):
        raise FetchError(f"unexpected Workday job URL shape: {url}")
    external_path = url[len(prefix):]

    session.cookies.clear()  # see fetch()'s comment on Workday's sticky-instance cookies
    # external_path already starts with "/job/..." (it's everything after
    # the site segment in fetch()'s constructed URL), so no extra "/job"
    # here — that would 422 with a doubled-up path.
    resp = session.get(f"{base}/wday/cxs/{tenant}/{site}{external_path}", timeout=30)
    session.cookies.clear()
    _check_not_maintenance("(job description fetch)", resp)
    resp.raise_for_status()
    description = (resp.json().get("jobPostingInfo") or {}).get("jobDescription", "")
    return strip_html(description)


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


def _fetch_pages(name, api_url, base, site, session, applied_facets, search_text, seen_ids, soft_match=False):
    """Paginate one query (an applied-facets filter, or a plain keyword
    search) and append JobPosting rows for anything not already in
    `seen_ids` (shared across calls so the soft-match pass in fetch() can't
    re-add a posting the confirmed pass already found)."""
    postings: list[JobPosting] = []
    offset = 0
    total = None

    for _ in range(MAX_PAGES):
        resp = session.post(
            api_url,
            json={"appliedFacets": applied_facets, "limit": PAGE_LIMIT, "offset": offset, "searchText": search_text},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        session.cookies.clear()
        _check_not_maintenance(name, resp)
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
                soft_match=soft_match,
            ))

        offset += PAGE_LIMIT
        if offset >= total:
            break

    return postings, total


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

    seen_ids: set[str] = set()
    postings: list[JobPosting] = []
    total = None

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
        _check_not_maintenance(name, probe)
        if probe.status_code != 200:
            raise FetchError(f"{name}: POST {api_url} -> HTTP {probe.status_code}")
        facet_key, location_ids = _location_facet_selection(probe.json(), location_filter)
        session.cookies.clear()

        if location_ids:
            # NOT an unconditional fetch: applied_facets is only ever {} here
            # when location_ids came back empty, and that path skips this
            # call entirely (see below) rather than fetching the tenant's
            # entire unfiltered listing by accident.
            postings, total = _fetch_pages(
                name, api_url, base, site, session, {facet_key: location_ids}, "", seen_ids)
        else:
            total = 0  # no facet matched — legitimately zero confirmed results
        # Either way, still worth the soft-match pass below: a posting can
        # mention the place in its title with no location tag whatsoever.
    else:
        postings, total = _fetch_pages(name, api_url, base, site, session, {}, "", seen_ids)

    if location_filter:
        # Confirmed real and worth surfacing (flagged by a user against
        # Zealand Pharma): a posting like "Senior Director, Computational
        # Drug Discovery (DK/US)" filed under Cambridge, MA with *no*
        # Denmark location tag at all — the recruiter just never configured
        # the ATS's multi-location field, even though the role is open to
        # either site. A full unfiltered crawl would catch these but blows
        # the request-count optimization this whole engine exists for on a
        # large tenant (thousands of postings) for the sake of maybe one
        # match, so instead: ask Workday's own keyword search for the place
        # name and its abbreviation (a couple of cheap, narrow queries, not
        # a crawl), then keep only the hits whose *title* actually mentions
        # it — that keyword search also matches unrelated hits deep in a
        # job's full description, which title_mentions_location filters out.
        needle_terms = {location_filter}
        abbr = COUNTRY_ABBREVIATIONS.get(location_filter.lower())
        if abbr:
            needle_terms.add(abbr)

        for term in needle_terms:
            candidates, _ = _fetch_pages(name, api_url, base, site, session, {}, term, seen_ids, soft_match=True)
            postings.extend(c for c in candidates if title_mentions_location(c.title, location_filter))
            # _fetch_pages already added every candidate's id to seen_ids
            # (shared set) even the ones filtered out here, which is exactly
            # what we want — no need to revisit the same id under the other
            # search term.

    if total is None:
        raise FetchError(f"{name}: no response from {api_url}")

    return postings
