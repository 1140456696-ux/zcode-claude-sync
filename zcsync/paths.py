#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径解析：用 ~ 展开 + 平台默认 + 环境变量，做到零写死。

约定（可被环境变量覆盖）：

- ZCode 会话库:  ZCODE_DB   (默认 ~/.zcode/cli/db/db.sqlite)
- Claude projects: CLAUDE_PROJECTS_DIR (默认 ~/.claude/projects)
- 项目目录名映射: slugify(path)
"""
import os
import re


def expand(p):
    if not p:
        return None
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


def zcode_db():
    return expand(os.environ.get("ZCODE_DB") or "~/.zcode/cli/db/db.sqlite")


def claude_projects_dir():
    return expand(os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects")


def slugify(path):
    """E:\\测试\\MI -> e-----mi（Claude Code 项目目录名规则）"""
    s = re.sub(r"[^a-zA-Z0-9]", "-", path)
    return s.lower()


def normpath(p):
    """统一路径格式：正反斜杠、大小写归一，便于关键字匹配。"""
    return p.replace("\\", "/").strip().lower() if p else ""


def session_jsonl_path(project_dir, session_id):
    """返回该会话在 Claude 项目目录下的 jsonl 路径。"""
    return os.path.join(project_dir, f"{session_id}.jsonl")