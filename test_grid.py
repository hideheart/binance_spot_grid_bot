# -*- coding: utf-8 -*-
"""
現貨網格交易系統 - 本地測試與驗證腳本
"""

import sys
import os
import unittest
import sqlite3

# 引入本地模組
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config
import db
from grid_bot import GridBot

class TestGridSystem(unittest.TestCase):
    
    def setUp(self):
        """測試前準備工作：使用記憶體資料庫或測試資料庫。"""
        # 修改 DB_FILE 指向測試庫
        config.DB_FILE = 'test_grid.db'
        # 強制指定網格參數以配合既有測試案例
        config.GRID_INTERVAL = 50.0
        config.TRADE_AMOUNT = 10.0
        config.GRID_NUM = 10
        db.init_db()
        self.db_path = db.get_db_path()
        self.clean_test_db()
        
    def tearDown(self):
        """測試後清理工作。"""
        self.clean_test_db()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass

    def clean_test_db(self):
        """清空測試資料表。"""
        if os.path.exists(self.db_path):
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM grid_orders")
                conn.commit()

    def test_grid_calculation(self):
        """測試網格區間與邊界計算是否符合 50 美元一格、70025, 70075... 的規則。"""
        bot = GridBot()
        
        # 測試價格 70040.0
        buy_range, sell_range = bot.calculate_grid_boundaries(70040.0)
        
        # 70040.0 離下方最近的 50*k+25 是 70025.0 (k=1400)
        # 買單 10 格應為：70025, 69975, 69925, 69875, 69825, 69775, 69725, 69675, 69625, 69575
        self.assertEqual(buy_range[0], 70025.0)
        self.assertEqual(buy_range[-1], 69575.0)
        self.assertEqual(len(buy_range), 10)
        
        # 賣單 10 格應為：70075, 70125, 70175, 70225, 70275, 70325, 70375, 70425, 70475, 70525
        self.assertEqual(sell_range[0], 70075.0)
        self.assertEqual(sell_range[-1], 70525.0)
        self.assertEqual(len(sell_range), 10)
        
        # 測試邊界價格：剛好是格點價格 70025.0
        # 70025.0 離下方最近的 50*k+25 是 70025.0 (k=1400)
        buy_range2, _ = bot.calculate_grid_boundaries(70025.0)
        self.assertEqual(buy_range2[0], 70025.0)

    def test_grid_calculation_25(self):
        """測試當網格間距為 25.0 時的格點計算。"""
        config.GRID_INTERVAL = 25.0
        bot = GridBot()
        
        # 測試價格 70040.0，應以 25 的整數倍對齊
        buy_range, sell_range = bot.calculate_grid_boundaries(70040.0)
        self.assertEqual(buy_range[0], 70025.0)
        self.assertEqual(buy_range[1], 70000.0)
        self.assertEqual(buy_range[-1], 69800.0)
        
        self.assertEqual(sell_range[0], 70050.0)
        self.assertEqual(sell_range[-1], 70275.0)

    def test_db_status_flow(self):
        """測試資料庫的訂單狀態轉換流程。"""
        # 1. 插入買單
        row_id = db.insert_buy_order(70025.0, "BUY123", 70025.0, 0.00014)
        self.assertTrue(row_id > 0)
        
        active = db.get_active_grids()
        self.assertIn(70025.0, active)
        self.assertEqual(active[70025.0]['status'], 'PENDING_BUY')
        self.assertEqual(active[70025.0]['buy_order_id'], 'BUY123')
        
        # 2. 確認買單成交
        db.confirm_buy_order("BUY123", 70024.5, 0.00014, 1716380000)
        active = db.get_active_grids()
        self.assertEqual(active[70025.0]['status'], 'FILLED_BUY')
        self.assertEqual(active[70025.0]['buy_filled_price'], 70024.5)
        self.assertEqual(active[70025.0]['buy_filled_qty'], 0.00014)
        
        # 3. 下賣單
        db.set_pending_sell(row_id, "SELL123")
        active = db.get_active_grids()
        self.assertEqual(active[70025.0]['status'], 'PENDING_SELL')
        self.assertEqual(active[70025.0]['sell_order_id'], 'SELL123')
        
        # 4. 測試賣單撤單恢復為買單狀態
        db.revert_sell_order_to_buy("SELL123")
        active = db.get_active_grids()
        self.assertEqual(active[70025.0]['status'], 'FILLED_BUY')
        self.assertIsNone(active[70025.0]['sell_order_id'])
        
        # 5. 重新掛賣單並確認成交
        db.set_pending_sell(row_id, "SELL456")
        db.confirm_sell_order("SELL456", 70074.5, 0.00014, 1716390000)
        
        # 結案的訂單不應該在活躍格點中
        active = db.get_active_grids()
        self.assertNotIn(70025.0, active)
        
        # 查詢已完成的訂單
        completed = db.get_orders_by_status(['FILLED_SELL'])
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]['sell_filled_price'], 70074.5)
        
        # 計算利潤：(70074.5 * 0.00014) - (70024.5 * 0.00014) = 50.0 * 0.00014 = 0.0070
        expected_profit = (70074.5 - 70024.5) * 0.00014
        self.assertAlmostEqual(completed[0]['profit'], expected_profit, places=6)
        self.assertAlmostEqual(db.get_total_profit(), expected_profit, places=6)

    def test_db_cancel_buy(self):
        """測試買單被撤銷的狀態轉移。"""
        db.insert_buy_order(70025.0, "BUY999", 70025.0, 0.00014)
        db.cancel_buy_order("BUY999")
        
        # 撤銷的買單不應該是活躍格點
        active = db.get_active_grids()
        self.assertNotIn(70025.0, active)
        
        canceled = db.get_orders_by_status(['CANCELED'])
        self.assertEqual(len(canceled), 1)
        self.assertEqual(canceled[0]['buy_order_id'], 'BUY999')

    def test_canceled_buy_with_fill_becomes_inventory(self):
        """買單已部分成交後被交易所結束，應入帳而非標 CANCELED。"""
        bot = GridBot()
        row_id = db.insert_buy_order(70025.0, "BUY_PF", 70025.0, 0.00016)
        row = db.get_orders_by_status(['PENDING_BUY'])[0]
        outcome = bot.apply_buy_exchange_status(
            row,
            {"status": "CANCELED", "filled_price": 70025.0, "filled_qty": 0.00016},
        )
        self.assertEqual(outcome, "filled")
        active = db.get_active_grids()
        self.assertEqual(active[70025.0]['status'], 'FILLED_BUY')
        self.assertAlmostEqual(active[70025.0]['buy_filled_qty'], 0.00016)
        self.assertAlmostEqual(active[70025.0]['sell_qty'], 0.00016)
        self.assertEqual(row_id, active[70025.0]['id'])

    def test_canceled_buy_below_min_notional_is_dust(self):
        """部分成交名義金額 < 5 FDUSD 時標塵倉，仍佔格、不掛賣。"""
        bot = GridBot()
        db.insert_buy_order(70025.0, "BUY_DUST", 70025.0, 0.00016)
        row = db.get_orders_by_status(['PENDING_BUY'])[0]
        # 0.00007 * 70075 ≈ 4.91 < 5
        outcome = bot.apply_buy_exchange_status(
            row,
            {"status": "CANCELED", "filled_price": 70025.0, "filled_qty": 0.00007},
        )
        self.assertEqual(outcome, "dust")
        active = db.get_active_grids()
        self.assertEqual(active[70025.0]['status'], 'HELD_DUST')
        self.assertAlmostEqual(active[70025.0]['buy_filled_qty'], 0.00007)
        self.assertTrue(bot.can_place_limit(70075.0, 0.00016))
        self.assertFalse(bot.can_place_limit(70075.0, 0.00007))

    def test_canceled_buy_zero_fill_stays_canceled(self):
        bot = GridBot()
        db.insert_buy_order(70025.0, "BUY_ZERO", 70025.0, 0.00016)
        row = db.get_orders_by_status(['PENDING_BUY'])[0]
        outcome = bot.apply_buy_exchange_status(
            row,
            {"status": "EXPIRED", "filled_price": 70025.0, "filled_qty": 0.0},
        )
        self.assertEqual(outcome, "canceled")
        self.assertNotIn(70025.0, db.get_active_grids())

    def test_partial_buy_is_not_closed(self):
        bot = GridBot()
        db.insert_buy_order(70025.0, "BUY_OPEN", 70025.0, 0.00016)
        row = db.get_orders_by_status(['PENDING_BUY'])[0]
        outcome = bot.apply_buy_exchange_status(
            row,
            {"status": "PARTIALLY_FILLED", "filled_price": 70025.0, "filled_qty": 0.00005},
        )
        self.assertEqual(outcome, "partial")
        self.assertEqual(db.get_active_grids()[70025.0]['status'], 'PENDING_BUY')

    def test_canceled_sell_partial_reverts_not_closes(self):
        """賣單部分成交後結束：本次不拆剩餘量，退回 FILLED_BUY。"""
        bot = GridBot()
        row_id = db.insert_buy_order(70025.0, "BUY_S", 70025.0, 0.00016)
        db.confirm_buy_order("BUY_S", 70025.0, 0.00016)
        db.set_pending_sell(row_id, "SELL_PF")
        row = db.get_orders_by_status(['PENDING_SELL'])[0]
        outcome = bot.apply_sell_exchange_status(
            row,
            {"status": "CANCELED", "filled_price": 70075.0, "filled_qty": 0.00006},
        )
        self.assertEqual(outcome, "reverted")
        self.assertEqual(db.get_active_grids()[70025.0]['status'], 'FILLED_BUY')
        self.assertEqual(len(db.get_orders_by_status(['FILLED_SELL'])), 0)

if __name__ == "__main__":
    unittest.main()
