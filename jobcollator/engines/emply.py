r"""Engine for the Emply career-site platform (emply.com) — a Danish HR-tech
vendor. Confirmed on Gubra's own-domain install (career.gubra.dk, "Powered by
Emply" footer credit) and matches the shared-subdomain install pattern too
(e.g. <company>.career.emply.com).

The listing page (whatever path the company points it at — Gubra's is
`/available-positions`) server-renders the *entire* vacancy list as a plain
JS call embedded in the page, no separate API round-trip needed:

    proceedBatch({ vacancies : JSON.parse('[{"id":"...",...}]'), count : N });

Two things make this trickier than a normal embedded-JSON scrape:

1. The page embeds *two* such `JSON.parse('...')` calls (the vacancies one,
   and an earlier one for something else — unconfirmed what, possibly a
   filter/facet widget — that isn't vacancy data). Matching greedily from
   the first `JSON.parse('` to the first following `'), count` swallows both
   in between. Fixed by using a proper JS-single-quoted-string regex
   (respecting `\x` escapes) via `re.finditer` and picking whichever match
   actually decodes to a list of vacancy-shaped dicts (has `titleAsUrl`).

2. The string body isn't just JS-escaped (backslash + the delimiter quote) —
   *both* quote characters are backslash-escaped (`\"` and `\'`), consistent
   with the server likely producing it via ASP.NET's
   `HttpUtility.JavaScriptStringEncode`, which escapes both regardless of
   which one is the actual delimiter. So `\"`, `\'`, `\\`, `\n`, and `\uXXXX`
   all need unescaping (see `_unescape_js`) before `json.loads`.

Job detail pages live at `{origin}/ad/<titleAsUrl>/<shortId>` (confirmed by
reading the actual rendered `<a href>` — a `/apply/...` URL seen in a search
result for a different company turned out not to be this pattern).
"""
import json
import re
from urllib.parse import urlparse

from .base import FetchError, JobPosting, title_mentions_location

_ESCAPE_MAP = {
    '\\\\': '\\',
    '\\"': '"',
    "\\'": "'",
    '\\n': '\n',
    '\\r': '\r',
    '\\t': '\t',
    '\\b': '\b',
    '\\f': '\f',
    '\\/': '/',
}
_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\.")

# Matches the body of a JS single-quoted string, respecting `\x` escape pairs
# so it doesn't stop early on an escaped quote or run past the real end.
_JS_STRING_RE = re.compile(r"JSON\.parse\('((?:[^'\\]|\\.)*)'\)")


def _unescape_js(s: str) -> str:
    def repl(m):
        seq = m.group(0)
        if seq in _ESCAPE_MAP:
            return _ESCAPE_MAP[seq]
        if seq.startswith("\\u"):
            return chr(int(seq[2:], 16))
        return seq
    return _ESCAPE_RE.sub(repl, s)


def _extract_vacancies(html_text: str):
    for m in _JS_STRING_RE.finditer(html_text):
        try:
            data = json.loads(_unescape_js(m.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict) and "titleAsUrl" in data[0]:
            return data
    return None


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    resp = session.get(start_url, timeout=30)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")

    vacancies = _extract_vacancies(resp.text)
    if vacancies is None:
        raise FetchError(f"{name}: {start_url} doesn't look like an Emply listing page")

    postings: list[JobPosting] = []
    for job in vacancies:
        job_id = job.get("id")
        title_as_url = job.get("titleAsUrl")
        short_id = job.get("shortId")
        title = (job.get("title") or "").strip()
        if not (job_id and title_as_url and short_id and title):
            continue

        location = job.get("location") or ""
        soft_match = False
        if location_filter:
            if location_filter.lower() not in location.lower():
                if title_mentions_location(title, location_filter):
                    soft_match = True
                else:
                    continue

        postings.append(JobPosting(
            company=name,
            external_id=job_id,
            title=title,
            location=location,
            category=job.get("department") or "",
            url=f"{origin}/ad/{title_as_url}/{short_id}",
            posted_date=(job.get("published") or "")[:10] or None,
            soft_match=soft_match,
        ))

    return postings
