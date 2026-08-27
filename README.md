# Job Collator

Pulls current job postings straight from employers' own career sites, keeps
history in a local SQLite database, and renders a filterable/sortable HTML
dashboard with clickable links to each job description.

## How it works

Career sites are built on a small number of ATS (applicant tracking system)
platforms, each with its own predictable page structure or API. Instead of
scraping arbitrary HTML per company, this tool has one **engine** per
platform (`jobcollator/engines/`), and each company in [`companies.json`](companies.json)
just points at the engine it needs:

| Engine     | Platform                                                | Confirmed on                                                         |
|------------|----------------------------------------------------------|------------------------------------------------------------------------|
| `sf_j2w`   | SAP SuccessFactors "Jobs2Web" career site template        | Novo Nordisk, Lundbeck, LEO Pharma                                     |
| `workday`  | Workday's `cxs` job-search API (`*.myworkdayjobs.com`)    | IQVIA, Parexel, Thermo Fisher (incl. PPD), Ferring, Genmab, Zealand Pharma, Bavarian Nordic |
| `workable` | Workable's public jobs API (`apply.workable.com`)         | Ascendis Pharma                                                        |
| `attrax`   | Attrax career-site platform                               | ICON plc                                                               |
| `jibe`     | Jibe / iCIMS "Careers Site" platform                       | Medpace                                                                |

All engines only hit URLs allowed by the site's `robots.txt`.

**Known gap:** ALK-Abelló's career site (EasyCruit) sits behind an AWS WAF
JavaScript challenge (`x-amzn-waf-action: challenge` on every plain request)
— not fetchable with `requests`, and this tool deliberately doesn't drive a
real browser to get around bot-detection. It's left out of `companies.json`
(see the `_unsupported` entry there) rather than silently failing daily.

### Location filtering

Each company entry can set `"location_filter": "Denmark"` (or any place
name). Every engine applies it **server-side** rather than fetching
everything and discarding most of it — via each platform's own search
param or facet mechanism (`locationsearch`, `appliedFacets`, `q`, `country`,
`location`, depending on the engine). This matters at scale: Thermo Fisher
alone lists 3,000+ postings globally but ~25 in Denmark, so filtering
server-side is the difference between ~165 requests and ~3. Omit the field
(or set it to `null`) to track a company's postings everywhere.

One Workday wrinkle worth knowing if you touch `workday.py`: tenants don't
consistently expose the same location facet shape. Some have a clean
country-level facet (name varies: `locationCountry`, `Location_Country`);
others only expose per-city facets, and on those the descriptor is
sometimes just a bare city name with no country in it at all (Bavarian
Nordic's Workday lists "Kvistgaard", not "Kvistgaard, Denmark"). The engine
prefers the country facet when present and only falls back to a city-name
substring match when a country name is actually visible in the descriptor
text — otherwise a naive substring match silently drops real matches. It
also clears the session's cookies before every request: Workday's
`Set-Cookie` pins a session to one specific backend instance, and reusing
that across the many tenants this tool visits in one run was observed to
occasionally route a later request to an instance that 502s for no other
reason — a fresh connection each time sidesteps it.

## Adding a new company

1. Find its career site's root URL.
2. Try auto-detection:
   ```bash
   python -m jobcollator.detect "https://careers.example.com/"
   ```
   If it prints an engine name, add an entry to `companies.json`:
   ```json
   { "name": "Example Corp", "start_url": "<printed start_url>", "engine": "<printed engine>" }
   ```
   You can also just add `{"name": ..., "start_url": ...}` with no `engine` —
   `collect.py` will auto-detect on its first run and write the result back
   into `companies.json` for you.
3. If detection says `unknown`, the site runs a platform this tool doesn't
   support yet. Common ones worth adding as a new engine in
   `jobcollator/engines/`: Greenhouse, Lever, SmartRecruiters, Teamtailor —
   all have simple public JSON APIs similar in spirit to `workday.py`.

## Usage

```bash
python -m pip install -r requirements.txt
python -m jobcollator.collect          # fetch, store, regenerate the report
```

This writes/updates `data/jobs.db`, `data/report.html`, and
`data/last_run_summary.json` (run date, total active roles, this run's new
postings, and any companies whose fetch failed — the machine-readable form
of what the printed summary and the report's warning banner already show).
Open the HTML file in a browser, or publish it somewhere you can view it on
your phone — that's how the scheduled version of this works (see below).

Postings that disappear from a company's listing are marked inactive and
drop out of the report automatically; postings seen for the first time in
the most recent run are flagged `NEW`.

## Scheduling

A daily scheduled Claude Code task (`job-collator-daily`) runs
`python -m jobcollator.collect`, republishes `data/report.html` to the same
Artifact URL each time (so you have one bookmarkable page that's always
current), and — only when `last_run_summary.json` shows `new_count > 0` — 
sends a push notification naming a few of the new postings plus the
dashboard link. No notification fires on a day with nothing new; that's a
deliberate choice to keep it from becoming noise.

There's no email-sending tool available in this environment (no connector
installed, none found in the registry), which is why this uses a push
notification instead of an actual email. If you connect a real email API
later (Gmail, SendGrid, Resend, etc.), the daily task's prompt can be
updated to send a proper email from `last_run_summary.json` instead.

The task only runs while the Claude Code app is running on this machine —
it's not a Windows startup service. If the app isn't open at run time, it
catches up automatically the next time you open it.
