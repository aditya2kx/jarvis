"""scripts/pin_git_identity.sh keeps commits and pushes on jarvis-agent-bot328 after Cursor
re-injects its own GitHub App token (global insteadOf, token in origin URL, gh active account)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = "jarvis-agent-bot328"
PUSH_URL = f"https://{BOT}@github.com/aditya2kx/jarvis"

FAKE_GH = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Two accounts like a Cloud Agent VM: `cursor` active, the bot logged in alongside.
    if [ "$1 $2" = "auth token" ]; then
      case " $* " in
        *" --user jarvis-agent-bot328 "*) [ -n "${FAKE_BOT_TOKEN:-}" ] && echo "$FAKE_BOT_TOKEN" && exit 0; exit 1 ;;
        *) echo cursor-app-token ;;
      esac
    fi
""")


class PinGitIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "repo")
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(bindir)
        for name in ("pin_git_identity.sh", "git-credential-jarvis-bot"):
            shutil.copy2(os.path.join(HERE, name), os.path.join(self.repo, "scripts", name))
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH)
        os.chmod(gh, 0o755)
        self.env = {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": self.tmp,
            "GIT_CONFIG_GLOBAL": os.path.join(self.tmp, "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "FAKE_BOT_TOKEN": "bot-token",
        }
        self.git("init", "-q")
        self.git("remote", "add", "origin", "https://github.com/aditya2kx/jarvis")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args, stdin=None, env=None):
        return subprocess.run(["git", *args], cwd=self.repo, env={**self.env, **(env or {})},
                              input=stdin, capture_output=True, text=True, check=True).stdout.strip()

    def pin(self):
        subprocess.run(["bash", "scripts/pin_git_identity.sh"], cwd=self.repo, env=self.env,
                       capture_output=True, check=True)

    def cursor_takes_over(self):
        """What Cursor does mid-session (docs/contributing/push-gotchas.md §2)."""
        self.git("config", "--global", "user.name", "Cursor Agent")
        self.git("config", "--global", "url.https://x-access-token:cursor-app-token@github.com/.insteadOf",
                 "https://github.com/")
        self.git("config", "--global", "--add", "credential.https://github.com.helper", "")
        self.git("config", "--global", "--add", "credential.https://github.com.helper", "!gh auth git-credential")
        self.git("remote", "set-url", "origin", "https://x-access-token:cursor-app-token@github.com/aditya2kx/jarvis")

    def push_credential(self, env=None):
        url = self.git("remote", "get-url", "--push", "origin")
        out = self.git("credential", "fill", stdin=f"url={url}\n\n", env=env)
        return url, dict(line.split("=", 1) for line in out.splitlines())

    def test_push_stays_on_bot_after_cursor_reinjects_its_token(self):
        self.pin()
        self.cursor_takes_over()
        url, cred = self.push_credential()
        self.assertEqual(url, PUSH_URL)
        self.assertEqual((cred["username"], cred["password"]), (BOT, "bot-token"))

    def test_commit_author_is_bot_despite_global_override(self):
        self.pin()
        self.cursor_takes_over()
        self.git("commit", "-q", "--allow-empty", "-m", "x")
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>"), f"{BOT} <aditya.2ky+jarvisbot@gmail.com>")

    def test_mac_falls_back_to_gh_token_env(self):
        self.pin()
        _, cred = self.push_credential(env={"FAKE_BOT_TOKEN": "", "GH_TOKEN": "keychain-bot-token"})
        self.assertEqual(cred["password"], "keychain-bot-token")

    def test_pin_is_idempotent(self):
        self.pin()
        self.pin()
        helpers = self.git("config", "--local", "-z", "--get-all", "credential.https://github.com.helper")
        helpers = helpers.rstrip("\0").split("\0")
        self.assertEqual(len(helpers), 2)
        self.assertEqual(helpers[0], "")
        self.assertIn("git-credential-jarvis-bot", helpers[1])


if __name__ == "__main__":
    unittest.main()
