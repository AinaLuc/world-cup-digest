#!/usr/bin/env python3
"""
World Cup match results emailer.

Fetches the latest match results from the official 2026 FIFA World Cup
Wikipedia page, finds YouTube highlight links, and emails a digest of any
newly-completed matches. State is persisted in state.json (committed back
to the repo by the GitHub Action) so we never re-email the same match.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------- Config ----------

WORLD_CUP_YEAR = 2026
WIKI_URL = f"https://en.wikipedia.org/wiki/{WORLD_CUP_YEAR}_FIFA_World_Cup"
STATE_FILE = Path(__file__).parent / "state.json"

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT = os.environ.get("RECIPIENT", GMAIL_USER)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

HTTP_HEADERS = {
    "User-Agent": "world-cup-bot/1.0 (personal email digest)"
}

# ---------- State ----------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"emailed": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


# ---------- Wikipedia parsing ----------

SCORE_RE = re.compile(r"^\s*(\d+)\s*[–—\-]\s*(\d+)\s*$")
PLACEHOLDER_RE = re.compile(r"^Match\s+\d+\s*$", re.IGNORECASE)
TBD_TEAM_RE = re.compile(r"^(Winner|Loser)\s+Match\s+\d+\s*$", re.IGNORECASE)
DATE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})|"
    r"(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*"
    r"(?:,\s*(\d{4}))?",
    re.IGNORECASE,
)


def parse_match_date(text: str) -> tuple[str, str]:
    """Return (iso_date, pretty_date) from a date string. Falls back to ('', raw)."""
    text = text.strip()
    m = DATE_RE.search(text)
    if not m:
        return ("", text)
    if m.group(1):  # YYYY-MM-DD
        y, mo, d = m.group(1), m.group(2), m.group(3)
        try:
            dt = datetime(int(y), int(mo), int(d), tzinfo=timezone.utc)
            return (dt.isoformat(), dt.strftime("%B %-d, %Y"))
        except ValueError:
            return ("", text)
    # "11 June 2026" or "June 11, 2026" style
    month_name = m.group(5)
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    month = months.get(month_name.lower(), 1)
    if m.group(4):  # "11 June"
        day = int(m.group(4))
    else:  # "June 11"
        # m.group(4) won't be set in this case; we have only month+year
        # Re-search to find day
        day_match = re.search(r"\d{1,2}", text)
        day = int(day_match.group(0)) if day_match else 1
    year = int(m.group(6)) if m.group(6) else WORLD_CUP_YEAR
    try:
        dt = datetime(year, month, day, tzinfo=timezone.utc)
        return (dt.isoformat(), dt.strftime("%B %-d, %Y"))
    except ValueError:
        return ("", text)


def get_team_name(cell) -> str:
    """Extract team name from a home/away cell."""
    if not cell:
        return ""
    a = cell.find("a")
    if a and a.get_text(strip=True):
        return a.get_text(strip=True)
    return cell.get_text(" ", strip=True)


def get_stage_for_element(el, soup) -> str:
    """Find the section heading (h3 with mw-headline) preceding this element."""
    # Walk back through previous siblings/parents to find the heading
    prev = el.find_previous(["h2", "h3", "h4"])
    if not prev:
        return "Match"
    span = prev.find("span", class_="mw-headline")
    heading = (span.get_text(" ", strip=True) if span else prev.get_text(" ", strip=True))
    heading = re.sub(r"\s*\[edit\]\s*$", "", heading).strip()
    return heading or "Match"


def fetch_matches() -> list[dict]:
    """Parse the 2026 FIFA World Cup Wikipedia page and return completed match dicts."""
    print(f"Fetching {WIKI_URL} ...", file=sys.stderr)
    r = requests.get(WIKI_URL, timeout=30, headers=HTTP_HEADERS)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    matches: list[dict] = []
    seen_keys: set[str] = set()

    # Each match is wrapped in a <div class="footballbox"> containing one <table class="fevent">.
    footballboxes = soup.find_all("div", class_="footballbox")
    print(f"Found {len(footballboxes)} match boxes", file=sys.stderr)

    for fb in footballboxes:
        table = fb.find("table", class_="fevent")
        if not table:
            continue

        # Header row: home, score, away
        name_row = table.find("tr", attrs={"itemprop": "name"})
        if not name_row:
            continue

        home_cell = name_row.find("th", class_="fhome")
        score_cell = name_row.find("th", class_="fscore")
        away_cell = name_row.find("th", class_="faway")

        home = get_team_name(home_cell)
        away = get_team_name(away_cell)

        score_text = score_cell.get_text(" ", strip=True) if score_cell else ""
        score_link = score_cell.find("a") if score_cell else None
        score_link_href = score_link.get("href", "") if score_link else ""

        # Skip future matches (placeholder "Match XX")
        if PLACEHOLDER_RE.match(score_text):
            continue

        # Skip knockout TBDs (Winner/Loser Match XX)
        if TBD_TEAM_RE.match(home) or TBD_TEAM_RE.match(away):
            continue

        # Only real numeric scores
        score_match = SCORE_RE.match(score_text)
        if not score_match:
            continue

        score = f"{score_match.group(1)}-{score_match.group(2)}"

        # Date: first line of the footballbox text, before "("
        # E.g. "June 11, 2026 (2026-06-11) 1:00 p.m. UTC−6 ..."
        fb_text = fb.get_text(" ", strip=True)
        first_chunk = fb_text.split("(", 1)[0].strip()
        iso_date, pretty_date = parse_match_date(first_chunk)

        # Stage: nearest preceding h3
        stage = get_stage_for_element(fb, soup)

        # Stable key for state-tracking
        key = f"{home}_vs_{away}_{score}".replace(" ", "_")

        if key in seen_keys:
            continue
        seen_keys.add(key)

        matches.append({
            "key": key,
            "stage": stage,
            "team1": home,
            "team2": away,
            "score": score,
            "date": iso_date,
            "date_text": pretty_date,
        })

    print(f"Parsed {len(matches)} completed matches with real scores", file=sys.stderr)
    return matches


# ---------- YouTube search ----------

def find_youtube_highlight(team1: str, team2: str) -> dict | None:
    """Search YouTube for an official highlight video. No API key needed."""
    queries = [
        f"{team1} vs {team2} {WORLD_CUP_YEAR} FIFA World Cup highlights",
        f"{team1} {team2} {WORLD_CUP_YEAR} World Cup highlights",
        f"{team1} vs {team2} highlights {WORLD_CUP_YEAR}",
        f"{team1} {team2} highlights {WORLD_CUP_YEAR} world cup",
    ]
    try:
        for query in queries:
            result = subprocess.run(
                ["yt-dlp", "--default-search", "ytsearch5", "--no-warnings",
                 "--print", "%(title)s|||%(id)s|||%(channel)s",
                 f"ytsearch5:{query}"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue
            candidates = []
            for line in result.stdout.strip().splitlines():
                parts = line.split("|||")
                if len(parts) < 3:
                    continue
                title, vid, channel = parts[0], parts[1], parts[2]
                if not vid or len(vid) < 6:
                    continue
                tlow = title.lower()
                clow = channel.lower()
                score = 0
                if "highlight" in tlow:
                    score += 5
                if "fifa" in clow or "fifa" in tlow:
                    score += 4
                if f"{WORLD_CUP_YEAR}" in tlow or "world cup" in tlow:
                    score += 2
                if team1.split()[0].lower() in tlow and team2.split()[0].lower() in tlow:
                    score += 3
                if re.search(r"\bvs\b|\bv\b|\s-\s", tlow):
                    score += 1
                if "extended" in tlow:
                    score += 1
                # Penalize obviously-wrong content
                if any(w in tlow for w in ["movie", "song", "music video", "trailer", "interview"]):
                    score -= 5
                candidates.append((score, title, vid, channel))
            if not candidates:
                continue
            candidates.sort(key=lambda x: -x[0])
            best = candidates[0]
            if best[0] <= 0:
                continue
            return {
                "title": best[1],
                "url": f"https://youtu.be/{best[2]}",
                "channel": best[3],
            }
    except subprocess.TimeoutExpired:
        print(f"YouTube search timed out for {team1} vs {team2}", file=sys.stderr)
    except FileNotFoundError:
        print("yt-dlp not installed (pip install yt-dlp)", file=sys.stderr)
    except Exception as e:
        print(f"YouTube search failed: {e}", file=sys.stderr)
    return None


# ---------- Email ----------

def render_email(matches: list[dict]) -> tuple[str, str]:
    """Return (subject, html_body) for the digest."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n = len(matches)
    if n == 1:
        subject = f"⚽ World Cup {WORLD_CUP_YEAR}: 1 new result"
    else:
        subject = f"⚽ World Cup {WORLD_CUP_YEAR}: {n} new results"

    blocks = []
    for m in matches:
        yt = m.get("yt")
        if yt:
            yt_html = (
                f'<p style="margin:8px 0 0 0;">'
                f'<a href="{yt["url"]}" style="display:inline-block;background:#ff0000;'
                f'color:#ffffff;padding:10px 16px;text-decoration:none;border-radius:4px;'
                f'font-weight:bold;">▶ Watch highlights</a></p>'
                f'<p style="margin:4px 0 0 0;color:#666;font-size:12px;">{yt["title"]}</p>'
            )
        else:
            yt_html = '<p style="margin:8px 0 0 0;color:#999;font-size:12px;">⚠ Highlights not yet available on YouTube</p>'

        stage_html = (
            f'<p style="margin:0 0 4px 0;color:#888;font-size:11px;'
            f'text-transform:uppercase;letter-spacing:0.6px;font-weight:bold;">'
            f'{m["stage"]}</p>'
        ) if m.get("stage") else ""

        date_html = (
            f'<p style="margin:0 0 10px 0;color:#666;font-size:13px;">{m.get("date_text", "")}</p>'
        ) if m.get("date_text") else ""

        blocks.append(f"""
        <div style="border:1px solid #e0e0e0;border-radius:8px;padding:18px;margin:14px 0;
                    background:#fafafa;">
            {stage_html}
            <h2 style="margin:0;font-size:20px;color:#222;font-weight:600;">
                {m['team1']} <span style="color:#999;font-weight:300;">vs</span> {m['team2']}
            </h2>
            {date_html}
            <p style="margin:10px 0;font-size:36px;font-weight:bold;color:#0066cc;line-height:1;">
                {m['score']}
            </p>
            {yt_html}
        </div>
        """)

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
             background:#f4f4f4;margin:0;padding:20px;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;padding:24px;border-radius:8px;">
        <h1 style="margin:0 0 4px 0;font-size:24px;color:#222;">⚽ World Cup {WORLD_CUP_YEAR} Digest</h1>
        <p style="margin:0 0 20px 0;color:#888;font-size:13px;">{now}</p>
        {''.join(blocks) if blocks else '<p>No new results.</p>'}
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0 12px 0;">
        <p style="margin:0;color:#999;font-size:11px;text-align:center;">
            Sent by your World Cup digest bot
        </p>
    </div>
</body></html>"""
    return subject, html


def send_email(subject: str, html: str) -> None:
    if DRY_RUN:
        print(f"[DRY RUN] Would email {RECIPIENT}: {subject}", file=sys.stderr)
        Path("preview.html").write_text(html, encoding="utf-8")
        print(f"[DRY RUN] Wrote preview to {Path('preview.html').resolve()}", file=sys.stderr)
        return

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD must be set")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT, msg.as_string())
    print(f"Email sent to {RECIPIENT}: {subject}", file=sys.stderr)


# ---------- Main ----------

def main() -> int:
    state = load_state()
    already_emailed = set(state.get("emailed", []))

    all_matches = fetch_matches()
    new_matches = [m for m in all_matches if m["key"] not in already_emailed]

    if not new_matches:
        print("No new matches to email.", file=sys.stderr)
        return 0

    print(f"Found {len(new_matches)} new match(es) to email", file=sys.stderr)

    for m in new_matches:
        print(f"Searching YouTube highlights for: {m['team1']} vs {m['team2']}", file=sys.stderr)
        yt = find_youtube_highlight(m["team1"], m["team2"])
        m["yt"] = yt
        if yt:
            print(f"  -> {yt['url']} ({yt['title']})", file=sys.stderr)
        else:
            print(f"  -> no highlight found", file=sys.stderr)

    subject, html = render_email(new_matches)
    send_email(subject, html)

    already_emailed.update(m["key"] for m in new_matches)
    state["emailed"] = sorted(already_emailed)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"State saved ({len(state['emailed'])} total matches emailed)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
