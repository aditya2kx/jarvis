#!/usr/bin/env python3
"""Process questionnaire answers → final registry + portal task list.

This is the pipeline that closes the loop:
  1. Start with derived registry (from prior-year return)
  2. Apply user answers (confirmations, changes, new items)
  3. Match each document to a portal (from available navigation modules)
  4. Output: final registry + prioritized portal task list

The portal task list tells CHITRA exactly which portals to visit and in
what order, considering dependencies (e.g. login first, then navigate).

Usage:
    # Interactive: CHITRA calls these functions as it processes answers
    processor = AnswerProcessor("path/to/derived-registry.json")
    processor.apply_answer("employment_acme", confirmed=True)
    processor.apply_answer("new_home", confirmed=True, details={"address": "123 Main St"})
    task_list = processor.generate_portal_tasks()

    # Batch: from a saved answers file
    python process_answers.py --answers answers.json --registry derived-registry.json
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import kb_path, project_dir
from agents.chitra.scripts.derive_registry_from_return import derive_folder_tree


class AnswerProcessor:
    """Processes questionnaire answers to build a complete document registry."""

    def __init__(self, derived_registry_path=None):
        """
        Args:
            derived_registry_path: Path to the registry derived from the prior-year return.
                                   If None, uses the default location.
        """
        path = derived_registry_path or kb_path("derived-registry-2025.json")
        if os.path.exists(path):
            data = json.loads(open(path).read())
            self.docs = data.get("derived_docs", data.get("documents", []))
        else:
            self.docs = []

        self.next_id = max((d.get("id", 0) for d in self.docs), default=0) + 1
        self.answers = []
        self.changes = []

    def apply_confirmation(self, doc_id, confirmed, details=None):
        """Process a confirmation answer (prior-year item still applies or not).

        Args:
            doc_id: ID of the document in the derived registry
            confirmed: True if the item still applies, False if it changed
            details: Optional dict with change details (new employer, new address, etc.)
        """
        doc = next((d for d in self.docs if d.get("id") == doc_id), None)
        if not doc:
            return

        self.answers.append({
            "doc_id": doc_id,
            "type": "confirmation",
            "confirmed": confirmed,
            "details": details,
        })

        if confirmed:
            doc["status"] = "expected"
        else:
            doc["status"] = "changed"
            if details:
                doc["change_details"] = details
                if details.get("new_issuer"):
                    old_issuer = doc["issuer"]
                    doc["issuer"] = details["new_issuer"]
                    self.changes.append(f"Issuer changed: {old_issuer} → {details['new_issuer']}")
                if details.get("removed"):
                    doc["status"] = "removed"
                    self.changes.append(f"Removed: {doc['docType']} from {doc['issuer']}")

    def add_new_document(self, doc_type, issuer, category, source,
                         details=None, for_whom="Joint"):
        """Add a new document discovered from questionnaire answers.

        Args:
            doc_type: Document type (e.g. "W-2", "Form 1098", "Property Tax Bill")
            issuer: Issuer name
            category: Category (Income, Deduction, Other)
            source: How to obtain it (e.g. "employer_portal", "county_website")
            details: Optional context dict
            for_whom: Who the document is for

        Returns:
            The new document dict
        """
        doc = {
            "id": self.next_id,
            "docType": doc_type,
            "issuer": issuer,
            "for": for_whom,
            "category": category,
            "source": source,
            "status": "expected",
            "addedFrom": "questionnaire",
        }
        if details:
            doc["details"] = details

        self.docs.append(doc)
        self.next_id += 1
        self.changes.append(f"Added: {doc_type} from {issuer}")
        return doc

    def add_from_life_event(self, event_type, details):
        """Add documents triggered by a life event answer.

        Encodes the "smart follow-up" logic: one answer → multiple documents.

        Args:
            event_type: "new_home", "new_employer", "new_brokerage", etc.
            details: Dict with event specifics

        Returns:
            List of new document dicts
        """
        new_docs = []
        handlers = {
            "new_home": self._handle_new_home,
            "home_sold": self._handle_home_sold,
            "new_employer": self._handle_new_employer,
            "employer_left": self._handle_employer_left,
            "new_brokerage": self._handle_new_brokerage,
            "new_rental": self._handle_new_rental,
            "rental_sold": self._handle_rental_sold,
            "new_business_employee": self._handle_business_employee,
            "new_partnership": self._handle_new_partnership,
            "state_move": self._handle_state_move,
            "new_charity": self._handle_new_charity,
            "homestead_exemption": self._handle_homestead_exemption,
        }

        handler = handlers.get(event_type)
        if handler:
            new_docs = handler(details)
        else:
            if details.get("doc_type") and details.get("issuer"):
                new_docs = [self.add_new_document(
                    details["doc_type"], details["issuer"],
                    details.get("category", "Other"),
                    details.get("source", "unknown"),
                    details=details,
                )]

        return new_docs

    # --- Life event handlers ---

    def _handle_new_home(self, details):
        """New primary residence → mortgage 1098, property tax, homestead."""
        docs = []
        address = details.get("address", "new primary residence")
        lender = details.get("lender")

        if lender:
            docs.append(self.add_new_document(
                "Form 1098 (Mortgage Interest) - PRIMARY RESIDENCE",
                lender, "Deduction", "lender_portal",
                details={"address": address},
            ))

        docs.append(self.add_new_document(
            "Property Tax Bill - PRIMARY RESIDENCE",
            f"County Tax Assessor",
            "Deduction", "county_website",
            details={"address": address, "auto_derive": "address → county → CAD URL"},
        ))

        docs.append(self.add_new_document(
            "Homestead Exemption Application/Approval",
            f"County Appraisal District",
            "Deduction", "county_website",
            details={"address": address},
        ))

        # HUD-1 closing statement for the purchase
        docs.append(self.add_new_document(
            "HUD-1 Closing Statement (Purchase)",
            details.get("title_company", "Title Company"),
            "Other", "user_provides",
            details={"address": address},
        ))

        return docs

    def _handle_home_sold(self, details):
        """Home sold → closing statement, final property tax, capital gains."""
        docs = []
        address = details.get("address", "sold property")

        docs.append(self.add_new_document(
            "HUD-1 Closing Statement (Sale)",
            details.get("title_company", "Title Company"),
            "Other", "user_provides",
            details={"address": address},
        ))

        # Mark existing mortgage 1098 as final year
        for doc in self.docs:
            if ("1098" in doc.get("docType", "") and
                    address.lower() in doc.get("issuer", "").lower()):
                doc["status"] = "final_year"
                doc["details"] = doc.get("details", {})
                doc["details"]["final_year"] = True

        return docs

    def _handle_new_employer(self, details):
        """New employer → W-2, possibly 1095-C."""
        docs = []
        employer = details.get("employer", "New Employer")
        person = details.get("for", "Joint")

        docs.append(self.add_new_document(
            "W-2", employer, "Income", "employer_portal",
            for_whom=person,
        ))

        if details.get("has_health_insurance", True):
            docs.append(self.add_new_document(
                "Form 1095-C", employer, "Other", "employer_portal",
                for_whom=person,
            ))

        if details.get("has_retirement_plan"):
            plan_type = details.get("retirement_plan_type", "401k")
            docs.append(self.add_new_document(
                f"{plan_type} Records", employer, "Other", "retirement_portal",
                for_whom=person,
            ))

        return docs

    def _handle_employer_left(self, details):
        """Left an employer → final W-2 still expected, mark as last year."""
        employer = details.get("employer", "Former Employer")
        for doc in self.docs:
            if doc.get("docType") == "W-2" and employer.lower() in doc.get("issuer", "").lower():
                doc["status"] = "final_year"
                doc["details"] = doc.get("details", {})
                doc["details"]["left_employer"] = True
        return []

    def _handle_new_brokerage(self, details):
        """New brokerage account → consolidated 1099."""
        return [self.add_new_document(
            "Consolidated 1099",
            details.get("broker", "New Broker"),
            "Income", "brokerage_portal",
            details=details,
        )]

    def _handle_new_rental(self, details):
        """New rental property → 1099-MISC, 1098, property tax, insurance."""
        docs = []
        address = details.get("address", "new rental property")

        if details.get("property_manager"):
            docs.append(self.add_new_document(
                "1099-MISC (Rental Income)",
                details["property_manager"],
                "Income", "property_manager_portal",
                details={"address": address},
            ))

        if details.get("lender"):
            docs.append(self.add_new_document(
                "Form 1098 (Mortgage Interest) - RENTAL",
                details["lender"],
                "Deduction", "lender_portal",
                details={"address": address},
            ))

        docs.append(self.add_new_document(
            "Property Tax Bill - RENTAL",
            "County Tax Assessor",
            "Deduction", "county_website",
            details={"address": address},
        ))

        if details.get("insurance_provider"):
            docs.append(self.add_new_document(
                "Property Insurance Declaration - RENTAL",
                details["insurance_provider"],
                "Deduction", "insurance_portal",
                details={"address": address},
            ))

        return docs

    def _handle_rental_sold(self, details):
        """Rental sold → closing statement, final Schedule E."""
        docs = []
        address = details.get("address", "sold rental")

        docs.append(self.add_new_document(
            "HUD-1 Closing Statement (Rental Sale)",
            details.get("title_company", "Title Company"),
            "Other", "user_provides",
            details={"address": address},
        ))

        for doc in self.docs:
            if ("RENTAL" in doc.get("docType", "") and
                    address.lower() in doc.get("details", {}).get("address", "").lower()):
                doc["status"] = "final_year"

        return docs

    def _handle_business_employee(self, details):
        """Business has employees → W-2, W-3, 941, 940."""
        business = details.get("business", "Your Business")
        payroll = details.get("payroll_provider")
        source = "payroll_portal" if payroll else "user_records"
        issuer = payroll or business

        return [
            self.add_new_document("W-2 (Employee)", issuer, "Other", source),
            self.add_new_document("W-3 (Transmittal)", issuer, "Other", source),
            self.add_new_document("Form 941 (Quarterly)", issuer, "Other", source),
            self.add_new_document("Form 940 (FUTA)", issuer, "Other", source),
        ]

    def _handle_new_partnership(self, details):
        """New partnership investment → K-1."""
        return [self.add_new_document(
            "K-1 (Form 1065)",
            details.get("name", "New Partnership"),
            "Income", "partnership_portal_or_cpa",
            details=details,
        )]

    def _handle_state_move(self, details):
        """Moved to a new state → partial-year filings."""
        docs = []
        from_state = details.get("from_state", "")
        to_state = details.get("to_state", "")

        if from_state:
            docs.append(self.add_new_document(
                f"Partial-Year State Return - {from_state}",
                f"{from_state} tax authority",
                "Other", "user_records",
            ))
        if to_state and to_state != from_state:
            docs.append(self.add_new_document(
                f"Partial-Year State Return - {to_state}",
                f"{to_state} tax authority",
                "Other", "user_records",
            ))

        return docs

    def _handle_new_charity(self, details):
        """Charitable donation → receipt."""
        return [self.add_new_document(
            "Charitable Contribution Receipt",
            details.get("organization", "Charity"),
            "Deduction", details.get("source", "user_records"),
            details=details,
        )]

    def _handle_homestead_exemption(self, details):
        """Homestead exemption filed → approval letter."""
        return [self.add_new_document(
            "Homestead Exemption Approval",
            "County Appraisal District",
            "Deduction", "county_website",
            details=details,
        )]

    # --- Folder tree derivation ---

    def rebuild_folder_tree(self, target_year=2025):
        """Re-derive the full folder tree from all current documents.

        This must be called after all answers are processed so that newly
        added documents get drivePath fields and the folder structure
        includes their subfolders.
        """
        active_docs = [d for d in self.docs if d.get("status") != "removed"]
        self._folder_tree = derive_folder_tree(active_docs, target_year)
        return self._folder_tree

    # --- Output generation ---

    def get_registry(self, target_year=2025):
        """Return the current registry as a JSON-serializable dict."""
        active_docs = [d for d in self.docs if d.get("status") != "removed"]
        if not hasattr(self, "_folder_tree"):
            self.rebuild_folder_tree(target_year)
        return {
            "documents": active_docs,
            "driveFolderStructure": self._folder_tree,
            "total": len(active_docs),
            "by_status": self._count_by("status", active_docs),
            "by_source": self._count_by("source", active_docs),
            "changes_applied": self.changes,
        }

    def generate_portal_tasks(self):
        """Generate a prioritized task list matching docs to portal modules.

        Returns a list of tasks the AI agent should execute, grouped by portal.
        """
        from agents.chitra.scripts.portals.base import list_portals

        available_portals = {p["module"]: p for p in list_portals() if p.get("has_config")}
        active_docs = [d for d in self.docs if d.get("status") not in ("removed",)]

        source_to_module = {
            "brokerage_portal": ["schwab", "etrade", "robinhood", "fidelity"],
            "county_website": ["county_property_tax"],
            "lender_portal": ["wells_fargo", "chase"],
            "payroll_portal": ["homebase"],
            "insurance_portal": ["obie"],
            "property_manager_portal": ["ziprent"],
            "hsa_provider_portal": ["hsa_bank"],
            "retirement_portal": ["fidelity"],
        }

        tasks = []
        docs_by_source = {}
        for doc in active_docs:
            source = doc.get("source", "unknown")
            docs_by_source.setdefault(source, []).append(doc)

        for source, docs in docs_by_source.items():
            possible_modules = source_to_module.get(source, [])
            matched_modules = [m for m in possible_modules if m in available_portals]

            automation_level = "fully_automated"
            if source in ("user_provides", "user_records"):
                automation_level = "user_provides"
            elif source in ("email", "cpa_provided"):
                automation_level = "email_skill"
            elif source == "partnership_portal_or_cpa":
                automation_level = "check_then_ask"
            elif not matched_modules:
                automation_level = "needs_module"

            task = {
                "source": source,
                "documents": [
                    {"id": d.get("id"), "type": d.get("docType"), "issuer": d.get("issuer")}
                    for d in docs
                ],
                "doc_count": len(docs),
                "automation_level": automation_level,
                "portal_modules": matched_modules,
                "action": self._task_action(automation_level, source, docs),
            }

            if automation_level == "fully_automated" and matched_modules:
                task["priority"] = 1
            elif automation_level == "check_then_ask":
                task["priority"] = 2
            elif automation_level == "needs_module":
                task["priority"] = 3
            elif automation_level == "email_skill":
                task["priority"] = 4
            else:
                task["priority"] = 5

            tasks.append(task)

        tasks.sort(key=lambda t: t["priority"])
        return tasks

    def _task_action(self, level, source, docs):
        """Generate a human-readable action description."""
        doc_types = ", ".join(set(d.get("docType", "") for d in docs))
        if level == "fully_automated":
            return f"Login, navigate, download: {doc_types}"
        elif level == "user_provides":
            return f"Ask user to upload: {doc_types}"
        elif level == "email_skill":
            return f"Search Gmail for: {doc_types}"
        elif level == "check_then_ask":
            return f"Check portal availability, ask user if not found: {doc_types}"
        elif level == "needs_module":
            return f"No navigation module — build one or ask user: {doc_types}"
        return f"Manual: {doc_types}"

    @staticmethod
    def _count_by(field, docs):
        counts = {}
        for d in docs:
            val = d.get(field, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts

    def save(self, path=None):
        """Save the processed registry to a JSON file."""
        path = path or kb_path("final-registry-2025.json")
        registry = self.get_registry()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(registry, f, indent=2)
        print(f"Saved registry ({registry['total']} docs) to {path}")
        return path

    def save_tasks(self, path=None):
        """Save the portal task list to a JSON file."""
        path = path or os.path.join(project_dir(), "agents", "chitra", "data", "portal-tasks.json")
        tasks = self.generate_portal_tasks()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(tasks, f, indent=2)
        print(f"Saved {len(tasks)} portal tasks to {path}")
        return path


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Process questionnaire answers into a final registry + portal tasks.",
    )
    parser.add_argument(
        "--registry", default=None,
        help="Path to derived registry JSON",
    )
    parser.add_argument(
        "--answers", default=None,
        help="Path to answers JSON file (batch mode)",
    )
    parser.add_argument(
        "--show-tasks", action="store_true",
        help="Show portal task list",
    )
    args = parser.parse_args()

    processor = AnswerProcessor(args.registry)
    print(f"Loaded {len(processor.docs)} documents from derived registry\n")

    if args.answers:
        answers_data = json.loads(open(args.answers).read())
        for ans in answers_data.get("confirmations", []):
            processor.apply_confirmation(
                ans["doc_id"], ans["confirmed"], ans.get("details"),
            )
        for event in answers_data.get("life_events", []):
            new_docs = processor.add_from_life_event(
                event["type"], event.get("details", {}),
            )
            print(f"  Life event '{event['type']}' → {len(new_docs)} new documents")

    registry = processor.get_registry()
    print(f"Final registry: {registry['total']} documents")
    print(f"  By status: {registry['by_status']}")
    print(f"  By source: {registry['by_source']}")

    if registry["changes_applied"]:
        print(f"\nChanges applied:")
        for c in registry["changes_applied"]:
            print(f"  • {c}")

    if args.show_tasks:
        tasks = processor.generate_portal_tasks()
        print(f"\n{'='*60}")
        print(f"Portal Task List ({len(tasks)} tasks):\n")
        for t in tasks:
            level = t["automation_level"]
            badge = {
                "fully_automated": "AUTO",
                "check_then_ask": "CHECK",
                "needs_module": "BUILD",
                "email_skill": "EMAIL",
                "user_provides": "USER",
            }.get(level, level.upper())
            modules = ", ".join(t["portal_modules"]) if t["portal_modules"] else "—"
            print(f"  [{badge:5s}] {t['source']:30s}  {t['doc_count']} docs  modules: {modules}")
            print(f"          → {t['action']}")
            for doc in t["documents"]:
                print(f"            - {doc['type']} [{doc['issuer']}]")
            print()


if __name__ == "__main__":
    main()
