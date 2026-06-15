# -*- coding: utf-8 -*-
"""
現貨網格交易系統 - Dashboard 後端伺服器
"""

import os
import sys
import json
import time
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

# 將上一級目錄加入 sys.path 以便引入 config 與 db 模組
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT_DIR not in sys.path:
    sys.path.append(PARENT_DIR)

import config
import db
from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import SPOT_REST_API_PROD_URL
from binance_sdk_spot.spot import Spot

# 初始化只讀用途的 Binance Spot Client（取得最新價格）
config_rest = ConfigurationRestAPI(base_path=SPOT_REST_API_PROD_URL)
spot_client = Spot(config_rest_api=config_rest)

def get_current_price():
    """獲取最新幣價，失敗則傳回 0.0"""
    try:
        res = spot_client.rest_api.ticker_price(symbol=config.SYMBOL)
        data = res.data().actual_instance
        return float(data.price)
    except Exception:
        return 0.0

def get_profit_by_days(days):
    """計算指定天數內的總利潤 (以本地時間為準)"""
    db_path = db.get_db_path()
    # 計算幾天前的 00:00:00 時間戳記
    local_now = datetime.now()
    target_date = local_now - timedelta(days=days-1)
    # 設定為當天 0 點
    target_start = datetime(target_date.year, target_date.month, target_date.day)
    start_timestamp = int(target_start.timestamp())

    with sqlite3.connect(db_path) as conn:
        val = conn.execute(
            "SELECT SUM(profit) FROM grid_orders WHERE status = 'FILLED_SELL' AND sell_filled_time >= ?",
            (start_timestamp,)
        ).fetchone()[0]
        return val if val else 0.0

def get_today_profit():
    """計算今天的利潤"""
    db_path = db.get_db_path()
    local_now = datetime.now()
    today_start = datetime(local_now.year, local_now.month, local_now.day)
    start_timestamp = int(today_start.timestamp())

    with sqlite3.connect(db_path) as conn:
        val = conn.execute(
            "SELECT SUM(profit) FROM grid_orders WHERE status = 'FILLED_SELL' AND sell_filled_time >= ?",
            (start_timestamp,)
        ).fetchone()[0]
        return val if val else 0.0

def get_daily_chart_data(days=30):
    """取得最近 N 天的每日獲利與累積獲利"""
    db_path = db.get_db_path()
    local_now = datetime.now()
    start_date = local_now - timedelta(days=days-1)
    start_timestamp = int(datetime(start_date.year, start_date.month, start_date.day).timestamp())

    with sqlite3.connect(db_path) as conn:
        # 使用 sqlite 的 date 函數配合 unixepoch 進行分組
        rows = conn.execute("""
            SELECT 
                date(sell_filled_time, 'unixepoch', 'localtime') as day,
                SUM(profit) as daily_profit
            FROM grid_orders 
            WHERE status = 'FILLED_SELL' AND sell_filled_time >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (start_timestamp,)).fetchall()
        
        # 轉換成 dict 方便快速查找
        profit_map = {row[0]: row[1] for row in rows}
        
        # 填滿這 N 天的每一天，以防某天沒有成交導致資料空缺
        labels = []
        daily_data = []
        cumulative_data = []
        running_total = 0.0
        
        # 計算起點前的歷史總收益，用於累積圖表
        prior_total = conn.execute(
            "SELECT SUM(profit) FROM grid_orders WHERE status = 'FILLED_SELL' AND sell_filled_time < ?",
            (start_timestamp,)
        ).fetchone()[0]
        running_total = prior_total if prior_total else 0.0

        for i in range(days):
            current_day_date = start_date + timedelta(days=i)
            day_str = current_day_date.strftime('%Y-%m-%d')
            day_profit = profit_map.get(day_str, 0.0)
            
            running_total += day_profit
            labels.append(current_day_date.strftime('%m-%d'))
            daily_data.append(round(day_profit, 4))
            cumulative_data.append(round(running_total, 4))
            
        return {
            "labels": labels,
            "daily": daily_data,
            "cumulative": cumulative_data
        }

def get_grid_statistics():
    """統計各狀態訂單數量"""
    db_path = db.get_db_path()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT status, COUNT(*) FROM grid_orders GROUP BY status").fetchall()
        stats = {row[0]: row[1] for row in rows}
        return {
            "pending_buy": stats.get("PENDING_BUY", 0),
            "filled_buy": stats.get("FILLED_BUY", 0),
            "pending_sell": stats.get("PENDING_SELL", 0),
            "filled_sell": stats.get("FILLED_SELL", 0),
            "canceled": stats.get("CANCELED", 0)
        }

def get_recent_transactions(limit=10):
    """獲取最近成交的賣單"""
    db_path = db.get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT id, grid_price, buy_filled_price, sell_filled_price, sell_filled_time, profit 
            FROM grid_orders 
            WHERE status = 'FILLED_SELL' 
            ORDER BY sell_filled_time DESC 
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 覆寫 log_message 以免終端機塞滿請求日誌，保持畫面乾淨
        pass

    def do_GET(self):
        # 設置路由
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            # 讀取 index.html
            html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            if os.path.exists(html_path):
                with open(html_path, "r", encoding="utf-8") as f:
                    self.wfile.write(f.read().encode("utf-8"))
            else:
                self.wfile.write(b"<h1>index.html not found!</h1>")
        
        elif self.path == "/api/summary":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            
            curr_price = get_current_price()
            today_profit = get_today_profit()
            p7 = get_profit_by_days(7)
            p30 = get_profit_by_days(30)
            total = db.get_total_profit()
            stats = get_grid_statistics()
            recent = get_recent_transactions(5)
            
            data = {
                "symbol": config.SYMBOL,
                "current_price": curr_price,
                "profit_today": round(today_profit, 4),
                "profit_7d": round(p7, 4),
                "profit_30d": round(p30, 4),
                "profit_total": round(total, 4),
                "stats": stats,
                "recent_sales": recent,
                "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self.wfile.write(json.dumps(data).encode("utf-8"))
            
        elif self.path == "/api/charts":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            
            chart_data = get_daily_chart_data(30)
            self.wfile.write(json.dumps(chart_data).encode("utf-8"))
            
        elif self.path == "/api/grids":
            # 獲取所有目前活跃網格列表
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            
            active_grids = db.get_active_grids()
            self.wfile.write(json.dumps(active_grids).encode("utf-8"))
            
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

def run_server(port=5000):
    db.init_db()
    server_address = ('127.0.0.1', port)
    httpd = HTTPServer(server_address, DashboardHTTPHandler)
    print(f"==================================================")
    print(f"網格交易 Dashboard 後端已在 http://localhost:{port} 啟動")
    print(f"==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在關閉 Dashboard 伺服器...")
        httpd.server_close()

if __name__ == "__main__":
    run_server(5000)
