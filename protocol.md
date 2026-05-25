# GoBang Protocol — GBP/1 协议规范

> **GoBang Protocol Version 1 (GBP/1)** 是为本项目自行设计的应用层协议, 直接运行在 TCP 之上.

---

## 1. 设计目标

- **简单**: 8 字节定长包头 + 1 段变长 JSON, 任何语言/工具 (Wireshark, Python, C, 浏览器开发者工具) 都能轻松解析;
- **可识别**: 帧首 2 字节为 ASCII `GB` (0x47 0x42), 抓包时一眼可辨;
- **可扩展**: TYPE 字段 1 字节最多 256 种消息; 业务字段全部走 JSON, 增删字段不影响协议本身;
- **可定位**: 每条消息带自增 `seq`, 请求/响应可一一对应;
- **TCP 字节流安全**: 用 4 字节大端长度前缀避免半包/粘包问题, 接收方 `recv_exact(8 + len)` 一定能取到完整一帧;
- **抗 DoS**: 限制单帧 `payload ≤ 1 MiB`, 超过即视为非法帧, 关闭连接.

---

## 2. 帧格式 (Frame Format)

```
 0           1           2           3
 0 1 2 3 4 5 6 7|0 1 2 3 4 5 6 7|0 1 2 3 4 5 6 7|0 1 2 3 4 5 6 7
+---------------+---------------+---------------+---------------+
|             MAGIC             |   VERSION     |     TYPE      |
|       'G' (0x47)              |       'B' (0x42)              |
+---------------+---------------+---------------+---------------+
|                         PAYLOAD_LEN (big-endian uint32)        |
+---------------+---------------+---------------+---------------+
|                                                                |
|             PAYLOAD: UTF-8 编码的 JSON 对象                    |
|             长度恰好为 PAYLOAD_LEN 字节                        |
|                                                                |
+----------------------------------------------------------------+
```

| 字段 | 偏移 | 长度 | 取值 | 说明 |
|---|---:|---:|---|---|
| `MAGIC` | 0 | 2 | `0x47 0x42` (ASCII "GB") | 固定. 非 `GB` 直接判定非法包并断开连接 |
| `VERSION` | 2 | 1 | `0x01` | 协议版本号. 未来不兼容升级时 +1 |
| `TYPE` | 3 | 1 | 见 §4 | 消息类型枚举 |
| `PAYLOAD_LEN` | 4 | 4 | big-endian uint32 | 后续 JSON payload 字节数 (≤ 1 MiB) |
| `PAYLOAD` | 8 | 变长 | UTF-8 JSON | 见 §3 |

> Python 端 `struct` 格式串为 `"!2sBBI"`, 长度 `struct.calcsize == 8`.

---

## 3. Payload 格式

Payload 是一个 UTF-8 编码的 JSON 对象, 根对象**必须**包含两个字段:

```json
{
  "seq":  123,
  "data": { ... }     // 业务字段, 因消息类型而异; 允许为空对象
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `seq` | 非负整数 | 发送端自增的序列号, 用于关联请求/响应 (服务端响应时通常回填请求的 seq) |
| `data` | object | 业务负载, 字段定义见 §4 |

非法 payload (非 JSON / 根不是 object / `data` 不是 object) 视为 **协议错误**, 服务端会:
1. 回一条 `S2C_ERROR { code: "BAD_FRAME", reason: "..." }`;
2. 关闭该连接.

---

## 4. 消息类型 (Message Types)

```
0x00         保留
0x01 - 0x0F  客户端 -> 服务端 (C2S)
0x10 - 0x40  保留
0x65 - 0x80  服务端 -> 客户端 (S2C)        # 0x65 = 101 (10进制)
```

### 4.1 客户端 → 服务端

| TYPE | 名称 | 时机 | data 字段 |
|---:|---|---|---|
| `0x01` | `C2S_REGISTER` | 注册账号 | `{ "username": str, "password": str }` |
| `0x02` | `C2S_LOGIN`    | 登录     | `{ "username": str, "password": str }` |
| `0x03` | `C2S_LOGOUT`   | 主动登出 | `{}` |
| `0x04` | `C2S_MATCH_START` | 加入匹配队列 | `{}` |
| `0x05` | `C2S_MATCH_STOP`  | 取消匹配 | `{}` |
| `0x06` | `C2S_MOVE`     | 落子     | `{ "row": int, "col": int }`  // 0 ≤ row,col ≤ 14 |
| `0x07` | `C2S_CHAT`     | 房间内聊天 | `{ "text": str }`  // ≤ 200 字符, 超长截断 |
| `0x08` | `C2S_LEAVE_ROOM` | 中途离开当前对局 (判负) | `{}` |
| `0x09` | `C2S_PING`     | 心跳     | `{}` |
| `0x0A` | `C2S_SPECTATE_LIST` | 查询进行中房间列表 | `{}` |
| `0x0B` | `C2S_SPECTATE_JOIN` | 加入观战 | `{ "room_id": int }` |
| `0x0C` | `C2S_RECONNECT_RESUME` | 断线重连恢复 | `{ "room_id"?: int }` |
| `0x0D` | `C2S_REPLAY_LIST` | 查询历史回放列表 | `{ "limit"?: int, "offset"?: int }` |
| `0x0E` | `C2S_REPLAY_GET` | 获取单局回放详情 | `{ "replay_id": int }` |
| `0x0F` | `C2S_RANK_LIST` | 查询排行榜 | `{ "limit"?: int }` |

### 4.2 服务端 → 客户端

| TYPE | 名称 | 触发 | data 字段 |
|---:|---|---|---|
| `0x65` (101) | `S2C_REGISTER_RESP` | 收到 C2S_REGISTER | `{ "ok": bool, "reason"?: str }` |
| `0x66` (102) | `S2C_LOGIN_RESP`    | 收到 C2S_LOGIN    | `{ "ok": bool, "reason"?: str, "uid"?: int, "username"?: str, "score"?: int, "total"?: int, "win"?: int }` |
| `0x67` (103) | `S2C_LOBBY_INFO`    | 大厅在线人数 / 匹配队列变化时推送 | `{ "online": [str], "online_count": int, "queue_size": int }` |
| `0x68` (104) | `S2C_MATCH_OK`      | 匹配成功 | `{ "room_id": int, "board_size": int, "you": str, "opponent": {"username": str, "score": int}, "your_color": 1\|2, "turn_color": 1\|2 }` |
| `0x69` (105) | `S2C_MOVE_RESULT`   | 落子反馈 / 房间广播 | `{ "ok": bool, "row": int, "col": int, "color": int, "next_turn": int, "winner": int, "reason"?: str }` |
| `0x6A` (106) | `S2C_CHAT_BCAST`    | 聊天广播 | `{ "from": str, "text": str }` |
| `0x6B` (107) | `S2C_ROOM_CLOSED`   | 对局结束 | `{ "reason": str, "your_result": "win"\|"lose"\|"draw"\|"abort" }` |
| `0x6C` (108) | `S2C_ERROR`         | 业务错误 | `{ "code": str, "reason": str }` |
| `0x6D` (109) | `S2C_PONG`          | 心跳响应 | `{}` |
| `0x6E` (110) | `S2C_SPECTATE_LIST` | 返回进行中房间列表 | `{ "rooms": [{ "room_id": int, "black": str, "white": str, "move_count": int, "started_at": int, "observer_count": int }] }` |
| `0x6F` (111) | `S2C_SPECTATE_SNAPSHOT` | 返回观战快照 | `{ "room_id": int, "black": str, "white": str, "board_size": int, "board": [[int]], "turn_color": 1\|2, "move_count": int, "moves": [...], "chat_log": [...], "ended": bool }` |
| `0x70` (112) | `S2C_RECONNECT_RESP` | 返回重连恢复结果 | `{ "ok": bool, "reason"?: str, "room_state"?: object }` |
| `0x71` (113) | `S2C_REPLAY_LIST` | 返回回放列表 | `{ "total": int, "items": [{ "replay_id": int, "players": [str], "winner": int, "result": str, "ended_at": int, "move_count": int }] }` |
| `0x72` (114) | `S2C_REPLAY_DATA` | 返回单局回放数据 | `{ "ok": bool, "reason"?: str, "replay"?: object }` |
| `0x73` (115) | `S2C_RANK_LIST` | 返回排行榜 | `{ "items": [{ "rank": int, "username": str, "score": int, "total": int, "win": int }] }` |

#### 颜色编码
```
0 = 空位 / 无,    1 = 黑棋 (先手),    2 = 白棋 (后手)
```

---

## 5. 状态机 (Client Side)

```
       +-------------+   connect    +-------------+
       | DISCONNECTED| ──────────▶  |  CONNECTED  |
       +-------------+              +-------------+
              ▲                            │
              │ S2C_ERROR(DISCONNECTED)    │ C2S_LOGIN + S2C_LOGIN_RESP(ok=true)
              │                            ▼
              │                     +-------------+
              │                     |  IN_LOBBY   |◀──┐
              │                     +-------------+   │ S2C_ROOM_CLOSED
              │   断线               │                │
              │                     │ C2S_MATCH_START│
              │                     ▼                │
              │              +---------------+       │
              │              |   MATCHING    |───────┤ C2S_MATCH_STOP
              │              +---------------+       │
              │                     │                │
              │   S2C_MATCH_OK      ▼                │
              │              +---------------+       │
              └──────────────|   IN_GAME     |───────┘
                             +---------------+
                             落子/聊天/认输离开
```

服务端用 **每连接一个 username + 一张 _online 表 + 一张 _user2room 表 + 一张待重连表** 来维护权威状态.

---

## 6. 状态变化规则 (服务端权威逻辑)

- **匹配**: FIFO. 队列 ≥ 2 时弹出两个用户; 任一已不在线则把另一个塞回队首.
- **落子规则**:
  1. 房间未结束;
  2. 落子方颜色 == 当前轮 (`room.turn`);
  3. 坐标在 `[0, 14]` 区间内;
  4. 目标格子为空;
  5. 满足以上条件则更新棋盘, 切换 `room.turn`, 落子点四个方向各延伸看是否 ≥ 5 子, 若是则 `winner=color, over=true`;
  6. 第 225 步仍无人胜出 → 平局 (`winner=3`).
- **胜负 / 中途退出**:
  - 正常胜利: 胜者 `score+30, win+1, total+1`; 负者 `score-30 (clamp 0), total+1`.
  - 中途离开 / 掉线: 离开者按 lose 处理, 对方按 win 处理, 房间销毁.
  - 断线重连: 断线后进入 60 秒待恢复窗口, 恢复成功继续原局; 超时后按离开处理.
  - 平局: 双方 `total+1`, `score` 不动.
- **重复登录**: 同一用户名已在线时, 拒绝新连接的登录请求.
- **观战**: 观战者只接收落子/聊天/结束广播, 不参与落子与回合控制.
- **回放**: 对局结束后服务端落盘 `moves[]`, 客户端可按 `replay_id` 拉取完整序列.

---

## 7. 错误处理 (`S2C_ERROR.code`)

| code | 含义 |
|---|---|
| `BAD_FRAME` | 协议格式错 (魔数/版本/JSON/超长), **服务端会立即关闭连接** |
| `BAD_TYPE`  | TYPE 不在已知集合 |
| `BAD_ARG`   | data 字段缺失或类型错误 (例如 `row/col` 不是整数) |
| `NOT_LOGIN` | 登录前发了需要登录的消息 |
| `IN_ROOM`   | 已在房间中又请求匹配 |
| `NO_ROOM`   | 不在房间中却发了落子/聊天/离开 |
| `NO_SUCH_ROOM` | 请求观战的房间不存在 |
| `RECONNECT_EXPIRED` | 超过重连窗口无法恢复 |
| `NO_SUCH_REPLAY` | 请求的回放不存在 |
| `INTERNAL`  | 服务端内部异常 (兜底) |
| `DISCONNECTED` | (客户端本地伪事件) 网络层告知 GUI 连接已断 |

---

## 8. 一次完整交互示例 (16 进制 + 注释)

下面以 `alice` 注册 + 登录 + 落子一手 为例 (`...` 表示字节流):

### 8.1 Register Request (alice -> server)

```
header  : 47 42 01 01 00 00 00 38         <-- "GB" v1 type=0x01(REG) len=56
payload : {"seq":1,"data":{"username":"alice","password":"pw1"}}
```

### 8.2 Register Response (server -> alice)

```
header  : 47 42 01 65 00 00 00 1d         <-- type=0x65(REG_RESP) len=29
payload : {"seq":1,"data":{"ok":true}}
```

### 8.3 Login Request

```
header  : 47 42 01 02 00 00 00 38
payload : {"seq":2,"data":{"username":"alice","password":"pw1"}}
```

### 8.4 Move Request (alice 在 (7,7) 落黑子)

```
header  : 47 42 01 06 00 00 00 28
payload : {"seq":15,"data":{"row":7,"col":7}}
```

### 8.5 Move Result Broadcast (server -> 双方)

```
header  : 47 42 01 69 00 00 00 6c
payload : {"seq":0,"data":{"ok":true,"row":7,"col":7,"color":1,"next_turn":2,"winner":0}}
```

`seq=0` 表示服务端主动广播 (非对某个请求的应答).

---

## 9. Wireshark 解读指南

- 协议固定运行在 9527/tcp;
- 找到任意一条带 payload 的 TCP segment, 在 **Packet Bytes** 面板里搜索 ASCII `GB`, 就能定位每一帧的起点;
- 紧随其后:
  - `01` = VERSION
  - 下一字节查 §4 表得知消息类型;
  - 后 4 字节大端是 payload 长度;
  - 再后面的字节直接当 UTF-8 文本读, 就是 JSON.

如果想做更精细的解析, 可以写一个 Wireshark Lua dissector (本项目暂未提供, 是后续可拓展工作).
