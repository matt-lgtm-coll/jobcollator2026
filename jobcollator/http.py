import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "JobCollator/1.0 (personal job-search aggregator; "
    "contact: mattiashag@gmail.com; respects robots.txt)"
)


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})

    # Several ATS platforms (Workday in particular) sit behind Cloudflare,
    # which occasionally answers a burst of sequential requests to different
    # tenants — normal for this tool, since it visits many companies per run
    # — with a transient 502 that a plain retry a moment later sails through.
    # Retry automatically so a single flaky response doesn't fail a whole
    # company's collection.
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session
