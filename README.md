# 网络五子棋 — 编译与运行

## 环境要求

- Windows 10/11（macOS / Linux 亦可）
- Python 3.8+，仅需标准库（`socket` / `threading` / `tkinter` / `sqlite3` 等）

## 运行（源码）

在项目根目录 `gobang_py/` 下执行。

**启动服务端**（默认 `0.0.0.0:9527`）：

```powershell
.\run_server.bat
# 或
python -m src.server.server --host 0.0.0.0 --port 9527
```

**启动客户端**（可多开；默认连 `127.0.0.1:9527`）：

```powershell
.\run_client.bat
# 或
python -m src.client.gui --host 127.0.0.1 --port 9527
```

跨机联机时，客户端登录页将服务器地址改为服务端 IP，端口保持 `9527`。

**可选 — 服务端监控面板**：

```powershell
.\run_server_monitor.bat
```

**可选 — 自动化冒烟测试**（不打开 GUI）：

```powershell
.\run_smoketest.bat
# 或
python smoketest.py
```

## 运行（exe)

有本地和云端两种方式

1. 本地：打开`server.bat`双击`网络五子棋.exe`即可无python环境直接体验客户端所有功能
2. 云端：双击`网络五子棋.exe`并将服务器地址改为服务端 IP`140.143.202.203`（*注：我们的服务器只租了一个月，超时可能连接不上*）

