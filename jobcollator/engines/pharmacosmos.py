"""Engine for Pharmacosmos's own career site (pharmacosmos.com/career/).

This is a bespoke Nuxt/DatoCMS corporate site, not a shared ATS platform —
unlike the other engines here, it's unlikely to cover any other company.

The `/career/job-openings/` page is itself the Denmark listing (headquarters
is in Holbæk): it renders one server-side `<table>` under an
"Job openings in <region>" heading, one `<tr>` per posting, columns
[title, department, deadline, apply-link]. No client-side API call needed —
plain `requests.get` on the page already returns the full table, and there's
no pagination (confirmed: every posting sits in one page, no "load more").
Links to subsidiary career pages (US/UK/China/Germany/Nordics) sit lower on
the same page under "Job openings in our subsidiaries" — deliberately not
followed, since this tool only tracks Denmark for this company for now.

`location_filter`, if given, is matched against the table's own heading
text (e.g. "Denmark" against "Job openings in Denmark") rather than a
platform facet, since the platform doesn't expose one — this is the
region already committed to server-side by which page got fetched.
"""
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    resp = session.get(start_url, timeout=30)
    if resp.status_code != 200:
        raise FetchError(f"{name}: GET {start_url} -> HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.select_one("table")
    if not table:
        raise FetchError(f"{name}: no job table found on {start_url}")

    heading = table.find_previous(["h1", "h2", "h3"])
    heading_text = heading.get_text(strip=True) if heading else ""
    region = heading_text.split(" in ", 1)[-1].strip() if " in " in heading_text else heading_text

    if location_filter and location_filter.lower() not in heading_text.lower():
        return []  # legitimately zero postings match the filter

    postings: list[JobPosting] = []
    seen_ids: set[str] = set()

    for row in table.select("tr"):
        cells = row.select("td")
        link = row.select_one("a[href]")
        if len(cells) < 2 or not link:
            continue

        href = link["href"]
        slug = [s for s in href.rstrip("/").split("/") if s][-1] if href else None
        if not slug or slug in seen_ids:
            continue
        seen_ids.add(slug)

        postings.append(JobPosting(
            company=name,
            external_id=slug,
            title=cells[0].get_text(strip=True),
            location=region,
            category=cells[1].get_text(strip=True) if len(cells) > 1 else "",
            url=urljoin(start_url, href),
            posted_date=None,  # only an application deadline is shown, not a posted date
        ))

    return postings
