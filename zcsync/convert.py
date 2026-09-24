#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双向会话转换核心。

Claude Code 会话是 ~/.claude/projects/<slug>/<uuid>.jsonl（message 是 JSONL 行）。
ZCode  会话是     ~/.zcode/cli/db/db.sqlite（message + part 两张表）。

转换方向：
  z2c : ZCode SQLite -> Claude JSONL
  c2z : Claude JSONL -> ZCode SQLite
"""
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone


CLAUDE_VERSION = "2.1.276"

FILTER_PART_TYPES = {"step-start", "step-finish", "timeline", "reasoning",
                     "step", "user-state", "subagent", "system", "milestone", "unknown"}


def cn(s):
    return s.decode("utf-8", errors="replace") if isinstance(s, bytes) else s


def to_iso(ms):
    if not ms:
        return "2026-01-01T00:00:00.000Z"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_ts(iso_str):
    """从 Claude JSONL 的 ISO 时间串还原 epoch 毫秒。"""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def default_cwd_of(zdb, sid):
    """该 ZCode 会话大多数消息里的 path.cwd（用于 c2z 回填 / z2c 兜底）。"""
    con = sqlite3.connect(zdb)
    con.text_factory = str
    cur = con.cursor()
    cnt = {}
    try:
        for (data,) in cur.execute("SELECT data FROM message WHERE session_id=?", (sid,)):
            try:
                m = json.loads(cn(data))
            except Exception:
                continue
            p = (m.get("path") or {}).get("cwd")
            if p:
                cnt[p] = cnt.get(p, 0) + 1
    finally:
        con.close()
    return max(cnt, key=cnt.get) if cnt else None


# --------------------------------------------------------------------------
# ZCode SQLite -> Claude Code JSONL
# --------------------------------------------------------------------------

def list_z_sessions(zdb):
    con = sqlite3.connect(zdb)
    con.text_factory = str
    cur = con.cursor()
    rows = cur.execute("""
        SELECT id, path, title, time_created, time_updated, task_type
        FROM session ORDER BY path, time_updated
    """).fetchall()
    con.close()
    dirs = {}
    for sid, path, title, tc, tu, tt in rows:
        path = cn(path or "") or "(无路径)"
        dirs.setdefault(path, []).append({
            "id": sid, "title": cn(title), "created": tc,
            "updated": tu, "type": tt})
    return dirs


def load_z_parts(zdb, sid):
    con = sqlite3.connect(zdb)
    con.text_factory = str
    cur = con.cursor()
    msgs = {}
    try:
        for mid, data in cur.execute(
                "SELECT id,data FROM message WHERE session_id=? ORDER BY sequence", (sid,)):
            try:
                msgs[mid] = json.loads(cn(data))
            except Exception:
                continue
        parts = []
        for pid, mid, seq, data in cur.execute(
                "SELECT id,message_id,sequence,data FROM part WHERE session_id=? ORDER BY sequence", (sid,)):
            try:
                parts.append((mid, json.loads(cn(data))))
            except Exception:
                continue
    finally:
        con.close()
    return msgs, parts


def z2c_part(part, default_cwd):
    """把一个 ZCode part 转成 Claude 侧可用的「内容块 + 需要产生的行为」。

    返回 (kind, payload)：
      kind in ("user_text","assistant_text","tool_use","tool_result")
      或 None 表示该 part 应被过滤。
    """
    typ = part.get("type", "")
    if typ in FILTER_PART_TYPES:
        return None
    if typ == "text":
        text = (part.get("text") or "").strip()
        if not text:
            return None
        return ("assistant_text", text)
    if typ == "tool":
        st = part.get("state", {}) or {}
        name = part.get("tool", "")
        if not name:
            return None
        tool_id = part.get("callID") or f"call_{uuid.uuid4().hex[:22]}"
        input_ = (st.get("input") or {}) if st.get("status", "completed") else {}
        out = st.get("output")
        if isinstance(out, str) and out.strip():
            return ("tool_result", {"id": tool_id, "name": name,
                                    "input": input_, "output": out,
                                    "is_error": st.get("status") in ("error", "failed")})
        return ("tool_use", {"id": tool_id, "name": name, "input": input_})
    return None


def _write_claude_entry(f, entry):
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def z2c_session(zdb, sid, out_jsonl, model_label="GLM-5.3-Flash", fallback_cwd=None):
    msgs, parts = load_z_parts(zdb, sid)
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"zcode:{sid}"))
    os.makedirs(os.path.dirname(out_jsonl), exist_ok=True)
    prev = None
    n = 0
    with open(out_jsonl, "w", encoding="utf-8", newline="\n") as f:
        for mid, part in parts:
            m = msgs.get(mid)
            if not m:
                continue
            role = m.get("role", "assistant")
            tc = m.get("time", {})
            ts = tc.get("created") if isinstance(tc, dict) else (tc if isinstance(tc, int) else None)
            iso = to_iso(ts)
            cwd = (m.get("path") or {}).get("cwd") or fallback_cwd or os.getcwd()
            r = z2c_part(part, cwd)
            if not r:
                continue
            kind, payload = r
            if kind in ("user_text", "assistant_text"):
                text = payload
                if role == "user":
                    entry = {
                        "parentUuid": prev, "isSidechain": False,
                        "promptId": str(uuid.uuid4()), "type": "user",
                        "message": {"role": "user", "content": text},
                        "uuid": str(uuid.uuid4()), "timestamp": iso,
                        "permissionMode": "default", "userType": "local",
                        "cwd": cwd, "sessionId": session_id,
                        "version": CLAUDE_VERSION, "gitBranch": "HEAD",
                    }
                else:
                    entry = {
                        "parentUuid": prev, "isSidechain": False,
                        "type": "assistant",
                        "message": {"id": f"resp_{uuid.uuid4().hex[:20]}",
                                    "type": "message", "role": "assistant",
                                    "model": model_label,
                                    "usage": {"input_tokens": 0, "output_tokens": 0},
                                    "content": [{"type": "text", "text": text}]},
                        "apiBlockIndex": 0, "uuid": str(uuid.uuid4()),
                        "timestamp": iso, "userType": "local",
                        "cwd": cwd, "sessionId": session_id,
                        "version": CLAUDE_VERSION, "gitBranch": "HEAD",
                    }
            elif kind == "tool_use":
                entry = {
                    "parentUuid": prev, "isSidechain": False,
                    "type": "assistant",
                    "message": {"id": f"resp_{uuid.uuid4().hex[:20]}",
                                "type": "message", "role": "assistant",
                                "model": model_label,
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                                "content": [{"type": "tool_use",
                                             "id": payload["id"],
                                             "name": payload["name"],
                                             "input": payload["input"]}]},
                    "apiBlockIndex": 0, "uuid": str(uuid.uuid4()),
                    "timestamp": iso, "userType": "local",
                    "cwd": cwd, "sessionId": session_id,
                    "version": CLAUDE_VERSION, "gitBranch": "HEAD",
                }
            else:  # tool_result
                entry = {
                    "parentUuid": prev, "isSidechain": False,
                    "promptId": str(uuid.uuid4()), "type": "user",
                    "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": payload["id"],
                         "content": payload["output"], "is_error": payload.get("is_error", False)}]},
                    "uuid": str(uuid.uuid4()), "timestamp": iso,
                    "toolUseResult": {"stdout": payload["output"]},
                    "userType": "local", "cwd": cwd,
                    "sessionId": session_id, "version": CLAUDE_VERSION, "gitBranch": "HEAD",
                }
            prev = entry["uuid"]
            _write_claude_entry(f, entry)
            n += 1
    return out_jsonl, n, session_id


# --------------------------------------------------------------------------
# Claude Code JSONL -> ZCode SQLite
# --------------------------------------------------------------------------

def _z_session_exists(zdb, sid):
    con = sqlite3.connect(zdb)
    try:
        row = con.execute("SELECT 1 FROM session WHERE id=?", (sid,)).fetchone()
        return row is not None
    finally:
        con.close()


def c2z_write(zdb, sid, title, path, jsonl_path):
    """把一个 Claude JSONL 会话写进 ZCode 库。目标会话已存在则先删除旧消息。"""
    # 读取 Claude 消息, 规整角色与内容
    msgs = []  # (order, role, ts_ms, content_xml_like_dict, meta)
    order = 0
    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = e.get("type")
            m = e.get("message") or {}
            role = m.get("role", "")
            cont_raw = m.get("content")
            ts = parse_ts(e.get("timestamp"))
            cwd = e.get("cwd") or path or "."
            if t == "tool_result" and role == "user" and isinstance(cont_raw, list):
                content = cont_raw
            elif isinstance(cont_raw, str):
                content = cont_raw
            elif isinstance(cont_raw, list):
                content = cont_raw
            else:
                content = ""
            msgs.append([order, role, ts, content, {"cwd": cwd, "modelId": "claude"}])
            order += 1

    con = sqlite3.connect(zdb)
    con.text_factory = str
    cur = con.cursor()
    try:
        cur.execute("BEGIN")
        # 删除目标会话旧数据
        cur.execute("DELETE FROM part WHERE session_id=?", (sid,))
        cur.execute("DELETE FROM message WHERE session_id=?", (sid,))
        cur.execute("DELETE FROM session WHERE id=?", (sid,))
        # 重建 session（严格遵守 NOT NULL：id/project_id/slug/title/time_created/time_updated）
        pid_from_path = "proj_" + re.sub(r"[^a-zA-Z0-9]", "-", path or "unknown").strip("-").lower()
        cur.execute("""
            INSERT INTO session (id, project_id, slug, directory, path, title,
                version, permission, time_created, time_updated,
                task_type, title_source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (sid, pid_from_path, sid, path, path, title,
              "0.16.9", '{"mode":"yolo"}',
              msgs[0][2] if msgs else 0, msgs[-1][2] if msgs else 0,
              "interactive", "custom"))
        # 重建 message + part
        for order, role, ts, content, meta in msgs:
            mid = f"msg_{uuid.uuid4().hex[:20]}"
            mdata = {
                "role": role,
                "time": {"created": ts, "completed": ts},
                "path": {"cwd": meta["cwd"], "root": meta["cwd"]},
                "agent": "claude-code-sync",
                "modelId": meta["modelId"],
            }
            if role == "user":
                mdata["semantics"] = {"origin": "real_user", "kind": "user_prompt",
                                      "uiVisibility": "visible", "providerVisibility": "visible",
                                      "transcriptVisibility": "visible"}
            else:
                mdata["semantics"] = {"origin": "assistant"}
            cur.execute("""INSERT INTO message (id, session_id, time_created, time_updated, sequence, data)
                           VALUES (?,?,?,?,?,?)""",
                        (mid, sid, ts, ts, order, json.dumps(mdata, ensure_ascii=False)))
            if isinstance(content, str):
                part_type = "text" if role != "tool" else "text"
                pdata = {"type": part_type, "text": content, "time": {"created": ts}}
                cur.execute("""INSERT INTO part (id, message_id, session_id, time_created, time_updated, sequence, data)
                               VALUES (?,?,?,?,?,?,?)""",
                            (f"part_{uuid.uuid4().hex[:18]}", mid, sid, ts, ts, 0, json.dumps(pdata, ensure_ascii=False)))
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    ctype = c.get("type")
                    if ctype == "tool_use":
                        pdata = {"type": "tool", "callID": c.get("id", f"call_{uuid.uuid4().hex[:22]}"),
                                 "declarationIndex": 0, "tool": c.get("name", ""),
                                 "state": {"status": "completed", "input": c.get("input") or {}}}
                    elif ctype == "tool_result":
                        pdata = {"type": "text",
                                 "text": c.get("content", ""),
                                 "synthetic": True,
                                 "time": {"created": ts}}
                    elif ctype == "text":
                        pdata = {"type": "text", "text": c.get("text", ""), "time": {"created": ts}}
                    elif ctype == "thinking":
                        pdata = {"type": "reasoning", "text": c.get("thinking", ""), "time": {"created": ts}}
                    else:
                        continue
                    cur.execute("""INSERT INTO part (id, message_id, session_id, time_created, time_updated, sequence, data)
                                   VALUES (?,?,?,?,?,?,?)""",
                                (f"part_{uuid.uuid4().hex[:18]}", mid, sid, ts, ts, len(content), json.dumps(pdata, ensure_ascii=False)))
        cur.execute("COMMIT")
        return len(msgs)
    except Exception:
        cur.execute("ROLLBACK")
        raise