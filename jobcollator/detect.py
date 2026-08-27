"""Best-effort auto-detection of which engine a career-site URL needs.

Usage:
    python -m jobcollator.detect <url>

Prints the detected engine name (or 'unknown') and, on success, the
normalized start_url to put in companies.json.
"""
import sys
from urllib.parse import urlparse

import requests

from .http import new_session

TIMEOUT = 20


def detect(url: str, session=None):
    session = session or new_session()
    parsed = urlparse(url)

    if "myworkdayjobs.com" in parsed.netloc:
        return "workday", url

    if "apply.workable.com" in parsed.netloc:
        return "workable", url

    # Try the SuccessFactors Jobs2Web fingerprint: a server-rendered
    # /search/ page with a #searchresults table.
    search_url = f"{parsed.scheme}://{parsed.netloc}/search/"
    try:
        resp = session.get(search_url, timeout=TIMEOUT)
        if resp.status_code == 200 and 'id="searchresults"' in resp.text:
            return "sf_j2w", search_url
    except requests.RequestException:
        pass

    # Try the URL as given: Attrax (server-rendered `attrax-vacancy-tile`
    # cards) and Jibe/iCIMS (a JSON `/api/jobs` behind a JS-rendered page,
    # but its markup still references jibecdn/icims) both fingerprint off
    # whatever page the caller pointed at, since neither has one fixed path
    # the way Jobs2Web's `/search/` is.
    try:
        resp = session.get(url, timeout=TIMEOUT)
        if resp.status_code == 200:
            lower = resp.text.lower()
            if "attrax-vacancy-tile" in lower or "attraxbundle" in lower:
                return "attrax", url
            if "jibecdn.com" in lower or ".icims.com" in lower:
                return "jibe", url
    except requests.RequestException:
        pass

    return "unknown", url


def main():
    if len(sys.argv) != 2:
        print("usage: python -m jobcollator.detect <career-site-url>")
        raise SystemExit(2)
    engine, start_url = detect(sys.argv[1])
    if engine == "unknown":
        print(f"unknown — could not fingerprint {sys.argv[1]}")
        print("add support for its ATS as a new engine in jobcollator/engines/, "
              "or set engine manually in companies.json if you know it.")
        raise SystemExit(1)
    print(f"engine:    {engine}")
    print(f"start_url: {start_url}")


if __name__ == "__main__":
    main()
