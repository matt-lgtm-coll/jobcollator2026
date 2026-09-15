"""Send a Telegram push notification summarizing new job postings.

Credentials come from (in priority order):
  1. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID environment variables — used in
     CI (GitHub Actions secrets)
  2. telegram_config.json at the project root — used for local runs
     (git-ignored — created once locally, never committed)

If neither is set, or new_count is 0, notify() is a no-op, so collect.py
keeps working fine without Telegram configured.

telegram_config.json format:
    {
      "bot_token": "123456789:AA...",
      "chat_id": "8671113431"
    }
"""
import html
import json
import os
import time
from pathlib import Path
from urllib import error, parse, request

CONFIG_PATH = Path(__file__).resolve().parent.parent / "telegram_config.json"
API_URL = "https://api.telegram.org/bot{token}/sendMessage"

MESSAGE_LIMIT = 4000  # Telegram's hard cap is 4096 chars; leave headroom for the "(Part i/n)" prefix
SEND_DELAY_SECONDS = 0.3  # be polite between multi-part messages


def _load_config():
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if env_token and env_chat_id:
        return {"bot_token": env_token, "chat_id": env_chat_id}

    if not CONFIG_PATH.exists():
        return None
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_messages(summary: dict, dashboard_url: str | None) -> list[str]:
    """Return one or more HTML-formatted message bodies covering every new
    job — split across multiple messages rather than truncating the list,
    since Telegram caps a single message at 4096 characters."""
    n = summary["new_count"]
    header = f"<b>{n} new job posting{'s' if n != 1 else ''}</b> in the Danish tracker"

    job_lines = []
    for job in summary["new_jobs"]:
        title = html.escape(job["title"])
        company = html.escape(job["company"])
        url = html.escape(job["url"], quote=True)
        job_lines.append(f'• <a href="{url}">{title}</a> ({company})')

    footer_lines = []
    if dashboard_url:
        footer_lines = ["", f'<a href="{html.escape(dashboard_url, quote=True)}">Full dashboard</a>']

    # Pack job lines into chunks under MESSAGE_LIMIT, first chunk gets the header.
    chunks: list[list[str]] = []
    current = [header, ""]
    current_len = len(header) + 1
    for line in job_lines:
        line_len = len(line) + 1
        if current_len + line_len > MESSAGE_LIMIT:
            chunks.append(current)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    chunks.append(current)

    if footer_lines:
        footer_len = sum(len(l) + 1 for l in footer_lines)
        if current_len + footer_len <= MESSAGE_LIMIT:
            chunks[-1].extend(footer_lines)
        else:
            chunks.append(footer_lines)

    total = len(chunks)
    messages = []
    for i, chunk in enumerate(chunks, start=1):
        text = "\n".join(chunk)
        if total > 1:
            text = f"(Part {i}/{total})\n{text}"
        messages.append(text[:MESSAGE_LIMIT])
    return messages


def _send_one(token: str, chat_id: str, text: str) -> bool:
    payload = parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = API_URL.format(token=token)
    try:
        req = request.Request(url, data=payload, method="POST")
        with request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            print(f"[notify_telegram] Telegram API returned an error: {body}")
            return False
        return True
    except error.URLError as e:
        print(f"[notify_telegram] failed to send: {e}")
        return False


def notify(summary: dict, dashboard_url: str | None = None) -> bool:
    """Send Telegram message(s) if new_count > 0 and credentials are configured.

    Returns True if all messages sent, False otherwise (no config, no new
    jobs, or a send failure). Never raises — a notification problem should
    never break the collection run.
    """
    if summary.get("new_count", 0) <= 0:
        return False

    config = _load_config()
    if not config:
        print(f"[notify_telegram] no {CONFIG_PATH.name} found — skipping notification")
        return False

    token = config.get("bot_token")
    chat_id = config.get("chat_id")
    if not token or not chat_id:
        print("[notify_telegram] telegram_config.json missing bot_token/chat_id — skipping")
        return False

    messages = build_messages(summary, dashboard_url)
    all_ok = True
    for i, text in enumerate(messages):
        if i > 0:
            time.sleep(SEND_DELAY_SECONDS)
        all_ok = _send_one(token, chat_id, text) and all_ok

    if all_ok:
        suffix = f" ({len(messages)} messages)" if len(messages) > 1 else ""
        print(f"[notify_telegram] notification sent{suffix}")
    return all_ok
