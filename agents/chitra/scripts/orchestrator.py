#!/usr/bin/env python3
"""End-to-end autonomous tax document collection orchestrator.

Manages the full loop: portal login → download → upload → validate → report.
All state is persisted to `extracted/orchestrator-state.json` so any new chat
session can pick up exactly where the previous one left off.

Usage by AI agent:
    from agents.chitra.scripts.orchestrator import Orchestrator
    orch = Orchestrator()
    task = orch.next_task()        # get the next portal/action to execute
    orch.mark_downloaded(task_id, local_path)
    orch.upload_and_validate(task_id)
    orch.mark_complete(task_id)

Usage CLI:
    python orchestrator.py --status          # show current state
    python orchestrator.py --next            # show next task
    python orchestrator.py --validate        # run validation against benchmark
    python orchestrator.py --reset           # reset all tasks to pending
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
from core.config_loader import load_config, project_dir, refresh_access_token, get_drive_id

STATE_FILE = os.path.join(project_dir(), "extracted", "orchestrator-state.json")
BENCHMARK_FILE = os.path.join(project_dir(), "extracted", "drive-2025-benchmark.json")
SHADOW_INV_FILE = os.path.join(project_dir(), "extracted", "drive-2025-test-current.json")
SHADOW_FOLDER_IDS = os.path.join(project_dir(), "extracted", "drive-2025-test-folder-ids.json")
DERIVED_REGISTRY = os.path.join(project_dir(), "agents", "chitra", "knowledge-base", "derived-registry-2025.json")
FINAL_REGISTRY = os.path.join(project_dir(), "agents", "chitra", "knowledge-base", "final-registry-2025.json")


class Orchestrator:
    """Persistent state manager for autonomous tax document collection."""

    def __init__(self):
        self._config = load_config()
        self._benchmark = self._load_benchmark()
        self._registry = self._load_registry()
        self._state = self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
        return self._init_state()

    def _save_state(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self._state, f, indent=2)

    def _load_benchmark(self):
        if os.path.exists(BENCHMARK_FILE):
            with open(BENCHMARK_FILE) as f:
                return json.load(f)
        return {"items": []}

    def _load_registry(self):
        for path in (FINAL_REGISTRY, DERIVED_REGISTRY):
            if os.path.exists(path):
                with open(path) as f:
                    return json.load(f)
        return {"documents": []}

    def _get_shadow_folder_id(self):
        """Get the Drive folder ID for the 2025-test shadow folder."""
        if os.path.exists(SHADOW_FOLDER_IDS):
            with open(SHADOW_FOLDER_IDS) as f:
                data = json.load(f)
                return data.get("root_id")
        return self._config.get("google_drive", {}).get("shadow_folder_id")

    def _init_state(self):
        """Initialize state from the document registry and portal configs."""
        state = {
            "created": datetime.now(timezone.utc).isoformat(),
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "benchmarkCaptured": os.path.exists(BENCHMARK_FILE),
            "driveAuthValid": True,
            "tasks": [],
            "completedUploads": [],
            "validationHistory": [],
            "slackChannel": self._config.get("slack", {}).get("dm_channel", "D0AP8SKH0HZ"),
        }

        docs_by_portal = self._group_docs_by_portal()
        for portal_id, info in docs_by_portal.items():
            task = {
                "id": portal_id,
                "portal": info["portal_name"],
                "portalModule": info.get("module"),
                "status": "pending",
                "documents": info["documents"],
                "driveFolderId": info.get("folder_id"),
                "driveFolderPath": info.get("folder_path"),
                "downloadedFiles": [],
                "uploadedFiles": [],
                "lastError": None,
                "attempts": 0,
            }
            state["tasks"].append(task)

        self._state = state
        self._save_state()
        return state

    def _group_docs_by_portal(self):
        """Group registry documents by their source portal for task planning."""
        from agents.chitra.scripts.run_portal_tasks import TaskRunner
        runner = TaskRunner()

        groups = {}
        folder_map = self._registry.get("driveFolderStructure", {})

        for doc in self._registry.get("documents", []):
            if doc.get("status") in ("received", "n/a"):
                continue

            issuer = doc.get("issuer", "")
            module = runner.resolve_portal(issuer)
            if not module:
                continue

            drive_path = doc.get("drivePath", "")
            folder_key = drive_path.rstrip("/").replace("Taxes/2025/", "")
            folder_id = folder_map.get(folder_key)

            if module not in groups:
                from agents.chitra.scripts.portals.base import load_portal
                try:
                    config = load_portal(module)
                    portal_name = config.get("name", module)
                except Exception:
                    portal_name = module
                groups[module] = {
                    "portal_name": portal_name,
                    "module": module,
                    "documents": [],
                    "folder_id": folder_id,
                    "folder_path": drive_path,
                }

            groups[module]["documents"].append({
                "registryId": doc.get("id"),
                "docType": doc.get("docType"),
                "issuer": issuer,
                "drivePath": drive_path,
                "folderId": folder_id,
            })

        already_received = {}
        for doc in self._registry.get("documents", []):
            if doc.get("status") == "received":
                issuer = doc.get("issuer", "")
                module = runner.resolve_portal(issuer)
                if not module:
                    continue
                drive_path = doc.get("drivePath", "")
                folder_key = drive_path.rstrip("/").replace("Taxes/2025/", "")
                folder_id = folder_map.get(folder_key)

                if module not in already_received:
                    from agents.chitra.scripts.portals.base import load_portal
                    try:
                        config = load_portal(module)
                        portal_name = config.get("name", module)
                    except Exception:
                        portal_name = module
                    already_received[module] = {
                        "portal_name": portal_name,
                        "module": module,
                        "documents": [],
                        "folder_id": folder_id,
                        "folder_path": drive_path,
                    }
                already_received[module]["documents"].append({
                    "registryId": doc.get("id"),
                    "docType": doc.get("docType"),
                    "status": "received",
                })

        for module, info in already_received.items():
            if module not in groups:
                groups[module] = info
                groups[module]["documents"] = [{
                    **d,
                    "note": "already received — verify still in Drive"
                } for d in info["documents"]]

        return groups

    # ── Task Management ─────────────────────────────────────────────

    def next_task(self):
        """Get the next pending task to execute.

        Returns:
            Task dict or None if all tasks are complete/blocked
        """
        for task in self._state["tasks"]:
            if task["status"] == "pending":
                return task
            if task["status"] == "failed" and task["attempts"] < 3:
                return task
        return None

    def get_task(self, task_id):
        for task in self._state["tasks"]:
            if task["id"] == task_id:
                return task
        return None

    def update_task(self, task_id, **updates):
        """Update a task's fields and persist state."""
        task = self.get_task(task_id)
        if task:
            task.update(updates)
            self._state["lastUpdated"] = datetime.now(timezone.utc).isoformat()
            self._save_state()

    def mark_in_progress(self, task_id):
        self.update_task(task_id, status="in_progress", attempts=self.get_task(task_id)["attempts"] + 1)

    def mark_downloaded(self, task_id, local_paths):
        """Record downloaded files for a task."""
        if isinstance(local_paths, str):
            local_paths = [local_paths]
        self.update_task(task_id, downloadedFiles=local_paths, status="downloaded")

    def mark_uploaded(self, task_id, upload_results):
        """Record uploaded files for a task."""
        self.update_task(task_id, uploadedFiles=upload_results, status="uploaded")

    def mark_complete(self, task_id):
        self.update_task(task_id, status="complete")

    def mark_failed(self, task_id, error):
        self.update_task(task_id, status="failed", lastError=str(error))

    def mark_skipped(self, task_id, reason):
        self.update_task(task_id, status="skipped", lastError=reason)

    # ── Validation ──────────────────────────────────────────────────

    def validate_against_benchmark(self):
        """Re-inventory the 2025-test shadow folder and compare against the
        sealed 2025 benchmark.

        HARD LESSON: This MUST inventory 2025-test (shadow), NOT 2025
        (benchmark). Comparing 2025 against itself always returns 0 diffs.
        """
        shadow_id = self._get_shadow_folder_id()
        if not shadow_id:
            return {"error": "No shadow folder ID found. Run create_shadow_folders.py first."}

        benchmark_id = None
        if os.path.exists(BENCHMARK_FILE):
            with open(BENCHMARK_FILE) as f:
                benchmark_id = json.load(f).get("folder_id")

        if shadow_id == benchmark_id:
            return {"error": f"SAFETY: shadow_folder_id ({shadow_id}) == benchmark_folder_id. "
                    "This would compare the folder against itself. Fix config."}

        from skills.google_drive.list_folder import inventory_folder, save_inventory
        token = refresh_access_token()
        shadow_items = inventory_folder(token, shadow_id)
        save_inventory(SHADOW_INV_FILE, shadow_id, shadow_items)

        from agents.chitra.scripts.drive_shadow_diff import load_inventory, compare_inventories
        benchmark = load_inventory(BENCHMARK_FILE)
        shadow = {"items": shadow_items}
        report = compare_inventories(benchmark.get("items", []), shadow.get("items", []))

        validation_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "shadowFolderId": shadow_id,
            "summary": report["summary"],
        }
        self._state["validationHistory"].append(validation_entry)
        self._save_state()

        return report

    def validate_single_folder(self, folder_path):
        """Check if specific benchmark files exist in the shadow (2025-test) inventory.

        Args:
            folder_path: The folder path prefix to check (e.g. "02 - Brokerage 1099s/Schwab")

        Returns:
            dict with matched/missing file lists
        """
        benchmark_files = [
            item for item in self._benchmark.get("items", [])
            if not item["isFolder"] and item["path"].startswith(folder_path)
        ]

        if not os.path.exists(SHADOW_INV_FILE):
            return {"error": "No shadow inventory. Run validate_against_benchmark() first."}

        with open(SHADOW_INV_FILE) as f:
            shadow = json.load(f)

        shadow_files = {
            item["path"] for item in shadow.get("items", [])
            if not item["isFolder"]
        }

        matched = [f for f in benchmark_files if f["path"] in shadow_files]
        missing = [f for f in benchmark_files if f["path"] not in shadow_files]

        return {
            "folder": folder_path,
            "benchmarkFiles": len(benchmark_files),
            "matched": len(matched),
            "missing": len(missing),
            "missingFiles": [f["name"] for f in missing],
            "matchedFiles": [f["name"] for f in matched],
        }

    # ── Upload + Validate combo ─────────────────────────────────────

    def resolve_folder_id(self, drive_path):
        """Resolve a drivePath like 'Taxes/2025/02 - Brokerage 1099s/Schwab/'
        to the corresponding folder ID in the shadow folder.
        """
        relative = drive_path.rstrip("/")
        for prefix in ("Taxes/2025/", "Taxes/2025-test/"):
            if relative.startswith(prefix):
                relative = relative[len(prefix):]

        if os.path.exists(SHADOW_FOLDER_IDS):
            with open(SHADOW_FOLDER_IDS) as f:
                mapping = json.load(f)
            fid = mapping.get("folders", {}).get(relative)
            if fid:
                return fid

        return None

    def upload_and_validate(self, task_id, local_paths, folder_id=None, drive_names=None):
        """Upload files to 2025-test and immediately validate against 2025 benchmark.

        HARD LESSON: Uploads go to 2025-test (shadow). Validation compares
        2025-test against the sealed 2025 benchmark. Never upload to 2025.
        """
        from skills.google_drive.upload import upload_file

        task = self.get_task(task_id)
        if not folder_id and task:
            folder_id = self.resolve_folder_id(task.get("driveFolderPath", ""))
        if not folder_id:
            return {"error": "No folder ID for upload. Run create_shadow_folders.py first."}

        token = refresh_access_token()
        upload_results = []

        for i, path in enumerate(local_paths if isinstance(local_paths, list) else [local_paths]):
            name = drive_names[i] if drive_names and i < len(drive_names) else None
            try:
                result = upload_file(token, path, folder_id, drive_name=name)
                upload_results.append({"file": os.path.basename(path), "driveId": result["id"], "status": "ok"})
            except Exception as e:
                upload_results.append({"file": os.path.basename(path), "error": str(e), "status": "failed"})

        self.mark_uploaded(task_id, upload_results)

        folder_path = task.get("driveFolderPath", "").replace("Taxes/2025/", "").rstrip("/") if task else ""
        validation = self.validate_against_benchmark()

        return {
            "uploads": upload_results,
            "validation": validation.get("summary", {}),
            "folderCheck": self.validate_single_folder(folder_path) if folder_path else None,
        }

    # ── Full Pipeline ──────────────────────────────────────────────

    def ensure_listener(self):
        """Ensure the Slack Socket Mode listener is running in background."""
        try:
            from skills.slack.listener import is_socket_mode_available, start_listener_background, has_unread_messages
            if not is_socket_mode_available():
                print("[orch] Socket Mode not available — Slack commands won't auto-respond")
                return False

            # Check if listener is already running by looking for recent inbox activity
            # If not, start it in a background thread
            start_listener_background()
            print("[orch] Slack listener started in background thread")
            return True
        except Exception as e:
            print(f"[orch] Failed to start listener: {e}")
            return False

    def run_pipeline(self, skip_folder_creation=False):
        """Run the full derive -> create folders -> prepare tasks pipeline.

        Steps:
          1. Load derived registry (must already exist from onboard_from_return.py)
          2. Create shadow folders in Drive (2025-test) from derived registry
          3. Initialize orchestrator state from registry + folder IDs
          4. Report readiness on Slack

        Does NOT execute portal tasks — that's the AI agent's job using
        next_task() / mark_downloaded() / upload_and_validate() / mark_complete().
        """
        import subprocess

        registry = self._load_registry()
        doc_count = len(registry.get("documents", []))
        folder_count = len(registry.get("driveFolderStructure", {}))
        print(f"Registry: {doc_count} documents, {folder_count} folder paths")

        if not skip_folder_creation:
            print("\nCreating shadow folders in Drive...")
            result = subprocess.run(
                [sys.executable, os.path.join(
                    project_dir(), "agents", "chitra", "scripts", "create_shadow_folders.py"),
                 "--registry", DERIVED_REGISTRY if os.path.exists(DERIVED_REGISTRY) else FINAL_REGISTRY,
                ],
                capture_output=True, text=True, timeout=120,
            )
            print(result.stdout)
            if result.returncode != 0:
                print(f"Error creating folders: {result.stderr}")
                return {"error": result.stderr}

        print("\nInitializing orchestrator state...")
        self._registry = registry
        self._state = self._init_state()

        task_count = len(self._state["tasks"])
        pending = [t for t in self._state["tasks"] if t["status"] == "pending"]
        print(f"Tasks: {task_count} total, {len(pending)} pending")

        self.notify(
            f":rocket: *Pipeline initialized*\n"
            f"Documents: {doc_count} | Folders: {folder_count} | "
            f"Tasks: {len(pending)} pending\n"
            f"Ready for autonomous collection."
        )

        return {
            "documents": doc_count,
            "folders": folder_count,
            "tasks": task_count,
            "pending": len(pending),
        }

    # ── Slack Bidirectional Communication ─────────────────────────

    def notify(self, message):
        """Send a progress message to Slack."""
        try:
            from skills.slack.adapter import send_progress
            send_progress(message)
        except Exception as e:
            print(f"[orch] Slack notify failed: {e}")

    def check_user_input(self):
        """Check the Slack listener inbox for new user messages.

        The background listener (skills/slack/listener.py) handles commands
        like status/pause/resume automatically. This reads everything else
        that was queued for the AI agent.

        Returns list of unread message dicts.
        """
        try:
            from skills.slack.listener import read_inbox
            return read_inbox(mark_read=True)
        except Exception as e:
            print(f"[orch] Inbox read failed: {e}")
            return []

    def ask_user_slack(self, question):
        """Ask the user a question via Slack and wait indefinitely for reply."""
        try:
            from skills.slack.adapter import ask_user
            return ask_user(question)
        except Exception as e:
            print(f"[orch] Slack ask failed: {e}")
            return None

    def process_user_commands(self):
        """Check inbox for pause/resume commands and user input.

        The background listener handles status/help auto-responses.
        This checks for __CMD_PAUSE__ / __CMD_RESUME__ sentinel messages
        that the listener queues when it receives pause/resume commands.

        Returns:
            "pause" if user requested pause, "continue" otherwise
        """
        messages = self.check_user_input()
        result = "continue"
        for msg in messages:
            text = msg.get("text", "").strip()
            if text == "__CMD_PAUSE__":
                result = "pause"
            elif text == "__CMD_RESUME__":
                result = "continue"
            else:
                self._state.setdefault("userInputQueue", []).append({
                    "text": text,
                    "ts": msg.get("ts"),
                })
                self._save_state()
        return result

    def get_queued_input(self):
        """Pop the next queued user input from Slack."""
        queue = self._state.get("userInputQueue", [])
        if queue:
            item = queue.pop(0)
            self._save_state()
            return item.get("text")
        return None

    def notify_task_progress(self, task_id, action, details=""):
        """Send a structured task progress update."""
        task = self.get_task(task_id)
        if not task:
            return
        portal = task.get("portal", task_id)
        msg = f":gear: *{portal}* — {action}"
        if details:
            msg += f"\n{details}"
        self.notify(msg)

    def notify_validation(self, report):
        """Send a validation summary to Slack."""
        s = report.get("summary", {})
        missing = report.get("missingFiles", [])

        msg = (
            f":mag: *Validation Report*\n"
            f"Benchmark: {s.get('benchmarkFiles', '?')} files | "
            f"Current: {s.get('shadowFiles', '?')} files\n"
            f"Missing: {s.get('missingFiles', '?')} | Extra: {s.get('extraFiles', '?')}"
        )
        if missing[:5]:
            msg += "\n*Sample missing:*\n" + "\n".join(f"  • {f}" for f in missing[:5])
        self.notify(msg)

    # ── Status & Summary ────────────────────────────────────────────

    def status_summary(self):
        """Generate a human-readable status summary."""
        tasks = self._state.get("tasks", [])
        by_status = {}
        for t in tasks:
            s = t["status"]
            by_status.setdefault(s, []).append(t["portal"])

        lines = [f"=== Orchestrator Status ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ==="]
        for status in ["complete", "uploaded", "downloaded", "in_progress", "pending", "failed", "skipped"]:
            portals = by_status.get(status, [])
            if portals:
                lines.append(f"  {status:15s}: {', '.join(portals)}")

        validations = self._state.get("validationHistory", [])
        if validations:
            last = validations[-1]
            lines.append(f"\n  Last validation: {last['timestamp']}")
            lines.append(f"    Missing files: {last['summary'].get('missingFiles', '?')}")
            lines.append(f"    Extra files:   {last['summary'].get('extraFiles', '?')}")

        return "\n".join(lines)

    def pending_portals(self):
        """List portal modules that still need work."""
        return [
            t["portalModule"] for t in self._state.get("tasks", [])
            if t["status"] in ("pending", "failed") and t.get("portalModule")
        ]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tax document collection orchestrator")
    parser.add_argument("--status", action="store_true", help="Show current orchestrator state")
    parser.add_argument("--next", action="store_true", help="Show the next task to execute")
    parser.add_argument("--validate", action="store_true", help="Run Drive validation against benchmark")
    parser.add_argument("--reset", action="store_true", help="Reset all tasks to pending")
    parser.add_argument("--init", action="store_true", help="(Re)initialize state from registry")
    parser.add_argument("--pipeline", action="store_true",
                        help="Run full pipeline: derive -> create folders -> init tasks")
    parser.add_argument("--skip-folders", action="store_true",
                        help="With --pipeline, skip Drive folder creation (folders already exist)")
    args = parser.parse_args()

    orch = Orchestrator()

    if args.pipeline:
        result = orch.run_pipeline(skip_folder_creation=args.skip_folders)
        if "error" in result:
            print(f"Pipeline failed: {result['error']}")
        else:
            print(f"\nPipeline complete: {result['pending']} tasks ready for execution.")
            print(orch.status_summary())
    elif args.init:
        orch._state = orch._init_state()
        print("State initialized.")
        print(orch.status_summary())
    elif args.status:
        print(orch.status_summary())
    elif args.next:
        task = orch.next_task()
        if task:
            print(json.dumps(task, indent=2))
        else:
            print("All tasks complete or blocked.")
    elif args.validate:
        report = orch.validate_against_benchmark()
        if "error" in report:
            print(f"Error: {report['error']}")
        else:
            from agents.chitra.scripts.drive_shadow_diff import print_summary
            print_summary(report)
    elif args.reset:
        for task in orch._state.get("tasks", []):
            task["status"] = "pending"
            task["attempts"] = 0
            task["lastError"] = None
        orch._save_state()
        print("All tasks reset to pending.")
    else:
        print(orch.status_summary())


if __name__ == "__main__":
    main()
