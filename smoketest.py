"""端到端冒烟测试: 覆盖基础流程 + 观战 + 重连 + 回放 + 排行榜."""

from __future__ import annotations

import logging
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.common.protocol import MsgType, recv_frame, send_frame
from src.server.server import GoBangServer


def _wait_frame(s, want_type, timeout=3.0):
    s.settimeout(timeout)
    f = recv_frame(s)
    s.settimeout(None)
    assert f.type == want_type, f"expected {want_type.name}, got {f.type.name}: {f.data}"
    return f


def _wait_for_type(s, want_type, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        left = max(0.05, end - time.time())
        s.settimeout(left)
        try:
            f = recv_frame(s)
        except socket.timeout:
            break
        finally:
            s.settimeout(None)
        if f.type == want_type:
            return f
    raise AssertionError(f"timeout waiting for {want_type.name}")


def _drain(s, timeout=0.2):
    """快速读取 socket 上的所有可读帧."""
    out = []
    end = time.time() + timeout
    while True:
        s.settimeout(max(0.01, end - time.time()))
        try:
            out.append(recv_frame(s))
        except (socket.timeout, OSError):
            break
        if time.time() >= end:
            break
    s.settimeout(None)
    return out


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    tmp = tempfile.mkdtemp(prefix="gbsmoke_")
    log_dir = os.path.join(tmp, "logs")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(log_dir); os.makedirs(data_dir)

    port = 19527
    server = GoBangServer("127.0.0.1", port, data_dir, log_dir)
    t = threading.Thread(target=server.start, daemon=True)
    t.start()
    time.sleep(0.3)

    try:
        # 三个客户端 socket (A/B 对局, C 观战)
        sA = socket.create_connection(("127.0.0.1", port)); sA.settimeout(None)
        sB = socket.create_connection(("127.0.0.1", port)); sB.settimeout(None)
        sC = socket.create_connection(("127.0.0.1", port)); sC.settimeout(None)

        # 注册
        send_frame(sA, MsgType.C2S_REGISTER, {"username": "alice", "password": "pw1"}, seq=1)
        r = _wait_frame(sA, MsgType.S2C_REGISTER_RESP)
        assert r.data.get("ok") is True, r.data
        send_frame(sB, MsgType.C2S_REGISTER, {"username": "bob", "password": "pw2"}, seq=1)
        r = _wait_frame(sB, MsgType.S2C_REGISTER_RESP)
        assert r.data.get("ok") is True, r.data
        send_frame(sC, MsgType.C2S_REGISTER, {"username": "cathy", "password": "pw3"}, seq=1)
        r = _wait_frame(sC, MsgType.S2C_REGISTER_RESP)
        assert r.data.get("ok") is True, r.data
        print("[OK] register")

        # 登录
        send_frame(sA, MsgType.C2S_LOGIN, {"username": "alice", "password": "pw1"}, seq=2)
        r = _wait_frame(sA, MsgType.S2C_LOGIN_RESP); assert r.data.get("ok"), r.data
        _drain(sA, 0.2)  # 丢掉 LOBBY_INFO
        send_frame(sB, MsgType.C2S_LOGIN, {"username": "bob", "password": "pw2"}, seq=2)
        r = _wait_frame(sB, MsgType.S2C_LOGIN_RESP); assert r.data.get("ok"), r.data
        send_frame(sC, MsgType.C2S_LOGIN, {"username": "cathy", "password": "pw3"}, seq=2)
        r = _wait_frame(sC, MsgType.S2C_LOGIN_RESP); assert r.data.get("ok"), r.data
        _drain(sB, 0.2)
        _drain(sA, 0.2)
        _drain(sC, 0.2)
        print("[OK] login")

        # 测试错误密码
        sX = socket.create_connection(("127.0.0.1", port))
        send_frame(sX, MsgType.C2S_LOGIN, {"username": "alice", "password": "wrong"}, seq=1)
        r = _wait_frame(sX, MsgType.S2C_LOGIN_RESP); assert r.data.get("ok") is False
        sX.close()
        print("[OK] wrong-password rejected")

        # 匹配
        send_frame(sA, MsgType.C2S_MATCH_START, {}, seq=3)
        send_frame(sB, MsgType.C2S_MATCH_START, {}, seq=3)
        time.sleep(0.3)

        # 收 MATCH_OK (可能夹着 LOBBY_INFO)
        framesA = _drain(sA, 0.5)
        framesB = _drain(sB, 0.5)
        mA = next(f for f in framesA if f.type == MsgType.S2C_MATCH_OK)
        mB = next(f for f in framesB if f.type == MsgType.S2C_MATCH_OK)
        print(f"[OK] match: A.color={mA.data['your_color']} B.color={mB.data['your_color']}")
        # alice < bob 字典序, 按 RoomManager 实现 alice 执黑(1)
        assert mA.data["your_color"] == 1 and mB.data["your_color"] == 2
        room_id = int(mA.data["room_id"])

        # 观战: cathy 查询并加入观战
        send_frame(sC, MsgType.C2S_SPECTATE_LIST, {}, seq=10)
        spect = _wait_for_type(sC, MsgType.S2C_SPECTATE_LIST)
        assert any(int(x.get("room_id", -1)) == room_id for x in spect.data.get("rooms", [])), spect.data
        send_frame(sC, MsgType.C2S_SPECTATE_JOIN, {"room_id": room_id}, seq=11)
        snap = _wait_for_type(sC, MsgType.S2C_SPECTATE_SNAPSHOT)
        assert int(snap.data.get("room_id", 0)) == room_id, snap.data
        print("[OK] spectate join + snapshot")

        # 先下一步，确认观战者能收到广播
        send_frame(sA, MsgType.C2S_MOVE, {"row": 7, "col": 5}, seq=100)
        time.sleep(0.1)
        c_frames = _drain(sC, 0.5)
        assert any(f.type == MsgType.S2C_MOVE_RESULT for f in c_frames), c_frames
        print("[OK] spectator receives live move broadcast")

        # 断线重连: 断开 alice, 重新登录并恢复
        sA.close()
        time.sleep(0.4)
        sA2 = socket.create_connection(("127.0.0.1", port)); sA2.settimeout(None)
        send_frame(sA2, MsgType.C2S_LOGIN, {"username": "alice", "password": "pw1"}, seq=201)
        login_a2 = _wait_frame(sA2, MsgType.S2C_LOGIN_RESP)
        assert login_a2.data.get("ok") is True, login_a2.data
        # 登录后服务端会自动尝试恢复; 显式发一次恢复请求兼容两种实现
        send_frame(sA2, MsgType.C2S_RECONNECT_RESUME, {"room_id": room_id}, seq=202)
        frames = _drain(sA2, 0.8)
        reconnect_frames = [f for f in frames if f.type == MsgType.S2C_RECONNECT_RESP]
        assert reconnect_frames and any(f.data.get("ok") is True for f in reconnect_frames), frames
        print("[OK] reconnect resume in-game")

        # 继续完成对局: alice 在第7行连5
        moves = [
            (sB, 8, 5),
            (sA2, 7, 6), (sB, 8, 6),
            (sA2, 7, 7), (sB, 8, 7),
            (sA2, 7, 8), (sB, 8, 8),
            (sA2, 7, 9),
        ]
        for sock, r_, c_ in moves:
            send_frame(sock, MsgType.C2S_MOVE, {"row": r_, "col": c_}, seq=100)
            time.sleep(0.06)

        # 双方都应该收到一系列 MOVE_RESULT, 并最后收到 ROOM_CLOSED
        allA = _drain(sA2, 1.0)
        allB = _drain(sB, 1.0)
        allC = _drain(sC, 1.0)
        roomA = next(f for f in allA if f.type == MsgType.S2C_ROOM_CLOSED)
        roomB = next(f for f in allB if f.type == MsgType.S2C_ROOM_CLOSED)
        assert roomA.data["your_result"] == "win", roomA.data
        assert roomB.data["your_result"] == "lose", roomB.data
        assert any(f.type == MsgType.S2C_ROOM_CLOSED for f in allC), allC
        # 校验最后一手 winner=1
        last_move = [f for f in allA if f.type == MsgType.S2C_MOVE_RESULT][-1]
        assert last_move.data["winner"] == 1
        print("[OK] alice wins by 5-in-a-row")

        # 试一个非法落子: bob 想在已下过的位置落子, 但他现在已不在房间, 应得 NO_ROOM
        send_frame(sB, MsgType.C2S_MOVE, {"row": 0, "col": 0}, seq=200)
        errs = _drain(sB, 0.5)
        assert any(f.type == MsgType.S2C_ERROR and f.data.get("code") == "NO_ROOM" for f in errs), errs
        print("[OK] move-after-game rejected with NO_ROOM")

        # 历史回放
        send_frame(sA2, MsgType.C2S_REPLAY_LIST, {"limit": 10, "offset": 0}, seq=300)
        rp_list = _wait_frame(sA2, MsgType.S2C_REPLAY_LIST)
        assert int(rp_list.data.get("total", 0)) >= 1, rp_list.data
        rid = int(rp_list.data["items"][0]["replay_id"])
        send_frame(sA2, MsgType.C2S_REPLAY_GET, {"replay_id": rid}, seq=301)
        rp_data = _wait_frame(sA2, MsgType.S2C_REPLAY_DATA)
        assert rp_data.data.get("ok") is True, rp_data.data
        assert len(rp_data.data.get("replay", {}).get("moves", [])) >= 1, rp_data.data
        print("[OK] replay list/get")

        # 排行榜
        send_frame(sA2, MsgType.C2S_RANK_LIST, {"limit": 5}, seq=302)
        rank = _wait_frame(sA2, MsgType.S2C_RANK_LIST)
        assert len(rank.data.get("items", [])) >= 1, rank.data
        print("[OK] rank list")

        # AI 对战: 服务端创建虚拟 AI 玩家, 玩家落子后 AI 自动回应一手
        send_frame(sA2, MsgType.C2S_AI_MATCH_START, {}, seq=400)
        ai_match = _wait_for_type(sA2, MsgType.S2C_MATCH_OK)
        assert str(ai_match.data.get("opponent", {}).get("username", "")).startswith("AI-Bot#"), ai_match.data
        assert int(ai_match.data.get("your_color", 0)) == 1, ai_match.data
        send_frame(sA2, MsgType.C2S_MOVE, {"row": 7, "col": 7}, seq=401)
        ai_frames = _drain(sA2, 0.8)
        move_frames = [f for f in ai_frames if f.type == MsgType.S2C_MOVE_RESULT]
        assert len(move_frames) >= 2, ai_frames
        assert move_frames[0].data.get("color") == 1, move_frames
        assert move_frames[1].data.get("color") == 2, move_frames
        send_frame(sA2, MsgType.C2S_LEAVE_ROOM, {}, seq=402)
        _drain(sA2, 0.5)
        print("[OK] AI match + automatic move")

        sA2.close(); sB.close(); sC.close()
        time.sleep(0.2)
        print("\nALL SMOKE TESTS PASSED [OK]")
        return 0
    finally:
        server.shutdown()
        time.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(main())
