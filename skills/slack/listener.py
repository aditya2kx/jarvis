#!/usr/bin/env python3
"""Slack Socket Mode listener for real-time message handling.

Connects via WebSocket so Slack pushes events instantly (no polling).
When an OTP reply arrives, writes it to a well-known file that the
portal automation can watch.

Prerequisites:
    - Socket Mode enabled in Slack app settings
    - App-Level Token (xapp-...) stored in Keychain:
      security add-generic-password -a SLACK_APP_TOKEN -s jarvis -w "xapp-..."
    - Bot scopes: same as adapter.py

Usage:
    # Start as background daemon
    python skills/slack/listener.py &

    # Or import and run programmatically
    from skills.slack.listener import start_listener
    start_listener()  # blocks forever
"""

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import struct
import hashlib
import ssl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

OTP_DIR = pathlib.Path("/tmp/jarvis-otp")
OTP_DIR.mkdir(exist_ok=True)

INBOX_FILE = pathlib.Path("/tmp/jarvis-slack-inbox.json")
COMMAND_LOG = pathlib.Path("/tmp/jarvis-slack-commands.json")

_app_token_cache = None


def _get_app_token():
    """Retrieve Slack App-Level Token (xapp-...) from macOS Keychain."""
    global _app_token_cache
    if _app_token_cache:
        return _app_token_cache
    cmd = "security find-generic-password -a SLACK_APP_TOKEN -s jarvis -w"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            _app_token_cache = result.stdout.strip()
            return _app_token_cache
        return None
    except subprocess.TimeoutExpired:
        return None


def _get_websocket_url(app_token):
    """Call apps.connections.open to get a WebSocket URL."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        "https://slack.com/api/apps.connections.open",
        data=b"",
        headers={
            "Authorization": f"Bearer {app_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return data["url"]
            raise RuntimeError(f"apps.connections.open failed: {data.get('error')}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"apps.connections.open HTTP {e.code}: {e.read().decode()}")


def _write_otp(portal_name, code, user_id):
    """Write an OTP code to a well-known file for the automation to pick up."""
    otp_file = OTP_DIR / f"{portal_name.lower().replace(' ', '-')}.json"
    otp_file.write_text(json.dumps({
        "code": code,
        "user_id": user_id,
        "portal": portal_name,
        "received_at": time.time(),
    }))
    print(f"[listener] OTP written: {otp_file}")


def read_otp(portal_name, timeout=300, poll_interval=1):
    """Read an OTP from the file the listener writes.

    This is the function portal automations call instead of polling Slack.
    Blocks until the file appears or timeout.

    Args:
        portal_name: Portal name (must match what was passed to request_otp_push)
        timeout: Max seconds to wait
        poll_interval: Seconds between file checks

    Returns:
        OTP code string, or None if timed out
    """
    otp_file = OTP_DIR / f"{portal_name.lower().replace(' ', '-')}.json"
    if otp_file.exists():
        otp_file.unlink()

    deadline = time.time() + timeout
    while time.time() < deadline:
        if otp_file.exists():
            try:
                data = json.loads(otp_file.read_text())
                otp_file.unlink()
                return data["code"]
            except (json.JSONDecodeError, KeyError):
                pass
        time.sleep(poll_interval)
    return None


def _is_otp_reply(text):
    """Check if a message looks like an OTP code (digits, possibly with spaces/dashes)."""
    cleaned = text.strip().replace(" ", "").replace("-", "")
    return cleaned.isdigit() and 4 <= len(cleaned) <= 10


def _find_pending_portal():
    """Find which portal is waiting for an OTP based on recent bot messages."""
    from skills.slack.adapter import _api_call, load_config

    cfg = load_config()
    dm_channel = cfg.get("slack", {}).get("dm_channel")
    if not dm_channel:
        return None

    result = _api_call("conversations.history", params={"channel": dm_channel, "limit": 5})
    if not result.get("ok"):
        return None

    for msg in result.get("messages", []):
        text = msg.get("text", "")
        if "OTP Required" in text and msg.get("bot_id"):
            for line in text.split("\n"):
                if "OTP Required" in line:
                    start = line.find("— ")
                    end = line.find("*", start + 2)
                    if start > -1 and end > -1:
                        return line[start + 2:end].strip()
    return None


class SocketModeClient:
    """Minimal Socket Mode client using stdlib only (no slack_sdk dependency)."""

    def __init__(self, app_token, on_message=None):
        self.app_token = app_token
        self.on_message = on_message or self._default_handler
        self._running = False

    def _default_handler(self, event_type, payload, envelope_id):
        print(f"[listener] Event: {event_type}")

    def _connect_websocket(self, url):
        """Connect to WebSocket URL using ssl + socket."""
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or 443
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query

        ctx = ssl.create_default_context()
        raw_sock = socket.create_connection((host, port), timeout=30)
        sock = ctx.wrap_socket(raw_sock, server_hostname=host)

        key = hashlib.sha1(os.urandom(16)).hexdigest()[:24]
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(handshake.encode())

        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)

        if b"101" not in response.split(b"\r\n")[0]:
            raise RuntimeError(f"WebSocket handshake failed: {response[:200]}")

        return sock

    def _read_frame(self, sock):
        """Read a single WebSocket frame, return (opcode, payload)."""
        header = self._recv_exact(sock, 2)
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", self._recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(sock, 8))[0]

        if masked:
            mask = self._recv_exact(sock, 4)
            data = self._recv_exact(sock, length)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        else:
            data = self._recv_exact(sock, length)

        return opcode, data

    def _recv_exact(self, sock, n):
        """Receive exactly n bytes."""
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WebSocket connection closed")
            buf += chunk
        return buf

    def _send_frame(self, sock, opcode, data):
        """Send a WebSocket frame (client frames must be masked)."""
        frame = bytearray()
        frame.append(0x80 | opcode)

        mask = os.urandom(4)
        if len(data) < 126:
            frame.append(0x80 | len(data))
        elif len(data) < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", len(data)))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", len(data)))

        frame.extend(mask)
        frame.extend(bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        sock.sendall(frame)

    def _send_ack(self, sock, envelope_id):
        """Acknowledge a Socket Mode envelope."""
        ack = json.dumps({"envelope_id": envelope_id}).encode()
        self._send_frame(sock, 0x1, ack)

    def start(self):
        """Connect and listen for events. Reconnects on failure."""
        self._running = True
        while self._running:
            try:
                ws_url = _get_websocket_url(self.app_token)
                print(f"[listener] Connecting to Socket Mode...")
                sock = self._connect_websocket(ws_url)
                print("[listener] Connected. Listening for events...")
                self._listen(sock)
            except Exception as e:
                print(f"[listener] Connection error: {e}. Reconnecting in 5s...")
                time.sleep(5)

    def _listen(self, sock):
        """Main event loop."""
        while self._running:
            try:
                opcode, data = self._read_frame(sock)

                if opcode == 0x1:  # text
                    envelope = json.loads(data)
                    envelope_id = envelope.get("envelope_id")
                    event_type = envelope.get("type")
                    payload = envelope.get("payload", {})

                    if envelope_id:
                        self._send_ack(sock, envelope_id)

                    self.on_message(event_type, payload, envelope_id)

                elif opcode == 0x9:  # ping
                    self._send_frame(sock, 0xA, data)  # pong

                elif opcode == 0x8:  # close
                    print("[listener] Server closed connection")
                    break

            except ConnectionError:
                break

    def stop(self):
        self._running = False


def _queue_message(text, user_id, ts):
    """Add a user message to the inbox file for the AI agent to process."""
    inbox = []
    if INBOX_FILE.exists():
        try:
            inbox = json.loads(INBOX_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            inbox = []

    inbox.append({"text": text, "user": user_id, "ts": ts, "read": False})

    # Keep last 50 messages
    if len(inbox) > 50:
        inbox = inbox[-50:]

    INBOX_FILE.write_text(json.dumps(inbox, indent=2))


def _log_command(command, response, user_id):
    """Log a handled command for debugging."""
    log = []
    if COMMAND_LOG.exists():
        try:
            log = json.loads(COMMAND_LOG.read_text())
        except (json.JSONDecodeError, OSError):
            log = []

    log.append({
        "command": command,
        "response": response,
        "user": user_id,
        "ts": time.time(),
    })

    if len(log) > 100:
        log = log[-100:]
    COMMAND_LOG.write_text(json.dumps(log, indent=2))


def _get_dm_channel():
    """Get the DM channel from config."""
    try:
        cfg_module = __import__("core.config_loader", fromlist=["load_config"])
        cfg = cfg_module.load_config()
        return cfg.get("slack", {}).get("dm_channel")
    except Exception:
        return None


def _reply(text):
    """Send a reply to the user's DM."""
    try:
        from skills.slack.adapter import send_message
        channel = _get_dm_channel()
        if channel:
            send_message(channel, text)
    except Exception as e:
        print(f"[listener] Reply failed: {e}")


def _handle_command(text, user_id):
    """Handle known commands and return True if handled, False if queued."""
    cmd = text.strip().lower()

    if cmd == "status":
        try:
            base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
            orch_state_path = os.path.join(base_dir, "extracted", "orchestrator-state.json")
            benchmark_path = os.path.join(base_dir, "extracted", "drive-2025-benchmark.json")
            shadow_inv_path = os.path.join(base_dir, "extracted", "drive-2025-test-current.json")
            registry_path = os.path.join(base_dir, "agents", "chitra",
                                         "knowledge-base", "derived-registry-2025.json")

            lines = [":bar_chart: *Progress Report*"]

            # Document counts from registry
            if os.path.exists(registry_path):
                with open(registry_path) as f:
                    reg = json.load(f)
                total_docs = len(reg.get("documents", []))
                total_folders = len(reg.get("driveFolderStructure", {}))
                lines.append(f"\n*Documents:* {total_docs} expected")
                lines.append(f"*Folders:* {total_folders} to create")

            # Benchmark file count (target)
            benchmark_files = 0
            if os.path.exists(benchmark_path):
                with open(benchmark_path) as f:
                    bm = json.load(f)
                benchmark_files = sum(1 for i in bm.get("items", []) if not i.get("isFolder"))
                benchmark_folders = sum(1 for i in bm.get("items", []) if i.get("isFolder"))
                lines.append(f"*Benchmark:* {benchmark_files} files in {benchmark_folders} folders")

            # Shadow (2025-test) progress
            if os.path.exists(shadow_inv_path):
                with open(shadow_inv_path) as f:
                    shadow = json.load(f)
                shadow_files = sum(1 for i in shadow.get("items", []) if not i.get("isFolder"))
                shadow_folders = sum(1 for i in shadow.get("items", []) if i.get("isFolder"))
                pct = round(shadow_files / benchmark_files * 100, 1) if benchmark_files else 0
                lines.append(f"\n*2025-test progress:* {shadow_files}/{benchmark_files} files ({pct}%)")
                lines.append(f"  Folders created: {shadow_folders}")

            # Task status from orchestrator
            if os.path.exists(orch_state_path):
                with open(orch_state_path) as f:
                    state = json.load(f)
                tasks = state.get("tasks", [])
                by_status = {}
                for t in tasks:
                    s = t["status"]
                    by_status.setdefault(s, []).append(t.get("portal", t["id"]))
                lines.append("\n*Portal tasks:*")
                for s in ["complete", "uploaded", "in_progress", "pending", "failed", "skipped"]:
                    if s in by_status:
                        lines.append(f"  {s}: {', '.join(by_status[s])}")

                vhist = state.get("validationHistory", [])
                if vhist:
                    last = vhist[-1].get("summary", {})
                    lines.append(f"\n*Last validation:*")
                    lines.append(f"  Missing: {last.get('missingFiles', '?')} files | "
                                 f"Extra: {last.get('extraFiles', '?')} files")
                    lines.append(f"  Missing folders: {last.get('missingFolders', '?')} | "
                                 f"Extra folders: {last.get('extraFolders', '?')}")
            elif not os.path.exists(registry_path):
                lines.append("\n:information_source: Pipeline not yet initialized.")

            _reply("\n".join(lines))
        except Exception as e:
            _reply(f":warning: Couldn't read status: {e}")
        _log_command(cmd, "status sent", user_id)
        return True

    if cmd in ("pause", "stop", "hold"):
        _reply(":pause_button: Pause requested. Jarvis will pause after the current action.")
        _queue_message("__CMD_PAUSE__", user_id, str(time.time()))
        _log_command(cmd, "pause queued", user_id)
        return True

    if cmd in ("resume", "continue", "go"):
        _reply(":arrow_forward: Resume requested. Jarvis will continue.")
        _queue_message("__CMD_RESUME__", user_id, str(time.time()))
        _log_command(cmd, "resume queued", user_id)
        return True

    if cmd == "help":
        _reply(":robot_face: *Jarvis Slack Commands*\n"
               "  `status` — current pipeline state\n"
               "  `pause` / `stop` — pause execution\n"
               "  `resume` / `continue` — resume execution\n"
               "  `help` — this message\n"
               "  Anything else → queued for Jarvis to read and act on")
        _log_command(cmd, "help sent", user_id)
        return True

    return False


def _handle_event(event_type, payload, envelope_id):
    """Process incoming Socket Mode events."""
    if event_type == "events_api":
        event = payload.get("event", {})
        if event.get("type") == "message" and event.get("channel_type") == "im":
            user_id = event.get("user")
            text = event.get("text", "")
            ts = event.get("ts", "")

            if event.get("bot_id") or event.get("subtype"):
                return

            print(f"[listener] DM from {user_id}: {text}")

            # OTP codes get priority handling
            if _is_otp_reply(text):
                portal = _find_pending_portal()
                if portal:
                    _write_otp(portal, text.strip(), user_id)
                    _reply(f":white_check_mark: Got it — using code `{text.strip()}` for {portal}")
                    return

            # Try known commands
            if _handle_command(text, user_id):
                return

            # Everything else → queue for the AI agent
            _queue_message(text, user_id, ts)
            _reply(f":inbox_tray: Got it — queued for Jarvis to process: _{text[:100]}_")

    elif event_type == "disconnect":
        print("[listener] Received disconnect event, will reconnect")


def read_inbox(mark_read=True):
    """Read all unread messages from the inbox file.

    Returns list of message dicts. If mark_read=True, marks them as read.
    """
    if not INBOX_FILE.exists():
        return []

    try:
        inbox = json.loads(INBOX_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    unread = [m for m in inbox if not m.get("read")]

    if mark_read and unread:
        for m in inbox:
            m["read"] = True
        INBOX_FILE.write_text(json.dumps(inbox, indent=2))

    return unread


def has_unread_messages():
    """Quick check if there are unread messages in the inbox."""
    if not INBOX_FILE.exists():
        return False
    try:
        inbox = json.loads(INBOX_FILE.read_text())
        return any(not m.get("read") for m in inbox)
    except (json.JSONDecodeError, OSError):
        return False


def is_socket_mode_available():
    """Check if Socket Mode is configured (app token exists in Keychain)."""
    return _get_app_token() is not None


def start_listener():
    """Start the Socket Mode listener. Blocks forever."""
    app_token = _get_app_token()
    if not app_token:
        print("[listener] No app-level token found in Keychain (SLACK_APP_TOKEN).")
        print("[listener] Store it: security add-generic-password -a SLACK_APP_TOKEN -s jarvis -w 'xapp-...'")
        sys.exit(1)

    client = SocketModeClient(app_token, on_message=_handle_event)
    print("[listener] Starting Slack Socket Mode listener...")
    print("[listener] OTP files will be written to /tmp/jarvis-otp/")
    client.start()


def start_listener_background():
    """Start the listener in a background thread. Non-blocking."""
    app_token = _get_app_token()
    if not app_token:
        return False

    client = SocketModeClient(app_token, on_message=_handle_event)
    thread = threading.Thread(target=client.start, daemon=True, name="slack-listener")
    thread.start()
    return True


if __name__ == "__main__":
    start_listener()
