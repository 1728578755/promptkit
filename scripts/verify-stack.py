#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility shim — 逐层验收已并入 `doctor.py`。

旧名字保留，是因为 v1.3.8 的部署脚本与文档已经引用它；实现只有一份：
`doctor.py` 会额外列出“本应存在但没有”的项，并给出结论与原因。

    python -X utf8 scripts/verify-stack.py            # 等同 doctor.py
    python -X utf8 scripts/verify-stack.py --json
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "doctor.py")
sys.exit(subprocess.run([sys.executable, "-X", "utf8", TARGET] + sys.argv[1:]).returncode)
