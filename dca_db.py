# -*- coding: utf-8 -*-
"""
現貨定投交易系統 - 資料庫模組
"""
import sqlite3
import time
import os
import dca_config

def get_db_path():
    """取得資料庫的絕對路徑，確保與 dca_db.py 在同一個資料夾。"""
    dir_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(dir_path, dca_config.DB_FILE)

def init_db():
    """初始化資料庫並建立定投任務與掛單明細資料表。"""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        # 1. 建立定投任務主表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS dca_tasks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol              TEXT    NOT NULL,
                amount              REAL    NOT NULL, -- 計劃定投金額 (FDUSD)
                filled_amount       REAL    DEFAULT 0.0, -- 已成交金額 (FDUSD)
                filled_qty          REAL    DEFAULT 0.0, -- 已成交數量
                status              TEXT    NOT NULL, -- 狀態: RUNNING, COMPLETED
                next_execution_time INTEGER NOT NULL, -- 下一次定投觸發時間戳記
                created_at          INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL
            )
        ''')
        
        # 2. 建立定投掛單明細表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS dca_orders (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id             INTEGER NOT NULL, -- 關聯 dca_tasks.id
                order_id            TEXT    UNIQUE NOT NULL, -- 幣安 Order ID
                price               REAL    NOT NULL, -- 掛單價格
                qty                 REAL    NOT NULL, -- 掛單數量
                filled_price        REAL    DEFAULT 0.0, -- 實際成交均價
                filled_qty          REAL    DEFAULT 0.0, -- 實際成交數量
                status              TEXT    NOT NULL, -- 狀態: NEW, PARTIALLY_FILLED, FILLED, CANCELED
                created_at          INTEGER NOT NULL,
                updated_at          INTEGER NOT NULL,
                FOREIGN KEY (task_id) REFERENCES dca_tasks(id)
            )
        ''')
        
        # 建立索引以優化查詢
        conn.execute('CREATE INDEX IF NOT EXISTS idx_dca_tasks_symbol ON dca_tasks(symbol)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_dca_tasks_status ON dca_tasks(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_dca_orders_task_id ON dca_orders(task_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_dca_orders_order_id ON dca_orders(order_id)')
        conn.commit()

def get_running_task(symbol: str) -> dict:
    """取得目前正在執行中的定投任務 (如果有的話)。"""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('''
            SELECT * FROM dca_tasks 
            WHERE symbol = ? AND status = 'RUNNING'
            LIMIT 1
        ''', (symbol,)).fetchone()
        return dict(row) if row else None

def get_last_completed_task(symbol: str) -> dict:
    """取得上一個已完成的定投任務。"""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('''
            SELECT * FROM dca_tasks 
            WHERE symbol = ? AND status = 'COMPLETED'
            ORDER BY created_at DESC
            LIMIT 1
        ''', (symbol,)).fetchone()
        return dict(row) if row else None

def create_task(symbol: str, amount: float, next_execution_time: int) -> int:
    """建立新的定投任務。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO dca_tasks 
            (symbol, amount, filled_amount, filled_qty, status, next_execution_time, created_at, updated_at)
            VALUES (?, ?, 0.0, 0.0, 'RUNNING', ?, ?, ?)
        ''', (symbol, amount, next_execution_time, now, now))
        conn.commit()
        return cursor.lastrowid

def update_task_progress(task_id: int, add_amount: float, add_qty: float):
    """更新定投任務的已買入金額與數量。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE dca_tasks 
            SET filled_amount = filled_amount + ?, 
                filled_qty = filled_qty + ?, 
                updated_at = ?
            WHERE id = ?
        ''', (add_amount, add_qty, now, task_id))
        conn.commit()

def complete_task(task_id: int):
    """標記定投任務為已完成。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE dca_tasks 
            SET status = 'COMPLETED', updated_at = ?
            WHERE id = ?
        ''', (now, task_id))
        conn.commit()

def add_order(task_id: int, order_id: str, price: float, qty: float):
    """紀錄掛單。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            INSERT INTO dca_orders 
            (task_id, order_id, price, qty, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'NEW', ?, ?)
        ''', (task_id, str(order_id), price, qty, now, now))
        conn.commit()

def update_order_status(order_id: str, status: str, filled_price: float = 0.0, filled_qty: float = 0.0):
    """更新訂單狀態。"""
    db_path = get_db_path()
    now = int(time.time())
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            UPDATE dca_orders 
            SET status = ?, filled_price = ?, filled_qty = ?, updated_at = ?
            WHERE order_id = ?
        ''', (status, filled_price, filled_qty, now, str(order_id)))
        conn.commit()

def get_active_order_by_task(task_id: int) -> dict:
    """取得該定投任務目前活躍 (未結案) 的掛單 (NEW 或 PARTIALLY_FILLED)。"""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('''
            SELECT * FROM dca_orders 
            WHERE task_id = ? AND status IN ('NEW', 'PARTIALLY_FILLED')
            LIMIT 1
        ''', (task_id,)).fetchone()
        return dict(row) if row else None
