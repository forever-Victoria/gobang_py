"""客户端启动入口（便于打包为 exe）."""

from __future__ import annotations

import os
import sys


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        root = os.path.dirname(sys.executable)
    if root not in sys.path:
        sys.path.insert(0, root)

    from src.client.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
