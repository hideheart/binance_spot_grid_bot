# -*- coding: utf-8 -*-
"""
一次啟動網格機器人、定投機器人與 Dashboard。
"""
import asyncio
import logging
import os
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from grid_bot import GridBot, logger as grid_logger
from dca_bot import DCABot, logger as dca_logger

# Dashboard 在 web/ 目錄
WEB_DIR = os.path.join(ROOT, "web")
if WEB_DIR not in sys.path:
    sys.path.insert(0, WEB_DIR)
import server as dashboard  # noqa: E402


def _start_dashboard(port: int):
    try:
        dashboard.run_server(port)
    except Exception as e:
        print(f"[WEB] Dashboard 異常退出: {e}", flush=True)


async def _run_bots(grid: GridBot, dca: DCABot):
    results = await asyncio.gather(
        grid.start(),
        dca.start(),
        return_exceptions=True,
    )
    for name, result in (("GRID", results[0]), ("DCA", results[1])):
        if isinstance(result, Exception):
            logging.getLogger("run_all").error("%s 任務結束: %s", name, result)


def main():
    port = 5000
    print("===================================================")
    print("  一次啟動：網格 + 定投 + Dashboard")
    print(f"  Dashboard: http://localhost:{port}")
    print("  結束請按 Ctrl+C")
    print("===================================================")
    print()

    web_thread = threading.Thread(
        target=_start_dashboard,
        args=(port,),
        name="dashboard",
        daemon=True,
    )
    web_thread.start()

    grid = GridBot()
    dca = DCABot()

    try:
        asyncio.run(_run_bots(grid, dca))
    except KeyboardInterrupt:
        grid_logger.info("偵測到 CTRL+C，正在撤銷網格交易對上的掛單...")
        try:
            import config
            grid.client.rest_api.delete_open_orders(symbol=config.SYMBOL)
            grid_logger.info("已撤銷網格交易對的交易所掛單。")
        except Exception as e:
            grid_logger.error("撤銷網格掛單失敗: %s", e)
        dca_logger.info("定投機器人隨整合行程結束。")
        print("已安全退出。", flush=True)


if __name__ == "__main__":
    main()
