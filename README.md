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

| Engine         | Platform                                                | Confirmed on                                                         |
|----------------|----------------------------------------------------------|------------------------------------------------------------------------|
| `sf_j2w`       | SAP SuccessFactors "Jobs2Web" career site template        | Novo Nordisk, Lundbeck, LEO Pharma                                     |
| `workday`      | Workday's `cxs` job-search API (`*.myworkdayjobs.com`)    | IQVIA, Parexel, Thermo Fisher (incl. PPD), Ferring, Zealand Pharma, Bavarian Nordic |
| `workable`     | Workable's public jobs API (`apply.workable.com`)         | Ascendis Pharma                                                        |
| `attrax`       | Attrax career-site platform                               | ICON plc                                                               |
| `jibe`         | Jibe / iCIMS "Careers Site" platform                       | Medpace                                                                |
| `phenom`       | Phenom People career-site platform                         | Genmab                                                                 |
| `hitalento`    | HiTalento WordPress career theme                           | Hemab                                                                  |
| `pharmacosmos` | Pharmacosmos's own career page                             | Pharmacosmos                                                           |
| `black_swan`   | WP Job Manager (WordPress career-board plugin)             | Black Swans Exist (a life-science recruitment agency — see below)      |

All engines only hit URLs allowed by the site's `robots.txt`.

**Aggregators/recruiters vs. employers' own sites:** `black_swan` is the one
engine here that isn't an employer's own career site — Black Swans Exist is a
recruitment agency, so its postings are for its clients (sometimes named,
often anonymised), not for itself. `collect.py` always stores postings under
the companies.json entry name regardless of what the engine reports as
`company` (see `db.record_success`), so for this kind of source the real
client/employer, when known, is carried in the `category` column instead —
the same convention `phenom.py` uses for Danaher's shared career site
(`jobs.danaher.com`), which lists several distinct operating companies under
one tracked site.

Two other aggregator/recruiter sources were investigated and deliberately
left out — see the `_unsupported` entries for **MedWatch.dk** and
**Jobindex.dk** in `companies.json` for why (in short: MedWatch's robots.txt
explicitly opts out Claude's own user-initiated-fetch agent, and Jobindex's
robots.txt disallows the one URL parameter precise enough to scope results
to pharma/biotech without excessive off-topic noise).

**Known gap:** ALK-Abelló's career site (EasyCruit) sits behind an AWS WAF
JavaScript challenge (`x-amzn-waf-action: challenge` on every plain request)
— not fetchable with `requests`, and this tool deliberately doesn't drive a
real browser to get around bot-detection. It's left out of `companies.json`
(see the `_unsupported` entry there) rather than silently failing daily.

### Location filtering

Each company entry can set `"location_filter": "Denmark"` (or any place
name). Most engines apply it **server-side** rather than fetching everything
and discarding most of it — via each platform's own search param or facet
mechanism (`locationsearch`, `appliedFacets`, `country`, depending on the
engine). This matters at scale: Thermo Fisher alone lists 3,000+ postings
globally but ~25 in Denmark, so filtering server-side is the difference
between ~165 requests and ~3. Omit the field (or set it to `null`) to track
a company's postings everywhere.

`attrax` is the one exception, and deliberately so — see below.

### Free-text location search isn't trustworthy — verify, or don't use it

Two platforms turned up the same failure mode independently, both caught
only because a real posting was reported missing, not by anything that
looked wrong in the data itself:

- **sf_j2w** (Lundbeck): `locationsearch` was silently a no-op on that
  tenant's default locale — passing "Denmark" and passing gibberish like
  "Mars" returned the identical fixed 10 postings, none of them Danish.
  `fetch()` now verifies at least one returned posting actually matches the
  filter before trusting the result; if none do, it re-fetches everything
  and filters client-side.
- **attrax** (ICON): `q=Denmark` genuinely narrowed the result set — just
  not correctly. It missed a real EMEA-wide "Medical Director" posting that
  `q=Copenhagen` happened to catch, because the tile's *displayed* location
  text only shows one of several valid locations, and `q`'s indexing didn't
  reliably cover the rest either. There was no unmatching-input signal to
  catch this the way Lundbeck's fixed-10 pattern gave one away — a
  plausible, narrowed, wrong-by-omission result set looks identical to a
  correct one from the outside. So `attrax.py` doesn't use `q` for location
  filtering at all: it always fetches the complete unfiltered listing and
  filters on each tile's CSS class list instead (Attrax tags every valid
  location as a `attrax-vacancy-tile--<slug>` class, confirmed complete and
  accurate) — correctness over request count, since a verify-then-fallback
  approach can't help when there's nothing detectably wrong with the
  narrowed result to trigger a fallback on.

The takeaway for adding a new engine: a location search param that reduces
the result count is not proof it filtered *correctly*. Cross-check a known
real posting against the filtered result before trusting a new mechanism,
the way both of these were actually caught.

A related, narrower thing to watch for regardless of engine: a posting can
legitimately be open to *several* locations at once, and platforms only
ever display one of them as "the" location — not necessarily the one that
matches your filter. `attrax.py` and `phenom.py` both handle this properly
(checking the *full* location list a posting carries, then reporting
whichever entry actually matched, rather than blindly trusting whatever the
platform picked as primary); a plain single-`location`-field engine will
silently miss or mislabel these. Confirmed concretely on Genmab: a posting
primarily located in Princeton, NJ correctly appeared in the Denmark-filtered
results because Copenhagen was also one of its valid locations.

Also concretely why Genmab and Thermo Fisher — visually the same Phenom
People frontend — ended up on two *different* engines: Thermo Fisher's
Phenom frontend is a thin wrapper over a live, fully-populated Workday
tenant (confirmed: same posting count on both), so `workday.py` talks to
that tenant directly and skips Phenom entirely. Genmab's Workday tenant, by
contrast, is real but empty — even Genmab's own public Workday search page
shows "0 JOBS FOUND" — so all its actual postings only exist behind the
Phenom frontend itself, which is what `phenom.py` was built for. Don't
assume two sites on the same frontend platform want the same engine;
check whether the frontend's own backend actually has the data first.

### Soft matches: a posting can be open to a place with no location tag at all

Distinct from the multi-location case above (where a posting has several
*structurally tagged* locations): a recruiter can also write a title like
"Senior Director, Computational Drug Discovery (DK/US)" for a req that,
in the ATS's own data, is only ever tagged with one location (confirmed
on Zealand Pharma: Cambridge, MA, with no Denmark tag anywhere in that
posting's record, `additionalLocations` included) — they just never
configured the platform's multi-location field for it, even though the
role is genuinely open to either site. No amount of correct facet or
class-list handling catches this, because the platform's own structured
data plainly says the posting isn't in Denmark.

`JobPosting.soft_match` exists for this: a posting flagged `True` didn't
match `location_filter` structurally, but its *title* mentions the place
(via `title_mentions_location()` in `base.py`, whole-word matched against
the place name and, where one's registered in `COUNTRY_ABBREVIATIONS`, its
abbreviation too — e.g. "DK" for "Denmark"). Soft matches are real
postings, shown in the report table (with a distinct "DK MENTIONED" badge,
slightly dimmed) rather than folded into the confirmed list — they're
excluded from "Open roles tracked", "New since last run", and the push
notification's new-count, and get their own stat instead, precisely
because a title mention is a hint worth a human's judgment, not a
confirmed opening the way a real location tag is.

`workday.py` implements the search for these cheaply rather than crawling
a tenant's full unfiltered listing to scan every title (which would undo
the whole point of filtering server-side on a large tenant): it asks the
platform's own keyword search for the place name and its abbreviation —
a couple of narrow, cheap queries — then keeps only the hits whose title
actually matches (that keyword search also matches unrelated hits buried
in a job's full description, which is why the title-only check still
matters even on the pre-filtered candidates). Only `workday.py` does this
today; another engine wanting the same behavior should follow the same
"narrow query first, filter by title after" shape rather than crawling
everything.

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

### Posted-date parsing

`sf_j2w`'s posted-date field is free text off the site, not a structured
value, and it's not safe to assume standard month abbreviations: Novo
Nordisk's instance spells September **"Sept"** (e.g. "1 Sept 2026") instead
of the 3-letter form Python's `%b` expects, which silently failed to parse
and made every September posting look date-less — in turn making the
dashboard's newest "Posted" date look frozen in August even as new postings
kept arriving. `_parse_date` normalizes that before parsing now. If another
tenant on this platform (or a new one) turns up with its own nonstandard
month spelling, add it there rather than assuming `%b`/`%B` cover everything.

Separately, worth knowing the `NEW` badge and the push notification's "new"
count are **not** the same signal as the Posted-date column: they're based
on `first_seen` — when this tool noticed a posting for the first time —
not on when the employer says they posted it. Those two can diverge (a
missed collection day, or a platform reissuing an internal id when a
listing gets refreshed, both make something look "new" to this tool without
being freshly posted). The Posted-date column is the more trustworthy
signal for "how recent is this, really."

Some tenants don't expose a posted date at all — Lundbeck's results table
(same platform, different admin config) simply has no date column, only
Title/Location/Country. `posted_date` is `None` for every Lundbeck posting
as a result, and always will be; the `NEW` badge is the only freshness
signal available there. Same story for `hitalento.py` (Hemab) and
`pharmacosmos.py` (Pharmacosmos, which shows an application *deadline*
instead) — not a bug, just what those sites publish.

### `locationsearch` isn't always honored — verify, don't trust

Confirmed on Lundbeck, the hard way: `locationsearch` is *accepted* but
silently **ignored** on some tenants — passing "Denmark", "Copenhagen", "DK",
or even gibberish like "Mars" all returned the exact same fixed 10 postings
(real postings, just a totally unfiltered — and in this case US/Poland/Japan
— default view, not Denmark). It turned out this tenant's default locale
board doesn't support the param at all; pinning `locale=en_GB` (already
Novo Nordisk's and LEO Pharma's own default, so a no-op for them) fixed it.
Since there's no way to know in advance whether a new tenant has the same
issue, `fetch()` verifies: if location_filter is set and *none* of the
returned postings' location text actually matches it, that's treated as
proof the filter was a no-op, and it falls back to fetching everything and
filtering client-side instead of trusting a plausible-looking result count.

A second, compounding bug turned up chasing this one: pagination inferred
"is there a next page?" from whether the last page came back short
(`len(rows) < page_size`) — which breaks exactly when the true total is an
*exact multiple* of the page size, since the last real page is then full,
not short. Confirmed on Lundbeck at `locale=en_GB&locationsearch=Denmark`
(5 real matches, page size also 5): the code dutifully requested a "page 2"
that didn't exist, and the site's own pagination fell over in response —
its own reported total *changed* between page 1 ("of 5") and that page 2
request ("of 10"), padded out with a mix of duplicates and completely
unrelated postings. `_fetch_pages` now parses the "Results X – Y of
**total**" the *first* page itself reports and stops once it's collected
that many, rather than inferring it from row-count parity — and never
trusts a total a later page tries to report differently.

Together these two were a real, user-visible bug, not just theoretical:
before the fix, Lundbeck's tracked "Denmark" postings were 10 US/Poland/Japan
jobs with zero actual Danish roles among them — including missing a real
"Vice President, Global Medical Safety" opening in Copenhagen entirely.

## Role interest tags

[`interest_tags.json`](interest_tags.json) defines keyword-based tags —
e.g. "Cardiology & Cardiometabolic" — matched against every posting's
title, category, and cached full job-description text. A posting matching
a tag gets a colored badge in the dashboard table and shows up when that
tag is picked in the toolbar's role filter, right next to the company
filter. A tag's `label` (e.g. "Cardiology & Cardiometabolic") is the full
name shown in the filter dropdown; an optional `badge` gives a shorter form
for the in-table pill (e.g. "Cardio/Metabolic") so a descriptive label
doesn't crowd a table row — falls back to `label` if omitted. A tag with no
current keyword hits simply doesn't appear as clutter (`_render_tag_filter`
renders nothing at all if `interest_tags.json` has no tags configured).

Keep keyword lists tight and specific (`"cardiology"`, `"cardiac"`, not
something bare like `"CV"` that will match unrelated postings) — this is a
scanning aid for spotting roles worth a second look, the same spirit as
`soft_match`, not a precise clinical-relevance filter.

### Title/category matching is instant; JD matching costs a fetch, so it's cached

Matching against title + category is free (no network call) and computed
fresh at report-render time — editing `interest_tags.json` changes what's
flagged on the very next render, no other changes needed. But the title
alone often isn't enough: confirmed on ICON, a "Medical Director" posting
only mentioned "Cardiology" and "GLP1" in its qualifications section,
nowhere in the title — a title-only match would always miss it. Matching
against the full job description fixes that, but fetching a description is
a real network call, not free, so it isn't done at render time — it's a
separate, **cached-forever** step (`tagging.backfill_descriptions`, called
once from `collect.py` after every run): once a posting's description is
fetched, it's stored in `jobs.jd_text` and never re-fetched, so the ongoing
daily cost is bounded to whatever's newly discovered that day, not the
whole active set. The very first run after adding a tag that needs this
does pay to backfill the entire current active set once (confirmed:
~289 postings, a few minutes at a polite pace) — expected, not a bug.

Each engine can define its own `fetch_description(session, url) -> str`
for when the generic "fetch the URL, strip HTML tags" approach
(`fetch_description_default` in `base.py`) doesn't work — confirmed
necessary for **Workday** and **Workable**, whose job detail *pages* are
JS-rendered shells with no description anywhere in the raw HTML; both
platforms do expose a separate per-job JSON endpoint with the real
description (Workday: the same `cxs` API `fetch()` uses, `.../job<path>`
instead of `.../jobs`, `jobPostingInfo.jobDescription`; Workable: `.../api
/v1/accounts/<account>/jobs/<shortcode>`, `description`/`requirements`/
`benefits`). `attrax`, `phenom`, `jibe`, `sf_j2w`, `hitalento`, and
`pharmacosmos` all confirmed to serve real description text in their
server-rendered detail-page HTML, so they use the default. A backfill
failure for one posting (checked per-engine — Workday additionally
distinguishes a real infrastructure maintenance window, see below) never
stores anything, leaving `jd_text` `NULL` so it's retried on a future run
rather than getting stuck.

### JD text is noisy — a single keyword hit there isn't enough

Description text carries boilerplate a title never has, and this bit hard
during testing: Novo Nordisk's standard "we improve the lives of 30 million
people living with diabetes" mission-statement text appears in essentially
*every* one of their postings, which flagged things like a plain
"Automation Supporter" role purely off that one incidental mention. So
`match_tags` trusts a single keyword hit in title/category outright (it's
short and deliberately written) but requires **at least two distinct
keywords** to hit in the JD text alone before trusting a JD-only match
(`MIN_DISTINCT_JD_KEYWORDS` in `tagging.py`) — a genuinely relevant JD
tends to mention several related terms together (the ICON case that
motivated JD matching hit four: cardiology, cardiovascular, diabetes,
obesity), while an incidental boilerplate mention is almost always just
one. Verified after the fix: several Novo Nordisk postings with generic
titles ("Senior Regulatory Professional", "Medical Writer (Maternity
cover)") still correctly matched, because their JDs turned out to
genuinely describe a diabetes/obesity-specific team or product, not just
mention it once in passing — the threshold filters the noise without
losing the real signal. A tag whose keyword list has fewer than two entries
can only ever match via title/category, never JD alone — expected, not a
bug, and a reason to keep at least a couple of related keywords per tag.

Separately: a short ALL-CAPS keyword (a disease abbreviation like `"NASH"`)
is matched case-sensitively as a *whole word*, not the same
case-insensitive substring check every other keyword gets — confirmed
necessary: lowercase substring matching had "NASH" match inside
"Nashville" in a list of a CRO's office locations. Any keyword written
in `interest_tags.json` as all-caps gets this treatment automatically
(`_keyword_matches` in `tagging.py`); everything else stays a plain
substring match, since some keywords are deliberately partial word-stems
(`"echocardiogra"` catches both "echocardiogram" and "echocardiography"
without listing both).

### A Workday-wide maintenance window looks like a JSON error unless you know what to look for

Workday occasionally takes an entire shard down for infrastructure
maintenance — confirmed concretely: the whole **wd3** cluster (Ferring,
Zealand Pharma, FUJIFILM Diosynth, and GN, four unrelated companies with
nothing else in common) went down together, every request 303-redirected
to `community.workday.com/maintenance-page` instead of failing cleanly.
`requests` follows that redirect automatically, so without checking for it
explicitly what surfaces is a confusing `JSONDecodeError: Expecting value`
from trying to parse that HTML status page as JSON. `workday.py` checks
`resp.url` for `community.workday.com` after every request (`_check_not_
maintenance`) and raises a `FetchError` that says what's actually
happening instead — there's nothing to fix on our end here; it resolves on
its own once Workday's maintenance ends, and the point of the clearer
message is purely so a future run's logs don't need re-diagnosing from
scratch.

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
