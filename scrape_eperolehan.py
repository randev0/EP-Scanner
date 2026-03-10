#!/usr/bin/env python3
"""
ePerolehan Notice Board Scraper
Scrapes Malaysian government procurement notices from the PUBLIC notice board.
URL: https://www.eperolehan.gov.my/quotation-tender-notice
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "eperolehan_config.json"
LOG_FILE = SCRIPT_DIR / "eperolehan_scraper.log"

PUBLIC_URL = "https://www.eperolehan.gov.my/quotation-tender-notice"

TELEGRAM_BOT_TOKEN = "8730148707:AAFry8NbTGhJHkhV3ZZUyFL5PfAi8CdPxL0"
TELEGRAM_CHAT_ID = "10087352"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def fetch_page(url):
    """Fetch the public notice board page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ms-MY,ms;q=0.9,en-MY;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    }
    log(f"Fetching: {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    log(f"Response: HTTP {resp.status_code}, {len(resp.content)} bytes")
    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code}")
    # Check we didn't get redirected to login
    if "LOG MASUK SSO" in resp.text or "ssologin" in resp.url:
        raise Exception("Redirected to SSO login page — not the public notice board")
    return resp.text


def parse_notices(html):
    """
    Parse ePerolehan public notice board HTML.
    The data is in a PrimeFaces DataTable — the last large table with
    7 columns: Tajuk, PTJ, Tarikh Iklan, Tarikh Tutup, Tempoh Berbaki,
    Taklimat, Tindakan.
    """
    soup = BeautifulSoup(html, "html.parser")
    notices = []

    # Find the data table: look for tables with header cells containing known column names
    data_table = None
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_row = rows[0]
        header_cells = header_row.find_all(["th", "td"])
        header_text = " ".join(c.get_text(strip=True) for c in header_cells).lower()
        if "tajuk perolehan" in header_text and "tarikh" in header_text:
            data_table = table
            break

    if not data_table:
        log("WARNING: Could not find the procurement data table")
        return notices

    rows = data_table.find_all("tr")
    log(f"Found data table with {len(rows) - 1} data rows")

    date_pattern = re.compile(r"\d{2}/\d{2}/\d{4}")

    for row in rows[1:]:
        cells = row.find_all("td")
        if len(cells) < 5:
            continue

        title = cells[0].get_text(strip=True)
        agency = cells[1].get_text(strip=True)
        publish_date = cells[2].get_text(strip=True)
        close_date = cells[3].get_text(strip=True)
        days_remaining = cells[4].get_text(strip=True)
        briefing = cells[5].get_text(strip=True) if len(cells) > 5 else ""

        # Skip rows that don't have proper dates (form/filter rows)
        if not date_pattern.search(publish_date):
            continue

        if not title:
            continue

        notices.append({
            "title": title,
            "agency": agency,
            "publish_date": publish_date,
            "close_date": close_date,
            "days_remaining": days_remaining,
            "briefing": briefing,
        })

    # Extract page info
    for el in soup.find_all(["span", "div"]):
        t = el.get_text(strip=True)
        m = re.search(r"Mukasurat\s*(\d+)\s*/\s*(\d+)", t)
        if m:
            log(f"Page {m.group(1)} of {m.group(2)}")
            break

    return notices


def is_relevant(notice, keywords):
    """Check if a procurement notice is relevant based on keywords."""
    text = (notice.get("title", "") + " " + notice.get("agency", "")).lower()
    for kw in keywords:
        kw_lower = kw.lower()
        # Use word boundary matching for short keywords to avoid false positives
        # e.g., "IT" should not match "UNIT", "HOSPITAL", etc.
        if len(kw) <= 3:
            if re.search(r'\b' + re.escape(kw_lower) + r'\b', text):
                return True
        else:
            if kw_lower in text:
                return True
    return False


def format_notices_markdown(notices, keywords, today):
    """Format notices as a markdown file for the OpenClaw agent."""
    relevant = [n for n in notices if is_relevant(n, keywords)]

    lines = [
        f"# PROCUREMENT NOTICES — {today}",
        "",
        f"**Source:** ePerolehan Public Notice Board",
        f"**URL:** {PUBLIC_URL}",
        f"**Scraped at:** {datetime.now().strftime('%H:%M MYT')}",
        f"**Total notices on page:** {len(notices)}",
        f"**Potentially relevant (IT/Digital/Software):** {len(relevant)}",
        "",
    ]

    if relevant:
        lines += ["## RELEVANT NOTICES (Review These)", ""]
        for i, n in enumerate(relevant, 1):
            text_lower = (n["title"] + " " + n["agency"]).lower()
            kws_matched = []
            for kw in keywords:
                kw_lower = kw.lower()
                if len(kw) <= 3:
                    if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                        kws_matched.append(kw)
                elif kw_lower in text_lower:
                    kws_matched.append(kw)
            lines += [
                f"### {i}. {n['title']}",
                f"- **Agency (PTJ):** {n['agency']}",
                f"- **Published:** {n['publish_date']}",
                f"- **Closing:** {n['close_date']}",
                f"- **Time remaining:** {n['days_remaining']}",
                f"- **Briefing/Site visit:** {n['briefing']}",
                f"- **Keywords matched:** {', '.join(kws_matched[:5])}",
                "",
            ]
    else:
        lines += [
            "## No Relevant Notices Today",
            "",
            "No IT/digital/software procurement notices found on the first page.",
            "",
        ]

    if notices:
        other = [n for n in notices if not is_relevant(n, keywords)]
        if other:
            lines += [
                "## All Other Notices (Not IT-Related)",
                "",
            ]
            for n in other:
                lines.append(f"- {n['title']} — {n['agency']} (tutup: {n['close_date']})")
            lines.append("")

    lines += [
        "---",
        f"*Last updated: {datetime.now().isoformat()}*",
    ]

    return "\n".join(lines)


def send_telegram(text):
    """Send a message to Telegram using the Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram has a 4096 char limit per message, split if needed
    chunks = []
    if len(text) <= 4096:
        chunks = [text]
    else:
        lines = text.split("\n")
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 4000:
                chunks.append(chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk.strip():
            chunks.append(chunk)

    for i, chunk in enumerate(chunks):
        try:
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=15)
            if resp.status_code == 200:
                log(f"Telegram message {i+1}/{len(chunks)} sent successfully")
            else:
                log(f"Telegram send failed: HTTP {resp.status_code} - {resp.text}")
                # Retry without Markdown parse mode in case of formatting issues
                resp2 = requests.post(url, json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }, timeout=15)
                if resp2.status_code == 200:
                    log(f"Telegram message {i+1}/{len(chunks)} sent (plain text fallback)")
                else:
                    log(f"Telegram plain text fallback also failed: {resp2.text}")
        except Exception as e:
            log(f"Telegram send error: {e}")
        if len(chunks) > 1 and i < len(chunks) - 1:
            time.sleep(1)  # Rate limit between chunks


def format_telegram_message(notices, keywords, today):
    """Format relevant notices as a concise Telegram message."""
    relevant = [n for n in notices if is_relevant(n, keywords)]

    if not relevant:
        return (
            f"*EP Scanner — {today}*\n\n"
            f"No IT/digital procurement notices found today.\n"
            f"Total notices on page: {len(notices)}\n\n"
            f"_Source: ePerolehan Public Notice Board_"
        )

    lines = [
        f"*EP Scanner — {today}*",
        f"Found *{len(relevant)}* relevant IT/digital notice(s)!\n",
    ]

    for i, n in enumerate(relevant, 1):
        text_lower = (n["title"] + " " + n["agency"]).lower()
        kws_matched = []
        for kw in keywords:
            kw_lower = kw.lower()
            if len(kw) <= 3:
                if re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
                    kws_matched.append(kw)
            elif kw_lower in text_lower:
                kws_matched.append(kw)

        lines.append(f"*{i}. {n['title']}*")
        lines.append(f"  Agency: {n['agency']}")
        lines.append(f"  Closing: {n['close_date']} ({n['days_remaining']})")
        if n['briefing']:
            lines.append(f"  Briefing: {n['briefing']}")
        lines.append(f"  Keywords: {', '.join(kws_matched[:5])}")
        lines.append("")

    lines.append(f"_Total notices: {len(notices)} | Source: ePerolehan_")
    return "\n".join(lines)


def save_output(content, output_path):
    """Save markdown output to the OpenClaw workspace."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log(f"Saved to: {output_path}")


def main():
    log("=" * 50)
    log("ePerolehan Scraper Starting")

    config = load_config()
    output_path = config["output_file"]
    keywords = config.get("keywords", [])
    today = datetime.now().strftime("%Y-%m-%d")

    html = None

    # Fetch the PUBLIC notice board (no login required)
    try:
        html = fetch_page(PUBLIC_URL)
        log("Public notice board fetched successfully!")
    except Exception as e:
        log(f"Fetch failed: {e}")

    # Write error state if fetch failed
    if html is None:
        content = (
            f"# PROCUREMENT NOTICES — {today}\n\n"
            f"**Status: SCRAPING FAILED**\n\n"
            f"Could not fetch the public notice board.\n\n"
            f"*Last attempt: {datetime.now().isoformat()}*\n"
        )
        save_output(content, output_path)
        log("Wrote error state to output file")
        sys.exit(1)

    # Parse and format
    log("Parsing notice board HTML...")
    notices = parse_notices(html)
    log(f"Found {len(notices)} notices")

    content = format_notices_markdown(notices, keywords, today)
    save_output(content, output_path)

    relevant_count = sum(1 for n in notices if is_relevant(n, keywords))
    log(f"Done. {len(notices)} total notices, {relevant_count} relevant.")

    # Send results directly to Telegram
    log("Sending results to Telegram...")
    tg_msg = format_telegram_message(notices, keywords, today)
    send_telegram(tg_msg)

    log("=" * 50)


if __name__ == "__main__":
    main()
