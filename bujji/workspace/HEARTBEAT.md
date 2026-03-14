# Heartbeat Tasks

Run these checks every time you wake up:

1. Scan `workspace/invoices/` for any new unprocessed PDF files. If found, call `scan_invoice_directory` on that folder and log the results.

2. Check today's date. If it is the 9th, 10th, or 11th of the month, compile GSTR-1 for the previous month using `compile_gstr1` and write the CSV to `workspace/outputs/`.

3. Check today's date. If it is the 17th, 18th, or 19th of the month, generate GSTR-3B summary for the previous month using `generate_gstr3b`.

4. If there are any flagged invoices (check `workspace/outputs/batch_extracted.json` for entries with non-empty `flags` arrays), call `generate_mismatch_report` and append a warning to `workspace/logs/activity.log`.

5. Append a one-line status entry to `workspace/logs/activity.log` in this format:
   `[HEARTBEAT] [timestamp] Scanned. Invoices: X new. Flags: Y. Status: idle/working.`