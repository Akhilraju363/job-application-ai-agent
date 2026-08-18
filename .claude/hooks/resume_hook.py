#!/usr/bin/env python3
"""PreToolUse hook: validates tailored resume content before it's written.

Only checks Write calls targeting output/tailored/*.md. Blocks (exit 2) if
required sections are missing or placeholder text remains, per PRD 3.4.

Thin wrapper around scripts/validate_resume.py — see that module for the
actual rule, shared with the automated tailor_job.py path.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from validate_resume import validate

data = json.load(sys.stdin)
tool_input = data.get("tool_input", {})
file_path = tool_input.get("file_path", "")

if "output/tailored/" not in file_path:
    sys.exit(0)

content = tool_input.get("content", "")
ok, reason = validate(content)

if not ok:
    print(f"Validation failed: {reason}", file=sys.stderr)
    sys.exit(2)

sys.exit(0)
