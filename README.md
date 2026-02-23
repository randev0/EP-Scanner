# EP-Scanner

Daily scraper for [ePerolehan](https://www.eperolehan.gov.my/web/epapp/notice-board) — Malaysian government procurement notice board. Filters for IT/digital/software tenders and delivers them via Telegram through OpenClaw.

## How It Works

```
[System Cron 9:00AM MYT]
        ↓
scrape_eperolehan.py
        ↓ (writes)
~/.openclaw/workspace-ep_scanner/PROCUREMENT.md
        ↓
[OpenClaw Cron 9:15AM MYT]
ep_scanner agent reads + analyzes
        ↓
Telegram → Your Bot → You
```

## Setup

### Requirements

- **Malaysian server IP** (ePerolehan is behind Akamai which blocks all non-MY datacenter IPs)
- Python 3.10+
- OpenClaw running with `ep_scanner` agent

### 1. Install dependencies

```bash
pip install -r requirements.txt
scrapling install   # installs browser binaries
```

### 2. Configure

Edit `eperolehan_config.json`:

```json
{
  "zenrows_api_key": "",
  "target_url": "https://www.eperolehan.gov.my/web/epapp/notice-board",
  "output_file": "/home/openclaw/.openclaw/workspace-ep_scanner/PROCUREMENT.md",
  "keywords": ["IT", "ICT", "software", "sistem", "web", "digital", ...]
}
```

- **`zenrows_api_key`**: Leave empty if running on a Malaysian IP (direct access works).
  If on a non-MY server, sign up free at [zenrows.com](https://www.zenrows.com) (no credit card, 1000 credits/month).
- **`output_file`**: Set to the `ep_scanner` workspace path on your server.
  Typical: `/home/openclaw/.openclaw/workspace-ep_scanner/PROCUREMENT.md`

### 3. Test

```bash
python3 scrape_eperolehan.py
```

Check `PROCUREMENT.md` was created with tender data.

### 4. System cron (9:00 AM MYT = 01:00 UTC)

```bash
crontab -e
# Add:
0 1 * * * /home/openclaw/.openclaw/workspace-ep_scanner/venv/bin/python3 /home/openclaw/.openclaw/workspace-ep_scanner/EP-Scanner/scrape_eperolehan.py >> /home/openclaw/.openclaw/workspace-ep_scanner/EP-Scanner/eperolehan_scraper.log 2>&1
```

### 5. OpenClaw cron job

Add this job to `~/.openclaw/cron/jobs.json` (restart OpenClaw after editing):

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "agentId": "ep_scanner",
  "name": "ePerolehan Procurement Scout",
  "enabled": true,
  "notify": true,
  "schedule": {
    "kind": "cron",
    "expr": "15 1 * * *",
    "tz": "Asia/Kuala_Lumpur"
  },
  "sessionTarget": "isolated",
  "wakeMode": "now",
  "payload": {
    "kind": "agentTurn",
    "message": "Read the file `PROCUREMENT.md` in your workspace.\n\n**YOUR JOB:**\nYou are a procurement scout. Read the daily scraped procurement notices and identify opportunities worth pursuing.\n\n**Business Profile:**\nIT company — software development, web/app development, digital platforms, ICT systems, data analytics, digital marketing, content creation, system integration, cloud services, cybersecurity consulting.\n\n**RULES:**\n1. If `PROCUREMENT.md` shows **SCRAPING FAILED** — reply with EXACTLY: `NO_REPLY`\n2. If there are **no relevant notices** today — reply with EXACTLY: `NO_REPLY`\n3. If there ARE relevant notices — deliver a Telegram message\n\n**MESSAGE FORMAT:**\n```\n🏛️ PEROLEHAN HARI INI — [DATE]\n\nAda [N] peluang:\n\n📋 [TITLE]\n   Ref: [REF]\n   Tutup: [CLOSING DATE]\n   Kenapa berkaitan: [1 sentence]\n\n---\nLink: https://www.eperolehan.gov.my/web/epapp/notice-board\n```",
    "model": "groq-cron/meta-llama/llama-4-scout-17b-16e-instruct"
  },
  "delivery": {
    "mode": "announce",
    "channel": "telegram",
    "to": "YOUR_TELEGRAM_ID"
  }
}
```

## Notes

- **Akamai block**: ePerolehan blocks all non-Malaysian datacenter/VPS IPs. Even Tor exit nodes, Playwright with stealth, and `curl_cffi` TLS fingerprint impersonation are blocked. A Malaysian server IP bypasses this natively.
- **ZenRows fallback**: If direct access fails, the script falls back to ZenRows API (residential proxy bypass). Set `zenrows_api_key` in config.
- **Keyword filtering**: Edit the `keywords` list in config to tune what counts as "relevant" for your business.
- **Silent on no results**: The OpenClaw agent sends `NO_REPLY` if nothing relevant found — no noise.
