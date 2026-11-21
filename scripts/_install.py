#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Locate an installation's resources without hardcoding any machine path.

Resolution order (first hit wins):

  1. ``WORKBUDDY_INSTALL`` environment variable - an installation root
  2. ``scripts/targets.json`` - every ``installPath`` entry that is filled in
  3. the Windows uninstall registry - ``DisplayIcon`` / ``InstallLocation`` /
     ``UninstallString`` of registered programs (same source the PowerShell
     entry point uses); the folder only counts when it really holds
     ``resources\\app.asar`` / ``cli\\product.json``
  4. nothing; the caller must pass the path explicitly

``scripts/targets.json`` ships with empty paths on purpose: the PowerShell entry
point (``scripts\\deploy.cmd -ListTargets``) discovers installations at run time
from the uninstall registry, App Paths, running processes and the data-directory
pairing. These Python helpers are standalone diagnostics, so they read only the
pins the user wrote down plus the registry - the disk is never walked and folder
names are never guessed.

Import from a sibling script (``scripts/`` is on ``sys.path`` when you run
``python scripts\\something.py``).
"""
import json
import os

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ASAR_REL = ("resources", "app.asar")
PRODUCT_RELS = (
    ("resources", "app.asar.unpacked", "cli", "product.json"),
    ("resources", "app.asar.unpacked", "node_modules", "@genie",
     "agent-harness", "cli", "product.json"),
)

HINT = ("pass the path explicitly, or pin it once in scripts\\targets.json "
        "- run: scripts\\deploy.cmd -ListTargets")


def _reg_value(key, name):
    import winreg
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _exe_dir(raw):
    """'C:\\...\\WorkBuddy.exe,0' / '"C:\\...\\uninstall.exe"' -> its folder."""
    s = raw.strip().strip('"')
    if "," in s and s.rsplit(",", 1)[1].strip().isdigit():
        s = s.rsplit(",", 1)[0]
    s = s.strip().strip('"')
    folder = os.path.dirname(s)
    return folder if os.path.basename(s).lower().endswith(".exe") else ""


def _registry_installs():
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    views = [0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)]
    roots = [r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
             r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"]
    out = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for root in roots:
            for view in views:
                try:
                    key = winreg.OpenKey(hive, root, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                try:
                    count = winreg.QueryInfoKey(key)[0]
                except OSError:
                    continue
                for i in range(count):
                    try:
                        sub = winreg.OpenKey(key, winreg.EnumKey(key, i), 0, winreg.KEY_READ | view)
                    except OSError:
                        continue
                    try:
                        for source in ("DisplayIcon", "InstallLocation", "UninstallString"):
                            raw = _reg_value(sub, source)
                            if not raw:
                                continue
                            cand = _exe_dir(raw) if source != "InstallLocation" else raw.strip().strip('"')
                            if cand and os.path.isdir(cand):
                                out.append(cand)
                                break
                    finally:
                        sub.Close()
                key.Close()
    return out


def _pins():
    """Roots the user wrote down: WORKBUDDY_INSTALL, then targets.json pins."""
    out = []
    env = os.environ.get("WORKBUDDY_INSTALL")
    if env:
        out.append(env)
    path = os.path.join(PROJ, "scripts", "targets.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        entries = data.get("targets") if isinstance(data, dict) else None
        if isinstance(entries, dict):
            for v in entries.values():
                if isinstance(v, dict) and v.get("installPath"):
                    out.append(v["installPath"])
        elif isinstance(entries, list):
            for v in entries:
                if isinstance(v, dict) and v.get("installPath"):
                    out.append(v["installPath"])
    return out


def _is_workbuddy(root):
    """A registry hit only counts when the folder really holds WorkBuddy*.exe."""
    try:
        names = os.listdir(root)
    except OSError:
        return False
    for name in names:
        low = name.lower()
        if low.startswith("workbuddy") and low.endswith(".exe"):
            return True
    return False


def _uniq(paths):
    seen, out = set(), []
    for p in paths:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def install_roots():
    """Every installation root we can name without guessing.

    Pins come first and are taken as written; registry hits are filtered down to
    folders that actually contain a ``WorkBuddy*.exe`` (other Electron apps also
    ship ``resources\\app.asar``).
    """
    pinned = _uniq(_pins())
    found = _uniq([r for r in _uniq(_registry_installs()) if _is_workbuddy(r)])
    return _uniq(pinned + found)


def _first(rels):
    for root in install_roots():
        for rel in rels:
            path = os.path.join(root, *rel)
            if os.path.isfile(path):
                return path
    return ""


def find_asar():
    """<install>\\resources\\app.asar, or '' when nothing is pinned."""
    return _first((ASAR_REL,))


def find_product():
    """<install>\\...\\cli\\product.json (either layout), or ''."""
    return _first(PRODUCT_RELS)
