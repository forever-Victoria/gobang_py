"""服务端监控面板.

该模块在同一进程内启动 GoBangServer, 并用 Tkinter 定时读取服务端快照。
监控面板只做展示, 不修改在线玩家、房间或匹配队列状态。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.server.server import GoBangServer


UI = {
    "bg": "#F6F3EA",
    "card": "#FFFDF8",
    "panel": "#EFE7D6",
    "text": "#1F2933",
    "muted": "#6B7280",
    "border": "#D9CEB8",
    "primary": "#0F766E",
    "dark": "#18231F",
    "danger": "#B91C1C",
}


def _font(size: int, bold: bool = False):
    return ("Microsoft YaHei", size, "bold" if bold else "normal")


def _format_uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时 {m}分 {s}秒"
    if m:
        return f"{m}分 {s}秒"
    return f"{s}秒"


class MonitorApp:
    REFRESH_MS = 1000

    def __init__(self, server: GoBangServer, log_path: str) -> None:
        self.server = server
        self.log_path = log_path

        self.root = tk.Tk()
        self.root.title("GoBang 服务端监控面板")
        self.root.geometry("1080x720")
        self.root.minsize(980, 640)
        self.root.configure(bg=UI["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._apply_style()
        self._build_ui()
        self.root.after(200, self._refresh)

    def run(self) -> None:
        self.root.mainloop()

    def _apply_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.root.option_add("*Font", _font(10))
        style.configure("TFrame", background=UI["bg"])
        style.configure("Card.TFrame", background=UI["card"])
        style.configure("TLabel", background=UI["bg"], foreground=UI["text"], font=_font(10))
        style.configure("Muted.TLabel", background=UI["bg"], foreground=UI["muted"], font=_font(9))
        style.configure("Title.TLabel", background=UI["bg"], foreground=UI["text"], font=_font(20, True))
        style.configure("CardTitle.TLabel", background=UI["card"], foreground=UI["text"], font=_font(12, True))
        style.configure("Metric.TLabel", background=UI["panel"], foreground=UI["text"], font=_font(10, True))
        style.configure("Treeview", font=_font(10), rowheight=28, background=UI["card"], fieldbackground=UI["card"], borderwidth=0)
        style.configure("Treeview.Heading", font=_font(10, True), background=UI["panel"], relief="flat")
        style.map("Treeview", background=[("selected", "#DDEFE9")], foreground=[("selected", UI["text"])])

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, style="TFrame")
        header.pack(fill=tk.X, padx=20, pady=(18, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="GoBang 服务端监控面板", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self._status = ttk.Label(header, text="启动中...", style="Muted.TLabel")
        self._status.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self._metrics = ttk.Frame(self.root, style="TFrame")
        self._metrics.pack(fill=tk.X, padx=20, pady=(0, 14))

        body = ttk.Frame(self.root, style="TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self._online_tree = self._make_tree(
            body,
            "在线用户",
            ("username", "status", "addr"),
            ("用户名", "状态", "地址"),
            (120, 170, 170),
        )
        self._online_tree.master.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))

        self._room_tree = self._make_tree(
            body,
            "活动房间",
            ("room", "players", "turn", "moves", "observers"),
            ("房间", "玩家", "轮到", "步数", "观战"),
            (70, 230, 80, 70, 70),
        )
        self._room_tree.master.grid(row=0, column=1, sticky="nsew", pady=(0, 10))

        self._queue_tree = self._make_tree(
            body,
            "匹配队列",
            ("pos", "username"),
            ("序号", "用户名"),
            (70, 180),
        )
        self._queue_tree.master.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        log_card = self._card(body, "服务端日志")
        log_card.grid(row=1, column=1, sticky="nsew")
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        self._log_text = tk.Text(
            log_card,
            height=10,
            bg="#121B18",
            fg="#EEF2E6",
            insertbackground="#EEF2E6",
            relief="flat",
            borderwidth=0,
            wrap=tk.WORD,
            font=("Consolas", 9),
        )
        self._log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._log_text.configure(state=tk.DISABLED)

    def _card(self, parent: tk.Widget, title: str) -> tk.Frame:
        card = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["border"], highlightthickness=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 8))
        return card

    def _make_tree(
        self,
        parent: tk.Widget,
        title: str,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
        widths: tuple[int, ...],
    ) -> ttk.Treeview:
        card = self._card(parent, title)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        tree = ttk.Treeview(card, columns=columns, show="headings")
        for col, heading, width in zip(columns, headings, widths):
            tree.heading(col, text=heading)
            tree.column(col, width=width, anchor="center")
        tree.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        return tree

    def _refresh(self) -> None:
        try:
            snap = self.server.get_monitor_snapshot()
            self._render_snapshot(snap)
            self._render_log()
        except Exception as exc:
            self._status.configure(text=f"刷新失败: {exc}")
        self.root.after(self.REFRESH_MS, self._refresh)

    def _render_snapshot(self, snap: dict[str, Any]) -> None:
        stats = snap["stats"]
        db = snap["db"]
        if stats.get("startup_error"):
            self._status.configure(
                text=f"启动失败 | {stats['host']}:{stats['port']} | {stats['startup_error']} | "
                     "请先关闭其它服务端窗口后重新打开监控面板"
            )
        else:
            state = "运行中" if stats["running"] else "已停止"
            self._status.configure(
                text=f"{state} | {stats['host']}:{stats['port']} | 启动时间 "
                     f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stats['started_at']))}"
            )
        metrics = [
            ("运行时长", _format_uptime(stats["uptime_sec"])),
            ("当前连接", str(stats["current_connections"])),
            ("累计连接", str(stats["total_connections"])),
            ("处理消息", str(stats["total_messages"])),
            ("注册用户", str(db["user_count"])),
            ("历史对局", str(db["replay_count"])),
        ]
        for child in self._metrics.winfo_children():
            child.destroy()
        for idx, (label, value) in enumerate(metrics):
            item = tk.Frame(self._metrics, bg=UI["panel"], highlightbackground=UI["border"], highlightthickness=1)
            item.grid(row=0, column=idx, sticky="ew", padx=(0, 10))
            self._metrics.columnconfigure(idx, weight=1)
            tk.Label(item, text=value, bg=UI["panel"], fg=UI["text"], font=_font(14, True)).pack(anchor="w", padx=12, pady=(8, 0))
            tk.Label(item, text=label, bg=UI["panel"], fg=UI["muted"], font=_font(9)).pack(anchor="w", padx=12, pady=(2, 8))

        self._replace_tree(self._online_tree, [
            (x["username"], x["status"], x["addr"]) for x in snap["online"]
        ])
        self._replace_tree(self._queue_tree, [
            (idx + 1, name) for idx, name in enumerate(snap["queue"])
        ])
        self._replace_tree(self._room_tree, [
            (
                f"#{x['room_id']}",
                f"{x['black']} vs {x['white']}",
                x["turn"],
                x["move_count"],
                x["observer_count"],
            )
            for x in snap["rooms"]
        ])

    @staticmethod
    def _replace_tree(tree: ttk.Treeview, rows: list[tuple[Any, ...]]) -> None:
        for item in tree.get_children():
            tree.delete(item)
        for row in rows:
            tree.insert("", tk.END, values=row)

    def _render_log(self) -> None:
        lines = self._tail_lines(self.log_path, 80)
        self._log_text.configure(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        self._log_text.insert(tk.END, "".join(lines))
        self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    @staticmethod
    def _tail_lines(path: str, limit: int) -> list[str]:
        if not os.path.exists(path):
            return ["日志文件尚未创建。\n"]
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-limit:]

    def _on_close(self) -> None:
        if messagebox.askokcancel("退出", "关闭监控面板并停止服务端?"):
            self.server.shutdown()
            self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="GoBang Server Monitor")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9527)
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    parser.add_argument("--data-dir", default=os.path.join(base, "data"))
    parser.add_argument("--log-dir", default=os.path.join(base, "logs"))
    args = parser.parse_args()

    server = GoBangServer(args.host, args.port, args.data_dir, args.log_dir)
    thread = threading.Thread(target=server.start, name="gobang-server", daemon=True)
    thread.start()

    app = MonitorApp(server, os.path.join(args.log_dir, "server.log"))
    try:
        app.run()
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
