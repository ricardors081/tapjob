#!/usr/bin/env python3
"""Forwarder to canonical tools/agent_guard/pre_tool_guard.py."""

import sys
from pathlib import Path

# Add repository root to sys.path
repo_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(repo_root))

from tools.agent_guard.pre_tool_guard import main  # noqa: E402

if __name__ == "__main__":
    main()
