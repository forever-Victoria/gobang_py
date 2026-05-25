"""历史对局回放存储.

使用 SQLite 存储, moves 以 JSON 文本形式持久化。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import json
import sqlite3

from src.server.db import GobangDB


class ReplayStore:
    def __init__(self, store_path: str, db: GobangDB) -> None:
        self._path = store_path  # 兼容旧路径, 仅用于迁移
        self._lock = threading.RLock()
        self._db = db

    def _con(self) -> sqlite3.Connection:
        return self._db.connect()

    def add_replay(self, payload: Dict[str, Any]) -> int:
        with self._lock:
            players = payload.get("players", []) or []
            black = str(players[0]) if len(players) >= 1 else "?"
            white = str(players[1]) if len(players) >= 2 else "?"
            con = self._con()
            try:
                cur = con.execute(
                    """
                    INSERT INTO replays(created_at, room_id, black_user, white_user, winner, result, started_at, ended_at, moves_json)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(time.time()),
                        int(payload.get("room_id", 0) or 0),
                        black,
                        white,
                        int(payload.get("winner", 0) or 0),
                        str(payload.get("result", "normal")),
                        int(payload.get("started_at", 0) or 0),
                        int(payload.get("ended_at", 0) or 0),
                        json.dumps(payload.get("moves", []) or [], ensure_ascii=False),
                    ),
                )
                con.commit()
                return int(cur.lastrowid)
            finally:
                con.close()

    def list_replays(self, username: Optional[str] = None, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self._lock:
            con = self._con()
            try:
                if username:
                    total = con.execute(
                        "SELECT COUNT(1) AS c FROM replays WHERE black_user=? OR white_user=?",
                        (username, username),
                    ).fetchone()["c"]
                    rows = con.execute(
                        """
                        SELECT replay_id, black_user, white_user, winner, result, ended_at, moves_json
                        FROM replays
                        WHERE black_user=? OR white_user=?
                        ORDER BY replay_id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (username, username, limit, offset),
                    ).fetchall()
                else:
                    total = con.execute("SELECT COUNT(1) AS c FROM replays").fetchone()["c"]
                    rows = con.execute(
                        """
                        SELECT replay_id, black_user, white_user, winner, result, ended_at, moves_json
                        FROM replays
                        ORDER BY replay_id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    ).fetchall()
                items = []
                for r in rows:
                    try:
                        moves = json.loads(r["moves_json"]) if r["moves_json"] else []
                    except Exception:
                        moves = []
                    items.append({
                        "replay_id": int(r["replay_id"]),
                        "players": [str(r["black_user"]), str(r["white_user"])],
                        "winner": int(r["winner"]),
                        "result": str(r["result"]),
                        "ended_at": int(r["ended_at"]),
                        "move_count": len(moves),
                    })
                return {"total": int(total), "items": items}
            finally:
                con.close()

    def get_replay(self, replay_id: int) -> Optional[Dict[str, Any]]:
        rid = int(replay_id)
        with self._lock:
            con = self._con()
            try:
                r = con.execute(
                    """
                    SELECT replay_id, created_at, room_id, black_user, white_user, winner, result, started_at, ended_at, moves_json
                    FROM replays WHERE replay_id=?
                    """,
                    (rid,),
                ).fetchone()
                if r is None:
                    return None
                try:
                    moves = json.loads(r["moves_json"]) if r["moves_json"] else []
                except Exception:
                    moves = []
                return {
                    "replay_id": int(r["replay_id"]),
                    "created_at": int(r["created_at"]),
                    "room_id": int(r["room_id"]),
                    "players": [str(r["black_user"]), str(r["white_user"])],
                    "winner": int(r["winner"]),
                    "result": str(r["result"]),
                    "started_at": int(r["started_at"]),
                    "ended_at": int(r["ended_at"]),
                    "moves": moves,
                }
            finally:
                con.close()
