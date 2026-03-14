"""
gst_reports.py — Bujji tool
Compiles GSTR-1, GSTR-3B, P&L reports, and reconciliation from extracted invoice data.
Drop this file into bujji/tools/ — hot-reloaded on next message.
"""

import csv
import json
import os
from datetime import datetime, date
from collections import defaultdict

try:
    from bujji.tools.base import register_tool, ToolContext
except ImportError:
    def register_tool(**kwargs):
        def decorator(fn): return fn
        return decorator
    ToolContext = object


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_extracted_invoices(workspace: str, month: str = None) -> list:
    """Load all extracted invoice records from workspace outputs."""
    batch_file = os.path.join(workspace, "outputs", "batch_extracted.json")
    all_invoices = []

    if os.path.exists(batch_file):
        with open(batch_file) as f:
            data = json.load(f)
            all_invoices = data.get("invoices", [])

    # Also load any individual invoice JSON files
    outputs_dir = os.path.join(workspace, "outputs")
    if os.path.exists(outputs_dir):
        for fname in os.listdir(outputs_dir):
            if fname.startswith("inv_") and fname.endswith(".json"):
                with open(os.path.join(outputs_dir, fname)) as f:
                    inv = json.load(f)
                    if isinstance(inv, dict) and "invoice_number" in inv:
                        all_invoices.append(inv)

    # Filter by month if provided (format: "YYYY-MM" or "March 2025")
    if month:
        filtered = []
        for inv in all_invoices:
            inv_date = inv.get("invoice_date", "")
            if month.lower() in inv_date.lower() or month in inv_date:
                filtered.append(inv)
        return filtered if filtered else all_invoices  # Fallback to all if no match

    return all_invoices


def write_csv(path: str, headers: list, rows: list):
    """Write a CSV file safely."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def log_activity(workspace: str, message: str):
    """Append to activity log."""
    log_path = os.path.join(workspace, "logs", "activity.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


# ─── GSTR-1 COMPILER ─────────────────────────────────────────────────────────

@register_tool(
    description="Compile GSTR-1 CSV from extracted invoices. Separates B2B and B2C supplies, applies HSN summary.",
    parameters={
        "type": "object",
        "properties": {
            "month": {
                "type": "string",
                "description": "Month to compile e.g. 'March 2025'. Leave empty to use all available invoices."
            },
            "output_filename": {
                "type": "string",
                "description": "Output filename e.g. 'gstr1-march-2025.csv'. Default: auto-generated."
            }
        }
    }
)
def compile_gstr1(month: str = "", output_filename: str = "", _ctx: ToolContext = None) -> str:
    workspace = _ctx.workspace if (_ctx and hasattr(_ctx, "workspace")) else "./workspace"
    invoices = load_extracted_invoices(workspace, month)

    if not invoices:
        return json.dumps({"error": "No invoice data found. Run scan_invoice_directory first."})

    b2b_rows = []
    b2c_rows = []
    hsn_summary = defaultdict(lambda: {"taxable": 0, "igst": 0, "cgst": 0, "sgst": 0, "count": 0})
    flags = []

    for inv in invoices:
        if inv.get("flags"):
            flags.extend([f"{inv['invoice_number']}: {fl}" for fl in inv["flags"]])

        row_base = {
            "Invoice Number":   inv.get("invoice_number", ""),
            "Invoice Date":     inv.get("invoice_date", ""),
            "Taxable Value":    inv.get("taxable_value", 0),
            "CGST":             inv.get("cgst", 0),
            "SGST":             inv.get("sgst", 0),
            "IGST":             inv.get("igst", 0),
            "Total Tax":        inv.get("total_tax", 0),
            "Invoice Value":    inv.get("total_amount", 0),
        }

        if inv.get("supply_type") == "B2B":
            b2b_rows.append({
                **row_base,
                "Receiver GSTIN":   inv.get("buyer_gstin", ""),
                "Place of Supply":  inv.get("seller_gstin", "")[:2] + " - Auto",
            })
        else:
            b2c_rows.append(row_base)

        # HSN summary
        for hsn in inv.get("hsn_codes", [])[:1]:  # Primary HSN only
            hsn_summary[hsn]["taxable"] += inv.get("taxable_value", 0)
            hsn_summary[hsn]["igst"]    += inv.get("igst", 0)
            hsn_summary[hsn]["cgst"]    += inv.get("cgst", 0)
            hsn_summary[hsn]["sgst"]    += inv.get("sgst", 0)
            hsn_summary[hsn]["count"]   += 1

    # Write CSVs
    ts = month.replace(" ", "-").lower() if month else datetime.now().strftime("%Y-%m")
    out_dir = os.path.join(workspace, "outputs")

    b2b_path = os.path.join(out_dir, output_filename or f"gstr1-b2b-{ts}.csv")
    b2c_path = os.path.join(out_dir, f"gstr1-b2c-{ts}.csv")
    hsn_path = os.path.join(out_dir, f"gstr1-hsn-{ts}.csv")

    if b2b_rows:
        write_csv(b2b_path, list(b2b_rows[0].keys()), b2b_rows)

    if b2c_rows:
        write_csv(b2c_path, list(b2c_rows[0].keys()), b2c_rows)

    hsn_rows = [
        {"HSN": hsn, "Description": "Auto-detected", "UOM": "NOS",
         "Total Quantity": v["count"], "Taxable Value": round(v["taxable"], 2),
         "IGST": round(v["igst"], 2), "CGST": round(v["cgst"], 2), "SGST": round(v["sgst"], 2)}
        for hsn, v in hsn_summary.items()
    ]
    if hsn_rows:
        write_csv(hsn_path, list(hsn_rows[0].keys()), hsn_rows)

    summary = {
        "status": "complete",
        "period": month or "all",
        "b2b_invoices": len(b2b_rows),
        "b2c_invoices": len(b2c_rows),
        "hsn_entries": len(hsn_rows),
        "total_taxable": round(sum(r["Taxable Value"] for r in b2b_rows + b2c_rows), 2),
        "total_igst": round(sum(r["IGST"] for r in b2b_rows + b2c_rows), 2),
        "total_cgst": round(sum(r["CGST"] for r in b2b_rows + b2c_rows), 2),
        "total_sgst": round(sum(r["SGST"] for r in b2b_rows + b2c_rows), 2),
        "flags": flags,
        "files_written": [b2b_path, b2c_path, hsn_path],
    }

    log_activity(workspace, f"GSTR-1 compiled for {month or 'all'} | B2B:{len(b2b_rows)} B2C:{len(b2c_rows)} Flags:{len(flags)}")
    return json.dumps(summary, indent=2)


# ─── GSTR-3B SUMMARY ─────────────────────────────────────────────────────────

@register_tool(
    description="Generate GSTR-3B summary report including tax liability computation and ITC details.",
    parameters={
        "type": "object",
        "properties": {
            "month": {"type": "string", "description": "Month e.g. 'March 2025'"},
            "opening_itc": {"type": "number", "description": "Opening ITC balance in rupees"}
        }
    }
)
def generate_gstr3b(month: str = "", opening_itc: float = 0, _ctx: ToolContext = None) -> str:
    workspace = _ctx.workspace if (_ctx and hasattr(_ctx, "workspace")) else "./workspace"
    invoices = load_extracted_invoices(workspace, month)

    if not invoices:
        return json.dumps({"error": "No invoice data found. Run scan_invoice_directory first."})

    # Outward supplies (sales)
    outward = [inv for inv in invoices if inv.get("supply_type") in ["B2B", "B2C"]]
    # Inward supplies (purchases) — flagged ITC eligible
    inward_eligible = [inv for inv in invoices if inv.get("itc_eligible", True)]

    total_outward_taxable = sum(inv.get("taxable_value", 0) for inv in outward)
    total_cgst_liability  = sum(inv.get("cgst", 0) for inv in outward)
    total_sgst_liability  = sum(inv.get("sgst", 0) for inv in outward)
    total_igst_liability  = sum(inv.get("igst", 0) for inv in outward)
    total_liability       = total_cgst_liability + total_sgst_liability + total_igst_liability

    itc_igst = sum(inv.get("igst", 0) for inv in inward_eligible)
    itc_cgst = sum(inv.get("cgst", 0) for inv in inward_eligible)
    itc_sgst = sum(inv.get("sgst", 0) for inv in inward_eligible)
    total_itc = itc_igst + itc_cgst + itc_sgst + opening_itc

    net_payable = max(0, round(total_liability - total_itc, 2))

    report = {
        "period": month or datetime.now().strftime("%B %Y"),
        "generated_at": datetime.now().isoformat(),

        "3_1_outward_supplies": {
            "taxable_value": round(total_outward_taxable, 2),
            "igst": round(total_igst_liability, 2),
            "cgst": round(total_cgst_liability, 2),
            "sgst": round(total_sgst_liability, 2),
            "total_liability": round(total_liability, 2),
        },

        "4_itc_available": {
            "opening_balance": opening_itc,
            "igst_itc": round(itc_igst, 2),
            "cgst_itc": round(itc_cgst, 2),
            "sgst_itc": round(itc_sgst, 2),
            "total_itc": round(total_itc, 2),
        },

        "6_tax_payable": {
            "total_liability": round(total_liability, 2),
            "less_itc": round(total_itc, 2),
            "net_payable": net_payable,
        },

        "summary": {
            "invoices_processed": len(invoices),
            "itc_eligible_invoices": len(inward_eligible),
            "itc_blocked_invoices": len(invoices) - len(inward_eligible),
        }
    }

    ts = month.replace(" ", "-").lower() if month else datetime.now().strftime("%Y-%m")
    out_path = os.path.join(workspace, "outputs", f"gstr3b-{ts}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    log_activity(workspace, f"GSTR-3B generated for {month or 'current'} | Liability:{total_liability} ITC:{total_itc} Net:{net_payable}")
    return json.dumps(report, indent=2)


# ─── MISMATCH REPORT ─────────────────────────────────────────────────────────

@register_tool(
    description="Generate a mismatch and error report from all flagged invoices. Highlights tax rate errors, missing GSTINs, blocked ITC, duplicates.",
    parameters={
        "type": "object",
        "properties": {
            "month": {"type": "string", "description": "Filter by month (optional)"}
        }
    }
)
def generate_mismatch_report(month: str = "", _ctx: ToolContext = None) -> str:
    workspace = _ctx.workspace if (_ctx and hasattr(_ctx, "workspace")) else "./workspace"
    invoices = load_extracted_invoices(workspace, month)

    flagged = [inv for inv in invoices if inv.get("flags")]

    categories = {
        "tax_rate_mismatch":    [],
        "missing_gstin":        [],
        "itc_blocked":          [],
        "duplicate_suspected":  [],
        "other":                [],
    }

    for inv in flagged:
        for flag in inv.get("flags", []):
            entry = {
                "invoice_number": inv.get("invoice_number"),
                "file": inv.get("file"),
                "flag": flag,
                "amount": inv.get("total_amount", 0),
            }
            if "TAX_RATE" in flag:
                categories["tax_rate_mismatch"].append(entry)
            elif "GSTIN" in flag:
                categories["missing_gstin"].append(entry)
            elif "ITC_BLOCKED" in flag:
                categories["itc_blocked"].append(entry)
            elif "DUPLICATE" in flag:
                categories["duplicate_suspected"].append(entry)
            else:
                categories["other"].append(entry)

    # Write CSV report
    ts = month.replace(" ", "-").lower() if month else datetime.now().strftime("%Y-%m")
    out_path = os.path.join(workspace, "outputs", f"mismatch-report-{ts}.csv")
    all_flags = []
    for cat, entries in categories.items():
        for e in entries:
            all_flags.append({**e, "category": cat})

    if all_flags:
        write_csv(out_path, ["category", "invoice_number", "file", "flag", "amount"], all_flags)

    report = {
        "status": "complete",
        "period": month or "all",
        "total_invoices": len(invoices),
        "flagged_invoices": len(flagged),
        "clean_invoices": len(invoices) - len(flagged),
        "categories": {k: len(v) for k, v in categories.items()},
        "details": categories,
        "report_file": out_path if all_flags else None,
    }

    log_activity(workspace, f"Mismatch report: {len(flagged)} flagged / {len(invoices)} total invoices")
    return json.dumps(report, indent=2)


# ─── P&L REPORT ──────────────────────────────────────────────────────────────

@register_tool(
    description="Generate a monthly P&L statement from extracted invoice data.",
    parameters={
        "type": "object",
        "properties": {
            "month": {"type": "string", "description": "Month e.g. 'March 2025'"}
        }
    }
)
def generate_pl_report(month: str = "", _ctx: ToolContext = None) -> str:
    workspace = _ctx.workspace if (_ctx and hasattr(_ctx, "workspace")) else "./workspace"
    invoices = load_extracted_invoices(workspace, month)

    if not invoices:
        return json.dumps({"error": "No invoice data found."})

    total_revenue    = sum(inv.get("total_amount", 0) for inv in invoices)
    total_taxable    = sum(inv.get("taxable_value", 0) for inv in invoices)
    total_tax_out    = sum(inv.get("total_tax", 0) for inv in invoices)
    itc_credit       = sum(inv.get("total_tax", 0) for inv in invoices if inv.get("itc_eligible"))
    net_tax_cost     = max(0, total_tax_out - itc_credit)

    pl = {
        "period": month or datetime.now().strftime("%B %Y"),
        "generated_at": datetime.now().isoformat(),
        "revenue": {
            "gross_revenue": round(total_revenue, 2),
            "taxable_value": round(total_taxable, 2),
            "gst_collected": round(total_tax_out, 2),
            "net_revenue": round(total_taxable, 2),
        },
        "tax": {
            "output_gst": round(total_tax_out, 2),
            "input_itc": round(itc_credit, 2),
            "net_gst_payable": round(net_tax_cost, 2),
        },
        "summary": {
            "total_invoices": len(invoices),
            "average_invoice_value": round(total_revenue / len(invoices), 2) if invoices else 0,
        }
    }

    ts = month.replace(" ", "-").lower() if month else datetime.now().strftime("%Y-%m")
    out_path = os.path.join(workspace, "outputs", f"pl-report-{ts}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(pl, f, indent=2)

    # Also write CSV for easy viewing
    csv_path = os.path.join(workspace, "outputs", f"pl-report-{ts}.csv")
    rows = [
        {"Category": "Gross Revenue",    "Amount": pl["revenue"]["gross_revenue"]},
        {"Category": "Taxable Value",    "Amount": pl["revenue"]["taxable_value"]},
        {"Category": "GST Collected",    "Amount": pl["revenue"]["gst_collected"]},
        {"Category": "Input ITC",        "Amount": pl["tax"]["input_itc"]},
        {"Category": "Net GST Payable",  "Amount": pl["tax"]["net_gst_payable"]},
    ]
    write_csv(csv_path, ["Category", "Amount"], rows)

    log_activity(workspace, f"P&L report: Revenue:{total_revenue} Tax:{total_tax_out} ITC:{itc_credit} Net:{net_tax_cost}")
    return json.dumps(pl, indent=2)