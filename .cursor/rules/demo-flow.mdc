# Demo Flow — Autonomous Tax Document Collection

When the user sends ANY message in Cursor (even just "go"), this playbook activates. The user's REAL instructions come via Slack — check Slack first. **Do not ask the user what to do at any point.** Every decision is encoded here. All communication happens on Slack. Cursor is just the engine.

## CRITICAL: Slack-First Architecture

The user will NOT type instructions in Cursor. They communicate via Slack only. The one Cursor message is just to start the engine. Immediately:
1. Check `/tmp/jarvis-pending-actions.json` for queued Slack messages
2. Check Slack DM directly using `check_for_user_messages()` for recent messages
3. If the user sent anything on Slack (e.g., "help me collect my taxes"), acknowledge it and begin
4. If no Slack message yet, send the greeting proactively

## CRITICAL: Never End Your Turn

During the entire demo, NEVER let your turn end. Always have a next action:
- Between portal automations: poll Slack, check for messages, send progress
- While waiting for MFA: sleep 15s, poll Slack, repeat
- While waiting for questionnaire answers: sleep 15s, poll Slack, repeat
- Between phases: update PROGRESS.md, check Slack, proceed
- If there's literally nothing to do: sleep 30s, poll Slack, check Drive inbox

The AI turn stays alive as long as you keep making tool calls. A tool call every 15-30 seconds is enough. NEVER go more than 60 seconds without a tool call.

If you sense the turn might end, immediately make another tool call (even just reading a file or checking Slack).

## Pre-flight Checks

1. Verify Slack connection: `python -c "from skills.slack.adapter import test_connection; print(test_connection())"`
2. Verify Google Drive token: `python -c "from core.config_loader import refresh_access_token; print('OK:', refresh_access_token()[:20])"`
3. Start the inbox processor if not running: check `/tmp/jarvis-inbox-processor.pid`, start if dead. Start with `--hours 8 --interval 30` for fast polling during demo.
4. If any check fails, fix it before proceeding. Do not ask the user.

## Phase 1: Greet + Create Inbox (2 min)

1. Check Slack DM for any recent user message. If the user already sent something (e.g., "help me"), reply to THAT message acknowledging it.

2. Send Slack DM (or reply to their message):
   ```
   Hey! I'm CHITRA, your AI tax assistant. I'll collect all your 2025 tax documents automatically.

   To get started, I need ONE thing from you:
   - Your 2024 federal tax return PDF

   I'm creating a "Jarvis Inbox" folder in your Google Drive now. Upload the PDF there and I'll take it from here.
   ```

2. Create "Jarvis Inbox" folder in Drive under Taxes root:
   ```python
   from core.config_loader import refresh_access_token, get_drive_id
   from skills.google_drive.create_folder import create_folder
   token = refresh_access_token()
   taxes_root = get_drive_id("taxes_root_id")
   result = create_folder(token, "Jarvis Inbox", taxes_root)
   inbox_id = result["id"]
   ```

3. Send Drive folder link on Slack:
   ```
   Your inbox folder is ready: https://drive.google.com/drive/folders/{inbox_id}
   Drop your 2024 tax return PDF in there. I'll start as soon as I see it.
   ```

4. Poll the inbox folder every 15 seconds until a PDF appears:
   ```python
   from skills.google_drive.list_folder import inventory_folder
   items = inventory_folder(token, inbox_id)
   pdfs = [i for i in items if i["name"].lower().endswith(".pdf")]
   ```

5. When PDF found, send Slack: "Got your return! Parsing now..."

## Phase 2: Parse Return (3 min)

1. Download the PDF from Drive to `extracted/`:
   ```python
   from skills.google_drive.upload import download_file  # or urllib
   # Download using Drive API: GET https://www.googleapis.com/drive/v3/files/{file_id}?alt=media
   ```
   If download helper doesn't exist, use urllib with the access token:
   ```python
   import urllib.request
   req = urllib.request.Request(
       f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
       headers={"Authorization": f"Bearer {token}"}
   )
   with urllib.request.urlopen(req) as resp:
       with open("extracted/2024-federal-return.pdf", "wb") as f:
           f.write(resp.read())
   ```

2. Extract text using pdfplumber:
   ```python
   from skills.pdf.extract import extract_text
   text = extract_text("extracted/2024-federal-return.pdf", "extracted/2024-federal-return.txt")
   ```

3. Parse the extracted text into a structured profile JSON. Read the text file and the schema at `agents/chitra/knowledge-base/schema/return-profile.schema.md`, then produce `agents/chitra/knowledge-base/profile-2024.json` following the schema exactly. Extract EVERY issuer, entity, amount, and form from the return.

   **If profile-2024.json already exists**, skip parsing and use the existing file.

4. Derive the initial document registry:
   ```python
   from agents.chitra.scripts.derive_registry_from_return import derive_documents, derive_folder_tree
   import json
   profile = json.load(open("agents/chitra/knowledge-base/profile-2024.json"))
   docs = derive_documents(profile, 2025)
   folders = derive_folder_tree(docs, 2025)
   ```
   Save as `agents/chitra/knowledge-base/derived-registry-2025.json`.

5. Send Slack progress:
   ```
   Parsed your 2024 return. Found {len(docs)} expected documents across {len(set(d['category'] for d in docs))} categories.

   Now I have some questions to make sure nothing changed for 2025. I'll send them in batches by topic.
   ```

## Phase 3: Questionnaire on Slack (10-15 min)

1. Generate and send questions using the Slack questionnaire module:
   ```python
   from skills.slack.questionnaire import run_questionnaire
   answers = run_questionnaire("agents/chitra/knowledge-base/profile-2024.json")
   ```
   This sends questions in category batches, waits for replies, parses answers.

2. After collecting all answers, process them:
   - Map "yes" answers to confirmed documents
   - Map "no" answers to removed/changed documents
   - Map life event answers (new home, new employer, etc.) to additional documents
   - Use the existing `user-answers-2025.json` as a reference for the expected answers

3. Derive the final registry with all life events applied:
   ```python
   from agents.chitra.scripts.process_answers import AnswerProcessor
   processor = AnswerProcessor("agents/chitra/knowledge-base/derived-registry-2025.json")
   # Apply confirmations and life events from the answers
   ```
   Save as `agents/chitra/knowledge-base/final-registry-2025.json`.

4. Derive the final folder tree from the final registry.

5. Send Slack progress:
   ```
   All answers processed! Here's what I'm collecting:

   - {N} documents expected
   - {M} folders in your Drive
   - {P} portals I can download from automatically
   - {Q} documents you'll need to provide

   Creating your Drive folder structure now...
   ```

## Phase 4: Create Drive Folders (1 min)

1. Create shadow folder with a unique run name:
   ```bash
   python agents/chitra/scripts/create_shadow_folders.py \
     --registry agents/chitra/knowledge-base/final-registry-2025.json \
     --shadow-name 2025-test-run2
   ```

2. Verify folder IDs saved to `extracted/drive-2025-test-run2-folder-ids.json`.

3. Send Slack:
   ```
   Drive folders ready! Created {N} folders.
   https://drive.google.com/drive/folders/{root_id}

   Checking credentials for your portals...
   ```

## Phase 5: Credential Check (30 sec)

1. Check macOS Keychain for all portal credentials:
   ```bash
   for svc in jarvis-schwab jarvis-etrade jarvis-wellsfargo jarvis-robinhood jarvis-chase jarvis-homebase jarvis-ziprent jarvis-investorcafe-bcgk jarvis-mhcapital jarvis-justappraised jarvis-obie; do
     security find-generic-password -s "$svc" -w >/dev/null 2>&1 && echo "OK: $svc" || echo "MISSING: $svc"
   done
   ```

2. Send Slack:
   ```
   Credentials ready for all 14 portals. Starting document collection now!

   I'll message you here when I need an MFA code or approval. Otherwise, just watch the progress updates roll in.
   ```

## Phase 6: Portal Automation (45-55 min)

Run portals in this exact order. For each portal, follow its config in `agents/chitra/scripts/portals/{name}.py`.

**Use `cursor-ide-browser` by default. Use `user-playwright` ONLY for Just Appraised (Auth0 fails in Electron).**

### Execution loop for each portal:

1. Read the portal config: `agents/chitra/scripts/portals/{portal}.py`
2. Get credentials: `security find-generic-password -s {keychain_service} -w` (password) and `-a` flag output for username
3. Navigate to portal URL using browser tools
4. Login using the documented flow from the portal config
5. If MFA required:
   - Send Slack: "Need your help! {portal} is asking for {mfa_type}. Reply here with the code."
   - For app push: "Please approve the push notification on your phone for {portal}."
   - Wait for reply (indefinitely — never timeout)
6. Navigate to tax documents section per portal config
7. Download document(s) using the appropriate method from `download-strategies.md`
8. Upload to the correct Drive subfolder using folder IDs from `extracted/drive-2025-test-run2-folder-ids.json`
9. Run validation: `python agents/chitra/scripts/validate_upload.py`
10. Send Slack progress:
    ```
    Portal {N}/14 done: {portal_name}
    Downloaded: {doc_names}
    Overall: {matched}/{total} files matched ({pct}%)
    ```
11. Update PROGRESS.md with the portal status

### Portal order:

**Group 1 — Public (no login):**
1. `county_property_tax` — Fort Bend County tax statement + receipt
2. `county_property_tax` — FBCAD appraisal notices (esearch.fbcad.org)
3. `san_mateo_county` — San Mateo property tax bills (Cloudflare checkbox)

**Group 2 — Login only (no MFA):**
4. `schwab` — 1099 Composite
5. `ziprent` — 1099-MISC rental income
6. `invportal` — MH Capital K-1

**Group 3 — App push MFA (user approves on phone):**
7. `robinhood` — 1099 Consolidated
8. `chase` — 1098 Mortgage Interest

**Group 4 — Code-based MFA (user replies on Slack):**
9. `wells_fargo` — 1098 Mortgage Interest (email code)
10. `etrade` — 1099 Consolidated + Supplement (SMS code)
11. `homebase` — 941, 940, W-2, W-3 (SMS code)
12. `obie_insurance` — Policies + Declarations (email PIN)
13. `investorcafe` — BCGK K-1 + Distributions (email 2FA)

**Group 5 — Playwright-only:**
14. `just_appraised` — Form 50-114 Homestead (MUST use user-playwright, NOT cursor-ide-browser)

### Error recovery during portal automation:

- **Browser dies**: Kill Chrome processes with `browser-profile`, remove lock files, retry
- **Login fails**: Retry once. If still fails, skip portal, send Slack "Skipping {portal} — login failed. Will retry later."
- **MFA timeout**: Never timeout. Keep waiting. Send reminder on Slack every 3 minutes.
- **Download fails**: Try fetch-based download (method 4 from download-strategies.md). If still fails, skip and note in PROGRESS.md.
- **Upload fails**: Retry with fresh token. If still fails, save file locally and note.

## Phase 7: Final Report

After all 14 portals complete:

1. Run full validation: `python agents/chitra/scripts/validate_upload.py --slack`

2. Send Slack final summary:
   ```
   Tax document collection complete!

   Results:
   - Portals visited: 14/14
   - Documents uploaded: {N}
   - Files matched vs benchmark: {matched}/{total} ({pct}%)

   8 remaining files need your help:
   - 2025 W-2 - DoorDash (upload from DoorDash Workday)
   - 2025 W-2 - Texas Childrens Hospital (Kajri uploads)
   - 2025 Student Loan Tax Info (Kajri uploads)
   - ISO Disposition Survey (Google Sheet, deferred)
   - Rastegar K-1 (expected Aug 2026)
   - Brisbane bank transactions (export from bank)
   - Donum Charitable Lending Note (you provide)
   - Palmetto Business Transactions (export from Copilot)

   Your Drive folder: https://drive.google.com/drive/folders/{root_id}
   ```

3. Update PROGRESS.md with final demo results.

## Slack Polling Pattern (use throughout)

Whenever waiting for user input (questionnaire answers, MFA codes, file uploads), use this pattern:

```python
# Poll Slack every 15 seconds while waiting
from skills.slack.adapter import check_for_user_messages, read_replies
import time

last_check_ts = None
while True:
    time.sleep(15)  # keeps the turn alive
    messages = check_for_user_messages(since_ts=last_check_ts)
    if messages:
        last_check_ts = messages[0]["ts"]
        # process the message
        break
    # Also check pending-actions from inbox processor
    # read /tmp/jarvis-pending-actions.json
```

NEVER use bare `time.sleep()` for more than 30 seconds. Always interleave with a tool call (read file, check Slack, etc.) to keep the Cursor turn alive.

## Recovery: If the Turn Ends

If for any reason the Cursor turn ends (timeout, error, user accidentally closed):
1. User types "continue" in Cursor (one word)
2. AI reads PROGRESS.md — it was updated after every portal
3. AI reads the demo-flow rule — picks up at the next unfinished phase
4. AI checks Slack for any messages sent while it was down
5. AI sends Slack: "I'm back! Resuming from portal {N}..."
6. Continues the flow from where it stopped

To minimize this risk: NEVER go more than 30 seconds without a tool call during the entire demo.

## Key Rules (never violate)

- **Never ask the user "what should I do next?"** — this playbook has every step.
- **Never end your turn** — always have a next tool call queued. Sleep + poll pattern.
- **Check Slack between every portal** — user may have sent instructions.
- **Update PROGRESS.md after every portal** — if Cursor dies, the next chat resumes seamlessly.
- **Never assume credentials** — always read from Keychain per portal config.
- **Never timeout on user input** — wait indefinitely for MFA codes (poll Slack every 15s).
- **Send progress on Slack after every portal** — the user is watching.
- **All communication on Slack** — never ask questions in Cursor. The user is watching Slack, not Cursor.
