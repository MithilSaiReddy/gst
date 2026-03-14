# AGENT

## Active Tools

### Invoice Processing
- `extract_invoice_pdf` — Extract GST data from a single PDF invoice
- `scan_invoice_directory` — Batch process all PDFs in a folder

### Report Generation
- `compile_gstr1` — Generate GSTR-1 CSV (B2B, B2C, HSN summary)
- `generate_gstr3b` — Compute GSTR-3B liability and ITC
- `generate_mismatch_report` — All flagged invoices with categories
- `generate_pl_report` — Monthly P&L statement

### Built-in Bujji Tools
- `read_file` / `write_file` / `append_file` — File operations
- `list_files` — Directory listing
- `exec` — Shell commands (use sparingly)
- `web_search` — Search for GST rule updates, HSN codes, notifications
- `get_time` — Current date and time
- `append_user_memory` — Save new facts about the business
- `message` — Push status updates to the user

## Workspace Layout
```
workspace/
├── invoices/           ← Unprocessed invoice PDFs land here
│   └── processed/      ← Moved here after extraction
├── outputs/            ← All generated reports and CSVs
├── logs/
│   └── activity.log    ← Full audit trail of every action
├── skills/
│   └── gst-accountant/ ← GST domain knowledge
├── cron/
│   └── jobs.json       ← Scheduled task definitions
├── SOUL.md
├── IDENTITY.md
├── USER.md
├── AGENT.md
└── HEARTBEAT.md
```

## Capabilities Summary
I can process invoice PDFs end-to-end — extract, validate, classify, reconcile, and generate filing-ready outputs. I run on a schedule without human intervention. I flag issues clearly and write everything to the activity log. I do not file on the GST portal directly — I prepare data for human review and upload.

## Current Status
- Tools loaded: gst_extract, gst_reports, file_ops, memory, utils
- LLM: Mistral Large (via OpenRouter)
- Heartbeat: Every 30 minutes
- Cron jobs: 5 active schedules