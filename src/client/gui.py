"""Tkinter GUI 客户端.

界面分三层 frame: 登录页 / 大厅页 / 对局页, 通过 _switch_to() 切换.

GUI 线程通过 root.after() 周期性地从 NetClient.events 队列里拉取后端推送,
保证所有 Tk 调用都在主线程.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from tkinter import messagebox, ttk
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.client.network import NetClient
from src.common.protocol import Frame, MsgType


# ============================================================
# UI Theme
# ============================================================

UI = {
    "bg": "#F6F3EA",
    "panel": "#EFE7D6",
    "card": "#FFFDF8",
    "text": "#1F2933",
    "muted": "#6B7280",
    "border": "#D9CEB8",
    "primary": "#0F766E",
    "primary_dark": "#115E59",
    "accent": "#B45309",
    "success": "#15803D",
    "danger": "#B91C1C",
    "danger_dark": "#991B1B",
    "warning_bg": "#FEF3C7",
    "warning_text": "#92400E",
    "dark_bg": "#18231F",
    "dark_card": "#203029",
    "dark_panel": "#121B18",
    "dark_border": "#40524A",
    "dark_text": "#EEF2E6",
}


def _ui_font(size: int, bold: bool = False):
    return ("Microsoft YaHei", size, "bold" if bold else "normal")


def _apply_style(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    root.option_add("*Font", _ui_font(10))
    root.option_add("*Entry.Font", _ui_font(10))
    root.option_add("*Listbox.Font", _ui_font(10))

    style.configure("TFrame", background=UI["bg"])
    style.configure("Panel.TFrame", background=UI["panel"])
    style.configure("Card.TFrame", background=UI["card"], relief="flat")
    style.configure("TLabel", background=UI["bg"], foreground=UI["text"], font=_ui_font(10))
    style.configure("Muted.TLabel", background=UI["bg"], foreground=UI["muted"], font=_ui_font(9))
    style.configure("Title.TLabel", background=UI["bg"], foreground=UI["text"], font=_ui_font(22, True))
    style.configure("Hero.TLabel", background=UI["card"], foreground=UI["text"], font=_ui_font(24, True))
    style.configure("H2.TLabel", background=UI["card"], foreground=UI["text"], font=_ui_font(16, True))
    style.configure("Card.TLabel", background=UI["card"], foreground=UI["text"], font=_ui_font(10))
    style.configure("CardMuted.TLabel", background=UI["card"], foreground=UI["muted"], font=_ui_font(9))
    style.configure("Metric.TLabel", background=UI["panel"], foreground=UI["text"], font=_ui_font(10, True))
    # 登录状态行：ttk.Label 不能用 fg=，须用独立 style
    style.configure("LoginStatusMuted.TLabel", background=UI["card"], foreground=UI["muted"], font=_ui_font(9))
    style.configure("LoginStatusErr.TLabel", background=UI["card"], foreground=UI["danger"], font=_ui_font(9))
    style.configure("LoginStatusOk.TLabel", background=UI["card"], foreground="#16A34A", font=_ui_font(9))

    style.configure("TEntry", fieldbackground="#FFFFFF", foreground=UI["text"], bordercolor=UI["border"], lightcolor=UI["border"], darkcolor=UI["border"], padding=(8, 6))
    style.map("TEntry", bordercolor=[("focus", UI["primary"])])

    style.configure("Primary.TButton", font=_ui_font(10, True), foreground="#FFFFFF", background=UI["primary"], borderwidth=0, padding=(12, 8))
    style.map("Primary.TButton", background=[("active", UI["primary_dark"])])
    style.configure("Secondary.TButton", font=_ui_font(10, True), foreground=UI["text"], background=UI["panel"], borderwidth=0, padding=(12, 8))
    style.map("Secondary.TButton", background=[("active", "#E3D8C4")])

    style.configure("Toolbar.TButton", font=_ui_font(9, True), foreground=UI["text"], background=UI["panel"], borderwidth=0, padding=(10, 7))
    style.map("Toolbar.TButton", background=[("active", "#E3D8C4")])

    style.configure("Treeview", font=_ui_font(10), rowheight=30, background=UI["card"], fieldbackground=UI["card"], foreground=UI["text"], borderwidth=0)
    style.configure("Treeview.Heading", font=_ui_font(10, True), background=UI["panel"], foreground=UI["text"], relief="flat")
    style.map("Treeview", background=[("selected", "#DDEFE9")], foreground=[("selected", UI["text"])])

    style.configure("Dark.TFrame", background=UI["dark_bg"])
    style.configure("DarkCard.TFrame", background=UI["dark_card"])
    style.configure("Dark.TLabel", background=UI["dark_bg"], foreground=UI["dark_text"], font=_ui_font(10))
    style.configure("DarkCard.TLabel", background=UI["dark_card"], foreground=UI["dark_text"], font=_ui_font(10))
    style.configure("DarkTitle.TLabel", background=UI["dark_bg"], foreground="#FFFFFF", font=_ui_font(17, True))
    style.configure("DarkMuted.TLabel", background=UI["dark_bg"], foreground="#B7C6BB", font=_ui_font(9))
    style.configure("TurnSpectate.TLabel", background=UI["dark_bg"], foreground="#FBBF24", font=_ui_font(10, True))
    style.configure("TurnYou.TLabel", background=UI["dark_bg"], foreground="#86EFAC", font=_ui_font(10, True))
    style.configure("TurnWait.TLabel", background=UI["dark_bg"], foreground="#B7C6BB", font=_ui_font(10, True))
    style.configure("TurnEmpty.TLabel", background=UI["dark_bg"], foreground=UI["dark_text"], font=_ui_font(10))
    style.configure("Danger.TButton", font=_ui_font(10, True), foreground="#FFFFFF", background=UI["danger"], borderwidth=0, padding=(12, 8))
    style.map("Danger.TButton", background=[("active", UI["danger_dark"])])

    return style

# ============================================================
# 日志
# ============================================================

def _setup_logging(log_dir: str, tag: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    name = f"gobang.client.{tag}"
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return log
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(
        os.path.join(log_dir, f"client_{tag}.log"),
        maxBytes=1024 * 1024, backupCount=2, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return log


# ============================================================
# 棋盘 Canvas 组件
# ============================================================

BOARD_BG = "#D9A85B"
BOARD_EDGE = "#8B5A2B"
LINE_COLOR = "#5C371B"
BLACK = 1
WHITE = 2


class BoardCanvas(tk.Canvas):
    def __init__(self, master, size: int = 15, cell: int = 32, on_click=None) -> None:
        self.size = size
        self.cell = cell
        self.margin = 28
        w = self.margin * 2 + cell * (size - 1)
        super().__init__(
            master,
            width=w,
            height=w,
            bg=BOARD_BG,
            highlightthickness=2,
            highlightbackground=BOARD_EDGE,
            relief="flat",
        )
        self._on_click = on_click
        self._stones = {}  # (r,c) -> canvas item ids
        self._last_marker = None
        self.bind("<Button-1>", self._on_click_evt)
        self._draw_grid()

    def _draw_grid(self) -> None:
        self.delete("grid")
        n = self.size
        board_max = self.margin + (n - 1) * self.cell
        self.create_rectangle(
            self.margin - 14,
            self.margin - 14,
            board_max + 14,
            board_max + 14,
            fill=BOARD_BG,
            outline=BOARD_EDGE,
            width=2,
            tags="grid",
        )
        for i in range(7):
            y = self.margin - 8 + i * 58
            self.create_line(
                self.margin - 8,
                y,
                board_max + 8,
                y + 16,
                fill="#E7BE77",
                width=1,
                tags="grid",
            )
        for i in range(n):
            x = self.margin + i * self.cell
            self.create_line(self.margin, x, board_max, x, fill=LINE_COLOR, tags="grid")
            self.create_line(x, self.margin, x, board_max, fill=LINE_COLOR, tags="grid")
        # 五个星位
        for r, c in [(3, 3), (3, 11), (11, 3), (11, 11), (7, 7)]:
            x = self.margin + c * self.cell
            y = self.margin + r * self.cell
            self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=LINE_COLOR, outline="", tags="grid")
        # 坐标
        for i in range(n):
            x = self.margin + i * self.cell
            self.create_text(x, 11, text=str(i), font=("Consolas", 8), fill="#6B4423", tags="grid")
            self.create_text(11, x, text=str(i), font=("Consolas", 8), fill="#6B4423", tags="grid")

    def reset(self) -> None:
        for items in self._stones.values():
            for item in items:
                self.delete(item)
        self._stones.clear()
        if self._last_marker:
            self.delete(self._last_marker)
            self._last_marker = None

    def place_stone(self, row: int, col: int, color: int) -> None:
        if (row, col) in self._stones:
            return
        x = self.margin + col * self.cell
        y = self.margin + row * self.cell
        r = self.cell // 2 - 2
        shadow = self.create_oval(x - r + 2, y - r + 3, x + r + 2, y + r + 3, fill="#7A4E26", outline="")
        if color == BLACK:
            item = self.create_oval(x - r, y - r, x + r, y + r, fill="#111111", outline="#000000")
            shine = self.create_oval(x - r + 5, y - r + 4, x - r + 12, y - r + 11, fill="#3A3A3A", outline="")
        else:
            item = self.create_oval(x - r, y - r, x + r, y + r, fill="#F8F5E9", outline="#B8AA91")
            shine = self.create_oval(x - r + 5, y - r + 4, x - r + 12, y - r + 11, fill="#FFFFFF", outline="")
        self._stones[(row, col)] = (shadow, item, shine)
        # 最近一手标记
        if self._last_marker:
            self.delete(self._last_marker)
        self._last_marker = self.create_oval(x - 7, y - 7, x + 7, y + 7, outline="#DC2626", width=2)

    def _on_click_evt(self, evt) -> None:
        col = round((evt.x - self.margin) / self.cell)
        row = round((evt.y - self.margin) / self.cell)
        if 0 <= row < self.size and 0 <= col < self.size and self._on_click:
            self._on_click(row, col)


# ============================================================
# 主 App
# ============================================================

class App:
    POLL_MS = 50  # 从 events 队列拉事件的间隔

    def __init__(self, host: str, port: int, log_dir: str) -> None:
        self.host = host
        self.port = port
        self.log_dir = log_dir
        self.log = _setup_logging(log_dir, "boot")

        self.net = NetClient(self.log)

        self.username: Optional[str] = None
        self.my_color: int = 0           # 1=黑, 2=白
        self.turn_color: int = 0
        self.board_size: int = 15
        self.in_game: bool = False
        self.is_spectating: bool = False
        self.opponent: str = ""
        self.current_room_id: int = 0

        self.root = tk.Tk()
        self.style = _apply_style(self.root)
        self.root.title("网络五子棋 (自定义 TCP 协议) - 未登录")
        self.root.geometry("980x680")
        self.root.minsize(860, 620)
        self.root.configure(bg=UI["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 三个 frame
        self._login_frame = self._build_login_frame()
        self._lobby_frame = self._build_lobby_frame()
        self._game_frame = self._build_game_frame()
        self._cur_frame: Optional[tk.Frame] = None
        self._switch_to(self._login_frame)

        # 启动事件轮询
        self.root.after(self.POLL_MS, self._poll_events)

    # ------------------------------ 公共 ------------------------------
    def run(self) -> None:
        self.root.mainloop()

    def _switch_to(self, frame: tk.Frame) -> None:
        if self._cur_frame is not None:
            self._cur_frame.pack_forget()
        self._cur_frame = frame
        frame.pack(fill=tk.BOTH, expand=True)

    def _on_close(self) -> None:
        try:
            if self.net.is_connected():
                self.net.send(MsgType.C2S_LOGOUT, {})
        except Exception:
            pass
        self.net.close()
        self.root.destroy()

    # ------------------------------ 登录页 ------------------------------
    def _build_login_frame(self) -> tk.Frame:
        root = ttk.Frame(self.root, style="TFrame")
        # 不要在这里 pack：由 _switch_to 统一显示，否则会与大厅/对局同时叠在主窗口

        shell = tk.Frame(root, bg=UI["card"], highlightbackground=UI["border"], highlightthickness=1)
        shell.place(relx=0.5, rely=0.5, anchor="center", width=760, height=460)

        brand = tk.Frame(shell, bg=UI["dark_bg"])
        brand.pack(side=tk.LEFT, fill=tk.BOTH, ipadx=26)
        tk.Label(
            brand,
            text="网络五子棋",
            bg=UI["dark_bg"],
            fg="#FFFFFF",
            font=_ui_font(24, True),
        ).pack(anchor="w", padx=28, pady=(48, 8))
        tk.Label(
            brand,
            text="TCP 对战 / 实时聊天 / 观战回放",
            bg=UI["dark_bg"],
            fg="#B7C6BB",
            font=_ui_font(10),
        ).pack(anchor="w", padx=28)
        board_preview = tk.Canvas(brand, width=180, height=180, bg=UI["dark_bg"], highlightthickness=0)
        board_preview.pack(anchor="w", padx=28, pady=(42, 0))
        for i in range(7):
            p = 22 + i * 22
            board_preview.create_line(22, p, 154, p, fill="#AF8B54")
            board_preview.create_line(p, 22, p, 154, fill="#AF8B54")
        for x, y, color in ((66, 66, "#111111"), (88, 88, "#F8F5E9"), (110, 88, "#111111"), (88, 110, "#F8F5E9")):
            board_preview.create_oval(x - 9, y - 9, x + 9, y + 9, fill=color, outline="#111111")

        card = ttk.Frame(shell, style="Card.TFrame")
        card.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=34, pady=34)

        header = ttk.Frame(card, style="Card.TFrame")
        header.pack(fill=tk.X, pady=(4, 10))
        ttk.Label(header, text="登录对战大厅", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(header, text="输入账号后即可进入匹配、排行榜和观战功能", style="CardMuted.TLabel").pack(anchor="w", pady=(6, 0))

        form = ttk.Frame(card, style="Card.TFrame")
        form.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=0)

        self._var_host = tk.StringVar(value=self.host)
        self._var_port = tk.StringVar(value=str(self.port))
        self._var_user = tk.StringVar()
        self._var_pwd = tk.StringVar()

        def field(row: int, label: str, var: tk.StringVar, width: int = 32, show: str | None = None):
            ttk.Label(form, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", pady=(10, 4))
            e = ttk.Entry(form, textvariable=var, width=width, show=show)
            e.grid(row=row + 1, column=0, sticky="ew")
            return e

        # server+port in one row
        ttk.Label(form, text="服务器", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=(10, 4))
        row0 = ttk.Frame(form, style="Card.TFrame")
        row0.grid(row=1, column=0, sticky="ew")
        row0.columnconfigure(0, weight=1)
        ttk.Entry(row0, textvariable=self._var_host).grid(row=0, column=0, sticky="ew")
        ttk.Label(row0, text="端口", style="CardMuted.TLabel").grid(row=0, column=1, padx=(10, 6))
        ttk.Entry(row0, textvariable=self._var_port, width=8).grid(row=0, column=2)

        field(2, "用户名", self._var_user)
        field(4, "密码", self._var_pwd, show="*")

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.pack(fill=tk.X, pady=(18, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="登录", style="Primary.TButton", command=self._do_login).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="注册", style="Secondary.TButton", command=self._do_register).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self._login_status = ttk.Label(card, text="", style="LoginStatusMuted.TLabel")
        self._login_status.pack(anchor="w", pady=(14, 0))
        return root

    def _set_login_status(self, text: str, kind: str = "muted") -> None:
        """ttk.Label 不支持 fg=，必须用 style 切换颜色。"""
        styles = {"muted": "LoginStatusMuted.TLabel", "error": "LoginStatusErr.TLabel", "ok": "LoginStatusOk.TLabel", "info": "LoginStatusMuted.TLabel"}
        self._login_status.configure(text=text, style=styles.get(kind, "LoginStatusMuted.TLabel"))

    def _ensure_connected(self) -> bool:
        if self.net.is_connected():
            return True
        try:
            host = self._var_host.get().strip() or "127.0.0.1"
            port = int(self._var_port.get())
            self.net.connect(host, port)
            return True
        except (OSError, ValueError) as e:
            messagebox.showerror("连接失败", f"无法连接到服务器: {e}")
            return False

    def _do_login(self) -> None:
        u = self._var_user.get().strip()
        p = self._var_pwd.get()
        if not u or not p:
            self._set_login_status("用户名和密码不能为空", "error")
            return
        if not self._ensure_connected():
            return
        self._set_login_status("登录中...", "info")
        try:
            self.net.send(MsgType.C2S_LOGIN, {"username": u, "password": p})
        except Exception as e:
            self._set_login_status(f"发送失败: {e}", "error")

    def _do_register(self) -> None:
        u = self._var_user.get().strip()
        p = self._var_pwd.get()
        if not u or not p:
            self._set_login_status("用户名和密码不能为空", "error")
            return
        if not self._ensure_connected():
            return
        self._set_login_status("注册中...", "info")
        try:
            self.net.send(MsgType.C2S_REGISTER, {"username": u, "password": p})
        except Exception as e:
            self._set_login_status(f"发送失败: {e}", "error")

    # ------------------------------ 大厅页 ------------------------------
    def _build_lobby_frame(self) -> tk.Frame:
        root = ttk.Frame(self.root, style="TFrame")
        # 不要在这里 pack，见 _build_login_frame 说明

        header = tk.Frame(root, bg=UI["card"], highlightbackground=UI["border"], highlightthickness=1)
        header.pack(fill=tk.X, padx=20, pady=18)

        header.columnconfigure(0, weight=1)

        me_row = ttk.Frame(header, style="Card.TFrame")
        me_row.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        me_row.columnconfigure(0, weight=1)
        self._lbl_me = ttk.Label(me_row, text="", style="H2.TLabel")
        self._lbl_me.grid(row=0, column=0, sticky="w")
        ttk.Label(me_row, text="大厅", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))

        btnbar = ttk.Frame(header, style="Card.TFrame")
        btnbar.grid(row=0, column=1, rowspan=2, sticky="e", padx=18, pady=16)
        ttk.Button(btnbar, text="退出", style="Toolbar.TButton", command=self._on_close).grid(row=0, column=0, padx=5, pady=4)
        ttk.Button(btnbar, text="历史回放", style="Toolbar.TButton", command=self._do_replay_list).grid(row=0, column=1, padx=5, pady=4)
        ttk.Button(btnbar, text="排行榜", style="Toolbar.TButton", command=self._do_rank_refresh).grid(row=0, column=2, padx=5, pady=4)
        ttk.Button(btnbar, text="刷新观战", style="Toolbar.TButton", command=self._do_spectate_refresh).grid(row=1, column=0, padx=5, pady=4)
        self._btn_match = ttk.Button(btnbar, text="开始匹配", style="Primary.TButton", command=self._do_match_start)
        self._btn_match.grid(row=1, column=1, padx=5, pady=4, sticky="ew")
        ttk.Button(btnbar, text="AI 对战", style="Primary.TButton", command=self._do_ai_match_start).grid(row=1, column=2, padx=5, pady=4, sticky="ew")

        # 主体：左右两列（参考页面：左在线/房间，右排行榜）
        body = ttk.Frame(root, style="TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 14))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)

        left_col = ttk.Frame(body, style="TFrame")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_col.rowconfigure(0, weight=1)
        left_col.rowconfigure(1, weight=1)

        right_col = ttk.Frame(body, style="TFrame")
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.rowconfigure(0, weight=1)

        online_card = tk.Frame(left_col, bg=UI["card"], highlightbackground=UI["border"], highlightthickness=1)
        online_card.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        ttk.Label(online_card, text="当前在线玩家", style="Card.TLabel", font=_ui_font(11, True)).pack(anchor="w", padx=12, pady=(10, 6))
        self._online_list = tk.Listbox(
            online_card,
            height=10,
            bg=UI["card"],
            fg=UI["text"],
            selectbackground="#DDEFE9",
            selectforeground=UI["text"],
            highlightthickness=0,
            relief="flat",
            activestyle="none",
            borderwidth=0,
        )
        self._online_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        room_card = tk.Frame(left_col, bg=UI["card"], highlightbackground=UI["border"], highlightthickness=1)
        room_card.grid(row=1, column=0, sticky="nsew")
        top_row = ttk.Frame(room_card, style="Card.TFrame")
        top_row.pack(fill=tk.X, padx=12, pady=(10, 6))
        ttk.Label(top_row, text="进行中房间", style="Card.TLabel", font=_ui_font(11, True)).pack(side=tk.LEFT)
        ttk.Label(room_card, text="双击房间进入观战", style="CardMuted.TLabel").pack(anchor="w", padx=12, pady=(0, 6))
        self._spectate_list = tk.Listbox(
            room_card,
            height=7,
            bg=UI["card"],
            fg=UI["text"],
            selectbackground="#FDE7BF",
            selectforeground=UI["text"],
            highlightthickness=0,
            relief="flat",
            activestyle="none",
            borderwidth=0,
        )
        self._spectate_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self._spectate_list.bind("<Double-Button-1>", self._on_spectate_double_click)
        self._spectate_room_ids: list[int] = []

        rank_card = tk.Frame(right_col, bg=UI["card"], highlightbackground=UI["border"], highlightthickness=1)
        rank_card.grid(row=0, column=0, sticky="nsew")
        ttk.Label(rank_card, text="排行榜 Top10", style="Card.TLabel", font=_ui_font(11, True)).pack(anchor="w", padx=12, pady=(10, 6))

        cols = ("rank", "user", "score", "win", "total")
        self._rank_tree = ttk.Treeview(rank_card, columns=cols, show="headings", height=12)
        self._rank_tree.heading("rank", text="排名")
        self._rank_tree.heading("user", text="用户名")
        self._rank_tree.heading("score", text="积分")
        self._rank_tree.heading("win", text="胜场")
        self._rank_tree.heading("total", text="总场")
        self._rank_tree.column("rank", width=40, anchor="center")
        self._rank_tree.column("user", width=120, anchor="center")
        self._rank_tree.column("score", width=80, anchor="center")
        self._rank_tree.column("win", width=80, anchor="center")
        self._rank_tree.column("total", width=80, anchor="center")
        self._rank_tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._lbl_queue = ttk.Label(root, text="匹配队列: 0 人", style="Muted.TLabel")
        self._lbl_queue.pack(anchor="w", padx=24, pady=(0, 12))
        return root

    def _do_match_start(self) -> None:
        if self._btn_match["text"] == "开始匹配":
            self.net.send(MsgType.C2S_MATCH_START, {})
            self._btn_match.config(text="取消匹配")
        else:
            self.net.send(MsgType.C2S_MATCH_STOP, {})
            self._btn_match.config(text="开始匹配")

    def _do_ai_match_start(self) -> None:
        if self._btn_match["text"] == "取消匹配":
            self.net.send(MsgType.C2S_MATCH_STOP, {})
            self._btn_match.config(text="开始匹配")
        self.net.send(MsgType.C2S_AI_MATCH_START, {})

    def _do_spectate_refresh(self) -> None:
        self.net.send(MsgType.C2S_SPECTATE_LIST, {})

    def _fill_spectate_rooms(self, rooms: list) -> None:
        """列表仅展示对局双方，room_id 存于 _spectate_room_ids 供协议使用。"""
        self._spectate_list.delete(0, tk.END)
        self._spectate_room_ids.clear()
        for room in rooms:
            self._spectate_room_ids.append(int(room.get("room_id", 0)))
            line = f"{room.get('black')} vs {room.get('white')}  观战:{room.get('observer_count', 0)}"
            self._spectate_list.insert(tk.END, line)

    def _on_spectate_double_click(self, _evt) -> None:
        sel = self._spectate_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._spectate_room_ids):
            return
        self.net.send(MsgType.C2S_SPECTATE_JOIN, {"room_id": self._spectate_room_ids[idx]})

    def _do_rank_refresh(self) -> None:
        self.net.send(MsgType.C2S_RANK_LIST, {"limit": 10})

    def _do_replay_list(self) -> None:
        self.net.send(MsgType.C2S_REPLAY_LIST, {"limit": 30, "offset": 0})

    # ------------------------------ 对局页 ------------------------------
    def _build_game_frame(self) -> tk.Frame:
        root = ttk.Frame(self.root, style="Dark.TFrame")
        # 不要在这里 pack，见 _build_login_frame 说明

        # 顶部栏（深色）
        header = ttk.Frame(root, style="Dark.TFrame")
        header.pack(fill=tk.X, padx=20, pady=16)
        header.columnconfigure(0, weight=1)

        left = ttk.Frame(header, style="Dark.TFrame")
        left.grid(row=0, column=0, sticky="w")
        self._lbl_game_title = ttk.Label(left, text="对局", style="DarkTitle.TLabel")
        self._lbl_game_title.pack(anchor="w")
        self._lbl_game_sub = ttk.Label(left, text="", style="DarkMuted.TLabel")
        self._lbl_game_sub.pack(anchor="w", pady=(3, 0))

        right = ttk.Frame(header, style="Dark.TFrame")
        right.grid(row=0, column=1, sticky="e")
        self._lbl_turn = ttk.Label(right, text="", style="Dark.TLabel")
        self._lbl_turn.grid(row=0, column=0, padx=(0, 10))
        self._btn_leave = ttk.Button(right, text="认输离开", style="Danger.TButton", command=self._do_leave)
        self._btn_leave.grid(row=0, column=1)

        # 主体：左棋盘卡片 + 右聊天卡片
        body = ttk.Frame(root, style="Dark.TFrame")
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        board_card = tk.Frame(body, bg=UI["dark_card"], highlightbackground=UI["dark_border"], highlightthickness=1)
        board_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        board_card.columnconfigure(0, weight=1)
        board_card.rowconfigure(0, weight=1)
        board_wrap = tk.Frame(board_card, bg=UI["dark_card"])
        board_wrap.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        self._board = BoardCanvas(board_wrap, size=15, cell=30, on_click=self._on_board_click)
        self._board.pack(expand=True)

        chat_card = tk.Frame(body, bg=UI["dark_card"], highlightbackground=UI["dark_border"], highlightthickness=1)
        chat_card.grid(row=0, column=1, sticky="nsew")
        chat_card.rowconfigure(1, weight=1)
        chat_card.columnconfigure(0, weight=1)
        ttk.Label(chat_card, text="房间聊天", style="DarkCard.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 8))

        chat_wrap = tk.Frame(chat_card, bg=UI["dark_card"])
        chat_wrap.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        chat_wrap.rowconfigure(0, weight=1)
        chat_wrap.columnconfigure(0, weight=1)
        self._chat = tk.Text(
            chat_wrap,
            height=18,
            width=30,
            state=tk.DISABLED,
            font=("Microsoft YaHei", 10),
            bg=UI["dark_panel"],
            fg="#E2E8F0",
            insertbackground="#E2E8F0",
            relief="flat",
            highlightthickness=1,
            highlightbackground=UI["dark_border"],
            wrap=tk.WORD,
            borderwidth=0,
        )
        self._chat.grid(row=0, column=0, sticky="nsew")
        self._bind_text_mousewheel(self._chat)

        chat_in = tk.Frame(chat_card, bg=UI["dark_card"])
        chat_in.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        chat_in.columnconfigure(0, weight=1)
        self._chat_var = tk.StringVar()
        e = tk.Entry(
            chat_in,
            textvariable=self._chat_var,
            bg=UI["dark_panel"],
            fg="#E2E8F0",
            insertbackground="#E2E8F0",
            relief="flat",
            highlightthickness=1,
            highlightbackground=UI["dark_border"],
        )
        e.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=6)
        e.bind("<Return>", lambda _e: self._do_chat())
        tk.Button(
            chat_in,
            text="发送",
            command=self._do_chat,
            bg=UI["primary"],
            fg="#FFFFFF",
            activebackground=UI["primary_dark"],
            activeforeground="#FFFFFF",
            relief="flat",
            borderwidth=0,
            font=_ui_font(10, True),
        ).grid(row=0, column=1, ipadx=16, ipady=7)
        return root

    def _on_board_click(self, row: int, col: int) -> None:
        if not self.in_game or self.is_spectating:
            return
        if self.my_color == 0 or self.turn_color != self.my_color:
            self._append_chat("[提示] 还没轮到你下棋")
            return
        self.net.send(MsgType.C2S_MOVE, {"row": row, "col": col})

    def _do_chat(self) -> None:
        text = self._chat_var.get().strip()
        if not text:
            return
        self._chat_var.set("")
        self.net.send(MsgType.C2S_CHAT, {"text": text})

    def _do_leave(self) -> None:
        if self.is_spectating:
            self.is_spectating = False
            self.in_game = False
            self._switch_to(self._lobby_frame)
            return
        if messagebox.askyesno("确认", "确定离开当前对局? 中途离开判负."):
            self.net.send(MsgType.C2S_LEAVE_ROOM, {})

    @staticmethod
    def _bind_text_mousewheel(text: tk.Text) -> None:
        """隐藏滚动条时仍可用滚轮浏览聊天记录。"""
        def on_wheel(event: tk.Event) -> str:
            if event.num == 5 or event.delta < 0:
                text.yview_scroll(3, "units")
            elif event.num == 4 or event.delta > 0:
                text.yview_scroll(-3, "units")
            return "break"

        text.bind("<MouseWheel>", on_wheel)
        text.bind("<Button-4>", on_wheel)
        text.bind("<Button-5>", on_wheel)

    def _append_chat(self, line: str) -> None:
        self._chat.config(state=tk.NORMAL)
        self._chat.insert(tk.END, line + "\n")
        self._chat.see(tk.END)
        self._chat.config(state=tk.DISABLED)

    # ------------------------------ 事件分发 ------------------------------
    def _poll_events(self) -> None:
        try:
            while True:
                frame = self.net.events.get_nowait()
                self._handle_frame(frame)
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll_events)

    def _handle_frame(self, f: Frame) -> None:
        t = f.type
        d = f.data
        if t == MsgType.S2C_REGISTER_RESP:
            if d.get("ok"):
                self._set_login_status("注册成功, 请登录", "ok")
            else:
                self._set_login_status(f"注册失败: {d.get('reason')}", "error")

        elif t == MsgType.S2C_LOGIN_RESP:
            if d.get("ok"):
                self.username = d.get("username")
                self.log = _setup_logging(self.log_dir, str(self.username))
                self.net.log = self.log
                self.root.title(f"网络五子棋 - {self.username} (score={d.get('score')})")
                self._lbl_me.config(
                    text=f"我是 [{self.username}]    积分 {d.get('score')}    "
                         f"总场 {d.get('total')}    胜场 {d.get('win')}"
                )
                self._switch_to(self._lobby_frame)
            else:
                self._set_login_status(f"登录失败: {d.get('reason')}", "error")

        elif t == MsgType.S2C_LOBBY_INFO:
            self._online_list.delete(0, tk.END)
            for name in d.get("online", []):
                tag = "  (我)" if name == self.username else ""
                self._online_list.insert(tk.END, name + tag)
            self._lbl_queue.config(text=f"匹配队列: {d.get('queue_size', 0)} 人 | "
                                         f"在线: {d.get('online_count', 0)} 人")
            self._fill_spectate_rooms(d.get("active_rooms", []))

        elif t == MsgType.S2C_MATCH_OK:
            self.is_spectating = False
            self._btn_leave.config(text="认输离开")
            self.my_color = int(d.get("your_color", 0))
            self.turn_color = int(d.get("turn_color", BLACK))
            self.board_size = int(d.get("board_size", 15))
            opp = d.get("opponent", {})
            self.opponent = opp.get("username", "?")
            self.current_room_id = int(d.get("room_id", 0))
            self.in_game = True
            self._board.reset()
            self._chat.config(state=tk.NORMAL)
            self._chat.delete("1.0", tk.END)
            self._chat.config(state=tk.DISABLED)
            my_str = "黑棋(先手)" if self.my_color == BLACK else "白棋(后手)"
            self._lbl_game_title.config(text="对局")
            self._lbl_game_sub.config(text=f"你: {self.username} ({my_str})   对手: {self.opponent} (积分 {opp.get('score', '-')})")
            self._update_turn_label()
            self._btn_match.config(text="开始匹配")
            self._append_chat(f"[系统] 匹配成功! 对手: {self.opponent}")
            self._append_chat("[系统] 黑棋先行")
            self._switch_to(self._game_frame)

        elif t == MsgType.S2C_MOVE_RESULT:
            if not d.get("ok"):
                self._append_chat(f"[非法落子] {d.get('reason')}")
                return
            row = int(d.get("row"))
            col = int(d.get("col"))
            color = int(d.get("color"))
            self._board.place_stone(row, col, color)
            self.turn_color = int(d.get("next_turn", 0))
            self._update_turn_label()

        elif t == MsgType.S2C_SPECTATE_LIST:
            self._fill_spectate_rooms(d.get("rooms", []))

        elif t == MsgType.S2C_SPECTATE_SNAPSHOT:
            self.in_game = True
            self.is_spectating = True
            self._btn_leave.config(text="离开")
            self.current_room_id = int(d.get("room_id", 0))
            self.my_color = 0
            self.turn_color = int(d.get("turn_color", BLACK))
            self.opponent = f"{d.get('black')} vs {d.get('white')}"
            self._board.reset()
            self._chat.config(state=tk.NORMAL)
            self._chat.delete("1.0", tk.END)
            self._chat.config(state=tk.DISABLED)
            board = d.get("board", [])
            for r, row in enumerate(board):
                for c, color in enumerate(row):
                    if color in (BLACK, WHITE):
                        self._board.place_stone(r, c, int(color))
            for line in d.get("chat_log", []):
                self._append_chat(f"{line.get('from')}: {line.get('text')}")
            self._lbl_game_title.config(text="观战")
            self._lbl_game_sub.config(text=f"玩家: {d.get('black')} vs {d.get('white')}")
            self._lbl_turn.configure(text="观战模式", style="TurnSpectate.TLabel")
            self._switch_to(self._game_frame)

        elif t == MsgType.S2C_RECONNECT_RESP:
            if not d.get("ok"):
                self._append_chat(f"[重连失败] {d.get('reason')}")
                return
            st = d.get("room_state", {})
            self.in_game = True
            self.is_spectating = False
            self._btn_leave.config(text="认输离开")
            self.current_room_id = int(st.get("room_id", 0))
            self.my_color = int(st.get("your_color", 0))
            self.turn_color = int(st.get("turn_color", BLACK))
            self.opponent = str(st.get("opponent", "?"))
            self._board.reset()
            for r, row in enumerate(st.get("board", [])):
                for c, color in enumerate(row):
                    if color in (BLACK, WHITE):
                        self._board.place_stone(r, c, int(color))
            self._lbl_game_title.config(text="对局")
            self._lbl_game_sub.config(text=f"你: {self.username}   对手: {self.opponent} (重连恢复)")
            self._update_turn_label()
            self._switch_to(self._game_frame)

        elif t == MsgType.S2C_REPLAY_LIST:
            items = d.get("items", [])
            if not items:
                messagebox.showinfo("历史回放", "暂无历史对局")
                return
            win = tk.Toplevel(self.root)
            win.title("历史回放列表")
            win.geometry("680x400")
            win.minsize(520, 320)

            def open_selected() -> None:
                sel = tree.selection()
                if not sel:
                    messagebox.showinfo("历史回放", "请先选择一条对局记录", parent=win)
                    return
                self.net.send(MsgType.C2S_REPLAY_GET, {"replay_id": int(sel[0])})

            btnbar = ttk.Frame(win)
            btnbar.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=(0, 12))
            ttk.Button(btnbar, text="关闭", style="Secondary.TButton", command=win.destroy).pack(side=tk.RIGHT, padx=(8, 0))
            ttk.Button(btnbar, text="开始回放", style="Primary.TButton", command=open_selected).pack(side=tk.RIGHT)

            table_wrap = ttk.Frame(win)
            table_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=12, pady=12)
            table_wrap.rowconfigure(0, weight=1)
            table_wrap.columnconfigure(0, weight=1)

            cols = ("players", "winner", "moves", "ended")
            tree = ttk.Treeview(table_wrap, columns=cols, show="headings", height=10)
            for c, h, w in (
                ("players", "玩家", 250),
                ("winner", "胜者", 100),
                ("moves", "步数", 80),
                ("ended", "结束时间", 160),
            ):
                tree.heading(c, text=h)
                tree.column(c, width=w, anchor="center")
            tree.grid(row=0, column=0, sticky="nsew")
            tree.bind("<Double-Button-1>", lambda _e: open_selected())
            for x in items:
                players = list(x.get("players", []) or [])
                black = players[0] if len(players) >= 1 else "?"
                white = players[1] if len(players) >= 2 else "?"
                winner = int(x.get("winner", 0) or 0)
                if winner == BLACK:
                    winner_name = str(black)
                elif winner == WHITE:
                    winner_name = str(white)
                elif winner == 3:
                    winner_name = "和棋"
                else:
                    winner_name = "-"
                ended_ts = x.get("ended_at")
                try:
                    ended_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ended_ts)))
                except Exception:
                    ended_str = str(ended_ts)
                tree.insert(
                    "",
                    tk.END,
                    iid=str(x.get("replay_id")),
                    values=(
                        f"{black} vs {white}",
                        winner_name,
                        x.get("move_count", 0),
                        ended_str,
                    ),
                )
            if tree.get_children():
                tree.selection_set(tree.get_children()[0])
                tree.focus(tree.get_children()[0])

        elif t == MsgType.S2C_REPLAY_DATA:
            if not d.get("ok"):
                messagebox.showerror("回放", d.get("reason", "读取失败"))
                return
            replay = d.get("replay", {})
            self._open_replay_window(replay)

        elif t == MsgType.S2C_RANK_LIST:
            for item in self._rank_tree.get_children():
                self._rank_tree.delete(item)
            for row in d.get("items", []):
                self._rank_tree.insert(
                    "",
                    tk.END,
                    values=(row.get("rank"), row.get("username"), row.get("score"), row.get("win"), row.get("total")),
                )

        elif t == MsgType.S2C_CHAT_BCAST:
            self._append_chat(f"{d.get('from')}: {d.get('text')}")

        elif t == MsgType.S2C_ROOM_CLOSED:
            self.in_game = False
            self.is_spectating = False
            self.my_color = 0
            self.turn_color = 0
            result = d.get("your_result", "?")
            reason = d.get("reason", "")
            zh = {"win": "胜利", "lose": "失败", "draw": "和棋", "abort": "中止"}.get(result, result)
            self._append_chat(f"[系统] 对局结束: {zh}  ({reason})")
            messagebox.showinfo("对局结束", f"结果: {zh}\n原因: {reason}")
            self._switch_to(self._lobby_frame)

        elif t == MsgType.S2C_ERROR:
            code = d.get("code", "")
            reason = d.get("reason", "")
            if code == "DISCONNECTED":
                if self.username:
                    self._append_chat("[系统] 检测到断线，正在尝试重连...")
                    self._try_auto_reconnect()
                else:
                    messagebox.showerror("连接断开", f"与服务器的连接已断开: {reason}")
                    self.in_game = False
                    self.username = None
                    self.root.title("网络五子棋 - 未登录")
                    self._switch_to(self._login_frame)
            else:
                self._append_chat(f"[服务端错误] {code}: {reason}")
                if self._cur_frame is self._login_frame:
                    self._set_login_status(f"错误: {reason}", "error")

        elif t == MsgType.S2C_PONG:
            pass

    def _update_turn_label(self) -> None:
        if not self.in_game:
            self._lbl_turn.configure(text="", style="TurnEmpty.TLabel")
            return
        if self.is_spectating:
            self._lbl_turn.configure(text="观战模式", style="TurnSpectate.TLabel")
            return
        if self.turn_color == self.my_color:
            self._lbl_turn.configure(text="● 轮到你了", style="TurnYou.TLabel")
        else:
            self._lbl_turn.configure(text="○ 等待对手...", style="TurnWait.TLabel")

    def _try_auto_reconnect(self) -> None:
        # 后台重连，成功后自动登录并请求恢复
        def worker() -> None:
            host = self._var_host.get().strip() or "127.0.0.1"
            port = int(self._var_port.get() or "9527")
            user = self._var_user.get().strip()
            pwd = self._var_pwd.get()
            for _ in range(5):
                try:
                    self.net.reconnect(host, port, timeout=3.0)
                    self.net.send(MsgType.C2S_LOGIN, {"username": user, "password": pwd})
                    time.sleep(0.2)
                    self.net.send_reconnect_resume(self.current_room_id if self.current_room_id else None)
                    return
                except Exception:
                    time.sleep(1.0)
            self.root.after(0, lambda: messagebox.showerror("连接断开", "自动重连失败，请手动重新登录"))

        threading.Thread(target=worker, name="auto-reconnect", daemon=True).start()

    def _open_replay_window(self, replay: dict) -> None:
        win = tk.Toplevel(self.root)
        win.title("回放")
        win.geometry("760x640")
        top = tk.Frame(win)
        top.pack(fill=tk.X, padx=8, pady=6)
        players = list(replay.get("players", []) or [])
        black = players[0] if len(players) >= 1 else "?"
        white = players[1] if len(players) >= 2 else "?"
        winner = int(replay.get("winner", 0) or 0)
        if winner == BLACK:
            winner_name = str(black)
        elif winner == WHITE:
            winner_name = str(white)
        elif winner == 3:
            winner_name = "和棋"
        else:
            winner_name = "-"
        tk.Label(top, text=f"玩家: {black} vs {white}").pack(side=tk.LEFT)
        tk.Label(top, text=f"胜者: {winner_name}").pack(side=tk.LEFT, padx=16)
        board = BoardCanvas(win, size=15, cell=30, on_click=None)
        board.pack(pady=6)
        moves = replay.get("moves", [])
        idx_var = tk.IntVar(value=0)
        lbl = tk.Label(win, text=f"步数: 0 / {len(moves)}")
        lbl.pack()

        def redraw(step: int) -> None:
            step = max(0, min(step, len(moves)))
            board.reset()
            for mv in moves[:step]:
                board.place_stone(int(mv["row"]), int(mv["col"]), int(mv["color"]))
            idx_var.set(step)
            lbl.config(text=f"步数: {step} / {len(moves)}")

        ctr = tk.Frame(win)
        ctr.pack(pady=6)
        tk.Button(ctr, text="|<", command=lambda: redraw(0)).pack(side=tk.LEFT, padx=4)
        tk.Button(ctr, text="<", command=lambda: redraw(idx_var.get() - 1)).pack(side=tk.LEFT, padx=4)
        tk.Button(ctr, text=">", command=lambda: redraw(idx_var.get() + 1)).pack(side=tk.LEFT, padx=4)
        tk.Button(ctr, text=">|", command=lambda: redraw(len(moves))).pack(side=tk.LEFT, padx=4)
        redraw(0)


# ============================================================
# 入口
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="GoBang Tk Client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9527)
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    parser.add_argument("--log-dir", default=os.path.join(base, "logs"))
    args = parser.parse_args()

    app = App(args.host, args.port, args.log_dir)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
