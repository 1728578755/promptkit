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
pairing. These Python helpers are standalone diagnostics: ``install_roots()``
reads only the pins plus the registry, while ``install_roots_deep()`` adds the
bounded disk scan (see the "对齐清单" block below) - the same drives / folders
``target.ps1`` and ``doctor-basic.ps1`` scan. Folder names are never guessed as
data-directory names; product.json's ``dataFolderName`` is authoritative.

Import from a sibling script (``scripts/`` is on ``sys.path`` when you run
``python scripts\\something.py``).
"""
import collections
import json
import os
import subprocess

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

# ---------------------------------------------------------------- deep scan
# 新电脑上最常见的两种"找不到"：
#   1) 安装目录不在 Program Files，注册表也没有（绿色版 / 换盘 / 改过目录名）；
#   2) 用户目录是新的，~/.workbuddy 还不存在。
# 下面这些来源都不写死任何路径：环境变量、注册表、正在运行的进程、开始菜单快捷方式，
# 最后才做一次有深度上限的磁盘扫描。

# ---- 磁盘发现规则（对齐清单）------------------------------------------------
# 三处实现必须一致：本文件的 scan_locations()、target.ps1 的 Get-InstallCandidates、
# doctor-basic.ps1 的 Find-Installs。
#   * 盘符：C..K（跳过不存在的盘）
#   * 盘根 <盘>:\：只看一层子目录，且只认名字含 workbuddy 的目录（便携版）；
#     其下再深一层（有界深度 2）
#   * 常见安装根：Program Files / Program Files (x86) / Apps / Programs / Software
#     （每个再深 1 层）
#   * 用户级：%LOCALAPPDATA%\Programs、%LOCALAPPDATA%\WorkBuddy（没有 Programs 这层）、
#     <盘>:\Users\<用户>\AppData\Local\Programs
# 只收"看着像 WorkBuddy"的候选（_looks_like_workbuddy）。
DISCOVERY_DRIVE_LETTERS = "CDEFGHIJK"
DISCOVERY_INSTALL_SUBDIRS = ("Program Files", "Program Files (x86)", "Apps", "Programs", "Software")

def _process_roots():
    """正在运行的 WorkBuddy / codebuddy 进程 -> 它所在的安装目录。"""
    if os.name != "nt":
        return []
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'WorkBuddy|codebuddy' } | "
          "ForEach-Object { $_.ExecutablePath }")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception:
        return []
    out = []
    for line in (p.stdout or "").splitlines():
        exe = line.strip().strip('"')
        if exe.lower().endswith(".exe") and os.path.isfile(exe):
            out.append(os.path.dirname(exe))
    return out


def _shortcut_roots():
    """开始菜单 / 桌面的 .lnk -> 目标 exe 所在目录。"""
    if os.name != "nt":
        return []
    # 用 .NET 的已知文件夹取路径，避免在 Python 里拼引号/反斜杠。
    # 全部 raw string：PowerShell 里的 `\M` / `\D` / `\.` 是字面反斜杠，
    # 写成普通字符串会触发 SyntaxWarning（未来版本 = SyntaxError）。
    ps = (
        r"$ErrorActionPreference='SilentlyContinue';"
        r"$dirs=@("
        r"(Join-Path ([Environment]::GetFolderPath('CommonStartMenu')) 'Programs'),"
        r"(Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'),"
        r"[Environment]::GetFolderPath('Desktop'),"
        r"[Environment]::GetFolderPath('CommonDesktopDirectory'));"
        r"$sh=New-Object -ComObject WScript.Shell;"
        r"foreach($d in $dirs){ if(Test-Path -LiteralPath $d){"
        r" Get-ChildItem -LiteralPath $d -Filter *.lnk -Recurse -Depth 2 -ErrorAction SilentlyContinue |"
        r" ForEach-Object { $t=$sh.CreateShortcut($_.FullName).TargetPath;"
        r" if($t -match 'WorkBuddy.*\.exe$'){ $t } } } }"
    )
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except Exception:
        return []
    out = []
    for line in (p.stdout or "").splitlines():
        exe = line.strip().strip('"')
        if exe.lower().endswith(".exe") and os.path.isfile(exe):
            out.append(os.path.dirname(exe))
    return out


def scan_locations():
    """磁盘扫描会看的位置（给诊断报告用，说明"找过哪里"）。

    盘根、Program Files / Program Files (x86) / Apps / Programs / Software、
    %LOCALAPPDATA%\\Programs、%LOCALAPPDATA%\\WorkBuddy（没有 Programs 这层），
    以及每个用户的 %LocalAppData%\\Programs。

    去重（dict.fromkeys 保序）：当前用户的 %LOCALAPPDATA%\\Programs 也会被 Users 循环
    列到一次，重复扫描会让同一个 base 打印两遍、白扫一遍。
    """
    bases = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        bases.append(os.path.join(local, "Programs"))
        bases.append(os.path.join(local, "WorkBuddy"))
    for letter in DISCOVERY_DRIVE_LETTERS:
        drive = letter + ":\\"
        if not os.path.isdir(drive):
            continue
        for sub in DISCOVERY_INSTALL_SUBDIRS:
            bases.append(os.path.join(drive, sub))
        users = os.path.join(drive, "Users")
        if os.path.isdir(users):
            try:
                names = sorted(os.listdir(users))[:20]
            except OSError:
                names = []
            for u in names:
                bases.append(os.path.join(users, u, "AppData", "Local", "Programs"))
    return list(dict.fromkeys(bases))


def _walk_for_asar(base, depth=2):
    """在 base 下有界（BFS）地找 WorkBuddy 安装根：<dir>\\resources\\app.asar。

    只收 _looks_like_workbuddy() 通过的目录——Electron 应用都带 app.asar，
    不先过滤的话别家应用也会算命中，很快把上限占满、把真安装挤掉。
    深度 0：所有子目录都看一眼（有些安装器会多套一层厂商目录）；
    更深一层只认名字里带 workbuddy 的，避免把整块盘翻一遍。
    """
    if not os.path.isdir(base):
        return []
    hits = []
    queue = collections.deque([(base, 0)])
    seen = 0
    while queue and seen < 2000:
        cur, d = queue.popleft()
        seen += 1
        if os.path.isfile(os.path.join(cur, "resources", "app.asar")) and _looks_like_workbuddy(cur):
            hits.append(cur)
        if d >= depth:
            continue
        try:
            entries = sorted(os.listdir(cur))[:300]
        except OSError:
            continue
        for name in entries:
            path = os.path.join(cur, name)
            low = name.lower()
            if not os.path.isdir(path):
                continue
            if d == 0 or "workbuddy" in low:
                queue.append((path, d + 1))
    return hits


def _scan_roots():
    """本机所有"像 WorkBuddy 的安装根"（磁盘扫描部分）。

    扫遍全部 base 再截断：旧实现"扫满 12 个任意 Electron 应用就 break"，本机实测
    28 个 base 被跳过，D:\\Program Files\\WorkBuddyAI 直接丢失。
    """
    out = []
    for base in scan_locations():
        out.extend(_walk_for_asar(base))
    return _uniq(out)[:48]


def _looks_like_workbuddy(root):
    """文件夹名、exe 名、或 product.json 的 productName 任一指向 WorkBuddy。

    Electron 应用都带 resources\app.asar，扫描时必须靠这三样把别家应用排除掉。
    """
    if "workbuddy" in os.path.basename(root).lower():
        return True
    if _is_workbuddy(root):
        return True
    try:
        path = os.path.join(root, "resources", "app.asar.unpacked", "cli", "product.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "workbuddy" in fh.read(4000).lower():
                    return True
    except OSError:
        pass
    return False


def install_roots_deep():
    """把能找到的安装全找出来：环境变量 / 注册表 / 进程 / 快捷方式 / 磁盘扫描。"""
    found = list(_pins())
    found.extend(_registry_installs())
    found.extend(_process_roots())
    found.extend(_shortcut_roots())
    found.extend(_scan_roots())
    out = []
    for root in _uniq(found):
        if not os.path.isfile(os.path.join(root, "resources", "app.asar")):
            continue
        if _looks_like_workbuddy(root):
            out.append(root)
    return out
