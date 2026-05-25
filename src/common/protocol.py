"""
GoBang Custom Application-Layer Protocol (GBP/1)

帧格式 (Frame Format, big-endian)
+----------+----------+----------+--------------+
| MAGIC    | VERSION  | TYPE     | PAYLOAD_LEN  |
| 2 bytes  | 1 byte   | 1 byte   | 4 bytes      |
+----------+----------+----------+--------------+
|              PAYLOAD (UTF-8 JSON)             |
|              PAYLOAD_LEN bytes                |
+-----------------------------------------------+

- MAGIC      = 0x47 0x42  ('G''B')  方便在 Wireshark 中肉眼识别本协议
- VERSION    = 0x01
- TYPE       = 1 字节消息类型 (见 MsgType)
- PAYLOAD_LEN= 大端无符号 32 位整数, 后续 JSON 正文字节数

所有消息正文均为 UTF-8 编码的 JSON 对象, 形如:
    { "seq": 123, "data": { ... } }

seq 由发送端自增, 用于将请求与响应配对, 便于抓包分析.
"""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, Optional


MAGIC = b"GB"
VERSION = 1
HEADER_FMT = "!2sBBI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # = 8
MAX_PAYLOAD = 1 * 1024 * 1024  # 1MB safety limit


class MsgType(IntEnum):
    """消息类型枚举 (1 byte)."""

    # --- 客户端 -> 服务端 ---
    C2S_REGISTER = 1       # 注册       { username, password }
    C2S_LOGIN = 2          # 登录       { username, password }
    C2S_LOGOUT = 3         # 登出       {}
    C2S_MATCH_START = 4    # 开始匹配   {}
    C2S_MATCH_STOP = 5     # 取消匹配   {}
    C2S_MOVE = 6           # 落子       { row, col }
    C2S_CHAT = 7           # 聊天       { text }
    C2S_LEAVE_ROOM = 8     # 离开房间   {}
    C2S_PING = 9           # 心跳       {}
    C2S_SPECTATE_LIST = 10     # 进行中房间列表 {}
    C2S_SPECTATE_JOIN = 11     # 加入观战 { room_id }
    C2S_RECONNECT_RESUME = 12  # 断线恢复 { room_id? }
    C2S_REPLAY_LIST = 13       # 历史对局列表 { limit, offset }
    C2S_REPLAY_GET = 14        # 获取单局回放 { replay_id }
    C2S_RANK_LIST = 15         # 排行榜列表 { limit }
    C2S_AI_MATCH_START = 16    # 开始 AI 对战 {}

    # --- 服务端 -> 客户端 ---
    S2C_REGISTER_RESP = 101  # { ok, reason }
    S2C_LOGIN_RESP = 102     # { ok, reason, uid, username, score, total, win }
    S2C_LOBBY_INFO = 103     # { online: [...], queue_size }
    S2C_MATCH_OK = 104       # { room_id, you, opponent: {uid, username, score}, your_color (1=black,2=white), turn_color }
    S2C_MOVE_RESULT = 105    # { ok, row, col, color, next_turn, winner, reason }
    S2C_CHAT_BCAST = 106     # { from, text }
    S2C_ROOM_CLOSED = 107    # { reason, your_result: "win"|"lose"|"draw"|"abort" }
    S2C_ERROR = 108          # { code, reason }
    S2C_PONG = 109           # {}
    S2C_SPECTATE_LIST = 110      # { rooms: [{room_id, black, white, move_count, started_at}] }
    S2C_SPECTATE_SNAPSHOT = 111  # { room_id, black, white, board, turn_color, move_count, moves, chat_log, ended }
    S2C_RECONNECT_RESP = 112     # { ok, reason, room_state? }
    S2C_REPLAY_LIST = 113        # { total, items: [{replay_id, players, winner, ended_at}] }
    S2C_REPLAY_DATA = 114        # { ok, reason, replay? }
    S2C_RANK_LIST = 115          # { items: [{rank, username, score, total, win}] }


class ProtocolError(Exception):
    """协议格式错误 / 非法包."""


@dataclass
class Frame:
    """解析后的一帧消息."""

    type: MsgType
    seq: int
    data: Dict[str, Any]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Frame type={self.type.name} seq={self.seq} data={self.data}>"


# --------------------------- 编/解码 ---------------------------

def encode(msg_type: MsgType, data: Optional[Dict[str, Any]] = None, seq: int = 0) -> bytes:
    """将消息编码成完整的一帧二进制数据 (含 8 字节包头)."""
    body = {"seq": int(seq), "data": data or {}}
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"payload too large: {len(payload)}")
    header = struct.pack(HEADER_FMT, MAGIC, VERSION, int(msg_type), len(payload))
    return header + payload


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """从 socket 上读取恰好 n 字节, 不足则抛 ConnectionError."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("peer closed during recv")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket) -> Frame:
    """从 socket 阻塞读取一帧, 校验并返回 Frame.

    抛出:
        ConnectionError: 对端关闭
        ProtocolError:   帧格式非法
    """
    header = _recv_exact(sock, HEADER_SIZE)
    magic, version, type_byte, payload_len = struct.unpack(HEADER_FMT, header)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic: {magic!r}")
    if version != VERSION:
        raise ProtocolError(f"bad version: {version}")
    if payload_len > MAX_PAYLOAD:
        raise ProtocolError(f"payload too large: {payload_len}")
    try:
        msg_type = MsgType(type_byte)
    except ValueError as e:
        raise ProtocolError(f"unknown type: {type_byte}") from e

    payload = _recv_exact(sock, payload_len) if payload_len > 0 else b""
    if payload:
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ProtocolError(f"bad json: {e}") from e
    else:
        body = {}

    if not isinstance(body, dict):
        raise ProtocolError("payload root must be object")
    seq = int(body.get("seq", 0))
    data = body.get("data", {})
    if not isinstance(data, dict):
        raise ProtocolError("data must be object")
    return Frame(type=msg_type, seq=seq, data=data)


def send_frame(sock: socket.socket, msg_type: MsgType, data: Optional[Dict[str, Any]] = None, seq: int = 0) -> None:
    """组帧并发送一条消息 (使用 sendall 保证完整写出)."""
    sock.sendall(encode(msg_type, data, seq))
