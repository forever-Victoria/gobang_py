# 计算机网络结课实验报告 — 基于自定义 TCP 协议的网络五子棋对战系统

> 课程: 计算机网络  
> 项目: 自定义应用层协议的联网交互系统  
---

## 1. 系统总体架构

本项目是一个面向 **C/S (客户端 / 服务端) 架构**的实时联网对战系统, 实现了一个经典的 15×15 五子棋游戏. 它由如下三部分组成:

```
+-----------------+                     +-------------------+                     +-----------------+
|  Tk Client #1   |◀═══ TCP / GBP/1 ═══▶|   GoBang Server   |◀═══ TCP / GBP/1 ═══▶|  Tk Client #2   |
|  (Windows GUI)  |   (8B 头 + JSON)   |  (TCP :9527)      |   (8B 头 + JSON)   |  (Windows GUI)  |
| - 登录/大厅/对局│                     | - UserManager     |                     | - 登录/大厅/对局│
| - 棋盘 Canvas   |                     | - Matcher (线程)  |                     | - 棋盘 Canvas   |
| - 聊天          │                     | - RoomManager     |                     | - 聊天          │
| - 后台 recv 线程│                     | - 每连接一工作线程│                     | - 后台 recv 线程│
+-----------------+                     +---------+---------+                     +-----------------+
                                                  │
                                                  │ 读写 (仅服务端)
                                                  ▼
                                    +---------------------------+
                                    |  data/gobang.db (SQLite)  |
                                    |  logs/server.log          |
                                    +---------------------------+
```

- **客户端**: Python 3 + `tkinter` 桌面 GUI. 单进程内 GUI 主线程负责绘制 / 事件, 后台 `recv` 线程负责从 socket 阻塞读取协议帧, 通过 `queue.Queue` 投递给主线程, 主线程用 `root.after(50ms)` 轮询消费, 保证所有 Tk API 都只在主线程调用. **客户端不访问数据库**, 登录/积分/回放等全部经 TCP 请求由服务端处理.
- **服务端**: Python 3 标准库实现的多线程 TCP 服务器. `accept()` 主线程 + 每个连接一个工作线程 + 独立 `Matcher` 匹配线程 + 重连看门狗线程, 共享状态用 `threading.RLock` 保护; 唯一读写 `data/gobang.db` 与 `logs/server.log`.
- **协议**: 自定义 GBP/1 (8 字节定长头 + JSON), **仅用于 Client ↔ Server 的 TCP 连接**, 详见 `protocol.md`.

---

## 2. 客户端 / 服务端分工

| 责任 | 客户端 | 服务端 |
|---|:---:|:---:|
| UI 渲染 (登录页 / 大厅 / 棋盘 / 聊天) | ✔ | — |
| 鼠标点击 → 落子坐标计算 | ✔ | — |
| 发送 `C2S_*` 请求 | ✔ | — |
| 用户表持久化 (SQLite `data/gobang.db`) | — | ✔ |
| 用户名/密码哈希校验 | — | ✔ |
| 在线表 `_online: username -> conn` | — | ✔ |
| 匹配队列 / 配对算法 | — | ✔ |
| **棋盘状态 / 当前轮次 / 五子连珠判定** | — | **✔ (权威)** |
| 房间创建 / 销毁 / 双向广播 | — | ✔ |
| 积分 / 胜场 / 总场更新 | — | ✔ |
| 日志 (server.log, client_*.log) | ✔ | ✔ |

> 重要原则: **客户端只是显示与输入设备, 所有游戏结果完全由服务端裁决**. 即使有人改了客户端代码、强行模拟"我赢了"的画面, 服务端仍然按照自己维护的棋盘状态来判定胜负, 不会受影响.

---

## 3. 自定义应用层协议设计

完整规范请见 `protocol.md`. 核心要点:

### 3.1 帧格式

```
+----+----+----+----+----+----+----+----+--------- ... ---------+
| 'G'| 'B'| VER|TYPE|       PAYLOAD_LEN(big-endian uint32)      | JSON payload (UTF-8) |
+----+----+----+----+----+----+----+----+--------- ... ---------+
   0    1    2    3    4    5    6    7    8 ......   8+LEN-1
```

- 用 `GB` 魔数 + 固定长度包头解决 TCP 字节流半包 / 粘包问题;
- 业务字段全部用 JSON, 增删字段对协议本身完全透明;
- 1 字节 `TYPE` 共定义 18 种消息 (9 个 C2S + 9 个 S2C).

### 3.2 消息分类

- **认证类**: `REGISTER` / `LOGIN` / `LOGOUT`
- **大厅类**: `LOBBY_INFO` (服务端推送)
- **匹配类**: `MATCH_START` / `MATCH_STOP` / `MATCH_OK`
- **对局类**: `MOVE` / `MOVE_RESULT` / `ROOM_CLOSED`
- **社交类**: `CHAT` / `CHAT_BCAST`
- **保活类**: `PING` / `PONG`
- **错误类**: `ERROR` (统一错误码: BAD_FRAME / BAD_TYPE / BAD_ARG / NOT_LOGIN / IN_ROOM / NO_ROOM / INTERNAL)

### 3.3 字段含义示例 (`S2C_MATCH_OK`)

```json
{
  "seq": 0,
  "data": {
    "room_id": 1,           // 服务端递增分配
    "board_size": 15,       // 棋盘尺寸
    "you": "alice",         // 收到这条消息的玩家
    "opponent": {           // 对手公开信息
      "username": "bob",
      "score": 1000
    },
    "your_color": 1,        // 1=黑(先手), 2=白
    "turn_color": 1         // 当前应该谁下
  }
}
```

### 3.4 状态变化规则

详见 `protocol.md` §6, 摘要:
- 落子: 必须满足 [房间未结束 / 颜色匹配当前轮 / 坐标合法 / 目标格为空], 否则服务端单独回 `MOVE_RESULT{ok:false, reason:...}`, **不切换轮次**;
- 胜负: 在落子点四方向 (横/纵/正斜/反斜) 各延伸数同色子, 任一方向 ≥ 5 即胜;
- 离开 / 掉线判负: 服务端会通过 socket recv 出错来检测, 自动给对方记一胜;
- 积分: 胜 +30 / 负 -30 (clamp 0) / 平局不动.

---

## 4. 服务端权威状态维护方式

服务端共有 4 张全局表, 全部用 `threading.RLock` 保护:

| 表 | 用途 | 写入时机 |
|---|---|---|
| `UserManager._users: {username -> {uid,salt,pwd,score,total,win}}` | 持久化账号 | 注册/对局结束 |
| `GoBangServer._online: {username -> Connection}` | 当前在线 | 登录/断线 |
| `Matcher._queue: deque[str]` + `_in_queue: set[str]` | 匹配队列 | 用户请求匹配/取消 |
| `RoomManager._rooms: {rid -> _RoomEntry}` + `_user2room: {username -> rid}` | 对局房间 | 匹配成功创建/结束销毁 |

`Room` 内部的 `board[15][15]`, `turn`, `over`, `winner` 全部由服务端自己改, 客户端没有任何修改权.

### 关键不变量
1. 同一时刻一个 username 最多对应一个 `Connection` (重复登录被拒绝);
2. 同一时刻一个 username 最多出现在 一个 房间内;
3. `room.turn` 严格在 {1, 2} 之间交替, 落子失败 / 非法消息不会改变它;
4. 房间销毁后, `_user2room` 同步清除, 后续任何走 `_user2room` 的请求都会得到 `NO_ROOM`.

---

## 5. 并发处理方式

- **连接接入**: `accept` 主线程接受新 TCP 连接, **per-connection thread** 模式 spawn 一个 daemon 工作线程处理该连接的所有读 + dispatch;
- **写串行化**: 每个 `Connection` 内部持有 `_send_lock`, 所有 `send_safe()` 上锁后调用 `socket.sendall()`, 保证多个房间广播线程并发写同一个 socket 时不会交叉;
- **匹配线程**: 单独的 `matcher` 后台线程, 通过 `Condition.wait()` 阻塞, 队列 push 时 `notify_all()`;
- **共享状态**: `UserManager`/`RoomManager`/`Matcher` 都使用 `RLock`, 临界区只覆盖必要的内存操作 (落子计算、用户表读写、队列出入).

冒烟测试 (`smoketest.py`) 已验证 3 个 TCP 连接、5 个并发线程 (accept + 2 客户端 + 匹配 + 主) 下不出现死锁 / 竞态.

---

## 6. 异常情况处理

| 异常类型 | 服务端处理 |
|---|---|
| 非法 frame (魔数错 / 版本错 / 超长 / JSON 解析失败) | 回 `BAD_FRAME`, 立即关闭连接, 写 WARNING 日志 |
| 未知消息类型 | 回 `BAD_TYPE`, 不断开 |
| 未登录就发业务消息 | 回 `NOT_LOGIN` |
| 重复登录 | 第二个登录拒绝 `用户已在别处登录` |
| 错误密码 | `LOGIN_RESP { ok:false, reason: 用户名或密码错误 }` |
| 重复请求匹配 (已在房间) | `IN_ROOM` |
| 不在房间却落子 / 聊天 | `NO_ROOM` |
| 还没轮到自己却落子 | `MOVE_RESULT{ ok:false, reason: 还没轮到你下棋 }`, 不切换轮次 |
| 已下过的位置 / 越界 | 同上, 单独回错, 不广播 |
| 对局中客户端断网 / 强退 | recv 抛 `ConnectionError`, 服务端在 finally 里调 `_on_disconnect()`, 自动判对方胜并广播 `ROOM_CLOSED` |
| dispatch 抛任何未预期异常 | 兜底 `except Exception`, 回 `INTERNAL`, 不影响其它连接 |

客户端同样有兜底:
- 网络层 `recv` 出错时投递伪事件 `S2C_ERROR{ code: DISCONNECTED }`, GUI 收到后弹窗并跳回登录页;
- 关闭窗口时调用 `LOGOUT` + 主动 close, 让服务端干净地释放资源.

---

## 7. 日志记录

- 服务端: `logs/server.log` (滚动 2MB×3, UTF-8). 关键事件:
  - 连接建立 / 断开
  - 注册 / 登录 (成功 / 失败 / 重复)
  - 匹配入队 / 取消 / 成功 (创建 room)
  - 落子 (room_id + 用户 + 坐标 + winner)
  - 房间结束 (正常胜 / 中途离开)
  - 协议错误 / 内部异常
- 客户端: `logs/client_<username>.log`, 记录每次 `send` / `recv` 的 type+seq+data, 用于事后比对.

样例 (节选自 smoketest 真实输出):

```
2026-05-17 14:52:30 [INFO] [matcher] room created: id=1 black=alice white=bob
2026-05-17 14:52:31 [INFO] [cli-127.0.0.1:61322] move ok: room=1 alice @ (7,5) color=1 winner=0
2026-05-17 14:52:32 [INFO] [cli-127.0.0.1:61322] move ok: room=1 alice @ (7,9) color=1 winner=1
2026-05-17 14:52:32 [INFO] [cli-127.0.0.1:61322] room finished: id=1 winner=1
```

---

## 8. 抓包分析结果

### 8.1 抓包步骤
1. 用 Wireshark 选择 "Adapter for loopback traffic capture" (Windows 上是 Npcap 的 Loopback);
2. 过滤 `tcp.port == 9527`;
3. 启动服务端 + 两个客户端, 走完 "注册→登录→匹配→几手棋→结束" 全过程;
4. **Stop 后另存为 `captures/gobang_session.pcapng`**.

### 8.2 一次完整交互的报文序列

抓包文件: `captures/gobang_session.pcapng`. 环境为 **Loopback** (`127.0.0.1`), 服务端端口 **9527**, 客户端 `ali` 使用临时端口 **60404**, 对手 `bbb` 使用 **58442**. 下列帧号均来自该文件.

**TCP 建连 + 注册/登录 (客户端 60404)**

```
Frame 1    60404 -> 9527   [SYN]                         三次握手
Frame 2    9527  -> 60404  [SYN, ACK]
Frame 3    60404 -> 9527   [ACK]
Frame 4    60404 -> 9527   [PSH, ACK]  tcp.payload=66   GBP/1 C2S_REGISTER
                                              {"seq":1,"data":{"username":"aaa","password":"pw1"}}
Frame 5    9527  -> 60404  [ACK]         (纯确认, 无应用数据)
Frame 6    9527  -> 60404  [PSH, ACK]  tcp.payload=76   S2C_REGISTER_RESP
                                              {"seq":1,"data":{"ok":false,"reason":"用户名已被占用"}}
Frame 8    60404 -> 9527   [PSH, ACK]  tcp.payload=66   C2S_REGISTER
                                              {"seq":2,"data":{"username":"ali","password":"pw1"}}
Frame 10   9527  -> 60404  [PSH, ACK]  tcp.payload=40   S2C_REGISTER_RESP {"seq":2,"data":{"ok":true}}
Frame 12   60404 -> 9527   [PSH, ACK]  tcp.payload=66   C2S_LOGIN
                                              {"seq":3,"data":{"username":"ali","password":"pw1"}}
Frame 14   9527  -> 60404  [PSH, ACK]  tcp.payload=106  S2C_LOGIN_RESP
                                              {"seq":3,"data":{"ok":true,"uid":3,"username":"ali","score":1000,...}}
Frame 16   9527  -> 60404  [PSH, ACK]  tcp.payload=103  S2C_LOBBY_INFO (推送在线人数)
```

**匹配 + 首手落子 + 广播**

```
Frame 47   60404 -> 9527   [PSH, ACK]  tcp.payload=30   C2S_MATCH_START {"seq":5,"data":{}}
Frame 51   9527  -> 60404  [PSH, ACK]  tcp.payload=156  S2C_MATCH_OK (room_id=1, ali vs bbb)
Frame 53   60404 -> 9527   [PSH, ACK]  tcp.payload=48   C2S_MOVE {"seq":6,"data":{"row":7,"col":7}}
Frame 55   9527  -> 60404  [PSH, ACK]  tcp.payload=101  S2C_MOVE_RESULT (winner=0, next_turn=2)
Frame 57   9527  -> 58442  [PSH, ACK]  tcp.payload=101  S2C_MOVE_RESULT (同上, 广播给对手)
```

**终局**

```
Frame 226  60404 -> 9527   [PSH, ACK]  tcp.payload=49   C2S_MOVE {"seq":16,"data":{"row":6,"col":9}}
Frame 228  9527  -> 60404  [PSH, ACK]  tcp.payload=101  S2C_MOVE_RESULT (winner=1, 黑胜)
Frame 234  9527  -> 60404  [PSH, ACK]  tcp.payload=76   S2C_ROOM_CLOSED
                                              {"reason":"五子连珠","your_result":"win"}
```

整次抓包共 394 帧, 除上述联机对局外, 还包含第三客户端 `ccc` 登录、大厅聊天 (`C2S_CHAT` / `S2C_CHAT_BCAST`) 及断连 `[FIN, ACK]` 等, 过滤 `tcp.port == 9527` 后可逐项对照.

### 8.3 字段对照 (Hex Dump 验证)

以 **Frame 4** (`60404 → 9527`, 注册请求) 的 TCP Segment Data 为例 (总长 66 字节):

```
0000  47 42 01 01 00 00 00 3a  7b 22 73 65 71 22 3a 20   GB.....:{"seq": 
0010  31 2c 20 22 64 61 74 61  22 3a 20 7b 22 75 73 65   1, "data": {"use
0020  72 6e 61 6d 65 22 3a 20  22 61 61 61 22 2c 20 22   rname": "aaa", "
0030  70 61 73 73 77 6f 72 64  22 3a 20 22 70 77 31 22   password": "pw1"
0040  7d 7d                                            }}
```

- `47 42` = ASCII `GB` → 协议魔数, 与 `protocol.md` §2 一致;
- `01` = 协议版本 `GBP/1`;
- `01` = TYPE = `C2S_REGISTER`;
- `00 00 00 3a` = 大端 **0x3a (58)** → 后续 JSON payload 恰好 58 字节;
- 明文 JSON: `{"seq": 1, "data": {"username": "aaa", "password": "pw1"}}`, 印证 §3 的 `{seq, data}` 结构.

### 8.4 抓包截图说明

报告附图来自 Wireshark 打开 `captures/gobang_session.pcapng` 的实机截图, 建议包含:

- 过滤 `tcp.port == 9527` 后的包列表 (可见 `127.0.0.1:60404 ↔ 127.0.0.1:9527`);
- **Frame 4** 展开 TCP Segment Data 的 Hex/ASCII 对照 (可见 `GB` 头与 JSON);
- **Frame 53 / 55 / 57** 一手落子后服务端向双方各发一条 `S2C_MOVE_RESULT` 的广播.

---

## 9. 测试过程与运行截图

### 9.1 自动化测试

```
> python smoketest.py
[OK] register
[OK] login
[OK] wrong-password rejected
[OK] match: A.color=1 B.color=2
[OK] alice wins by 5-in-a-row
[OK] move-after-game rejected with NO_ROOM
ALL SMOKE TESTS PASSED [OK]
```

覆盖了: 注册 / 登录 / 错误密码 / 匹配 / 多手对局 / 胜负判定 / 房间销毁后非法落子 / 并发 socket 三个连接.

### 9.2 手工测试用例

| # | 步骤 | 期望结果 | 实际结果 |
|---|---|---|---|
| 1 | 启动服务端 + 两客户端, 都注册新账号 | 注册成功 | ✔ |
| 2 | 用相同账号在第二个客户端再登录 | 拒绝, 提示 "已在别处登录" | ✔ |
| 3 | 单边点 "开始匹配" | 大厅显示 "队列: 1 人", 一直等待 | ✔ |
| 4 | 第二个客户端也点匹配 | 双方立刻进入房间, 黑方先手 | ✔ |
| 5 | 黑方不下, 白方点棋盘 | 收到提示 "还没轮到你下棋", 棋盘无变化 | ✔ |
| 6 | 黑方点已下过的格子 | 收到 "该位置已有棋子" 错误, 不切轮 | ✔ |
| 7 | 黑方连下 5 子横向 | 服务端广播胜负, 弹窗 "胜利", 回大厅 | ✔ |
| 8 | 对局中关闭黑方窗口 | 白方弹 "对方离开了房间", 判负, 大厅更新积分 | ✔ |
| 9 | Wireshark 捕获 9527 流量 | 能看到 "GB" 魔数, 帧解析与文档一致 | ✔ |
| 10 | 服务端日志 logs/server.log | 上述事件全部有记录 | ✔ |

### 9.3 截图 (附在 demo/)

- `demo/login.png`        — 登录界面
- `demo/lobby.png`        — 大厅, 在线列表 + 匹配按钮
- `demo/game_room.png`    — 对局界面, 含棋盘 + 聊天
- `demo/game_over.png`    — 胜负结算弹窗
- `demo/server_log.png`   — 服务端日志截图
- `demo/two_clients_demo.png` — 两个客户端同屏对战截图

---

## 10. 新增扩展功能实现

课程文档列出的 9 个「扩展方向」全部实现, 外加 SQLite 持久化共 10 项, 概述如下。

### 10.1 观战模式
`Room.observers` 集合 + 大厅进行中房间摘要广播, 观战者按 `room_id` 加入, 只收不发。

### 10.2 游戏内聊天
房间内通过 `C2S_CHAT` / `S2C_CHAT` 双向广播, 复用既有 TCP 连接, 观战者只读。

### 10.3 断线重连 (60 秒)
`pending_reconnect` 表保存 `username → room_id, deadline`, 同账号重登在窗口期内可恢复棋盘快照与回合, 超时按离开判负。

### 10.4 对局回放
`ReplayStore` 落盘到 SQLite, 每局记录 `players / winner / start / end / moves[]`, 客户端支持首步 / 上一步 / 下一步 / 末步逐手回放。

### 10.5 排行榜
服务端按 `score desc, win desc` 排序返回 TopN, 客户端大厅表格展示 `rank / username / score / win / total`。

### 10.6 AI 对战
服务端 `src/server/ai_player.py` 中的 `GobangAI` 在权威棋盘上计算下一步, 不直接修改房间状态. 客户端发 `C2S_AI_MATCH_START` 开局, 服务端创建 `AI-Bot#N` 虚拟对手房间 (当前玩家固定执黑先行); 玩家落子后 `_maybe_ai_move()` 在轮到 AI 时调用 `choose_move()`, 再经 `room.place()` 与 `S2C_MOVE_RESULT` 广播, 判胜逻辑与联机对局一致.

**落子策略** (规则型启发式, 纯标准库):
1. 己方能一手成五则直接下;
2. 否则若对手下一手能成五则堵;
3. 否则在已有棋子周围 2 格内的空位中, 按连子长度与开口数评分, 综合进攻/防守分与距棋盘中心距离选点.

### 10.7 多房间并发
`RoomManager` 用 `Dict[room_id, Room]` 管理活跃房间, 单实例支持任意数量房间并行, 共享结构用 `threading.RLock` 保护。

### 10.8 服务端监控面板
`src/server/monitor.py` (Tkinter), 同进程承载 `GoBangServer` 并周期采样, 展示在线连接 / 在线玩家 / 进行中房间 / 匹配队列 / 待重连 / 累计落子等指标, 只读不修改状态, 一键 `run_server_monitor.bat` 启动。

### 10.9 云服务器部署
服务端跑在**腾讯云 CVM**, 公网 `140.143.202.203:9527`, 安全组放行 9527/tcp, 客户端把 host 改成该地址即可联机, 全链路已用 Wireshark 抓包验证。

### 10.10 SQLite 持久化 (附加)
标准库 `sqlite3`, 数据库 `data/gobang.db` 自动建表 (`users` / `replays` / `pending_reconnect`), 兼容旧版本 `users.json` / `replays.json` 首次启动自动迁移。

---

## 11. 项目不足与改进方向

| 不足 | 改进思路 |
|---|---|
| 匹配是 FIFO, 没有按积分分档 (原项目按 score 分了三档) | 增加 3 个 deque, 按 score 区间路由 |
| AI 为规则型启发式, 无难度档位, 棋力中等 | 可增加 minimax 搜索、难度档位或执白选项 |
| 没有 Wireshark Lua dissector | 写一个 `.lua` 文件按 `protocol.md` 解协议, 抓包时直接显示 "MsgType: C2S_MOVE row=7 col=7" |
| 协议未加密 | 接 TLS (Python ssl 模块), 或在 GBP/1 之上加 AES |
| 云部署只放了单机服务端进程, 无负载均衡 / 多实例 | 接 nginx stream 模块 + 多服务端实例, 或 docker-compose |

以上扩展方向均可作为后续工作.

---

## 附录 A — 一键运行清单

```powershell
cd gobang_py
.\run_server.bat               # 启动服务端
.\run_client.bat               # 启动一个客户端 (可多次执行)
.\run_smoketest.bat            # 跑一遍自动化测试
```

## 附录 B — 协议消息速查表

| TYPE (hex) | 方向 | 名称 | 说明 |
|---:|:---:|---|---|
| 0x01 | C→S | REGISTER     | 注册 |
| 0x02 | C→S | LOGIN        | 登录 |
| 0x03 | C→S | LOGOUT       | 登出 |
| 0x04 | C→S | MATCH_START  | 加入匹配 |
| 0x05 | C→S | MATCH_STOP   | 取消匹配 |
| 0x06 | C→S | MOVE         | 落子 |
| 0x07 | C→S | CHAT         | 房内聊天 |
| 0x08 | C→S | LEAVE_ROOM   | 中途离开 |
| 0x09 | C→S | PING         | 心跳 |
| 0x65 | S→C | REGISTER_RESP| 注册结果 |
| 0x66 | S→C | LOGIN_RESP   | 登录结果 |
| 0x67 | S→C | LOBBY_INFO   | 大厅信息广播 |
| 0x68 | S→C | MATCH_OK     | 匹配成功 |
| 0x69 | S→C | MOVE_RESULT  | 落子反馈 / 广播 |
| 0x6A | S→C | CHAT_BCAST   | 聊天广播 |
| 0x6B | S→C | ROOM_CLOSED  | 对局结束 |
| 0x6C | S→C | ERROR        | 错误 |
| 0x6D | S→C | PONG         | 心跳响应 |
