"""
gst_extract.py — Bujji tool
Extracts GST invoice data from PDF or image files using LLM vision + text parsing.
Drop this file into bujji/tools/ — hot-reloaded on next message.
"""

import json
import os
import re
from datetime import datetime

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from bujji.tools.base import register_tool, ToolContext
except ImportError:
    # Fallback for standalone testing
    def register_tool(**kwargs):
        def decorator(fn): return fn
        return decorator
    ToolContext = object


# ─── HSN TAX RATE TABLE ──────────────────────────────────────────────────────
# Common HSN codes and their correct GST rates
HSN_TAX_RATES = {
    "0101": 0, "0201": 0, "0401": 5,   # Livestock, meat, dairy
    "1001": 0, "1006": 5,               # Wheat, rice
    "2201": 18, "2202": 18,             # Water, beverages
    "3004": 5, "3006": 12,              # Medicines
    "4011": 28, "4012": 28,             # Tyres
    "6101": 5, "6201": 5,              # Clothing
    "7108": 3, "7113": 3,              # Gold, jewellery
    "8415": 28, "8418": 28,            # AC, refrigerator
    "8471": 18, "8473": 18,            # Computers, peripherals
    "8517": 18, "8525": 18,            # Phones, electronics
    "8703": 28, "8711": 28,            # Cars, motorcycles
    "9401": 18, "9403": 18,            # Furniture
    "9989": 18,                         # Other services
}

# SAC codes for services
SAC_TAX_RATES = {
    "9954": 18,  # Construction
    "9961": 5,   # Retail trade
    "9971": 18,  # Financial services
    "9972": 18,  # Real estate
    "9983": 18,  # IT services
    "9984": 18,  # Telecom
    "9985": 18,  # Support services
    "9997": 18,  # Other services
}

ITC_BLOCKED = [
    "food", "beverages", "club", "health", "cosmetic",
    "personal", "motor vehicle", "travel", "entertainment",
    "restaurant", "hotel", "rent a cab"
]


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def extract_text_from_pdf(file_path: str) -> str:
    """Extract raw text from PDF using PyMuPDF."""
    if not PYMUPDF_AVAILABLE:
        return f"[PyMuPDF not installed — run: pip install pymupdf]\nFile: {file_path}"
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text.strip()
    except Exception as e:
        return f"[PDF read error: {e}]"


def parse_gstin(text: str) -> list:
    """Extract all GSTINs from text using regex."""
    pattern = r'\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b'
    return list(set(re.findall(pattern, text.upper())))


def parse_invoice_number(text: str) -> str:
    """Try to extract invoice number."""
    patterns = [
        r'(?:invoice|inv|bill|receipt)\s*(?:no|number|#|:)?\s*([A-Z0-9\-/]+)',
        r'\b(INV[-/][A-Z0-9\-/]+)\b',
        r'\b(BILL[-/][A-Z0-9\-/]+)\b',
    ]
    for p in patterns:
        m = re.search(p, text.upper())
        if m:
            return m.group(1)
    return "NOT_FOUND"


def parse_amounts(text: str) -> dict:
    """Extract tax amounts from text."""
    amounts = {}
    patterns = {
        "cgst":     r'CGST\s*[@\s]*[\d.]+\s*%?\s*[:\s]*[\u20B9Rs]*\s*([\d,]+\.?\d*)',
        "sgst":     r'SGST\s*[@\s]*[\d.]+\s*%?\s*[:\s]*[\u20B9Rs]*\s*([\d,]+\.?\d*)',
        "igst":     r'IGST\s*[@\s]*[\d.]+\s*%?\s*[:\s]*[\u20B9Rs]*\s*([\d,]+\.?\d*)',
        "taxable":  r'(?:taxable\s*(?:value|amount)|sub\s*total)\s*[:\s]*[\u20B9Rs]*\s*([\d,]+\.?\d*)',
        "total":    r'(?:grand\s*total|total\s*amount|amount\s*payable)\s*[:\s]*[\u20B9Rs]*\s*([\d,]+\.?\d*)',
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text.upper())
        if m:
            try:
                amounts[key] = float(m.group(1).replace(",", ""))
            except Exception:
                pass
    return amounts


def parse_hsn_codes(text: str) -> list:
    """Extract HSN/SAC codes."""
    pattern = r'\b([0-9]{4,8})\b'
    candidates = re.findall(pattern, text)
    # Filter to plausible HSN lengths (4, 6, 8 digits)
    return list(set(c for c in candidates if len(c) in [4, 6, 8]))


def parse_date(text: str) -> str:
    """Try to parse invoice date."""
    patterns = [
        r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return datetime.today().strftime("%d/%m/%Y")


def validate_tax_rate(hsn: str, claimed_rate: float) -> dict:
    """Check if claimed tax rate matches expected rate for HSN."""
    hsn_prefix = hsn[:4] if len(hsn) >= 4 else hsn
    expected = HSN_TAX_RATES.get(hsn_prefix) or SAC_TAX_RATES.get(hsn_prefix)
    if expected is None:
        return {"status": "unknown", "message": f"HSN {hsn} not in lookup table"}
    if abs(claimed_rate - expected) > 0.5:
        return {
            "status": "mismatch",
            "message": f"HSN {hsn}: expected {expected}%, found {claimed_rate}%",
            "expected": expected,
            "found": claimed_rate
        }
    return {"status": "ok", "expected": expected, "found": claimed_rate}


def check_itc_eligibility(description: str) -> dict:
    """Check if expense is ITC-eligible."""
    desc_lower = description.lower()
    for blocked in ITC_BLOCKED:
        if blocked in desc_lower:
            return {"eligible": False, "reason": f"Blocked credit: '{blocked}' in description"}
    return {"eligible": True}


def build_invoice_record(file_path: str, raw_text: str) -> dict:
    """Build structured invoice record from extracted text."""
    gstins = parse_gstin(raw_text)
    amounts = parse_amounts(raw_text)
    hsn_codes = parse_hsn_codes(raw_text)

    # Compute effective tax rate
    taxable = amounts.get("taxable", 0)
    total_tax = amounts.get("cgst", 0) + amounts.get("sgst", 0) + amounts.get("igst", 0)
    effective_rate = round((total_tax / taxable * 100), 1) if taxable > 0 else 0

    # Classify supply type
    supply_type = "B2C"
    if len(gstins) >= 2:
        supply_type = "B2B"
    elif len(gstins) == 1:
        supply_type = "B2B" if amounts.get("total", 0) > 2.5e5 else "B2C"

    record = {
        "file": os.path.basename(file_path),
        "invoice_number": parse_invoice_number(raw_text),
        "invoice_date": parse_date(raw_text),
        "supply_type": supply_type,
        "seller_gstin": gstins[0] if len(gstins) > 0 else "NOT_FOUND",
        "buyer_gstin": gstins[1] if len(gstins) > 1 else "CONSUMER",
        "hsn_codes": hsn_codes[:5],  # Top 5 detected
        "taxable_value": amounts.get("taxable", 0),
        "cgst": amounts.get("cgst", 0),
        "sgst": amounts.get("sgst", 0),
        "igst": amounts.get("igst", 0),
        "total_tax": round(total_tax, 2),
        "total_amount": amounts.get("total", 0),
        "effective_tax_rate": effective_rate,
        "flags": [],
        "itc_eligible": True,
        "extracted_at": datetime.now().isoformat(),
    }

    # ── Validations ──
    if record["invoice_number"] == "NOT_FOUND":
        record["flags"].append("MISSING_INVOICE_NUMBER")

    if record["seller_gstin"] == "NOT_FOUND":
        record["flags"].append("MISSING_SELLER_GSTIN")

    if supply_type == "B2B" and record["buyer_gstin"] == "CONSUMER":
        record["flags"].append("B2B_MISSING_BUYER_GSTIN")

    for hsn in hsn_codes[:3]:
        validation = validate_tax_rate(hsn, effective_rate)
        if validation["status"] == "mismatch":
            record["flags"].append(f"TAX_RATE_MISMATCH:{hsn}")

    if record["taxable_value"] == 0 and record["total_amount"] > 0:
        record["flags"].append("TAXABLE_VALUE_NOT_DETECTED")

    itc = check_itc_eligibility(raw_text[:500])
    if not itc["eligible"]:
        record["itc_eligible"] = False
        record["flags"].append(f"ITC_BLOCKED:{itc['reason']}")

    return record


# ─── BUJJI TOOLS ─────────────────────────────────────────────────────────────

@register_tool(
    description="Extract GST invoice data from a PDF file. Returns structured invoice data including GSTIN, HSN codes, tax amounts, and validation flags.",
    parameters={
        "type": "object",
        "required": ["file_path"],
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the invoice PDF file"
            }
        }
    }
)
def extract_invoice_pdf(file_path: str, _ctx: ToolContext = None) -> str:
    if not os.path.exists(file_path):
        return json.dumps({"error": f"File not found: {file_path}"})

    raw_text = extract_text_from_pdf(file_path)

    if raw_text.startswith("["):
        return json.dumps({"error": raw_text, "file": file_path})

    record = build_invoice_record(file_path, raw_text)

    # Log to workspace
    log_path = None
    if _ctx and hasattr(_ctx, "workspace"):
        log_path = os.path.join(_ctx.workspace, "logs", "activity.log")

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] EXTRACTED: {record['file']} | "
                    f"INV: {record['invoice_number']} | "
                    f"GSTIN: {record['seller_gstin']} | "
                    f"Total: {record['total_amount']} | "
                    f"Flags: {len(record['flags'])}\n")

    return json.dumps(record, indent=2)


@register_tool(
    description="Scan a directory for unprocessed invoice PDFs and extract GST data from all of them. Returns a batch summary.",
    parameters={
        "type": "object",
        "required": ["directory"],
        "properties": {
            "directory": {
                "type": "string",
                "description": "Path to folder containing invoice PDFs"
            },
            "move_processed": {
                "type": "boolean",
                "description": "If true, move processed files to a /processed subdirectory"
            }
        }
    }
)
def scan_invoice_directory(directory: str, move_processed: bool = True, _ctx: ToolContext = None) -> str:
    import shutil

    if not os.path.exists(directory):
        return json.dumps({"error": f"Directory not found: {directory}"})

    pdf_files = [
        f for f in os.listdir(directory)
        if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))
        and not f.startswith(".")
    ]

    if not pdf_files:
        return json.dumps({"status": "no_files", "message": "No invoice files found in directory"})

    results = []
    errors = []
    total_taxable = 0
    total_tax = 0
    flags_all = []

    for filename in pdf_files:
        file_path = os.path.join(directory, filename)
        try:
            raw = extract_text_from_pdf(file_path)
            record = build_invoice_record(file_path, raw)
            results.append(record)
            total_taxable += record["taxable_value"]
            total_tax += record["total_tax"]
            flags_all.extend(record["flags"])

            if move_processed:
                processed_dir = os.path.join(directory, "processed")
                os.makedirs(processed_dir, exist_ok=True)
                shutil.move(file_path, os.path.join(processed_dir, filename))

        except Exception as e:
            errors.append({"file": filename, "error": str(e)})

    summary = {
        "status": "complete",
        "files_processed": len(results),
        "errors": len(errors),
        "total_taxable_value": round(total_taxable, 2),
        "total_tax_collected": round(total_tax, 2),
        "total_flags": len(flags_all),
        "flag_summary": list(set(flags_all)),
        "invoices": results,
        "error_details": errors,
    }

    # Save to workspace outputs
    if _ctx and hasattr(_ctx, "workspace"):
        out_path = os.path.join(_ctx.workspace, "outputs", "batch_extracted.json")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(summary, f, indent=2)

    return json.dumps(summary, indent=2)