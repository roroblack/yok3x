#!/usr/bin/env python3
"""launcher: ./yok3x.py <cmd> ...  (python3 -m yok3x.cli 와 동일)"""
from yok3x.cli import entry
raise SystemExit(entry())
