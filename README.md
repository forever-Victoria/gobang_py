# 网络五子棋 (GoBang) — 基于自定义 TCP 应用层协议的联网对战系统

> 计算机网络课程结课实验项目  
> 仿照原 C++ / WebSocket 项目 (`online_gobang-master`) 的功能, 用 **Python + 自定义 TCP 应用层协议 (GBP/1) + Tkinter** 实现, **在 Windows 上零依赖直接运行**.

---

## 1. 项目特性

| 课程要求 | 本项目实现 |
|---|---|
| C/S 架构, 独立服务端 + ≥2 个客户端 | TCP 服务器 + Tkinter 桌面客户端, 可同时启动多个客户端 |
| 自定义应用层协议 | **GBP/1**: `MAGIC(2) + VERSION(1) + TYPE(1) + LEN(4) + JSON Payload`, 详见 [`protocol.md`](./protocol.md) |
| 实时交互 | 服务端事件驱动地把落子/聊天/匹配/胜负实时广播给房间内 / 大厅内的双方 |
| 服务端权威状态 | 棋盘、轮次、胜负判定全部由服务端维护, 客户端只发"想下哪"的请求 |
| 并发处理 | 每个客户端连接一个工作线程; `UserManager` / `RoomManager` / `Matcher` 全部线程安全 |
| 异常处理 | 非法帧 / 非法落子 / 重复登录 / 中途掉线 / 错误密码 全部有兜底并写日志 |
| 日志记录 | 服务端 `logs/server.log` + 客户端 `logs/client_<username>.log` (滚动归档) |
| 抓包分析 | 协议明文 JSON + 固定 `GB` 魔数, 用 Wireshark 一眼就能识别 |

新增扩展功能: ✅ 实时观战 ✅ 60 秒断线重连 ✅ 历史回放(服务端落盘) ✅ 排行榜 UI.

---

## 2. 运行环境

- **操作系统**: Windows 10/11 (本项目即为此而生; macOS/Linux 同样能跑)
- **Python**: 3.8 及以上 (推荐 3.10+). **不需要任何第三方包**, 只用标准库 (`socket` / `threading` / `tkinter` / `json` / `struct` / `hashlib` / `logging`).

数据库说明:
- 默认使用 **SQLite** 数据库 `data/gobang.db` 存储用户与回放数据；
- 若检测到旧版 `data/users.json` / `data/replays.json`, 且数据库为空，会在首次启动时自动迁移导入。

检查方式:

```powershell
python --version          # 需要 >= 3.8
python -c "import tkinter; print(tkinter.TkVersion)"  # 应能正常打印
```

> 如果 `python` 找不到, 请先到 <https://www.python.org/downloads/> 下载安装, 安装时勾选 *Add Python to PATH*.

---

## 3. 目录结构

```
gobang_py/
├── src/
│   ├── common/
│   │   └── protocol.py        # 自定义应用层协议: 帧格式 / 编码 / 解码
│   ├── server/
│   │   ├── server.py          # TCP 服务器主程序 (入口)
│   │   ├── user_manager.py    # 用户表 (文件持久化, SHA-256+salt)
│   │   ├── room.py            # 五子棋房间 + 棋盘 + 胜负判定 + 房间管理器
│   │   └── matcher.py         # 玩家匹配队列 (后台线程, FIFO)
│   └── client/
│       ├── gui.py             # Tkinter 客户端 (入口, 含棋盘 Canvas)
│       └── network.py         # 客户端网络层 (后台 recv 线程 + 事件队列)
├── data/                      # 持久化数据 (users.json 自动生成)
│   └── gobang.db              # SQLite 数据库 (用户/回放); 支持从旧 JSON 自动迁移
├── logs/                      # 运行日志 (server.log, client_*.log)
├── captures/                  # Wireshark 抓包文件 (.pcapng)
├── demo/                      # 演示截图 / 视频
├── smoketest.py               # 端到端自动化冒烟测试
├── run_server.bat             # Windows 一键启动服务端
├── run_client.bat             # Windows 一键启动客户端
├── run_smoketest.bat          # Windows 一键跑冒烟测试
├── README.md                  # 本文档
├── protocol.md                # 协议设计文档
└── report.md                  # 实验报告
```

---

## 4. 一键运行 (Windows)

### 4.1 启动服务端

双击 `run_server.bat`, 或在 PowerShell:

```powershell
cd gobang_py
.\run_server.bat
```

看到 `GoBang server listening on 0.0.0.0:9527` 即成功. 服务端默认监听 **9527/tcp**.

### 4.2 启动两个客户端

双击 **两次** `run_client.bat`, 或在 PowerShell 跑两次:

```powershell
.\run_client.bat
```

会弹出两个 GUI 窗口. 在每个窗口里:
1. 服务器地址默认填 `127.0.0.1:9527`, 跨机对战时改成服务端 IP;
2. 输入用户名/密码, 第一次点 **注册**, 再点 **登录**;
3. 进入大厅, 点 **开始匹配**;
4. 两个客户端都点匹配后, 几乎立刻就会进入对局界面, 棋盘上方会标出 "你: alice (黑棋, 先手)" 等信息;
5. 黑棋先行, 鼠标点击棋盘即可落子; 右侧可以发送聊天消息;
6. 任一方五子连珠, 服务端判胜并广播给双方, 房间销毁, 自动回到大厅; 中途关闭客户端 = 认输.

> 想本机模拟多人对战, 直接多开 `run_client.bat` 即可; 想多机对战, 把服务端机的防火墙 9527/tcp 放行, 客户端填服务端的局域网 IP.

### 4.3 一键回归测试 (不打开 GUI)

```powershell
.\run_smoketest.bat
```

会自动启动一个本地服务端 + 模拟三客户端流程, 覆盖:
- 注册 / 登录 / 错误密码拒绝
- 匹配 / 对局 / 五子连珠胜利
- 第三方观战并接收实时广播
- 对局中断线后 60 秒内重连恢复
- 历史回放列表与详情拉取
- 排行榜查询

最后打印 `ALL SMOKE TESTS PASSED [OK]`.

---

## 5. 命令行方式启动 (高级)

```powershell
# 服务端 (默认 0.0.0.0:9527)
python -m src.server.server --host 0.0.0.0 --port 9527

# 客户端 (默认连 127.0.0.1:9527)
python -m src.client.gui --host 127.0.0.1 --port 9527
```

---

## 6. 抓包分析步骤 (Wireshark)

1. 关闭服务端, 在 Wireshark 选择 **本地回环 / 实际网卡**, 过滤表达式:

   ```
   tcp.port == 9527
   ```

2. 启动 `run_server.bat`, 然后启动两个 `run_client.bat`, 走完一局.
3. 在 Wireshark 里随便选一条 TCP 包 → 右键 **Decode As... → Data**, 或者直接看 Hex 视图:
   - 每帧的前 2 字节固定是 ASCII `GB` (0x47 0x42), 这就是我们的协议魔数;
   - 第 3 字节是版本 `0x01`, 第 4 字节是消息类型 (例如 `0x06` = `C2S_MOVE`, `0x65` = `S2C_REGISTER_RESP`);
   - 第 5–8 字节大端是 payload 长度, 后面就是明文 JSON, 一眼能看出 `{"seq":xxx,"data":{...}}` 的结构.
4. 保存抓包为 `captures/gobang_session.pcapng`, 提交报告时附上.

详见 [`protocol.md`](./protocol.md) §3.

---

## 7. 常见问题

| 问题 | 处理 |
|---|---|
| 双击 `.bat` 闪退 | 在 PowerShell 里手动跑, 查看错误信息. 大概率是 Python 没装 / 没在 PATH |
| 9527 端口被占用 | `run_server.bat` 改成 `--port 9528`, 客户端启动时 host:port 同步改 |
| 局域网内别人连不上 | 关闭 Windows 防火墙对该端口的拦截, 或新增入站规则放行 9527/tcp |
| 客户端弹"协议错误" | 通常是服务端与客户端协议版本不一致, 重新拉最新代码即可 |
| 想清空所有账号 | 关掉服务端, 删除 `data/users.json` |

---

## 8. 新增功能使用说明

### 8.1 实时观战
- 大厅中会显示“进行中房间”列表;
- 双击某个房间可进入观战模式;
- 观战时可看到实时落子/聊天/结算, 但不可落子。

### 8.2 断线重连 (60 秒)
- 对局玩家断线后, 服务端保留对局 60 秒;
- 客户端会自动尝试重连并恢复棋盘状态;
- 超过 60 秒未恢复则按离开判负。

### 8.3 历史回放
- 大厅点击“历史回放”可查看个人历史对局列表;
- 选中一局后可打开回放窗口, 支持首步/上一步/下一步/末步。

### 8.4 排行榜 UI
- 大厅点击“排行榜”可刷新 Top10;
- 排行按积分降序, 同分按胜场排序。

---

## 9. 项目限制 / 改进方向

- 用户表用 JSON 文件存, 没有真正的数据库支持, 大规模并发不适用;
- 当前匹配是 FIFO, 没有按积分分档 (原项目有), 后续可以做;
- 客户端是单局即返回大厅, 没有"重连恢复"逻辑;
- 暂未实现 AI 对手 / 云部署 / Wireshark Lua dissector 等扩展方向, 详见 `report.md` §10.
