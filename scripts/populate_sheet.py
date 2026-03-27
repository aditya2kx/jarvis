#!/usr/bin/env python3
"""Populate the CHITRA Tax Tracker Google Sheet with data from the knowledge base."""

import json
import os
import urllib.request
import urllib.parse

from config_loader import get_sheet_id, refresh_access_token, project_dir, kb_path

SPREADSHEET_ID = get_sheet_id("tax_tracker_id")


def clear_sheet(token, range_str):
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
        f"/values/{urllib.parse.quote(range_str)}:clear"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        json.loads(resp.read())


def batch_update_values(token, ranges_and_values):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate"
    body = {
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": r, "values": v} for r, v in ranges_and_values],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    print(f"Updated {result.get('totalUpdatedCells', 0)} cells")
    return result


def format_checklist_status(raw: str) -> str:
    m = {
        "received": "Received",
        "not_received": "Not Received",
        "n/a": "N/A",
    }
    key = (raw or "").strip().lower()
    if key in m:
        return m[key]
    return raw.replace("_", " ").title()


def build_document_checklist():
    registry = json.load(open(kb_path("document-registry.json")))

    header = ["Doc Type", "Issuer / Source", "For", "Category", "Available By", "Status", "Notes"]
    rows = [header]
    for doc in registry["documents"]:
        status = format_checklist_status(doc["status"])
        rows.append([
            doc["docType"],
            doc["issuer"],
            doc.get("for", "Joint"),
            doc["category"],
            doc["availabilityMonth"],
            status,
            doc.get("notes", ""),
        ])
    return rows


def build_return_summary():
    profile_path = kb_path("profile-2024.json")
    if not os.path.exists(profile_path):
        return [["Form / Schedule", "Description", "Amount", "Category"],
                ["", "No prior year profile found", "", ""]]

    profile = json.load(open(profile_path))

    header = ["Form / Schedule", "Description", "Amount", "Category"]
    rows = [header]

    def add(form, desc, amt, cat):
        rows.append([form, desc, f"${amt:,.0f}" if isinstance(amt, (int, float)) else str(amt), cat])

    inc = profile["income"]
    add("Form 1040", "Total Wages (Line 1z)", inc["wages"]["total"], "Income")
    for emp in inc["wages"]["employers"]:
        add("  W-2", f"  {emp['name']} ({emp['for']})", emp["wages"], "Income")
    add("Schedule B", "Taxable Interest", inc["interest"]["total"], "Income")
    add("Schedule D", "Net Capital Gains", inc["capitalGains"]["total"], "Income")
    add("  Sched D", "  Short-term gains", inc["capitalGains"]["shortTerm"]["total"], "Income")
    add("  Sched D", "  Long-term gains", inc["capitalGains"]["longTerm"]["total"], "Income")

    biz = inc.get("businessIncome", {})
    if biz:
        add("Schedule C", f"Business Income/Loss ({biz.get('entity', 'Business')})", biz.get("netLoss", biz.get("netIncome", 0)), "Income")

    for prop in inc.get("rentalRealEstate", {}).get("properties", []):
        add("Schedule E", f"Rental: {prop.get('address', 'Property')} (net to return)", prop.get("netToReturn", 0), "Income")

    add("Form 1040", "Total Income (Line 9)", inc["totalIncome"], "Income")
    add("Form 1040", "Adjusted Gross Income (Line 11)", inc["agi"], "Income")

    ded = profile["deductions"]
    add("Schedule A", "Total Itemized Deductions", ded["totalItemized"], "Deduction")
    add("  Sched A", "  SALT (capped)", ded["taxesPaid"]["totalAfterCap"], "Deduction")
    add("  Sched A", "  Mortgage Interest", ded["mortgageInterest"]["total"], "Deduction")
    add("  Sched A", "  Charitable Contributions", ded["charitableContributions"]["total"], "Deduction")
    add("  Sched A", "  Investment Interest", ded["investmentInterest"]["total"], "Deduction")

    tax = profile["taxComputation"]
    add("Form 1040", "Taxable Income (Line 15)", inc["taxableIncome"], "Tax")
    add("Form 1040", "Regular Tax (Line 16)", tax["regularTax"], "Tax")
    add("Form 6251", "Alternative Minimum Tax", tax["amt"], "Tax")
    if tax.get("amtDetails"):
        add("  6251", "  ISO Exercise Excess (AMT driver)", tax["amtDetails"].get("isoExerciseExcess", 0), "Tax")
        add("  6251", "  AMTI", tax["amtDetails"].get("amti", 0), "Tax")
    add("Form 8959", "Additional Medicare Tax", tax["additionalMedicareTax"], "Tax")
    add("Form 8960", "Net Investment Income Tax", tax["netInvestmentIncomeTax"], "Tax")
    add("Form 1040", "Total Tax (Line 24)", tax["totalTax"], "Tax")

    pay = profile["payments"]
    add("Form 1040", "Federal Withholding (W-2s)", pay["federalWithholdingW2"], "Payment")
    add("Form 8959", "Additional Medicare Withholding", pay["additionalMedicareWithholding"], "Payment")
    add("Form 4868", "Extension Payment", pay["extensionPayment"], "Payment")
    add("Form 1040", "Total Payments", pay["totalPayments"], "Payment")
    add("Form 1040", "Refund", pay["refund"], "Payment")

    add("Form 1040", "Effective Tax Rate", f"{tax['effectiveRate']:.1%}", "Summary")
    add("Form 1040", "Tax Bracket", tax["taxBracket"], "Summary")

    # Carryovers — dynamically read whatever keys exist
    carryovers = profile.get("carryoversTo2025", profile.get("carryoversToNextYear", {}))
    pal = carryovers.get("passiveActivityLosses", {})
    for activity, amount in pal.items():
        if activity not in ("total", "details"):
            label = activity.replace("_", " ").replace("LLC", " LLC").strip()
            label = " ".join(w.capitalize() if w.islower() else w for w in label.split())
            add("Carryover", f"Passive Loss - {label}", amount, "Carryover")

    if "minimumTaxCredit" in carryovers:
        add("Carryover", "Minimum Tax Credit (Form 8801)", carryovers["minimumTaxCredit"], "Carryover")

    qbi = carryovers.get("qbiLossCarryforward", {})
    for vintage, amount in qbi.items():
        label = vintage.replace("_", " ").replace("From", "from")
        label = " ".join(w.capitalize() if w.islower() else w for w in label.split())
        add("Carryover", f"QBI Loss - {label}", amount, "Carryover")

    return rows


def build_changes_log():
    header = ["Date Reported", "Change Type", "Description", "Impact on Docs", "Impact on Estimate", "New Docs Needed"]
    return [header]


def build_cpa_questions():
    registry = json.load(open(kb_path("document-registry.json")))
    cpa = registry.get("cpaQuestions", {})

    header = ["Section", "Question", "Status", "Notes"]
    rows = [header]
    for q in cpa.get("overall", []):
        rows.append(["Overall / Strategy", q, "Open", ""])
    for section, questions in cpa.get("bySection", {}).items():
        section_label = section.replace("_", " ").title()
        for q in questions:
            rows.append([section_label, q, "Open", ""])
    return rows


def build_cpa_document_navigator():
    """CPA-facing tab: one row per leaf folder in the Drive zip.
    Generated dynamically from the document registry — no hardcoded PII."""

    registry = json.load(open(kb_path("document-registry.json")))
    folder_structure = registry.get("driveFolderStructure", {})

    header = ["Folder Path", "Purpose", "Files in This Folder", "Status / Notes"]
    rows = [header]

    folder_docs = {}
    for doc in registry["documents"]:
        path = doc.get("drivePath", "")
        if not path:
            continue
        path = path.rstrip("/")

        if path not in folder_docs:
            folder_docs[path] = []

        if doc.get("driveFiles"):
            for df in doc["driveFiles"]:
                folder_docs[path].append({
                    "name": df.get("name", ""),
                    "note": df.get("note", doc.get("notes", "")),
                    "status": doc["status"],
                })
        elif doc.get("driveFileName"):
            folder_docs[path].append({
                "name": doc["driveFileName"],
                "note": doc.get("notes", ""),
                "status": doc["status"],
            })
        else:
            folder_docs[path].append({
                "name": f"{doc['docType']} - {doc['issuer']}",
                "note": doc.get("notes", ""),
                "status": doc["status"],
            })

    for folder_path in sorted(folder_docs.keys()):
        docs = folder_docs[folder_path]
        file_lines = []
        statuses = set()
        for d in docs:
            note_short = (d["note"] or "").split(".")[0].strip()
            if note_short:
                file_lines.append(f"• {d['name']} — {note_short}")
            else:
                file_lines.append(f"• {d['name']}")
            statuses.add(d["status"])

        files_str = "\n".join(file_lines)

        if all(s == "received" for s in statuses):
            status = "Complete"
        elif all(s == "n/a" for s in statuses):
            status = "N/A"
        elif any(s == "not_received" for s in statuses):
            status = "Pending — some documents not yet received"
        else:
            status = "Mixed"

        folder_label = folder_path.split("/")[-1].strip() if "/" in folder_path else folder_path
        rows.append([folder_path, folder_label, files_str, status])

    return rows


def main():
    token = refresh_access_token()

    for tab in ["2025 Document Checklist", "2024 Return Summary", "2025 Changes Log", "CPA Questions & Goals"]:
        try:
            clear_sheet(token, f"'{tab}'!A1:Z200")
        except Exception:
            pass

    checklist = build_document_checklist()
    summary = build_return_summary()
    changes = build_changes_log()
    cpa_q = build_cpa_questions()

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        ss = json.loads(resp.read())
    sheet_titles = [s["properties"]["title"] for s in ss["sheets"]]

    tabs_to_create = []
    if "CPA Questions & Goals" not in sheet_titles:
        tabs_to_create.append({"title": "CPA Questions & Goals", "index": 3})
    if "CPA Document Navigator" not in sheet_titles:
        tabs_to_create.append({"title": "CPA Document Navigator", "index": 4})

    if tabs_to_create:
        add_sheet_body = {
            "requests": [
                {"addSheet": {"properties": {"title": t["title"], "index": t["index"], "gridProperties": {"frozenRowCount": 1}}}}
                for t in tabs_to_create
            ]
        }
        add_url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}:batchUpdate"
        add_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        add_req = urllib.request.Request(add_url, data=json.dumps(add_sheet_body).encode(), headers=add_headers, method="POST")
        with urllib.request.urlopen(add_req) as resp:
            json.loads(resp.read())
        for t in tabs_to_create:
            print(f"Created '{t['title']}' tab")

    nav = build_cpa_document_navigator()

    last_cl = len(checklist)
    last_su = len(summary)
    last_cpa = len(cpa_q)
    last_nav = len(nav)

    try:
        clear_sheet(token, "'CPA Document Navigator'!A1:Z200")
    except Exception:
        pass

    batch_update_values(token, [
        (f"'2025 Document Checklist'!A1:G{last_cl}", checklist),
        (f"'2024 Return Summary'!A1:D{last_su}", summary),
        ("'2025 Changes Log'!A1:F1", changes),
        (f"'CPA Questions & Goals'!A1:D{last_cpa}", cpa_q),
        (f"'CPA Document Navigator'!A1:D{last_nav}", nav),
    ])

    received = sum(1 for r in checklist[1:] if r[5] == "Received")
    total = len(checklist) - 1
    print(f"Checklist: {received}/{total} received, {total - received} missing")
    print(f"Summary: {last_su - 1} line items")
    print(f"CPA Questions: {last_cpa - 1} questions")
    print(f"Navigator: {last_nav - 1} document rows")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    main()
