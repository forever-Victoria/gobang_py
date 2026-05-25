# 公网部署与发给同学游玩

## 一、重要说明

| 方式 | 是否可行 |
|------|----------|
| 浏览器打开 `http://140.143.202.203` 直接下棋 | **不行**（当前是 Tk 桌面 + TCP，不是网页游戏） |
| 双击 `开始游戏.bat` / `网络五子棋.exe` | **可以** |
| 访问你放的说明网页，再下载客户端 | **可以** |

默认连接配置在 `config/online.json`：

```json
{
  "host": "140.143.202.203",
  "port": 9527
}
```

---

## 二、云服务器（你已部署的一侧）

1. 上传项目，运行 `run_server.bat`（或 `python -m src.server.server --host 0.0.0.0 --port 9527`）
2. **安全组 / 防火墙** 放行 **TCP 9527** 入站
3. 看到日志 `GoBang server listening on 0.0.0.0:9527` 即正常

---

## 三、打包 Windows 客户端（在你自己电脑上执行一次）

```bat
cd gobang_py
build_release.bat
```

完成后得到文件夹 `release/`：

```
release/
  网络五子棋.exe      # 主程序
  开始游戏.bat        # 推荐给同学双击这个
  config/online.json  # 默认服务器 IP（可改）
  使用说明.txt
```

把整个 `release` 打成 **`gobang_client.zip`** 发给同学。

---

## 四、可选：做一个「网址」说明页（不是网页下棋）

把 `web/landing/index.html` 放到云服务器 Nginx，例如：

- `http://140.143.202.203/` → 显示下载说明 + 服务器地址
- `http://140.143.202.203/gobang_client.zip` → 提供客户端下载

Nginx 示例：

```nginx
server {
    listen 80;
    server_name 140.143.202.203;
    root /path/to/gobang_py/web/landing;
    index index.html;
}
```

---

## 五、同学端步骤

1. 解压 `gobang_client.zip`
2. 双击 **开始游戏.bat**
3. 注册 → 登录 → 匹配 / 人机对战

若连不上：检查服务器 9527 是否开放、服务端是否在跑。
