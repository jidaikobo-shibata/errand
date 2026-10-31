#!/usr/bin/env python3
"""Compatibility entry point for the unified Gemini CLI GUI smoke check."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name('acp_smoke.py')), run_name='__main__')
