"""Renders data/jobs.db into a single self-contained HTML dashboard."""
import html
import json
from datetime import date, datetime
from pathlib import Path

from . import db

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "report.html"


def _latest_run_date(conn) -> str:
    cur = conn.execute("SELECT MAX(run_date) FROM runs WHERE status='ok'")
    row = cur.fetchone()
    return row[0] if row and row[0] else date.today().isoformat()


def _today_failures(conn, run_date: str):
    cur = conn.execute(
        "SELECT company, detail FROM runs WHERE status='error' AND run_date=? "
        "ORDER BY id DESC", (run_date,),
    )
    return cur.fetchall()


def generate(conn, out_path: Path = DEFAULT_OUT) -> Path:
    run_date = _latest_run_date(conn)
    jobs = db.active_jobs(conn)
    failures = _today_failures(conn, run_date)

    companies = sorted({j["company"] for j in jobs})
    new_count = sum(1 for j in jobs if j["first_seen"] == run_date)

    payload = [{
        "company": j["company"],
        "title": j["title"],
        "location": j["location"] or "—",
        "category": j["category"] or "—",
        "url": j["url"],
        "posted": j["posted_date"] or "",
        "isNew": j["first_seen"] == run_date,
    } for j in jobs]

    generated_at = datetime.now().strftime("%A %d %b %Y, %H:%M")

    html_out = _TEMPLATE.format(
        generated_at=html.escape(generated_at),
        run_date=html.escape(run_date),
        total_jobs=len(jobs),
        new_count=new_count,
        company_count=len(companies),
        failures_html=_render_failures(failures),
        company_options=_render_company_options(companies),
        jobs_json=json.dumps(payload, ensure_ascii=False),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out, encoding="utf-8")
    return out_path


def _render_failures(failures) -> str:
    if not failures:
        return ""
    items = "".join(
        f'<li><strong>{html.escape(company)}</strong>'
        f'<span>{html.escape(detail or "unknown error")}</span></li>'
        for company, detail in failures
    )
    return f'''
    <div class="alert" role="status">
      <span class="alert-icon" aria-hidden="true">&#9888;</span>
      <div>
        <p class="alert-title">{len(failures)} source{"s" if len(failures) != 1 else ""} failed to update today — showing last known data for {"them" if len(failures) != 1 else "it"}.</p>
        <ul class="alert-list">{items}</ul>
      </div>
    </div>'''


def _render_company_options(companies) -> str:
    return "".join(f'<option value="{html.escape(c)}">{html.escape(c)}</option>' for c in companies)


_TEMPLATE = """<!doctype html>
<title>Job Collator</title>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #f5f6f8;
    --surface: #ffffff;
    --surface-2: #eceef2;
    --border: #dadfe6;
    --text: #1a1f27;
    --text-muted: #5b6472;
    --accent: #2d6a6e;
    --accent-strong: #1f4d50;
    --accent-soft: #e4efee;
    --new: #9a5f1c;
    --new-soft: #fbeedc;
    --shadow: 0 1px 2px rgba(20, 24, 30, 0.06), 0 8px 24px -12px rgba(20, 24, 30, 0.12);
    --radius: 10px;
    color-scheme: light;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #12151a;
      --surface: #1b1f27;
      --surface-2: #232833;
      --border: #2e3440;
      --text: #e7eaf0;
      --text-muted: #9aa3b2;
      --accent: #5fb3ae;
      --accent-strong: #8ccac6;
      --accent-soft: #1b3a3a;
      --new: #e3a857;
      --new-soft: #3a2c14;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.3), 0 8px 24px -12px rgba(0, 0, 0, 0.5);
      color-scheme: dark;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #12151a;
    --surface: #1b1f27;
    --surface-2: #232833;
    --border: #2e3440;
    --text: #e7eaf0;
    --text-muted: #9aa3b2;
    --accent: #5fb3ae;
    --accent-strong: #8ccac6;
    --accent-soft: #1b3a3a;
    --new: #e3a857;
    --new-soft: #3a2c14;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.3), 0 8px 24px -12px rgba(0, 0, 0, 0.5);
    color-scheme: dark;
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .mono {{ font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace; font-variant-numeric: tabular-nums; }}

  .page {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; display: flex; flex-direction: column; gap: 20px; }}

  header.top {{ display: flex; flex-direction: column; gap: 4px; }}
  header.top h1 {{
    margin: 0;
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    text-wrap: balance;
  }}
  header.top .subtitle {{
    margin: 0;
    color: var(--text-muted);
    font-size: 0.9rem;
  }}
  header.top .subtitle .mono {{ color: var(--text-muted); }}

  .stats {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  .stat {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 120px;
    box-shadow: var(--shadow);
  }}
  .stat .value {{ font-family: "IBM Plex Mono", monospace; font-size: 1.4rem; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .stat .label {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); }}
  .stat.accent .value {{ color: var(--accent-strong); }}

  .alert {{
    display: flex;
    gap: 10px;
    background: var(--new-soft);
    border: 1px solid color-mix(in srgb, var(--new) 35%, var(--border));
    color: var(--text);
    border-radius: var(--radius);
    padding: 12px 14px;
    font-size: 0.85rem;
  }}
  .alert-icon {{ color: var(--new); font-size: 1rem; line-height: 1.4; }}
  .alert-title {{ margin: 0 0 4px; font-weight: 600; }}
  .alert-list {{ margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 2px; color: var(--text-muted); }}
  .alert-list strong {{ color: var(--text); font-weight: 600; }}
  .alert-list span {{ margin-left: 6px; }}

  .toolbar {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 10px 12px;
    box-shadow: var(--shadow);
    position: sticky;
    top: 12px;
    z-index: 5;
  }}
  .toolbar input[type="search"], .toolbar select {{
    font: inherit;
    font-size: 0.88rem;
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 7px;
    padding: 7px 10px;
  }}
  .toolbar input[type="search"] {{ flex: 1 1 220px; min-width: 160px; }}
  .toolbar select {{ flex: 0 0 auto; }}
  .toolbar .count {{ margin-left: auto; font-size: 0.8rem; color: var(--text-muted); white-space: nowrap; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  .table-wrap {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow-x: auto;
  }}
  thead th {{
    text-align: left;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    font-weight: 600;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }}
  thead th:hover {{ color: var(--text); }}
  thead th .arrow {{ opacity: 0.4; margin-left: 3px; }}
  thead th.active .arrow {{ opacity: 1; color: var(--accent); }}
  tbody tr {{ border-bottom: 1px solid var(--border); }}
  tbody tr:last-child {{ border-bottom: none; }}
  tbody tr:hover {{ background: var(--surface-2); }}
  td {{ padding: 10px 14px; vertical-align: top; }}
  td.title-cell {{ max-width: 420px; }}
  td.title-cell a {{
    color: var(--text);
    font-weight: 500;
    text-decoration: none;
  }}
  td.title-cell a:hover {{ color: var(--accent-strong); text-decoration: underline; }}
  td.title-cell a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }}
  td.muted {{ color: var(--text-muted); }}

  .badge-new {{
    display: inline-block;
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--new);
    background: var(--new-soft);
    border: 1px solid color-mix(in srgb, var(--new) 40%, transparent);
    border-radius: 4px;
    padding: 1px 5px;
    margin-left: 7px;
    vertical-align: 1px;
  }}

  .company-tag {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 0.78rem;
    font-weight: 500;
    white-space: nowrap;
  }}
  .company-tag::before {{
    content: "";
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--dot, var(--accent));
  }}

  .category-pill {{
    display: inline-block;
    font-size: 0.75rem;
    color: var(--text-muted);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 100px;
    padding: 2px 9px;
    white-space: nowrap;
  }}

  .empty {{ padding: 40px 20px; text-align: center; color: var(--text-muted); }}

  footer {{ text-align: center; color: var(--text-muted); font-size: 0.78rem; padding-top: 8px; }}
  footer a {{ color: var(--accent-strong); }}

  @media (prefers-reduced-motion: no-preference) {{
    tbody tr {{ transition: background-color 120ms ease; }}
  }}
</style>

<div class="page">
  <header class="top">
    <h1>Job Collator</h1>
    <p class="subtitle">Openings pulled from each employer's own career site &middot; last updated <span class="mono">{generated_at}</span></p>
  </header>

  {failures_html}

  <div class="stats">
    <div class="stat accent"><span class="value mono">{total_jobs}</span><span class="label">Open roles tracked</span></div>
    <div class="stat"><span class="value mono">{new_count}</span><span class="label">New since last run</span></div>
    <div class="stat"><span class="value mono">{company_count}</span><span class="label">Companies</span></div>
  </div>

  <div class="toolbar">
    <input type="search" id="search" placeholder="Search title or location&hellip;" aria-label="Search jobs" />
    <select id="companyFilter" aria-label="Filter by company">
      <option value="">All companies</option>
      {company_options}
    </select>
    <span class="count" id="resultCount"></span>
  </div>

  <div class="table-wrap">
    <table id="jobsTable">
      <thead>
        <tr>
          <th data-key="company">Company<span class="arrow">&#9662;</span></th>
          <th data-key="title">Role<span class="arrow">&#9662;</span></th>
          <th data-key="location">Location<span class="arrow">&#9662;</span></th>
          <th data-key="category">Category<span class="arrow">&#9662;</span></th>
          <th data-key="posted" class="active">Posted<span class="arrow">&#9662;</span></th>
        </tr>
      </thead>
      <tbody id="jobsBody"></tbody>
    </table>
    <div class="empty" id="emptyState" hidden>No roles match your filters.</div>
  </div>

  <footer>Sources are each employer's official career site. Runs on a daily schedule &mdash; postings that disappear from a listing drop off this page automatically.</footer>
</div>

<script>
  const JOBS = {jobs_json};

  const DOT_HUES = [178, 206, 26, 265, 340, 92, 12, 232];
  function hashHue(str) {{
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
    return DOT_HUES[h % DOT_HUES.length];
  }}
  function companyDot(name) {{
    const hue = hashHue(name);
    return `hsl(${{hue}} 55% 45%)`;
  }}

  const state = {{ search: "", company: "", sortKey: "posted", sortDir: "desc" }};

  const els = {{
    search: document.getElementById("search"),
    companyFilter: document.getElementById("companyFilter"),
    body: document.getElementById("jobsBody"),
    count: document.getElementById("resultCount"),
    empty: document.getElementById("emptyState"),
    headers: document.querySelectorAll("#jobsTable thead th"),
  }};

  function escapeHtml(s) {{
    return s.replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
  }}

  function render() {{
    const q = state.search.trim().toLowerCase();
    let rows = JOBS.filter(j => {{
      if (state.company && j.company !== state.company) return false;
      if (!q) return true;
      return j.title.toLowerCase().includes(q) || j.location.toLowerCase().includes(q);
    }});

    rows.sort((a, b) => {{
      const dir = state.sortDir === "asc" ? 1 : -1;
      const av = (a[state.sortKey] || "").toLowerCase();
      const bv = (b[state.sortKey] || "").toLowerCase();
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    }});

    els.count.textContent = rows.length + (rows.length === 1 ? " role" : " roles");
    els.empty.hidden = rows.length !== 0;

    els.body.innerHTML = rows.map(j => `
      <tr>
        <td><span class="company-tag" style="--dot:${{companyDot(j.company)}}">${{escapeHtml(j.company)}}</span></td>
        <td class="title-cell">
          <a href="${{j.url}}" target="_blank" rel="noopener noreferrer">${{escapeHtml(j.title)}}</a>
          ${{j.isNew ? '<span class="badge-new">NEW</span>' : ''}}
        </td>
        <td class="muted">${{escapeHtml(j.location)}}</td>
        <td><span class="category-pill">${{escapeHtml(j.category)}}</span></td>
        <td class="muted mono">${{escapeHtml(j.posted || "—")}}</td>
      </tr>
    `).join("");
  }}

  els.search.addEventListener("input", e => {{ state.search = e.target.value; render(); }});
  els.companyFilter.addEventListener("change", e => {{ state.company = e.target.value; render(); }});
  els.headers.forEach(th => {{
    th.addEventListener("click", () => {{
      const key = th.dataset.key;
      if (state.sortKey === key) {{
        state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      }} else {{
        state.sortKey = key;
        state.sortDir = key === "posted" ? "desc" : "asc";
      }}
      els.headers.forEach(h => h.classList.toggle("active", h === th));
      render();
    }});
  }});

  render();
</script>
"""
