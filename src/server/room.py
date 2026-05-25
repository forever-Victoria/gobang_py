"""五子棋房间逻辑 + 房间管理器.

服务端是权威状态: 棋盘状态、当前轮到谁、胜负判定全部在服务端进行,
客户端只发起 "我想下 (row, col)" 的请求, 由服务端裁决并广播结果.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

BOARD_SIZE = 15
EMPTY = 0
BLACK = 1  # 先手
WHITE = 2  # 后手

# 四个方向: 横, 纵, 主对角, 副对角
DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]


class Room:
    """单局五子棋对局."""

    def __init__(self, room_id: int, black_user: str, white_user: str) -> None:
        self.room_id = room_id
        self.black_user = black_user
        self.white_user = white_user
        self.board: List[List[int]] = [[EMPTY] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self.turn: int = BLACK              # 当前应该下棋的颜色
        self.over: bool = False
        self.winner: int = 0                # 0=未结束/平局, 1=黑胜, 2=白胜
        self.move_count: int = 0
        self.started_at: float = time.time()
        self.moves: List[Dict[str, int]] = []  # [{index,row,col,color,ts}]
        self.chat_log: List[Dict[str, str]] = []  # [{from,text}]
        self.observers: Set[str] = set()
        self.lock = threading.RLock()

    # ------------------------- 工具 -------------------------
    def color_of(self, username: str) -> int:
        if username == self.black_user:
            return BLACK
        if username == self.white_user:
            return WHITE
        return EMPTY

    def opponent_of(self, username: str) -> Optional[str]:
        if username == self.black_user:
            return self.white_user
        if username == self.white_user:
            return self.black_user
        return None

    # ------------------------- 落子 + 胜负判定 -------------------------
    def place(self, username: str, row: int, col: int) -> Tuple[bool, str, int, int]:
        """尝试为 username 在 (row,col) 落子.

        返回 (ok, reason, color, winner):
          ok=False    -> reason 给出原因, color/winner 无意义
          ok=True     -> color 是本次落子的颜色; winner: 0=继续, 1=黑胜, 2=白胜, 3=平局
        """
        with self.lock:
            if self.over:
                return False, "对局已结束", 0, 0
            color = self.color_of(username)
            if color == EMPTY:
                return False, "你不在本房间中", 0, 0
            if color != self.turn:
                return False, "还没轮到你下棋", 0, 0
            if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
                return False, "坐标越界", 0, 0
            if self.board[row][col] != EMPTY:
                return False, "该位置已有棋子", 0, 0

            self.board[row][col] = color
            self.move_count += 1
            self.moves.append({
                "index": self.move_count,
                "row": row,
                "col": col,
                "color": color,
                "ts": int(time.time()),
            })

            if self._check_win(row, col, color):
                self.over = True
                self.winner = color
                return True, "", color, color
            if self.move_count >= BOARD_SIZE * BOARD_SIZE:
                self.over = True
                self.winner = 0
                return True, "", color, 3  # 平局
            self.turn = WHITE if color == BLACK else BLACK
            return True, "", color, 0

    def _check_win(self, row: int, col: int, color: int) -> bool:
        """从落子点出发, 四个方向各延伸, 是否能连成 >=5 子."""
        for dr, dc in DIRECTIONS:
            cnt = 1
            # 正向
            r, c = row + dr, col + dc
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == color:
                cnt += 1
                r += dr
                c += dc
            # 反向
            r, c = row - dr, col - dc
            while 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE and self.board[r][c] == color:
                cnt += 1
                r -= dr
                c -= dc
            if cnt >= 5:
                return True
        return False


@dataclass
class _RoomEntry:
    room: Room
    members: List[str] = field(default_factory=list)  # [black, white]


class RoomManager:
    """全局房间管理. 通过 username 反查所在房间."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._next_id = 1
        self._rooms: Dict[int, _RoomEntry] = {}
        self._user2room: Dict[str, int] = {}
        self._observer2room: Dict[str, int] = {}

    def create_room(self, user_a: str, user_b: str) -> Room:
        """随机指派黑白 (这里用字典序较小者执黑, 便于复现)."""
        with self._lock:
            if user_a < user_b:
                black, white = user_a, user_b
            else:
                black, white = user_b, user_a
            room = Room(self._next_id, black, white)
            self._rooms[self._next_id] = _RoomEntry(room=room, members=[black, white])
            self._user2room[black] = self._next_id
            self._user2room[white] = self._next_id
            self._next_id += 1
            return room

    def create_room_fixed(self, black_user: str, white_user: str) -> Room:
        """按指定黑白方创建房间, 用于人机对战等确定先后手的场景。"""
        with self._lock:
            room = Room(self._next_id, black_user, white_user)
            self._rooms[self._next_id] = _RoomEntry(room=room, members=[black_user, white_user])
            self._user2room[black_user] = self._next_id
            self._user2room[white_user] = self._next_id
            self._next_id += 1
            return room

    def get_room_of(self, username: str) -> Optional[Room]:
        with self._lock:
            rid = self._user2room.get(username)
            if rid is None:
                return None
            entry = self._rooms.get(rid)
            return entry.room if entry else None

    def get_room_by_id(self, room_id: int) -> Optional[Room]:
        with self._lock:
            entry = self._rooms.get(room_id)
            return entry.room if entry else None

    def list_active_rooms(self) -> List[Room]:
        with self._lock:
            rooms = [entry.room for entry in self._rooms.values()]
        return [r for r in rooms if not r.over]

    def add_observer(self, room_id: int, username: str) -> Optional[Room]:
        with self._lock:
            entry = self._rooms.get(room_id)
            if entry is None:
                return None
            room = entry.room
            with room.lock:
                room.observers.add(username)
            self._observer2room[username] = room_id
            return room

    def remove_observer(self, username: str) -> bool:
        with self._lock:
            rid = self._observer2room.pop(username, None)
            if rid is None:
                return False
            entry = self._rooms.get(rid)
            if entry is None:
                return False
            with entry.room.lock:
                entry.room.observers.discard(username)
            return True

    def get_observed_room(self, username: str) -> Optional[Room]:
        with self._lock:
            rid = self._observer2room.get(username)
            if rid is None:
                return None
            entry = self._rooms.get(rid)
            return entry.room if entry else None

    def remove_user(self, username: str) -> Optional[Room]:
        """玩家离开 / 掉线. 返回所在 Room (如有)."""
        with self._lock:
            self.remove_observer(username)
            rid = self._user2room.pop(username, None)
            if rid is None:
                return None
            entry = self._rooms.get(rid)
            if entry is None:
                return None
            if username in entry.members:
                entry.members.remove(username)
            if not entry.members:
                self._rooms.pop(rid, None)
            return entry.room

    def destroy_room(self, room_id: int) -> None:
        with self._lock:
            entry = self._rooms.pop(room_id, None)
            if entry is None:
                return
            with entry.room.lock:
                observers = list(entry.room.observers)
                entry.room.observers.clear()
            for u in entry.members:
                self._user2room.pop(u, None)
            for ou in observers:
                self._observer2room.pop(ou, None)
