"""客户端网络层. 单独一个后台线程做 recv, 收到的帧通过 queue 投递到 GUI."""

from __future__ import annotations

import logging
import queue
import socket
import threading
from typing import Any, Dict, Optional

import sys, os
if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.common.protocol import (
    Frame,
    MsgType,
    ProtocolError,
    recv_frame,
    send_frame,
)


class NetClient:
    """非阻塞的客户端: send_*() 可在 GUI 线程调用, 接收事件从 events 队列拉."""

    def __init__(self, logger: logging.Logger) -> None:
        self.log = logger
        self.events: "queue.Queue[Frame]" = queue.Queue()
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._recv_thread: Optional[threading.Thread] = None
        self._stop = False
        self._seq = 0

    # ------------------------------ 连接管理 ------------------------------
    def connect(self, host: str, port: int, timeout: float = 5.0) -> None:
        if self._sock is not None:
            self.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.settimeout(None)
        self._sock = s
        self._stop = False
        self._recv_thread = threading.Thread(target=self._recv_loop, name="net-recv", daemon=True)
        self._recv_thread.start()
        self.log.info("connected to %s:%d", host, port)

    def reconnect(self, host: str, port: int, timeout: float = 5.0) -> None:
        self.connect(host, port, timeout=timeout)

    def close(self) -> None:
        self._stop = True
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.log.info("connection closed")

    def is_connected(self) -> bool:
        return self._sock is not None and not self._stop

    # ------------------------------ 发送 ------------------------------
    def send(self, msg_type: MsgType, data: Optional[Dict[str, Any]] = None) -> int:
        """发送一帧消息, 返回本次使用的 seq."""
        if self._sock is None:
            raise ConnectionError("not connected")
        with self._send_lock:
            self._seq += 1
            seq = self._seq
            try:
                send_frame(self._sock, msg_type, data, seq)
            except (OSError, ConnectionError) as e:
                self.log.warning("send error: %s", e)
                self._post_disconnected(reason=str(e))
                raise
        self.log.info("-> %s seq=%d data=%s", msg_type.name, seq, data)
        return seq

    def send_reconnect_resume(self, room_id: Optional[int] = None) -> int:
        data: Dict[str, Any] = {}
        if room_id is not None:
            data["room_id"] = int(room_id)
        return self.send(MsgType.C2S_RECONNECT_RESUME, data)

    # ------------------------------ 接收 ------------------------------
    def _recv_loop(self) -> None:
        assert self._sock is not None
        try:
            while not self._stop:
                try:
                    frame = recv_frame(self._sock)
                except ProtocolError as e:
                    self.log.warning("protocol error: %s", e)
                    self._post_disconnected(reason=f"协议错误: {e}")
                    return
                except (ConnectionError, OSError) as e:
                    if not self._stop:
                        self.log.info("recv disconnected: %s", e)
                        self._post_disconnected(reason=str(e))
                    return
                self.log.info("<- %s seq=%d data=%s", frame.type.name, frame.seq, frame.data)
                self.events.put(frame)
        finally:
            self._stop = True

    def _post_disconnected(self, reason: str) -> None:
        # 用一个特殊的伪事件通知 GUI 已断开
        evt = Frame(type=MsgType.S2C_ERROR, seq=0,
                    data={"code": "DISCONNECTED", "reason": reason})
        self.events.put(evt)
