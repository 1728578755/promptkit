#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin launcher for the canonical copy shipped inside the context-dump skill."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(
    HERE, "..", "plugins", "workbuddy-builtin", "skills",
    "context-dump", "scripts", "dump-latest-context.py",
)
TARGET = os.path.abspath(TARGET)
if not os.path.exists(TARGET):
    sys.stderr.write("context-dump script not found: %s\n" % TARGET)
    sys.exit(2)
runpy.run_path(TARGET, run_name="__main__")
