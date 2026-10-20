"""Engine for Eightfold AI career sites (AstraZeneca).

start_url must carry the tenant's `domain` as a query param, because Eightfold
can't derive it from the hostname reliably:

    https://astrazeneca.eightfold.ai/careers?domain=astrazeneca.com

Listing:  GET {origin}/api/pcsx/search?domain=D&query=&start=N&location=L
          -> {"data": {"count": N, "positions": [...]}}, always 10 per page
          (`num=` is ignored — confirmed 10/25/50/100 all return 10).
Detail:   GET {origin}/api/pcsx/position_details?position_id=ID&domain=D
          -> data.jobDescription (HTML). The /careers/job/ID page itself is
          a JS shell, so this engine defines its own `fetch_description`.

The `location=` param is a *fuzzy geographic* match, not a string filter, so
it is only used to cut the result set down, never trusted: querying
"Gothenburg" returned AstraZeneca's Mölndal site (correct — that is its
Gothenburg-area site) but also a Stockholm "Strategic Account Manager"
role. Every returned position is therefore re-checked client-side against
the filter (plus aliases below) before being kept.
"""
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from .base import FetchError, JobPosting, strip_html

PAGE_SIZE = 10
MAX_PAGES = 200  # safety valve: 2000 positions

# Eightfold's own location strings use the municipality, not the metro area
# people search by. AstraZeneca's "Gothenburg" site is registered as Mölndal.
_LOCATION_ALIASES = {"gothenburg": ("gothenburg", "göteborg", "mölndal")}


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _domain(name: str, start_url: str) -> str:
    domain = parse_qs(urlparse(start_url).query).get("domain", [None])[0]
    if not domain:
        raise FetchError(f"{name}: start_url needs a ?domain=<tenant domain> query param")
    return domain


def _clean_location(raw: str) -> str:
    return re.sub(r"(,\s*)+", ", ", raw).strip(", ")


def _location_ok(position: dict, wanted: str) -> bool:
    needles = _LOCATION_ALIASES.get(wanted.lower(), (wanted.lower(),))
    haystack = " | ".join(
        (position.get("locations") or []) + (position.get("standardizedLocations") or [])
    ).lower()
    return any(n in haystack for n in needles)


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    origin = _origin(start_url)
    domain = _domain(name, start_url)
    api = f"{origin}/api/pcsx/search"

    positions: list[dict] = []
    for page in range(MAX_PAGES):
        params = {"domain": domain, "query": "", "start": page * PAGE_SIZE, "num": PAGE_SIZE}
        if location_filter:
            params["location"] = location_filter
        resp = session.get(api, params=params, headers={"Accept": "application/json"}, timeout=30)
        if resp.status_code != 200:
            raise FetchError(f"{name}: GET {api} -> HTTP {resp.status_code}")
        try:
            data = resp.json()["data"]
        except (ValueError, KeyError):
            raise FetchError(f"{name}: unexpected response shape from {api}")
        batch = data.get("positions") or []
        positions.extend(batch)
        if not batch or len(positions) >= data.get("count", 0):
            break

    postings: list[JobPosting] = []
    seen = set()
    for p in positions:
        pid = str(p.get("id") or "")
        title = (p.get("name") or "").strip()
        if not pid or not title or pid in seen:
            continue
        seen.add(pid)
        if location_filter and not _location_ok(p, location_filter):
            continue

        locs = [_clean_location(l) for l in (p.get("locations") or [])]
        location = locs[0] if locs else ""
        if len(locs) > 1:
            location += f" (+{len(locs) - 1} more)"

        ts = p.get("postedTs") or p.get("creationTs")
        posted = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else None

        postings.append(JobPosting(
            company=name,
            external_id=str(p.get("displayJobId") or pid),
            title=title,
            location=location,
            category=(p.get("department") or "").strip(),
            url=f"{origin}/careers/job/{pid}?domain={domain}",
            posted_date=posted,
        ))
    return postings


def fetch_description(session, url: str) -> str:
    parsed = urlparse(url)
    position_id = parsed.path.rsplit("/", 1)[-1]
    domain = parse_qs(parsed.query).get("domain", [None])[0]
    resp = session.get(
        f"{_origin(url)}/api/pcsx/position_details",
        params={"position_id": position_id, "domain": domain, "hl": "en"},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return strip_html(resp.json()["data"].get("jobDescription") or "")
