#!/usr/bin/env python3
"""Pre-tool hook shared by every coding agent that supports one (AGENTS.md, Harnesses).

AGENTS.md states the rules; this refuses the tool calls that would break them, so they hold
whichever harness is driving. It reads the hook payload (JSON on stdin, the shape Devin and
Claude Code both send: tool_name, tool_input) and exits 2 with a reason to block the call.

Blocked:
  - reading or writing a secrets file (.env, .env.live, .env.tunnel, ...) with a file tool,
  - a shell command that would print one (cat .env, grep without -q, source .env, ...),
  - `docker compose ... config` without -q, which prints every secret substituted in,
  - anything aimed at the live stack (ENV=live, .env.live, the jap-live project), unless the
    harness was started with JAP_AGENT_ALLOW_LIVE=1.

It is a backstop, not a sandbox: a determined command can get round a pattern. `make reset`
and `make clean` are not handled here; they ask for confirmation themselves and the harness
configs mark them as always-ask.

  agent-guard.py              read one payload from stdin (the hook)
  agent-guard.py --self-test  run the built-in cases (make test-agents, CI)
"""

from __future__ import annotations

import json
import os
import re
import sys

EXAMPLES = {".env.example", ".env.tunnel.example"}
# A path whose last part is .env or .env.<something>.
ENV_PATH = re.compile(r"""(?:^|[\s'"=<>(/])(\.env(?:\.[A-Za-z0-9_.-]+)?)(?=$|[\s'");|&<>])""")
SPLIT = re.compile(r"&&|\|\||[;|\n]")
READERS = {
    "cat", "head", "tail", "less", "more", "bat", "nl", "sed", "awk", "cut", "sort", "uniq",
    "strings", "xxd", "od", "hexdump", "open", "code", "vim", "vi", "nano", "emacs", "pbcopy",
    "source", ".", "rg", "base64", "tee", "diff", "paste", "tr", "dd", "column", "fold", "rev",
    "tac", "jq", "python", "python3", "node", "ruby", "perl", "printf", "echo", "export", "set",
}  # fmt: skip
LIVE = re.compile(r"(?:^|\s)ENV=live\b|\.env\.live\b|\bjap-live\b")
COMPOSE_CONFIG = re.compile(r"\bdocker[ -]compose\b.*\sconfig\b")
QUIET = re.compile(r"\s(?:-q|--quiet)\b")


def is_secret_file(path: str) -> bool:
    name = os.path.basename(path.rstrip("/"))
    return (name == ".env" or name.startswith(".env.")) and name not in EXAMPLES


def secret_files_in(text: str) -> list[str]:
    return [m for m in ENV_PATH.findall(text) if m not in EXAMPLES]


def check_command(command: str, allow_live: bool) -> str | None:
    for segment in SPLIT.split(command):
        words = segment.split()
        if not words:
            continue
        # git and gh only name the live stack in a commit message or a PR body.
        writes_about_it = os.path.basename(words[0]) in {"git", "gh"}
        if not allow_live and not writes_about_it and LIVE.search(segment):
            return (
                "This targets the live stack, which submits real applications. AGENTS.md: never "
                "run anything with ENV=live without being asked. Ask Aditya to run it himself, "
                "or to start this harness with JAP_AGENT_ALLOW_LIVE=1."
            )
        if COMPOSE_CONFIG.search(segment) and not QUIET.search(segment):
            return (
                "`docker compose config` prints every secret substituted in. Use `config -q`, "
                "which validates and prints nothing (AGENTS.md, Secrets)."
            )
        if not secret_files_in(segment):
            continue
        program = os.path.basename(words[0])
        grep_prints = program in {"grep", "egrep", "fgrep"} and not re.search(
            r"\s-[A-Za-z]*q|\s--quiet|\s--silent", segment
        )
        if program in READERS or grep_prints or "<" in segment:
            return (
                "This would print a secrets file. AGENTS.md: never open .env, .env.* or "
                ".env.tunnel. To check a key, print only set/empty: "
                "grep -qE '^KEY=.+' .env && echo set"
            )
    return None


def check(payload: dict[str, object], allow_live: bool = False) -> str | None:
    """The reason to block this tool call, or None to let it through."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and is_secret_file(value):
            return (
                f"{os.path.basename(value)} holds secrets. AGENTS.md: never open .env, .env.* "
                "or .env.tunnel with a file tool. Read .env.example for the keys instead."
            )
    for key in ("input", "patch"):  # apply_patch names its files inside the patch text
        value = tool_input.get(key)
        if isinstance(value, str):
            for line in value.splitlines():
                m = re.match(r"\*\*\* (?:Add|Update|Delete) File: (.+)", line)
                if m and is_secret_file(m.group(1).strip()):
                    return "This patch touches a secrets file (AGENTS.md, Secrets)."
    command = tool_input.get("command")
    if isinstance(command, str):
        return check_command(command, allow_live)
    return None


CASES: list[tuple[bool, dict[str, str]]] = [
    # (blocked, tool_input)
    (True, {"file_path": "/repo/.env"}),
    (True, {"file_path": ".env.live"}),
    (True, {"path": "infra/tunnel/.env.tunnel"}),
    (False, {"file_path": "/repo/.env.example"}),
    (False, {"file_path": "infra/tunnel/.env.tunnel.example"}),
    (False, {"file_path": "backend/config.py"}),
    (False, {"path": "backend", "pattern": "OPENROUTER_API_KEY"}),
    (True, {"input": "*** Begin Patch\n*** Update File: .env\n+X=1\n"}),
    (True, {"command": "cat .env"}),
    (True, {"command": "cd infra && head -3 ../.env"}),
    (True, {"command": "grep OPENROUTER .env"}),
    (True, {"command": "source .env && echo $OPENROUTER_API_KEY"}),
    (True, {"command": "wc -l < .env"}),
    (True, {"command": "sed -n 1,5p infra/tunnel/.env.tunnel"}),
    (False, {"command": "grep -qE '^OPENROUTER_API_KEY=.+' .env && echo set"}),
    (False, {"command": "test -f .env || cp .env.example .env"}),
    (False, {"command": "cat .env.example"}),
    (False, {"command": "ls -la"}),
    (False, {"command": "make test"}),
    (False, {"command": "git status --short"}),
    (False, {"command": "docker compose -p jap exec app pytest -q tests/test_matching.py"}),
    (True, {"command": "docker compose --env-file .env -f infra/compose.yaml config"}),
    (True, {"command": "docker compose -f infra/compose.yaml config | grep PORT"}),
    (False, {"command": "docker compose --env-file .env -f infra/compose.yaml config -q"}),
    (False, {"command": "docker compose -f infra/compose.yaml config --quiet"}),
    (True, {"command": "make start ENV=live"}),
    (True, {"command": "ENV=live make migrate"}),
    (True, {"command": "docker compose -p jap-live ps"}),
    (True, {"command": "grep -q X .env.live && echo set"}),
    (False, {"command": "make start ENV=local"}),
    (False, {"command": "grep -n 'ENV=live' Makefile README.md"}),
    (False, {"command": "git commit -m 'Explain make start ENV=live and .env.live'"}),
    (True, {"command": "git status && make stop ENV=live"}),
]


def self_test() -> int:
    failed = 0
    for blocked, tool_input in CASES:
        reason = check({"tool_input": tool_input})
        if (reason is not None) != blocked:
            failed += 1
            print(f"FAIL (expected {'block' if blocked else 'allow'}): {tool_input}")
    if check({"tool_input": {"command": "make ps ENV=live"}}, allow_live=True) is not None:
        failed += 1
        print("FAIL: JAP_AGENT_ALLOW_LIVE=1 did not allow ENV=live")
    print(f"agent-guard: {len(CASES) + 1 - failed}/{len(CASES) + 1} cases passed")
    return 1 if failed else 0


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0  # not a payload this script understands: never block on that
    if not isinstance(payload, dict):
        return 0
    reason = check(payload, allow_live=os.environ.get("JAP_AGENT_ALLOW_LIVE") == "1")
    if reason is None:
        return 0
    print(f"Blocked by infra/scripts/agent-guard.py: {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
