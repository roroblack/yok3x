#!/usr/bin/env python3
"""launcher: ./harness.py <cmd> ...  (python3 -m harness.cli 와 동일)"""
from harness.cli import entry
raise SystemExit(entry())
