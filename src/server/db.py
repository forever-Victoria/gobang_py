"""SQLite 数据库封装与初始化/迁移工具.

只使用 Python 标准库 sqlite3, 面向课程作业“零依赖可运行”。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, Optional


class GobangDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        return con

    def _init_schema(self) -> None:
        with self._lock:
            con = self.connect()
            try:
                con.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                      uid INTEGER PRIMARY KEY,
                      username TEXT NOT NULL UNIQUE,
                      salt TEXT NOT NULL,
                      pwd TEXT NOT NULL,
                      score INTEGER NOT NULL DEFAULT 1000,
                      total INTEGER NOT NULL DEFAULT 0,
                      win INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS replays (
                      replay_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      created_at INTEGER NOT NULL,
                      room_id INTEGER NOT NULL,
                      black_user TEXT NOT NULL,
                      white_user TEXT NOT NULL,
                      winner INTEGER NOT NULL,
                      result TEXT NOT NULL,
                      started_at INTEGER NOT NULL,
                      ended_at INTEGER NOT NULL,
                      moves_json TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_users_score ON users(score DESC, win DESC);
                    CREATE INDEX IF NOT EXISTS idx_replays_players ON replays(black_user, white_user, ended_at DESC);
                    """
                )
                con.commit()
            finally:
                con.close()

    def has_any_user(self) -> bool:
        con = self.connect()
        try:
            row = con.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            return row is not None
        finally:
            con.close()

    def migrate_from_json_if_needed(self, users_json_path: str, replays_json_path: str) -> None:
        """当 DB 为空时, 从 JSON 导入(若存在). 迁移是幂等的: 仅在 users 表无数据时执行。"""
        with self._lock:
            if self.has_any_user():
                return

            if os.path.exists(users_json_path):
                try:
                    with open(users_json_path, "r", encoding="utf-8") as f:
                        obj = json.load(f)
                    users = obj.get("users", {}) or {}
                except Exception:
                    users = {}
            else:
                users = {}

            # replays.json 可能不存在(旧版本); 只有存在才导入
            if os.path.exists(replays_json_path):
                try:
                    with open(replays_json_path, "r", encoding="utf-8") as f:
                        robj = json.load(f)
                    replay_items = list(robj.get("items", []) or [])
                except Exception:
                    replay_items = []
            else:
                replay_items = []

            con = self.connect()
            try:
                con.execute("BEGIN")
                for username, rec in users.items():
                    con.execute(
                        """
                        INSERT OR IGNORE INTO users(uid, username, salt, pwd, score, total, win)
                        VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            int(rec.get("uid", 0) or 0),
                            str(username),
                            str(rec.get("salt", "")),
                            str(rec.get("pwd", "")),
                            int(rec.get("score", 1000)),
                            int(rec.get("total", 0)),
                            int(rec.get("win", 0)),
                        ),
                    )

                for item in replay_items:
                    players = item.get("players", []) or []
                    black = str(players[0]) if len(players) >= 1 else "?"
                    white = str(players[1]) if len(players) >= 2 else "?"
                    con.execute(
                        """
                        INSERT INTO replays(created_at, room_id, black_user, white_user, winner, result, started_at, ended_at, moves_json)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(item.get("created_at", item.get("ended_at", 0) or 0)),
                            int(item.get("room_id", 0) or 0),
                            black,
                            white,
                            int(item.get("winner", 0) or 0),
                            str(item.get("result", "normal")),
                            int(item.get("started_at", 0) or 0),
                            int(item.get("ended_at", 0) or 0),
                            json.dumps(item.get("moves", []) or [], ensure_ascii=False),
                        ),
                    )

                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

