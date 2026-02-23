#!/usr/bin/env python3
"""
ePerolehan Notice Board Scraper
Scrapes Malaysian government procurement notices from ePerolehan daily.
Uses ZenRows API to bypass Akamai bot protection.

Setup: Sign up free at https://www.zenrows.com (no credit card needed)
       Get API key → paste into eperolehan_config.json
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

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def fetch_via_zenrows(url, api_key):
    """Fetch ePerolehan notice board using ZenRows API (bypasses Akamai)."""
    params = {
        "apikey": api_key,
        "url": url,
        "js_render": "true",
        "antibot": "true",
        "wait": "3000",  # wait 3s for JS to render
        "premium_proxy": "true",  # use residential proxy
    }
    log(f"Fetching via ZenRows: {url}")
    resp = requests.get("https://api.zenrows.com/v1/", params=params, timeout=60)
    log(f"ZenRows response: HTTP {resp.status_code}, {len(resp.content)} bytes")
    if resp.status_code != 200:
        raise Exception(f"ZenRows error: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.text


def fetch_direct(url):
    """Attempt direct access (will work if on Malaysian IP or VPN)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ms-MY,ms;q=0.9,en-MY;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.eperolehan.gov.my/",
    }
    log(f"Attempting direct fetch: {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    log(f"Direct fetch response: HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise Exception(f"Direct access blocked: HTTP {resp.status_code}")
    return resp.text


def parse_notices(html):
    """
    Parse ePerolehan notice board HTML and extract procurement notices.
    Handles multiple possible HTML structures from Liferay/SPA rendering.
    """
    soup = BeautifulSoup(html, "html.parser")
    notices = []

    # --- Strategy 1: Look for table rows (most common Liferay layout) ---
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        header_row = rows[0] if rows else None
        if not header_row:
            continue
        # Detect header columns
        headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
        if not any(kw in " ".join(headers) for kw in ["no", "tajuk", "title", "tarikh", "date", "tender", "sebut"]):
            continue
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) >= 2:
                notice = {"raw": " | ".join(cells[:6]), "source": "table"}
                # Try to identify key fields
                for i, cell in enumerate(cells):
                    cl = cell.lower()
                    if re.match(r"[A-Z0-9]{2,}[-/][0-9]", cell):
                        notice["ref"] = cell
                    elif len(cell) > 20 and i <= 2:
                        notice.setdefault("title", cell)
                    elif re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", cell):
                        notice.setdefault("date", cell)
                if notice.get("title") or notice.get("ref"):
                    notices.append(notice)

    # --- Strategy 2: Look for card/list items (React/Angular SPA) ---
    if not notices:
        # Common SPA class patterns
        card_selectors = [
            "[class*='notice']", "[class*='tender']", "[class*='result']",
            "[class*='item']", "[class*='card']", "[class*='row']",
            "li", ".list-group-item",
        ]
        for selector in card_selectors:
            items = soup.select(selector)
            for item in items:
                text = item.get_text(separator=" ", strip=True)
                if len(text) > 30 and (
                    re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text) or
                    re.match(r"[A-Z0-9]{2,}[-/][0-9]", text)
                ):
                    notice = {"raw": text[:300], "source": "card"}
                    # Try to extract title (longest text segment)
                    parts = [p.strip() for p in text.split("  ") if len(p.strip()) > 10]
                    if parts:
                        notice["title"] = parts[0][:200]
                    notices.append(notice)
            if notices:
                break

    # --- Strategy 3: Search for procurement-like text blocks ---
    if not notices:
        # Look for any text that contains reference numbers or procurement keywords
        all_text = soup.get_text(separator="\n")
        lines = [l.strip() for l in all_text.split("\n") if l.strip()]
        current_block = []
        for line in lines:
            if re.match(r"[A-Z]{2,}[/-]\d", line) or re.search(r"(?:tender|sebut harga|quotation|iklan)", line, re.IGNORECASE):
                if current_block:
                    text = " | ".join(current_block)
                    notices.append({"raw": text[:300], "title": current_block[0], "source": "text"})
                current_block = [line]
            elif current_block and len(current_block) < 5:
                current_block.append(line)
        if current_block:
            notices.append({"raw": " | ".join(current_block), "title": current_block[0], "source": "text"})

    return notices


def is_relevant(notice, keywords):
    """Check if a procurement notice is relevant based on keywords."""
    text = notice.get("raw", "") + " " + notice.get("title", "")
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def format_notices_markdown(notices, keywords, today):
    """Format notices as a markdown file for the OpenClaw agent."""
    all_notices = [n for n in notices if n.get("title") or n.get("raw")]
    relevant = [n for n in all_notices if is_relevant(n, keywords)]

    lines = [
        f"# PROCUREMENT NOTICES — {today}",
        "",
        f"**Source:** ePerolehan Notice Board",
        f"**Scraped at:** {datetime.now().strftime('%H:%M MYT')}",
        f"**Total notices found:** {len(all_notices)}",
        f"**Potentially relevant (IT/Digital/Software):** {len(relevant)}",
        "",
    ]

    if relevant:
        lines += ["## RELEVANT NOTICES (Review These)", ""]
        for i, n in enumerate(relevant, 1):
            ref = n.get("ref", "N/A")
            title = n.get("title", n.get("raw", "")[:120])
            date = n.get("date", "")
            kws_matched = [kw for kw in keywords if kw.lower() in (n.get("raw", "") + n.get("title", "")).lower()]
            lines += [
                f"### {i}. {title}",
                f"- **Reference:** {ref}" if ref != "N/A" else "",
                f"- **Date:** {date}" if date else "",
                f"- **Keywords matched:** {', '.join(kws_matched[:5])}",
                f"- **Raw data:** {n.get('raw', '')[:200]}",
                "",
            ]
        lines = [l for l in lines if l is not None]
    else:
        lines += [
            "## No Relevant Notices Today",
            "",
            "No IT/digital/software procurement notices found for today.",
            "This could mean: (1) No relevant tenders today, (2) Site content changed format.",
            "",
        ]

    if all_notices and all_notices != relevant:
        lines += [
            "## All Other Notices (Not IT-Related)",
            "",
        ]
        for n in all_notices:
            if n not in relevant:
                title = n.get("title", n.get("raw", ""))[:100]
                lines.append(f"- {title}")
        lines.append("")

    lines += [
        "---",
        f"*Last updated: {datetime.now().isoformat()}*",
    ]

    return "\n".join(lines)


def save_output(content, output_path):
    """Save markdown output to the OpenClaw workspace."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log(f"Saved to: {output_path}")


def rotate_tor_circuit():
    """Attempt to rotate Tor circuit via control socket."""
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect("/run/tor/control")
        s.sendall(b'AUTHENTICATE ""\r\nSIGNAL NEWNYM\r\nQUIT\r\n')
        s.close()
        log("Rotated Tor circuit")
        time.sleep(5)
    except Exception as e:
        log(f"Tor circuit rotation failed (non-critical): {e}")


def main():
    log("=" * 50)
    log("ePerolehan Scraper Starting")

    config = load_config()
    api_key = config.get("zenrows_api_key", "")
    url = config["target_url"]
    output_path = config["output_file"]
    keywords = config.get("keywords", [])
    today = datetime.now().strftime("%Y-%m-%d")

    html = None
    error_msg = None

    # --- Attempt 1: Direct access (works if on Malaysian IP/VPN) ---
    try:
        html = fetch_direct(url)
        log("Direct access succeeded!")
    except Exception as e:
        log(f"Direct access failed: {e}")

    # --- Attempt 2: ZenRows API (requires API key) ---
    if html is None:
        if not api_key or api_key.startswith("REPLACE"):
            log("ZenRows API key not configured. Set zenrows_api_key in eperolehan_config.json")
            log("Sign up free at https://www.zenrows.com (no credit card required)")
            error_msg = "API key not configured. See setup instructions below."
        else:
            try:
                html = fetch_via_zenrows(url, api_key)
                log("ZenRows fetch succeeded!")
            except Exception as e:
                log(f"ZenRows fetch failed: {e}")
                error_msg = f"ZenRows error: {str(e)[:200]}"

    # --- Write error state if all methods failed ---
    if html is None:
        content = (
            f"# PROCUREMENT NOTICES — {today}\n\n"
            f"**Status: SCRAPING FAILED**\n\n"
            f"**Error:** {error_msg or 'All fetch methods failed'}\n\n"
            "## Setup Required\n\n"
            "ePerolehan is protected by Akamai bot detection and blocks VPS IPs.\n\n"
            "**To fix this:**\n"
            "1. Sign up free at https://www.zenrows.com (no credit card needed)\n"
            "2. Copy your API key\n"
            "3. Edit `/home/openclaw/scripts/eperolehan_config.json`\n"
            "4. Replace `REPLACE_WITH_YOUR_ZENROWS_API_KEY` with your actual key\n"
            "5. Re-run: `python3 /home/openclaw/scripts/scrape_eperolehan.py`\n\n"
            "**Alternative:** Set up a Malaysian VPN on this VPS to bypass geo-blocking.\n\n"
            f"*Last attempt: {datetime.now().isoformat()}*\n"
        )
        save_output(content, output_path)
        log("Wrote error state to output file")
        sys.exit(1)

    # --- Parse and format ---
    log("Parsing notice board HTML...")
    notices = parse_notices(html)
    log(f"Found {len(notices)} notices")

    content = format_notices_markdown(notices, keywords, today)
    save_output(content, output_path)

    relevant_count = sum(1 for n in notices if is_relevant(n, keywords))
    log(f"Done. {len(notices)} total notices, {relevant_count} relevant.")
    log("=" * 50)


if __name__ == "__main__":
    main()
