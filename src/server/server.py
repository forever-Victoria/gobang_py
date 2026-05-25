"""GoBang TCP 服务器主程序.

设计要点:
- 每个客户端连接一个工作线程 (threading.Thread, daemon=True) 处理 I/O
- 全局共享状态 (UserManager / RoomManager / Matcher / 在线表) 用锁保护
- 服务端是权威状态: 棋盘、轮次、胜负、分数全部由服务端裁决
- 所有 socket 写操作通过 Connection.send_safe(), 内部上锁串行化
- 关键事件 (连接/登录/匹配/落子/胜负/异常/断线) 全部写入 logs/server.log
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

# 允许 "python -m src.server.server" 以及 "python src/server/server.py" 两种启动方式
if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.common.protocol import (
    Frame,
    MsgType,
    ProtocolError,
    recv_frame,
    send_frame,
)
from src.server.ai_player import GobangAI
from src.server.matcher import Matcher
from src.server.db import GobangDB
from src.server.replay_store import ReplayStore
from src.server.room import BOARD_SIZE, BLACK, WHITE, Room, RoomManager
from src.server.user_manager import UserManager


RECONNECT_WINDOW_SEC = 60
AI_PREFIX = "AI-Bot#"


# ============================================================
# 日志
# ============================================================

def _setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log = logging.getLogger("gobang.server")
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return log
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(
        os.path.join(log_dir, "server.log"),
        maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    return log


# ============================================================
# 客户端连接对象
# ============================================================

class Connection:
    """封装单个 TCP 客户端的状态."""

    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr  # (ip, port)
        self.username: Optional[str] = None
        self.uid: Optional[int] = None
        self._send_lock = threading.Lock()
        self.alive = True

    def send_safe(self, msg_type: MsgType, data: Optional[Dict[str, Any]] = None, seq: int = 0) -> bool:
        """加锁串行化写操作. 出错则标记 alive=False 并返回 False."""
        if not self.alive:
            return False
        try:
            with self._send_lock:
                send_frame(self.sock, msg_type, data, seq)
            return True
        except (OSError, ConnectionError, ProtocolError):
            self.alive = False
            return False

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ============================================================
# 服务器
# ============================================================

class GoBangServer:
    def __init__(self, host: str, port: int, data_dir: str, log_dir: str) -> None:
        self.host = host
        self.port = port
        self.data_dir = data_dir
        self.log_dir = log_dir
        self.log = _setup_logging(log_dir)

        db_path = os.path.join(data_dir, "gobang.db")
        self.db = GobangDB(db_path)
        # 兼容旧版 json 文件，首次启动自动迁移
        self.db.migrate_from_json_if_needed(
            users_json_path=os.path.join(data_dir, "users.json"),
            replays_json_path=os.path.join(data_dir, "replays.json"),
        )
        self.users = UserManager(os.path.join(data_dir, "users.json"), db=self.db)
        self.replays = ReplayStore(os.path.join(data_dir, "replays.json"), db=self.db)
        self.rooms = RoomManager()
        self.matcher = Matcher(on_match=self._on_match)
        self.ai = GobangAI()

        # 在线表: username -> Connection
        self._online: Dict[str, Connection] = {}
        self._online_lock = threading.RLock()
        self._pending_reconnect: Dict[str, Dict[str, Any]] = {}
        self._pending_lock = threading.RLock()
        self._reconnect_thread: Optional[threading.Thread] = None
        self._ai_lock = threading.RLock()
        self._ai_seq = 0

        self._server_sock: Optional[socket.socket] = None
        self._stop_evt = threading.Event()
        self._started_at = time.time()
        self._listening = False
        self._startup_error = ""
        self._stats_lock = threading.RLock()
        self._total_connections = 0
        self._current_connections = 0
        self._total_messages = 0

    # ------------------------------ 生命周期 ------------------------------
    def start(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        self.matcher.start()
        self._reconnect_thread = threading.Thread(target=self._reconnect_watchdog_loop, name="reconnect-watchdog", daemon=True)
        self._reconnect_thread.start()

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server_sock.bind((self.host, self.port))
            self._server_sock.listen(64)
        except OSError as e:
            self._startup_error = str(e)
            self._stop_evt.set()
            self.log.error("GoBang server failed to listen on %s:%d: %s", self.host, self.port, e)
            try:
                self._server_sock.close()
            except OSError:
                pass
            return
        self._started_at = time.time()
        self._listening = True
        self.log.info("GoBang server listening on %s:%d", self.host, self.port)

        try:
            while not self._stop_evt.is_set():
                try:
                    self._server_sock.settimeout(1.0)
                    cli_sock, addr = self._server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                cli_sock.settimeout(None)
                with self._stats_lock:
                    self._total_connections += 1
                    self._current_connections += 1
                conn = Connection(cli_sock, addr)
                t = threading.Thread(
                    target=self._handle_client,
                    args=(conn,),
                    name=f"cli-{addr[0]}:{addr[1]}",
                    daemon=True,
                )
                t.start()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._stop_evt.is_set():
            return
        self._stop_evt.set()
        self.log.info("server shutting down...")
        self.matcher.stop()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
        self._listening = False
        with self._online_lock:
            conns = list(self._online.values())
        for c in conns:
            c.close()

    # ------------------------------ 单客户端循环 ------------------------------
    def _handle_client(self, conn: Connection) -> None:
        self.log.info("client connected: %s:%d", conn.addr[0], conn.addr[1])
        try:
            while conn.alive and not self._stop_evt.is_set():
                try:
                    frame = recv_frame(conn.sock)
                except ProtocolError as e:
                    self.log.warning("[%s] protocol error: %s", conn.username or conn.addr, e)
                    conn.send_safe(MsgType.S2C_ERROR, {"code": "BAD_FRAME", "reason": str(e)})
                    break
                except (ConnectionError, OSError) as e:
                    self.log.info("[%s] disconnected: %s", conn.username or conn.addr, e)
                    break
                with self._stats_lock:
                    self._total_messages += 1
                try:
                    self._dispatch(conn, frame)
                except Exception:  # 兜底, 不让一条非法消息打死整条连接
                    self.log.exception("[%s] dispatch error", conn.username or conn.addr)
                    conn.send_safe(MsgType.S2C_ERROR,
                                   {"code": "INTERNAL", "reason": "服务端内部异常"},
                                   seq=frame.seq)
        finally:
            self._on_disconnect(conn)

    def _on_disconnect(self, conn: Connection) -> None:
        conn.close()
        with self._stats_lock:
            self._current_connections = max(0, self._current_connections - 1)
        if conn.username:
            self.log.info("user offline: %s", conn.username)
            self.matcher.remove(conn.username)
            with self._online_lock:
                if self._online.get(conn.username) is conn:
                    self._online.pop(conn.username, None)
            # 玩家断线进入待重连; 观战者直接移除
            room = self.rooms.get_room_of(conn.username)
            if room is not None and not room.over:
                deadline = int(time.time() + RECONNECT_WINDOW_SEC)
                with self._pending_lock:
                    self._pending_reconnect[conn.username] = {
                        "room_id": room.room_id,
                        "deadline": deadline,
                    }
                self.log.info("user pending reconnect: user=%s room=%d deadline=%d", conn.username, room.room_id, deadline)
                self._broadcast_room(room, MsgType.S2C_CHAT_BCAST, {
                    "from": "system",
                    "text": f"{conn.username} 断线，等待 {RECONNECT_WINDOW_SEC} 秒重连...",
                })
            self.rooms.remove_observer(conn.username)
            self._broadcast_lobby()

    # ------------------------------ 路由 ------------------------------
    def _dispatch(self, conn: Connection, f: Frame) -> None:
        t = f.type
        # 登录前只允许 4 种消息
        if conn.username is None and t not in (
            MsgType.C2S_REGISTER, MsgType.C2S_LOGIN, MsgType.C2S_PING, MsgType.C2S_LOGOUT,
        ):
            conn.send_safe(MsgType.S2C_ERROR, {"code": "NOT_LOGIN", "reason": "请先登录"}, seq=f.seq)
            return

        if t == MsgType.C2S_REGISTER:
            self._handle_register(conn, f)
        elif t == MsgType.C2S_LOGIN:
            self._handle_login(conn, f)
        elif t == MsgType.C2S_LOGOUT:
            self._handle_logout(conn, f)
        elif t == MsgType.C2S_PING:
            conn.send_safe(MsgType.S2C_PONG, {}, seq=f.seq)
        elif t == MsgType.C2S_MATCH_START:
            self._handle_match_start(conn, f)
        elif t == MsgType.C2S_MATCH_STOP:
            self._handle_match_stop(conn, f)
        elif t == MsgType.C2S_AI_MATCH_START:
            self._handle_ai_match_start(conn, f)
        elif t == MsgType.C2S_MOVE:
            self._handle_move(conn, f)
        elif t == MsgType.C2S_CHAT:
            self._handle_chat(conn, f)
        elif t == MsgType.C2S_LEAVE_ROOM:
            self._handle_leave_room(conn, f)
        elif t == MsgType.C2S_SPECTATE_LIST:
            self._handle_spectate_list(conn, f)
        elif t == MsgType.C2S_SPECTATE_JOIN:
            self._handle_spectate_join(conn, f)
        elif t == MsgType.C2S_RECONNECT_RESUME:
            self._handle_reconnect_resume(conn, f)
        elif t == MsgType.C2S_REPLAY_LIST:
            self._handle_replay_list(conn, f)
        elif t == MsgType.C2S_REPLAY_GET:
            self._handle_replay_get(conn, f)
        elif t == MsgType.C2S_RANK_LIST:
            self._handle_rank_list(conn, f)
        else:
            conn.send_safe(MsgType.S2C_ERROR,
                           {"code": "BAD_TYPE", "reason": f"未知消息类型: {int(t)}"},
                           seq=f.seq)

    # ------------------------------ 业务处理 ------------------------------
    def _handle_register(self, conn: Connection, f: Frame) -> None:
        username = str(f.data.get("username", "")).strip()
        password = str(f.data.get("password", ""))
        err = self.users.register(username, password)
        if err is None:
            self.log.info("register ok: %s from %s", username, conn.addr)
            conn.send_safe(MsgType.S2C_REGISTER_RESP, {"ok": True}, seq=f.seq)
        else:
            self.log.info("register fail: %s reason=%s", username, err)
            conn.send_safe(MsgType.S2C_REGISTER_RESP, {"ok": False, "reason": err}, seq=f.seq)

    def _handle_login(self, conn: Connection, f: Frame) -> None:
        if conn.username is not None:
            conn.send_safe(MsgType.S2C_LOGIN_RESP,
                           {"ok": False, "reason": "本连接已登录"}, seq=f.seq)
            return
        username = str(f.data.get("username", "")).strip()
        password = str(f.data.get("password", ""))
        info = self.users.login(username, password)
        if info is None:
            self.log.info("login fail: %s from %s", username, conn.addr)
            conn.send_safe(MsgType.S2C_LOGIN_RESP,
                           {"ok": False, "reason": "用户名或密码错误"}, seq=f.seq)
            return
        with self._online_lock:
            if username in self._online:
                conn.send_safe(MsgType.S2C_LOGIN_RESP,
                               {"ok": False, "reason": "该用户已在别处登录"}, seq=f.seq)
                self.log.info("login refused (already online): %s", username)
                return
            self._online[username] = conn
        conn.username = username
        conn.uid = info["uid"]
        self.log.info("login ok: %s (uid=%d) from %s", username, conn.uid, conn.addr)
        conn.send_safe(MsgType.S2C_LOGIN_RESP, {"ok": True, **info}, seq=f.seq)
        self._resume_if_pending(conn)
        self._broadcast_lobby()

    def _handle_logout(self, conn: Connection, f: Frame) -> None:
        # 主动登出 == 主动断开
        conn.alive = False
        try:
            conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _handle_match_start(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        self.rooms.remove_observer(conn.username)
        if self.rooms.get_room_of(conn.username) is not None:
            conn.send_safe(MsgType.S2C_ERROR,
                           {"code": "IN_ROOM", "reason": "你已在房间中, 无法重复匹配"},
                           seq=f.seq)
            return
        added = self.matcher.add(conn.username)
        if added:
            self.log.info("match enqueue: %s (queue=%d)", conn.username, self.matcher.size())
        self._broadcast_lobby()

    def _handle_match_stop(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        removed = self.matcher.remove(conn.username)
        if removed:
            self.log.info("match cancel: %s", conn.username)
        self._broadcast_lobby()

    def _handle_ai_match_start(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        self.rooms.remove_observer(conn.username)
        self.matcher.remove(conn.username)
        if self.rooms.get_room_of(conn.username) is not None:
            conn.send_safe(MsgType.S2C_ERROR,
                           {"code": "IN_ROOM", "reason": "你已在房间中, 无法开始 AI 对战"},
                           seq=f.seq)
            return

        ai_name = self._new_ai_name()
        room = self.rooms.create_room_fixed(conn.username, ai_name)
        self.log.info("ai room created: id=%d human=%s black=%s white=%s",
                      room.room_id, conn.username, room.black_user, room.white_user)
        conn.send_safe(MsgType.S2C_MATCH_OK, {
            "room_id": room.room_id,
            "board_size": BOARD_SIZE,
            "you": conn.username,
            "opponent": {
                "username": ai_name,
                "score": 0,
            },
            "your_color": BLACK,
            "turn_color": room.turn,
        }, seq=f.seq)
        with room.lock:
            room.chat_log.append({"from": "system", "text": "AI 对战开始，玩家执黑先行。"})
        conn.send_safe(MsgType.S2C_CHAT_BCAST, {"from": "system", "text": "AI 对战开始，玩家执黑先行。"})
        self._broadcast_lobby()

    def _handle_move(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        room = self.rooms.get_room_of(conn.username)
        if room is None:
            conn.send_safe(MsgType.S2C_ERROR,
                           {"code": "NO_ROOM", "reason": "你不在任何房间中"}, seq=f.seq)
            return
        with self._pending_lock:
            if conn.username in self._pending_reconnect:
                self._pending_reconnect.pop(conn.username, None)
        try:
            row = int(f.data.get("row"))
            col = int(f.data.get("col"))
        except (TypeError, ValueError):
            conn.send_safe(MsgType.S2C_ERROR,
                           {"code": "BAD_ARG", "reason": "row/col 必须为整数"}, seq=f.seq)
            return

        ok, reason, color, winner = room.place(conn.username, row, col)
        if not ok:
            # 仅给该玩家回错误, 不广播
            conn.send_safe(MsgType.S2C_MOVE_RESULT,
                           {"ok": False, "row": row, "col": col, "reason": reason},
                           seq=f.seq)
            self.log.info("move reject: %s @ (%d,%d) reason=%s", conn.username, row, col, reason)
            return

        next_turn = 0 if winner else room.turn
        result_pkt = {
            "ok": True,
            "row": row, "col": col, "color": color,
            "next_turn": next_turn,
            "winner": winner,
        }
        self.log.info("move ok: room=%d %s @ (%d,%d) color=%d winner=%d",
                      room.room_id, conn.username, row, col, color, winner)
        self._broadcast_room(room, MsgType.S2C_MOVE_RESULT, result_pkt)

        if winner:  # 1=黑胜, 2=白胜, 3=平局
            self._finish_room(room, winner)
            return
        self._maybe_ai_move(room)

    def _handle_chat(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        room = self.rooms.get_room_of(conn.username)
        if room is None:
            conn.send_safe(MsgType.S2C_ERROR,
                           {"code": "NO_ROOM", "reason": "你不在任何房间中"}, seq=f.seq)
            return
        text = str(f.data.get("text", "")).strip()
        if not text:
            return
        if len(text) > 200:
            text = text[:200] + "..."
        self.log.info("chat: room=%d %s: %s", room.room_id, conn.username, text)
        with room.lock:
            room.chat_log.append({"from": conn.username, "text": text})
        self._broadcast_room(room, MsgType.S2C_CHAT_BCAST,
                             {"from": conn.username, "text": text})

    def _handle_leave_room(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        room = self.rooms.get_room_of(conn.username)
        if room is None:
            return
        self._end_room_by_abort(room, leaver=conn.username)

    def _handle_spectate_list(self, conn: Connection, f: Frame) -> None:
        rooms = self.rooms.list_active_rooms()
        payload = {"rooms": [self._room_summary(r) for r in rooms]}
        conn.send_safe(MsgType.S2C_SPECTATE_LIST, payload, seq=f.seq)

    def _handle_spectate_join(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        if self.rooms.get_room_of(conn.username) is not None:
            conn.send_safe(MsgType.S2C_ERROR, {"code": "IN_ROOM", "reason": "对局中不能观战"}, seq=f.seq)
            return
        try:
            room_id = int(f.data.get("room_id"))
        except (TypeError, ValueError):
            conn.send_safe(MsgType.S2C_ERROR, {"code": "BAD_ARG", "reason": "room_id 必须为整数"}, seq=f.seq)
            return
        room = self.rooms.add_observer(room_id, conn.username)
        if room is None:
            conn.send_safe(MsgType.S2C_ERROR, {"code": "NO_SUCH_ROOM", "reason": "房间不存在"}, seq=f.seq)
            return
        conn.send_safe(MsgType.S2C_SPECTATE_SNAPSHOT, self._room_snapshot(room), seq=f.seq)

    def _handle_reconnect_resume(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        requested = f.data.get("room_id")
        try:
            room_id = int(requested) if requested is not None else None
        except (TypeError, ValueError):
            room_id = None
        self._resume_if_pending(conn, seq=f.seq, room_id=room_id)

    def _handle_replay_list(self, conn: Connection, f: Frame) -> None:
        assert conn.username is not None
        try:
            limit = int(f.data.get("limit", 20))
            offset = int(f.data.get("offset", 0))
        except (TypeError, ValueError):
            limit, offset = 20, 0
        payload = self.replays.list_replays(username=conn.username, limit=limit, offset=offset)
        conn.send_safe(MsgType.S2C_REPLAY_LIST, payload, seq=f.seq)

    def _handle_replay_get(self, conn: Connection, f: Frame) -> None:
        try:
            replay_id = int(f.data.get("replay_id"))
        except (TypeError, ValueError):
            conn.send_safe(MsgType.S2C_REPLAY_DATA, {"ok": False, "reason": "replay_id 无效"}, seq=f.seq)
            return
        item = self.replays.get_replay(replay_id)
        if item is None:
            conn.send_safe(MsgType.S2C_REPLAY_DATA, {"ok": False, "reason": "回放不存在"}, seq=f.seq)
            return
        conn.send_safe(MsgType.S2C_REPLAY_DATA, {"ok": True, "replay": item}, seq=f.seq)

    def _handle_rank_list(self, conn: Connection, f: Frame) -> None:
        try:
            limit = int(f.data.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        items = self.users.get_top_n(limit)
        rows = []
        rank = 1
        for x in items:
            rows.append({"rank": rank, **x})
            rank += 1
        conn.send_safe(MsgType.S2C_RANK_LIST, {"items": rows}, seq=f.seq)

    # ------------------------------ 匹配回调 ------------------------------
    def _on_match(self, user_a: str, user_b: str) -> None:
        with self._online_lock:
            ca = self._online.get(user_a)
            cb = self._online.get(user_b)
        # 任一掉线: 把另一个重新塞回队列
        if ca is None or not ca.alive:
            if cb is not None and cb.alive:
                self.matcher.add(user_b)
            return
        if cb is None or not cb.alive:
            self.matcher.add(user_a)
            return

        room = self.rooms.create_room(user_a, user_b)
        self.log.info("room created: id=%d black=%s white=%s",
                      room.room_id, room.black_user, room.white_user)

        info_a = self.users.get_info(user_a) or {"score": 0}
        info_b = self.users.get_info(user_b) or {"score": 0}
        for me, opp in ((ca, cb), (cb, ca)):
            my_color = room.color_of(me.username or "")
            opp_info = info_b if me is ca else info_a
            me.send_safe(MsgType.S2C_MATCH_OK, {
                "room_id": room.room_id,
                "board_size": BOARD_SIZE,
                "you": me.username,
                "opponent": {
                    "username": opp.username,
                    "score": opp_info.get("score", 0),
                },
                "your_color": my_color,
                "turn_color": room.turn,
            })
        self._broadcast_lobby()

    def _new_ai_name(self) -> str:
        with self._ai_lock:
            self._ai_seq += 1
            return f"{AI_PREFIX}{self._ai_seq}"

    @staticmethod
    def _is_ai_user(username: str) -> bool:
        return username.startswith(AI_PREFIX)

    def _maybe_ai_move(self, room: Room) -> None:
        with room.lock:
            if room.over:
                return
            ai_user = room.black_user if self._is_ai_user(room.black_user) else room.white_user if self._is_ai_user(room.white_user) else ""
            if not ai_user:
                return
            ai_color = room.color_of(ai_user)
            if room.turn != ai_color:
                return
            board = [list(row) for row in room.board]

        move = self.ai.choose_move(board, ai_color)
        if move is None:
            return
        row, col = move
        ok, reason, color, winner = room.place(ai_user, row, col)
        if not ok:
            self.log.warning("ai move reject: room=%d %s @ (%d,%d) reason=%s",
                             room.room_id, ai_user, row, col, reason)
            return

        result_pkt = {
            "ok": True,
            "row": row, "col": col, "color": color,
            "next_turn": 0 if winner else room.turn,
            "winner": winner,
        }
        self.log.info("ai move ok: room=%d %s @ (%d,%d) color=%d winner=%d",
                      room.room_id, ai_user, row, col, color, winner)
        self._broadcast_room(room, MsgType.S2C_MOVE_RESULT, result_pkt)
        if winner:
            self._finish_room(room, winner)

    # ------------------------------ 房间结束 ------------------------------
    def _finish_room(self, room: Room, winner: int) -> None:
        """winner: 1=黑胜, 2=白胜, 3=平局."""
        if winner == 3:
            self.users.update_result(room.black_user, win=False, draw=True)
            self.users.update_result(room.white_user, win=False, draw=True)
            payload_black = {"reason": "和棋", "your_result": "draw"}
            payload_white = {"reason": "和棋", "your_result": "draw"}
        else:
            black_win = (winner == BLACK)
            self.users.update_result(room.black_user, win=black_win)
            self.users.update_result(room.white_user, win=not black_win)
            payload_black = {
                "reason": "五子连珠" if black_win else "对方五子连珠",
                "your_result": "win" if black_win else "lose",
            }
            payload_white = {
                "reason": "五子连珠" if not black_win else "对方五子连珠",
                "your_result": "win" if not black_win else "lose",
            }
        self._send_to_user(room.black_user, MsgType.S2C_ROOM_CLOSED, payload_black)
        self._send_to_user(room.white_user, MsgType.S2C_ROOM_CLOSED, payload_white)
        with room.lock:
            observers = list(room.observers)
        for ou in observers:
            self._send_to_user(ou, MsgType.S2C_ROOM_CLOSED, {"reason": "对局结束", "your_result": "abort"})
        self._persist_replay(room, winner=winner, result="normal")
        self.log.info("room finished: id=%d winner=%d", room.room_id, winner)
        self.rooms.destroy_room(room.room_id)
        self._broadcast_lobby()

    def _end_room_by_abort(self, room: Room, leaver: str) -> None:
        """玩家中途离开 / 掉线, 房间作为对方胜."""
        with room.lock:
            if room.over:
                self.rooms.destroy_room(room.room_id)
                return
            room.over = True
        opponent = room.opponent_of(leaver)
        if opponent is None:
            self.rooms.destroy_room(room.room_id)
            return
        self.users.update_result(leaver, win=False)
        self.users.update_result(opponent, win=True)
        self._send_to_user(leaver, MsgType.S2C_ROOM_CLOSED,
                           {"reason": "你已离开房间", "your_result": "lose"})
        self._send_to_user(opponent, MsgType.S2C_ROOM_CLOSED,
                           {"reason": "对方离开了房间", "your_result": "win"})
        with room.lock:
            observers = list(room.observers)
        for ou in observers:
            self._send_to_user(ou, MsgType.S2C_ROOM_CLOSED, {"reason": "对局中止", "your_result": "abort"})
        self._persist_replay(room, winner=room.color_of(opponent), result="abort")
        self.log.info("room aborted: id=%d leaver=%s -> %s wins",
                      room.room_id, leaver, opponent)
        self.rooms.destroy_room(room.room_id)
        self._broadcast_lobby()

    # ------------------------------ 广播工具 ------------------------------
    def _send_to_user(self, username: str, t: MsgType, data: Dict[str, Any]) -> None:
        with self._online_lock:
            conn = self._online.get(username)
        if conn is not None:
            conn.send_safe(t, data)

    def _broadcast_room(self, room: Room, t: MsgType, data: Dict[str, Any]) -> None:
        self._send_to_user(room.black_user, t, data)
        self._send_to_user(room.white_user, t, data)
        with room.lock:
            observers = list(room.observers)
        for u in observers:
            self._send_to_user(u, t, data)

    def _broadcast_lobby(self) -> None:
        with self._online_lock:
            online_names = list(self._online.keys())
        active_rooms = [self._room_summary(r) for r in self.rooms.list_active_rooms()]
        payload = {
            "online": online_names,
            "online_count": len(online_names),
            "queue_size": self.matcher.size(),
            "active_rooms": active_rooms,
        }
        with self._online_lock:
            conns = list(self._online.values())
        for c in conns:
            # 不打扰正在对局中的玩家
            if c.username and self.rooms.get_room_of(c.username) is None:
                c.send_safe(MsgType.S2C_LOBBY_INFO, payload)

    def _room_summary(self, room: Room) -> Dict[str, Any]:
        with room.lock:
            return {
                "room_id": room.room_id,
                "black": room.black_user,
                "white": room.white_user,
                "move_count": room.move_count,
                "started_at": int(room.started_at),
                "observer_count": len(room.observers),
            }

    def _room_snapshot(self, room: Room) -> Dict[str, Any]:
        with room.lock:
            board = [list(row) for row in room.board]
            moves = list(room.moves)
            chat_log = list(room.chat_log)
            return {
                "room_id": room.room_id,
                "black": room.black_user,
                "white": room.white_user,
                "board_size": BOARD_SIZE,
                "board": board,
                "turn_color": room.turn,
                "move_count": room.move_count,
                "moves": moves,
                "chat_log": chat_log,
                "ended": room.over,
            }

    def _persist_replay(self, room: Room, winner: int, result: str) -> None:
        with room.lock:
            payload = {
                "room_id": room.room_id,
                "players": [room.black_user, room.white_user],
                "winner": winner,
                "result": result,
                "started_at": int(room.started_at),
                "ended_at": int(time.time()),
                "moves": list(room.moves),
            }
        self.replays.add_replay(payload)

    def _resume_if_pending(self, conn: Connection, seq: int = 0, room_id: Optional[int] = None) -> None:
        assert conn.username is not None
        with self._pending_lock:
            pending = self._pending_reconnect.get(conn.username)
        if pending is None:
            if seq:
                conn.send_safe(MsgType.S2C_RECONNECT_RESP, {"ok": False, "reason": "没有待恢复对局"}, seq=seq)
            return
        if room_id is not None and int(pending.get("room_id")) != int(room_id):
            conn.send_safe(MsgType.S2C_RECONNECT_RESP, {"ok": False, "reason": "room_id 不匹配"}, seq=seq)
            return
        if int(time.time()) > int(pending.get("deadline", 0)):
            with self._pending_lock:
                self._pending_reconnect.pop(conn.username, None)
            conn.send_safe(MsgType.S2C_RECONNECT_RESP, {"ok": False, "reason": "重连超时"}, seq=seq)
            return
        room = self.rooms.get_room_by_id(int(pending["room_id"]))
        if room is None or room.over or room.color_of(conn.username) == 0:
            with self._pending_lock:
                self._pending_reconnect.pop(conn.username, None)
            conn.send_safe(MsgType.S2C_RECONNECT_RESP, {"ok": False, "reason": "对局不存在或已结束"}, seq=seq)
            return
        with self._pending_lock:
            self._pending_reconnect.pop(conn.username, None)
        state = self._room_snapshot(room)
        my_color = room.color_of(conn.username)
        state["your_color"] = my_color
        state["opponent"] = room.opponent_of(conn.username)
        conn.send_safe(MsgType.S2C_RECONNECT_RESP, {"ok": True, "room_state": state}, seq=seq)
        self._broadcast_room(room, MsgType.S2C_CHAT_BCAST, {
            "from": "system",
            "text": f"{conn.username} 已重连恢复对局",
        })

    def _reconnect_watchdog_loop(self) -> None:
        while not self._stop_evt.is_set():
            now = int(time.time())
            expired = []
            with self._pending_lock:
                for user, item in self._pending_reconnect.items():
                    if now > int(item.get("deadline", 0)):
                        expired.append((user, int(item.get("room_id", 0))))
            for user, room_id in expired:
                with self._pending_lock:
                    cur = self._pending_reconnect.get(user)
                    if cur is None or int(cur.get("room_id", 0)) != room_id:
                        continue
                    self._pending_reconnect.pop(user, None)
                room = self.rooms.get_room_by_id(room_id)
                if room is not None and not room.over and room.color_of(user) != 0:
                    self.log.info("reconnect timeout: user=%s room=%d", user, room_id)
                    self._end_room_by_abort(room, leaver=user)
            time.sleep(1.0)

    # ------------------------------ 监控快照 ------------------------------
    def get_monitor_snapshot(self) -> Dict[str, Any]:
        """返回服务端监控面板需要的只读状态快照。"""
        now = time.time()
        with self._stats_lock:
            stats = {
                "host": self.host,
                "port": self.port,
                "running": self._listening and not self._stop_evt.is_set(),
                "startup_error": self._startup_error,
                "started_at": int(self._started_at),
                "uptime_sec": int(now - self._started_at),
                "total_connections": self._total_connections,
                "current_connections": self._current_connections,
                "total_messages": self._total_messages,
            }

        with self._online_lock:
            online_items = list(self._online.items())
        online = []
        for username, conn in online_items:
            room = self.rooms.get_room_of(username)
            observed = self.rooms.get_observed_room(username)
            if room is not None:
                status = f"对局中 Room#{room.room_id}"
            elif observed is not None:
                status = f"观战中 Room#{observed.room_id}"
            elif username in self.matcher.snapshot():
                status = "匹配队列中"
            else:
                status = "大厅中"
            online.append({
                "username": username,
                "addr": f"{conn.addr[0]}:{conn.addr[1]}",
                "status": status,
            })

        rooms = []
        for room in self.rooms.list_active_rooms():
            with room.lock:
                rooms.append({
                    "room_id": room.room_id,
                    "black": room.black_user,
                    "white": room.white_user,
                    "turn": "黑棋" if room.turn == BLACK else "白棋",
                    "move_count": room.move_count,
                    "observer_count": len(room.observers),
                    "started_at": int(room.started_at),
                })

        db_stats = self._db_stats()
        return {
            "stats": stats,
            "online": sorted(online, key=lambda x: x["username"]),
            "queue": self.matcher.snapshot(),
            "rooms": sorted(rooms, key=lambda x: x["room_id"]),
            "db": db_stats,
        }

    def _db_stats(self) -> Dict[str, int]:
        con = self.db.connect()
        try:
            user_count = con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            replay_count = con.execute("SELECT COUNT(*) AS n FROM replays").fetchone()["n"]
            return {"user_count": int(user_count), "replay_count": int(replay_count)}
        finally:
            con.close()


# ============================================================
# 入口
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="GoBang TCP Server (custom protocol GBP/1)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9527, help="监听端口 (默认 9527)")
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    parser.add_argument("--data-dir", default=os.path.join(base, "data"))
    parser.add_argument("--log-dir", default=os.path.join(base, "logs"))
    args = parser.parse_args()

    server = GoBangServer(args.host, args.port, args.data_dir, args.log_dir)
    try:
        server.start()
    except KeyboardInterrupt:
        print("\n[server] Ctrl+C received, shutting down...")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
