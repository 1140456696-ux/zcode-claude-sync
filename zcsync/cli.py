#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZCode <-> Claude Code 会话双向同步（交互式向导）。

用法：
    python run.py                                  # 交互向导：问方向 → 问目录 → 问会话
    python run.py z2c                              # 直接 ZCode→Claude 的向导（跳过方向问题）
    python run.py c2z                              # 直接 Claude→ZCode 的向导
    python run.py z2c --path E:\\xx                 # 指定源目录（进入会话选择）
    python run.py z2c --all                        # 全部目录（进入会话选择）
    python run.py --dry-run                        # 只预览不写入
    python run.py --help                           # 帮助
"""
import json
import os
import re
import sqlite3
import sys
import uuid
from types import SimpleNamespace

from . import paths
from .convert import (
    list_z_sessions, z2c_session,
    c2z_write, parse_ts, to_iso,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- utils

def banner():
    print("=" * 60)
    print("  ZCode ↔ Claude Code 会话双向同步")
    print("=" * 60)


def confirm(msg):
    try:
        raw = input(f"{msg} [y/N]: ").strip().lower()
    except EOFError:
        raw = ""
    return raw in ("y", "yes")


def pick_number(msg, lo, hi):
    """要求输入 0..n 的编号（0=全选/继续）。返回 int 或 None（q 退出）。"""
    while True:
        raw = input(f"{msg}").strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw == "":
            return 0
        if re.fullmatch(r"0|[0-9]+", raw) and lo <= int(raw) <= hi:
            return int(raw)
        print(f"  请输入 {lo}~{hi} 之间的编号")


def pick_session_ids(items, label="转换的会话"):
    """会话多选：先列出可选会话，支持 1,3,5 / 0全选 / q退出。返回选中 items 或 None。"""
    n = len(items)
    print(f"\n--- {label}（可转 {n} 个）---")
    for i, s in enumerate(items, 1):
        ts = to_iso(s.get("updated") or s.get("created") or 0)[:16].replace("T", " ")
        sid8 = (s.get("id") or "")[:8]
        print(f"  [{i:>2}] {ts}  {sid8}  {(s.get('title') or '')[:52]}")
    print("  [0] 全选\n  [q] 退出")
    while True:
        raw = input(f"选择要转换的会话（编号/逗号分隔/0全选/q退出）: ").strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw == "0":
            return list(items)
        if re.fullmatch(r"[0-9,\s]+", raw):
            idxs = [int(x) for x in re.split(r"[,\s]+", raw) if x]
            sel = [items[i-1] for i in idxs if 1 <= i <= n]
            if sel:
                return sel
        print(f"  输入无效，请输入 1~{n} 或逗号分隔")


def choose_dir(dirs, label):
    """列目录菜单，返回选中的 [(path, sessions), ...] 或 None。支持编号/多选/关键字/路径。"""
    items = sorted(dirs.items())
    print(f"\n--- {label} ---")
    for i, (path, sess) in enumerate(items, 1):
        print(f"  [{i:>2}] {path}  ({len(sess)}个会话)")
    print("  [0] 全选\n  [q] 退出")
    while True:
        raw = input("选择目录（编号/逗号分隔/关键字/路径，0=全选，q=退出）: ").strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw == "0":
            return list(items)
        if re.fullmatch(r"[0-9,\s]+", raw):
            idxs = [int(x) for x in re.split(r"[,\s]+", raw) if x]
            sel = [items[i-1] for i in idxs if 1 <= i <= len(items)]
            if sel:
                return sel
            print("  编号越界，再试"); continue
        rn = paths.normpath(raw)
        matches = [(p, s) for p, s in items if rn in paths.normpath(p)]
        if matches:
            return matches
        print(f"  未匹配到含「{raw}」的目录，再试")


# ---------------------------------------------------------------- 方向选择

def ask_direction():
    """交互问方向。返回 'z2c' 或 'c2z' 或 None。"""
    print("你要把哪个方向搬？")
    print("  [1] ZCode → Claude Code")
    print("  [2] Claude Code → ZCode")
    while True:
        raw = input("选择（1/2，q=退出）: ").strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw == "1":
            return "z2c"
        if raw == "2":
            return "c2z"
        print("  请输入 1 或 2")


# ---------------------------------------------------------------- 主流程

def do_z2c(dir_filter=None, skip_sessions=False, dry_run=False, path_filter=None):
    """ZCode → Claude。dir_filter: 'all' 或 None（交互）。path_filter: 关键字/路径过滤。"""
    zdb = paths.zcode_db()
    if not os.path.isfile(zdb):
        print(f"[错误] 找不到 ZCode 库：{zdb}")
        print("       可用环境变量 ZCODE_DB 指定库路径")
        return 1
    dirs = list_z_sessions(zdb)
    if not dirs:
        print("ZCode 库里没有会话"); return 1

    if dir_filter == "all":
        targets = sorted(dirs.items())
    elif path_filter:
        rn = paths.normpath(path_filter)
        targets = [(p, s) for p, s in dirs.items() if rn in paths.normpath(p)]
        if not targets:
            print(f"没找到含「{path_filter}」的目录"); return 1
    else:
        targets = choose_dir(dirs, "ZCode 中的工作目录")
        if targets is None:
            print("已取消"); return 0

    proj_root = paths.claude_projects_dir()
    os.makedirs(proj_root, exist_ok=True)
    total = 0
    for path, sessions in targets:
        # 会话选择
        if not skip_sessions:
            sessions = pick_session_ids(sessions, f"{path} 下的 ZCode 会话")
            if sessions is None:
                print("已取消"); return 0
        pdir = os.path.join(proj_root, paths.slugify(path))
        os.makedirs(pdir, exist_ok=True)
        for s in sessions:
            sid_file = str(uuid.uuid5(uuid.NAMESPACE_URL, f"zcode:{s['id']}"))
            out = os.path.join(pdir, f"{sid_file}.jsonl")
            if dry_run:
                print(f"[Z→C] 计划写入  {out}")
                continue
            _p, n, _sid = z2c_session(zdb, s["id"], out)
            total += n
            print(f"[Z→C] ✅ {s['title'][:40]:<42} -> {_sid}.jsonl  ({n}行)")
    print(f"\n完成：ZCode→Claude 共 {total} 行" if not dry_run else "\n(dry-run 未写入)")
    return 0


def list_claude_sessions(proj_root):
    """列出 Claude 项目下所有 jsonl 会话（按项目目录分组）。"""
    groups = {}
    if not os.path.isdir(proj_root):
        return groups
    for proj in sorted(os.listdir(proj_root)):
        pdir = os.path.join(proj_root, proj)
        if not os.path.isdir(pdir):
            continue
        for f in sorted(os.listdir(pdir)):
            if not f.endswith(".jsonl"):
                continue
            fpath = os.path.join(pdir, f)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    first = json_parse_first(fh)
                if not first:
                    continue
                session_id = first.get("sessionId") or f[:-6]
                title = first.get("message", {}).get("content")
                if isinstance(title, list):
                    title = " ".join(str(c.get("text", "")) for c in title if c.get("type") == "text")[:60]
                elif not isinstance(title, str):
                    title = ""
                groups.setdefault(proj, []).append({
                    "id": session_id, "title": title or "(无标题)",
                    "file": fpath, "updated": parse_ts(first.get("timestamp", "")),
                    "created": parse_ts(first.get("timestamp", "")),
                })
            except Exception:
                continue
    return groups


def json_parse_first(fh):
    for line in fh:
        try:
            e = json.loads(line)
            if e.get("type") in ("user", "assistant"):
                return e
        except Exception:
            continue
    return None


def guess_cwd_from_jsonl(jsonl_path):
    """从 Claude JSONL 第一条 user 消息的 cwd 反向猜目录。"""
    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("cwd"):
                return e["cwd"]
    return None


def do_c2z(dir_filter=None, skip_sessions=False, dry_run=False, force=False, path_filter=None):
    """Claude → ZCode。dir_filter: 'all' 或 None（交互）。path_filter: 关键字/路径/slug过滤。"""
    proj_root = paths.claude_projects_dir()
    zdb = paths.zcode_db()
    if not os.path.isfile(zdb):
        print(f"[错误] 找不到 ZCode 库：{zdb}")
        print("       可用环境变量 ZCODE_DB 指定库路径")
        return 1
    groups = list_claude_sessions(proj_root)
    if not groups:
        print("Claude 目录没有会话"); return 1

    if dir_filter == "all":
        targets = sorted(groups.items())
    elif path_filter:
        rn = paths.normpath(path_filter)
        targets = []
        for p, s in groups.items():
            real = guess_cwd_from_jsonl(s[0]["file"]) or p
            if rn in paths.normpath(p) or rn in paths.normpath(real):
                targets.append((p, s))
        if not targets:
            print(f"没找到含「{path_filter}」的项目目录"); return 1
    else:
        targets = choose_dir({p: s for p, s in groups.items()}, "Claude Code 中的项目目录")
        if targets is None:
            print("已取消"); return 0

    total = 0
    for proj, sessions in targets:
        if not skip_sessions:
            sessions = pick_session_ids(sessions, f"{proj} 下的 Claude 会话")
            if sessions is None:
                print("已取消"); return 0
        for s in sessions:
            sid, title, jsonl_path = s["id"], s["title"], s["file"]
            cwd = guess_cwd_from_jsonl(jsonl_path) or "unknown"
            if dry_run:
                print(f"[C→Z] 计划写入  会话 {title[:40]}  (cwd={cwd})")
                continue
            if not force and not confirm(f"写入 ZCode 会话「{title[:40]}」(cwd={cwd})？"):
                continue
            n = c2z_write(zdb, sid, title, cwd, jsonl_path)
            total += n
            print(f"[C→Z] ✅ {title[:40]:<42} -> 写入 {n} 条消息 (cwd={cwd})")
    print(f"\n完成：Claude→ZCode 共 {total} 条消息" if not dry_run else "\n(dry-run 未写入)")
    return 0


# ---------------------------------------------------------------- 入口

def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    banner()

    # 解析参数
    direction = None          # z2c / c2z
    op = {}                   # dict: path/all/dry_run/yes/no_input
    for _k in ("path", "all", "dry_run", "yes", "no_input"):
        op[_k] = None if _k == "path" else False
    dargs = []
    for a in argv:
        if a in ("z2c", "c2z"):
            direction = a
        elif a.startswith("--path="):
            op["path"] = a.split("=", 1)[1]
        elif a == "--path":
            op["path"] = None  # 取下一个值
        elif a == "--all":
            op["all"] = True
        elif a == "--dry-run":
            op["dry_run"] = True
        elif a == "--yes":
            op["yes"] = True
        elif a == "--no-input":
            op["no_input"] = True
        elif a in ("-h", "--help"):
            print(__doc__); return 0
        elif a.startswith("-"):
            print(f"未知参数: {a}"); return 1
        else:
            dargs.append(a)   # 位置参数：方向或路径
    # 若 --path 后跟值还没吃，位置参数补进去
    if op["path"] is None and dargs and direction:
        op["path"] = dargs[-1]

    # 无方向 → 交互问方向
    if not direction:
        direction = ask_direction()
        if direction is None:
            print("已退出"); return 0

    dry_run = op["dry_run"]
    path_filter = op["path"] if op["path"] else None
    skip = op["no_input"]
    if direction == "z2c":
        return do_z2c(dir_filter="all" if op["all"] else None,
                      skip_sessions=skip, dry_run=dry_run, path_filter=path_filter)
    else:
        return do_c2z(dir_filter="all" if op["all"] else None,
                      skip_sessions=skip, dry_run=dry_run, force=op["yes"],
                      path_filter=path_filter)


if __name__ == "__main__":
    sys.exit(main())