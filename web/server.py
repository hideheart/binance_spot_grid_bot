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

# 全局價格快取變數，防止多人同時在線刷爆幣安 API 權重
_multi_price_cache = {}

def get_symbol_price(symbol: str) -> float:
    """獲取指定交易對的最新價格，具備 60 秒快取防刷機制。"""
    now = time.time()
    cache = _multi_price_cache.get(symbol)
    if cache and (now - cache["time"] < 60.0) and cache["price"] > 0:
        return cache["price"]

    try:
        res = spot_client.rest_api.ticker_price(symbol=symbol)
        data = res.data().actual_instance
        price = float(data.price)
        _multi_price_cache[symbol] = {"price": price, "time": now}
        return price
    except Exception:
        return cache["price"] if cache else 0.0

def get_current_price():
    """獲取網格主交易對的最新價格。"""
    return get_symbol_price(config.SYMBOL)

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
            
        elif self.path == "/api/dca":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            
            dca_data = []
            try:
                import dca_config
                import dca_db
                dca_db_path = dca_db.get_db_path()
                if os.path.exists(dca_db_path):
                    with sqlite3.connect(dca_db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        for plan in dca_config.DCA_PLANS:
                            symbol = plan["symbol"]
                            times_per_day = plan["times_per_day"]
                            amount_per_time = plan["amount_per_time"]
                            
                            # 1. 累計投資額與持倉量
                            summary = conn.execute('''
                                SELECT SUM(filled_amount) as total_invested,
                                       SUM(filled_qty) as total_qty
                                FROM dca_tasks
                                WHERE symbol = ?
                            ''', (symbol,)).fetchone()
                            
                            total_invested = summary["total_invested"] if summary["total_invested"] else 0.0
                            total_qty = summary["total_qty"] if summary["total_qty"] else 0.0
                            
                            # 2. 獲取下一次定投時間
                            latest_task = conn.execute('''
                                SELECT next_execution_time FROM dca_tasks
                                WHERE symbol = ?
                                ORDER BY id DESC LIMIT 1
                            ''', (symbol,)).fetchone()
                            
                            next_time = 0
                            if latest_task:
                                next_time = latest_task["next_execution_time"]
                            
                            # 3. 獲取實時價格與計算盈虧
                            curr_price = get_symbol_price(symbol)
                            curr_value = total_qty * curr_price
                            profit = curr_value - total_invested
                            
                            profit_percent = 0.0
                            if total_invested > 0:
                                profit_percent = (profit / total_invested) * 100
                            
                            dca_data.append({
                                "symbol": symbol,
                                "frequency": f"一天 {times_per_day} 次",
                                "amount_per_time": amount_per_time,
                                "total_invested": round(total_invested, 4),
                                "total_qty": round(total_qty, 6),
                                "current_price": curr_price,
                                "current_value": round(curr_value, 4),
                                "profit": round(profit, 4),
                                "profit_percent": round(profit_percent, 2),
                                "next_execution_time": next_time
                            })
            except Exception as e:
                print("獲取定投數據失敗:", e)
                
            self.wfile.write(json.dumps(dca_data).encode("utf-8"))
            
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

def run_server(port=5000):
    # 初始化網格資料庫
    db.init_db()
    
    # 主動初始化定投資料庫，確保資料表存在
    try:
        import dca_db
        dca_db.init_db()
    except Exception as e:
        print("Dashboard 初始化定投資料庫失敗:", e)
        
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
