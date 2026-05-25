"""客户端默认连接配置（本地开发 / 打包发布共用）."""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, Any


# 单文件 exe 内置公网配置（与 config/online.json 保持一致，改服务器时同步修改）
_EMBEDDED_ONLINE: Dict[str, Any] = {
    "host": "140.143.202.203",
    "port": 9527,
    "title": "网络五子棋 - 公网服务器",
}


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_json(path: str) -> Dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_online_defaults() -> Dict[str, Any]:
    """开发时读 config/online.json；打包 exe 内置公网地址，无需附带 config 目录。"""
    frozen = getattr(sys, "frozen", False)
    if frozen:
        out = dict(_EMBEDDED_ONLINE)
        meipass = getattr(sys, "_MEIPASS", "")
        candidates = (
            os.path.join(meipass, "config", "online.json") if meipass else "",
            os.path.join(_app_dir(), "online.json"),
            os.path.join(_app_dir(), "config", "online.json"),
        )
    else:
        out: Dict[str, Any] = {"host": "127.0.0.1", "port": 9527}
        base = _app_dir()
        candidates = (
            os.path.join(base, "config", "online.json"),
            os.path.join(base, "online.json"),
        )
    for path in candidates:
        if not path:
            continue
        data = _read_json(path)
        if data:
            out.update(data)
            break
    if os.environ.get("GOBANG_HOST"):
        out["host"] = os.environ["GOBANG_HOST"].strip()
    if os.environ.get("GOBANG_PORT"):
        try:
            out["port"] = int(os.environ["GOBANG_PORT"])
        except ValueError:
            pass
    return out
