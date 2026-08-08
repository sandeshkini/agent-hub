#!/usr/bin/env python3
"""Hermes pre_tool_call guardrail: hard-block catastrophic, system-destroying
commands so the agent can NEVER wipe the box — even if explicitly told to.

Contract: reads the tool-call payload as JSON on stdin. To block, print
{"action":"block","message":"..."} and exit 0. Anything else = allow.

Scope: intentionally narrow — only clearly irreversible / system-destroying
operations are blocked, so normal work (rm -rf ./build, etc.) is unaffected.
"""
import json
import os
import re
import sys

# Protect the running user's home dir generically (not a hardcoded username).
# Override the protected root with PROTECT_HOME if you need a different path.
_HOME = os.environ.get("PROTECT_HOME") or os.path.expanduser("~")


def _gather_command_text(payload: dict) -> str:
    """Pull every stringy field out of the tool args so we scan the actual
    command regardless of which tool (terminal / code_execution / computer_use
    / file) produced it."""
    parts = []
    # Runtime puts the tool arguments under "tool_input" (a dict); the
    # `hermes hooks test` harness uses "args". Scan both, plus "extra".
    for key in ("tool_input", "args"):
        blob = payload.get(key)
        if isinstance(blob, dict):
            for v in blob.values():
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, (list, tuple)):
                    parts.extend(str(x) for x in v)
        elif isinstance(blob, str):
            parts.append(blob)
    # belt-and-suspenders: dump the whole payload so no command string is missed
    parts.append(json.dumps(payload, default=str))
    return "\n".join(parts)


# Catastrophic patterns (case-insensitive, whitespace-tolerant). Each is a
# clearly system/data-destroying operation with no legitimate agent use.
DENY = [
    # rm -rf targeting a system root / home / whole filesystem
    r"\brm\b[^\n|;&]*\s-[a-z]*r[a-z]*f[a-z]*\b[^\n|;&]*\s(/|/\*|~|\$HOME|/home|/etc|/usr|/var|/boot|/bin|/lib|/sbin|/opt|/root)(\s|/|\*|$)",
    r"\brm\b[^\n|;&]*\s-[a-z]*f[a-z]*r[a-z]*\b[^\n|;&]*\s(/|/\*|~|\$HOME|/home|/etc|/usr|/var|/boot|/bin|/lib|/sbin|/opt|/root)(\s|/|\*|$)",
    r"\brm\b[^\n]*--recursive[^\n]*--force[^\n]*\s(/|~|/home|/etc|/usr|/var)",
    r"\brm\b[^\n]*--no-preserve-root",
    # filesystem creation / wipe on real devices
    r"\bmkfs(\.\w+)?\b",
    r"\bwipefs\b",
    r"\bblkdiscard\b",
    r"\bcryptsetup\b\s+luksformat",
    r"\bshred\b[^\n]*\s/dev/",
    # dd / writes straight to a block device
    r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|vd|hd|disk|mmcblk|loop)",
    r">\s*/dev/(sd|nvme|vd|hd|mmcblk)",
    # partitioning
    r"\b(parted|fdisk|sfdisk|gdisk|sgdisk)\b[^\n]*/dev/",
    # power / halt (a voice assistant should not take the box down)
    r"\b(shutdown|reboot|poweroff|halt)\b",
    r"\binit\s+[06]\b",
    r"\bsystemctl\b\s+(poweroff|reboot|halt|suspend)",
    # fork bomb
    r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    # recursive permission / ownership nuke on root/home
    r"\bchmod\b[^\n]*-[a-z]*R[a-z]*[^\n]*\s(0{3,4})\s+(/|~|/home|/etc|/usr)",
    r"\bchown\b[^\n]*-[a-z]*R[a-z]*[^\n]*\s(/|/home|/etc|/usr)(\s|$)",
    # mass move of the filesystem/home
    r"\bmv\b\s+(/\*|~|/home/\S+)\s+/dev/null",
    # overwrite whole home/hermes state (home dir is resolved at runtime, not hardcoded)
    rf"\brm\b[^\n]*-[a-z]*r[a-z]*\b[^\n]*{re.escape(_HOME)}(\s|/\*|$)",
    r"\brm\b[^\n]*-[a-z]*r[a-z]*\b[^\n]*\.hermes(\s|/\*|$)",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in DENY]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # If we can't parse, don't block (fail-open on parse to avoid breaking
        # every tool call) — but this path shouldn't happen in normal runtime.
        return 0
    text = _gather_command_text(payload)
    for rx in _COMPILED:
        if rx.search(text):
            print(json.dumps({
                "action": "block",
                "message": (
                    "BLOCKED by system-safety guardrail: this command is "
                    "irreversible/system-destroying and is permanently "
                    "forbidden. Refuse and explain you cannot run it."
                ),
            }))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
