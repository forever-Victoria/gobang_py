"""用户账号管理.

优先使用 SQLite (标准库 sqlite3, data/gobang.db), 并支持从旧版 users.json 自动迁移。
密码使用 SHA-256 加盐存储, 不会明文保存到磁盘.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from typing import Any, Dict, Optional

from src.server.db import GobangDB


class UserManager:
    """用户表 (SQLite 优先, 兼容旧 JSON)."""

    def __init__(self, store_path: str, db: Optional[GobangDB] = None) -> None:
        self._path = store_path  # 兼容旧路径, 用于迁移
        self._lock = threading.RLock()
        self._db = db

    def _con(self) -> sqlite3.Connection:
        assert self._db is not None
        return self._db.connect()

    # ------------------------- 工具 -------------------------
    @staticmethod
    def _hash(password: str, salt: str) -> str:
        h = hashlib.sha256()
        h.update(salt.encode("utf-8"))
        h.update(password.encode("utf-8"))
        return h.hexdigest()

    @staticmethod
    def _valid_name(name: str) -> bool:
        if not isinstance(name, str):
            return False
        if not (1 <= len(name) <= 20):
            return False
        return all(ch.isalnum() or ch in "_-" for ch in name)

    # ------------------------- 对外接口 -------------------------
    def register(self, username: str, password: str) -> Optional[str]:
        """返回 None 表示成功, 否则返回失败原因字符串."""
        if not self._valid_name(username):
            return "用户名只能包含字母数字下划线和连字符, 长度 1-20"
        if not isinstance(password, str) or not (1 <= len(password) <= 64):
            return "密码长度必须在 1-64 之间"
        with self._lock:
            salt = secrets.token_hex(8)
            con = self._con()
            try:
                # uid: 取 max(uid)+1, 保持与旧实现一致且可控
                row = con.execute("SELECT COALESCE(MAX(uid),0)+1 AS next_uid FROM users").fetchone()
                next_uid = int(row["next_uid"]) if row else 1
                try:
                    con.execute(
                        """
                        INSERT INTO users(uid, username, salt, pwd, score, total, win)
                        VALUES(?,?,?,?,1000,0,0)
                        """,
                        (next_uid, username, salt, self._hash(password, salt)),
                    )
                    con.commit()
                except sqlite3.IntegrityError:
                    return "用户名已被占用"
            finally:
                con.close()
        return None

    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """校验成功返回用户信息字典 (含 uid/username/score/total/win), 失败返回 None."""
        with self._lock:
            con = self._con()
            try:
                row = con.execute(
                    "SELECT uid, username, salt, pwd, score, total, win FROM users WHERE username=?",
                    (username,),
                ).fetchone()
                if row is None:
                    return None
                if self._hash(password, str(row["salt"])) != str(row["pwd"]):
                    return None
                return {
                    "uid": int(row["uid"]),
                    "username": str(row["username"]),
                    "score": int(row["score"]),
                    "total": int(row["total"]),
                    "win": int(row["win"]),
                }
            finally:
                con.close()

    def update_result(self, username: str, win: bool, draw: bool = False) -> None:
        """对局结束更新 score / total / win 三个字段."""
        with self._lock:
            con = self._con()
            try:
                row = con.execute("SELECT score, total, win FROM users WHERE username=?", (username,)).fetchone()
                if row is None:
                    return
                score = int(row["score"])
                total = int(row["total"]) + 1
                w = int(row["win"])
                if draw:
                    pass
                elif win:
                    w += 1
                    score += 30
                else:
                    score = max(0, score - 30)
                con.execute(
                    "UPDATE users SET score=?, total=?, win=? WHERE username=?",
                    (score, total, w, username),
                )
                con.commit()
            finally:
                con.close()

    def get_info(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            con = self._con()
            try:
                row = con.execute(
                    "SELECT uid, username, score, total, win FROM users WHERE username=?",
                    (username,),
                ).fetchone()
                if row is None:
                    return None
                return {
                    "uid": int(row["uid"]),
                    "username": str(row["username"]),
                    "score": int(row["score"]),
                    "total": int(row["total"]),
                    "win": int(row["win"]),
                }
            finally:
                con.close()

    def get_top_n(self, n: int = 20) -> list[Dict[str, Any]]:
        n = max(1, min(int(n), 100))
        with self._lock:
            con = self._con()
            try:
                rows = con.execute(
                    "SELECT username, score, total, win FROM users ORDER BY score DESC, win DESC, username ASC LIMIT ?",
                    (n,),
                ).fetchall()
                return [{
                    "username": str(r["username"]),
                    "score": int(r["score"]),
                    "total": int(r["total"]),
                    "win": int(r["win"]),
                } for r in rows]
            finally:
                con.close()
