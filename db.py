# -*- coding: utf-8 -*-
"""
現貨網格交易系統 - 資料庫模組
"""

import sqlite3
import time
import os
import config

def get_db_path():
    """取得資料庫的絕對路徑，確保與 db.py 在同一個資料夾。"""
    dir_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(dir_path, config.DB_FILE)

def init_db():
    """初始化資料庫並建立 orders 資料表與索引。"""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS grid_orders (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_price          REAL    NOT NULL, -- 網格買入格點價格 (50*k + 25)
                buy_order_id        TEXT    UNIQUE,   -- 幣安買單 Order ID
                buy_price           REAL    NOT NULL, -- 買入掛單價格
                buy_qty             REAL    NOT NULL, -- 買入下單數量 (BTC)
                buy_filled_price    REAL    DEFAULT -1, -- 實際買入成交均價
                buy_filled_qty      REAL    DEFAULT -1, -- 實際買入成交數量
                buy_filled_time     INTEGER DEFAULT 0,  -- 買入成交時間戳記
                
                sell_order_id       TEXT    UNIQUE,   -- 幣安賣單 Order ID
                sell_price          REAL    NOT NULL, -- 賣出掛單價格 (buy_price + 50)
                sell_qty            REAL    NOT NULL, -- 賣出下單數量 (等於 buy_filled_qty)
                sell_filled_price   REAL    DEFAULT -1, -- 實際賣出成交均價
                sell_filled_qty     REAL    DEFAULT -1, -- 實際賣出成交數量
                sell_filled_time    INTEGER DEFAULT 0,  -- 賣出成交時間戳記
                
                status              TEXT    NOT NULL, -- 狀態: PENDING_BUY, FILLED_BUY, PENDING_SELL, FILLED_SELL, CANCELED
                profit              REAL    DEFAULT 0, -- 該筆交易淨利潤 (FDUSD)
                created_at          INTEGER NOT NULL, -- 記錄建立時間
                updated_at          INTEGER NOT NULL  -- 記錄更新時間
            )
        ''')
        # 建立索引以優化狀態查詢和格點查詢
        conn.execute('CREATE INDEX IF NOT EXISTS idx_grid_orders_status ON grid_orders(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_grid_orders_grid_price ON grid_orders(grid_price)')
        conn.commit()

def insert_buy_order(grid_price: float, buy_order_id: str, buy_price: float, buy_qty: float) -> int:
    """新增一筆已掛單的買入記錄（狀態為 PENDING_BUY）。"""
    db_path = get_db_path()
    now = int(time.time())
    sell_price = grid_price + config.GRID_INTERVAL  # 賣出價格為買入價 + 網格間距
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO grid_orders 
            (grid_price, buy_order_id, buy_price, buy_qty, sell_price, sell_qty, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (grid_price, str(buy_order_id), buy_price, buy_qty, sell_price, buy_qty, 'PENDING_BUY', now, now))
        conn.commit()
        return cursor.lastrowid

def confirm_buy_order(buy_order_id: str, filled_price: float, filled_qty: float, filled_time: int = None):
    """買入訂單成交，更新狀態為 FILLED_BUY。"""
    db_path = get_db_path()
    now = int(time.time())
    f_time = filled_time if filled_time else now
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE grid_orders 
            SET status = 'FILLED_BUY', buy_filled_price = ?, buy_filled_qty = ?, buy_filled_time = ?, sell_qty = ?, updated_at = ?
            WHERE buy_order_id = ? AND status = 'PENDING_BUY'
        ''', (filled_price, filled_qty, f_time, filled_qty, now, str(buy_order_id)))
        conn.commit()

def cancel_buy_order(buy_order_id: str):
    """買入單被撤銷或失效，更新狀態為 CANCELED。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE grid_orders 
            SET status = 'CANCELED', updated_at = ?
            WHERE buy_order_id = ? AND status = 'PENDING_BUY'
        ''', (now, str(buy_order_id)))
        conn.commit()

def set_pending_sell(db_id: int, sell_order_id: str):
    """下賣出限價單後，更新狀態為 PENDING_SELL。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE grid_orders 
            SET status = 'PENDING_SELL', sell_order_id = ?, updated_at = ?
            WHERE id = ? AND status = 'FILLED_BUY'
        ''', (str(sell_order_id), now, db_id))
        conn.commit()

def confirm_sell_order(sell_order_id: str, filled_price: float, filled_qty: float, filled_time: int = None):
    """賣出訂單成交，計算利潤，更新狀態為 FILLED_SELL。"""
    db_path = get_db_path()
    now = int(time.time())
    f_time = filled_time if filled_time else now
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # 先查詢對應的買入成本
        row = conn.execute('SELECT buy_filled_price, buy_filled_qty FROM grid_orders WHERE sell_order_id = ?', (str(sell_order_id),)).fetchone()
        if row:
            buy_cost = row['buy_filled_price'] * row['buy_filled_qty']
            sell_revenue = filled_price * filled_qty
            profit = sell_revenue - buy_cost
            
            conn.execute('''
                UPDATE grid_orders 
                SET status = 'FILLED_SELL', sell_filled_price = ?, sell_filled_qty = ?, sell_filled_time = ?, profit = ?, updated_at = ?
                WHERE sell_order_id = ? AND status = 'PENDING_SELL'
            ''', (filled_price, filled_qty, f_time, profit, now, str(sell_order_id)))
            conn.commit()

def revert_sell_order_to_buy(sell_order_id: str):
    """賣單超出範圍被撤銷後，恢復狀態為 FILLED_BUY，等待下次機會重新掛賣。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE grid_orders 
            SET status = 'FILLED_BUY', sell_order_id = NULL, updated_at = ?
            WHERE sell_order_id = ? AND status = 'PENDING_SELL'
        ''', (now, str(sell_order_id)))
        conn.commit()

def get_orders_by_status(status_list: list) -> list:
    """取得特定狀態的所有訂單列表。"""
    db_path = get_db_path()
    placeholders = ','.join('?' for _ in status_list)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(f'''
            SELECT * FROM grid_orders WHERE status IN ({placeholders})
        ''', status_list).fetchall()

def get_active_grids() -> dict:
    """
    取得目前所有活躍格點（包含 PENDING_BUY, FILLED_BUY, PENDING_SELL）。
    鍵為 grid_price，值為對應的訂單紀錄 dict。
    這可以用來判斷某個價格格點是否已經有部位或已有掛單。
    """
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''
            SELECT * FROM grid_orders 
            WHERE status IN ('PENDING_BUY', 'FILLED_BUY', 'PENDING_SELL')
        ''').fetchall()
        # 轉成 dictionary, grid_price 映射到 row dict
        return {row['grid_price']: dict(row) for row in rows}

def get_total_profit() -> float:
    """取得累計利潤。"""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        val = conn.execute("SELECT SUM(profit) FROM grid_orders WHERE status = 'FILLED_SELL'").fetchone()[0]
        return val if val else 0.0
