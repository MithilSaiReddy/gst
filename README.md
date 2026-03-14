# GST Agent — AI Accounting Employee

An autonomous AI employee that handles end-to-end GST compliance for Indian businesses.
Built on [Bujji](https://github.com/MithilSaiReddy/bujji) — a lightweight open-source agent framework.

---

## What it does

You drop invoices. It handles everything else.

- Reads invoice PDFs — extracts GSTIN, HSN codes, tax amounts
- Validates tax rates against HSN lookup table
- Checks ITC eligibility under Section 17(5)
- Compiles GSTR-1 CSV (B2B + B2C + HSN summary)
- Generates GSTR-3B with liability and net payable
- Produces mismatch and error reports
- Generates monthly P&L statements
- Runs on a schedule — no human involvement needed

---

## Quick Start

### First time only
```
Double-click SETUP.bat
```
This installs dependencies, clones Bujji, copies tools, and configures your LLM.

When prompted during setup:
- Provider: `openrouter`
- API Key: get free key from https://openrouter.ai/keys
- Model: `mistral/mistral-large-latest`
- Workspace: press Enter

### Every time after
```
Double-click START.bat
```
Then open: **http://localhost:8000**

---

## How to use

1. Go to **Upload** tab
2. Drop invoice PDFs (or click to browse)
3. Click **Process All**
4. Go to **Live Activity** — watch the agent work in real time
5. When done, go to **Analytics** — numbers update automatically
6. Download GSTR-1 CSV or any report from **Upload → Output Files**

---

## Folder structure

```
gst/
├── START.bat              ← Run this every time
├── SETUP.bat              ← Run this once
├── server.py              ← Dashboard + API server
├── dashboard.html         ← The UI
├── bujji_tools/           ← GST tools (copied into Bujji during setup)
│   ├── gst_extract.py     ← Invoice PDF reader + validator
│   └── gst_reports.py     ← GSTR-1, GSTR-3B, P&L, mismatch reports
└── workspace/
    ├── invoices/          ← Drop PDFs here (or use Upload tab)
    │   └── processed/     ← Agent moves files here after processing
    ├── outputs/           ← All generated reports land here
    │   ├── gstr1-b2b-*.csv
    │   ├── gstr3b-*.json
    │   ├── mismatch-report-*.csv
    │   └── pl-report-*.json
    ├── logs/
    │   └── activity.log   ← Full audit trail
    ├── cron/
    │   └── jobs.json      ← Scheduled tasks (managed by dashboard)
    └── skills/
        └── gst-accountant/
            └── SKILL.md   ← Agent's GST knowledge base
```

---

## Dashboard tabs

| Tab | What it shows |
|-----|--------------|
| Live Activity | Real-time agent logs streaming as it works |
| Analytics | Tax liability, ITC, charts, filing history — auto-updates |
| Task Board | Kanban — drag tasks to assign or track |
| Upload | Drop invoices, see output files, download reports |
| Schedule | Add/edit/toggle automated schedules in plain English |
| Settings | GSTIN, business name, LLM provider, notifications |

---

## Scheduled tasks (automatic)

| Task | When |
|------|------|
| Invoice scan | Every day at 9 AM |
| GSTR-1 compilation | 11th of every month |
| GSTR-3B summary | 18th of every month |
| Monthly P&L | End of month |
| Mismatch audit | Every week |

All schedules can be edited from the **Schedule** tab — no JSON or cron syntax needed.

---

## Agent files (Bujji workspace)

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent values — accuracy, no noise, conservatism with money |
| `IDENTITY.md` | What the agent is and what it produces |
| `USER.md` | Your GSTIN, business name, preferences — fill this in |
| `AGENT.md` | Active tools and workspace layout |
| `HEARTBEAT.md` | Runs every 30 min — checks for new invoices |
| `SKILL.md` | GST knowledge — tax slabs, HSN codes, ITC rules, deadlines |

---

## Tech stack

- **Agent framework**: [Bujji](https://github.com/MithilSaiReddy/bujji) (Python, zero heavy dependencies)
- **LLM**: Mistral Large via OpenRouter
- **PDF extraction**: PyMuPDF
- **Server**: Python stdlib HTTP server (no Flask/FastAPI needed)
- **Frontend**: Pure HTML/CSS/JS — no build step, no Node.js
- **GST tools**: Custom Bujji tools — hot-reloaded, no restart needed

---

## Requirements

- Python 3.9+
- Git
- Internet connection (for LLM API calls)

Python packages: `requests`, `pymupdf`

---

## Troubleshooting

**Dashboard not loading**
→ Make sure `server.py` is running (`python server.py`)
→ Open via `http://localhost:8000` not by double-clicking the HTML file

**Agent not processing invoices**
→ Make sure Bujji is running in a second terminal (`cd bujji && python main.py serve`)
→ Check that `gst_extract.py` is in `bujji/bujji/tools/`
→ Make sure PyMuPDF is installed: `pip install pymupdf`

**Analytics not updating**
→ Analytics auto-refreshes every 30 seconds and after every agent run
→ Click Refresh button on the Analytics tab to force update
→ Make sure the agent has actually run and produced output files

**GSTR-3B shows zero**
→ Run the agent first via Upload tab or Run Now button
→ Check `workspace/outputs/` for `gstr3b-*.json` files

**ImportError: cannot import name run_server**
→ The GST server.py accidentally got placed inside `bujji/bujji/`
→ Restore original: `curl -o bujji\bujji\server.py https://raw.githubusercontent.com/MithilSaiReddy/bujji/main/bujji/server.py`

---

## Built with

- [Bujji](https://github.com/MithilSaiReddy/bujji) by Mithil Sai Reddy
- Mistral Large (via OpenRouter)
- Built at c0mpiled x Magicball x Razorpay Hackathon, Bangalore — March 2026
