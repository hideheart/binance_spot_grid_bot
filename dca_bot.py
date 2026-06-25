# -*- coding: utf-8 -*-
"""
現貨定投交易系統 - 核心主程式
"""
import time
import logging
import logging.handlers
import math
import traceback
import asyncio
import os
import sys
from datetime import datetime

from binance_common.configuration import ConfigurationRestAPI
from binance_common.constants import SPOT_REST_API_PROD_URL
from binance_sdk_spot.spot import Spot
from binance_sdk_spot.rest_api.models import NewOrderSideEnum, NewOrderTypeEnum

import dca_config
import dca_db

# 建立 log 資料夾
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "dca_bot.log")

# 設定日誌（每天午夜輪轉，保留 30 天歷史紀錄）
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

file_handler = logging.handlers.TimedRotatingFileHandler(
    filename=log_file_path,
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)

class DCABot:
    def __init__(self):
        # 1. 初始化 Rest API 客戶端
        self.configuration = ConfigurationRestAPI(
            api_key=dca_config.API_KEY,
            api_secret=dca_config.API_SECRET,
            base_path=SPOT_REST_API_PROD_URL
        )
        self.client = Spot(config_rest_api=self.configuration)
        
        # 2. 用來保護資料庫操作的鎖
        self.db_lock = asyncio.Lock()
        
        # 3. 儲存各個交易對的規格規格資訊
        self.symbol_specs = {}
        logging.info("幣安 DCA 定投客戶端初始化完成。")

    async def _run_async(self, func, *args, **kwargs):
        """在執行器中運行同步阻塞函數。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def update_symbol_specs(self, symbol: str):
        """獲取交易對的精度與過濾器限制。"""
        if symbol in self.symbol_specs:
            return self.symbol_specs[symbol]
        
        try:
            logging.info(f"正在獲取 {symbol} 的交易所規格資訊...")
            res = await self._run_async(self.client.rest_api.exchange_info, symbols=f'["{symbol}"]')
            data = res.data()
            if not data.symbols:
                raise Exception(f"找不到交易對 {symbol} 資訊")
            
            sym_info = data.symbols[0]
            price_decimals = 2
            qty_decimals = 5
            min_notional = 5.0
            
            for f in sym_info.filters:
                f_act = f.actual_instance
                ftype = getattr(f_act, "filter_type", None)
                if ftype == "PRICE_FILTER":
                    tick_size = float(f_act.tick_size)
                    price_decimals = int(round(-math.log10(tick_size)))
                elif ftype == "LOT_SIZE":
                    step_size = float(f_act.step_size)
                    qty_decimals = int(round(-math.log10(step_size)))
                elif ftype == "NOTIONAL":
                    min_notional = float(f_act.min_notional)
                    
            specs = {
                "price_decimals": price_decimals,
                "qty_decimals": qty_decimals,
                "min_notional": min_notional
            }
            self.symbol_specs[symbol] = specs
            logging.info(f"{symbol} 規格載入成功: 小數價格={price_decimals}, 小數數量={qty_decimals}, 最小交易量={min_notional} FDUSD")
            return specs
        except Exception as e:
            logging.error(f"獲取 {symbol} 交易所規格規格失敗: {e}，使用預設安全值")
            specs = {
                "price_decimals": 2,
                "qty_decimals": 5 if "BTC" in symbol else 4,
                "min_notional": 5.0
            }
            self.symbol_specs[symbol] = specs
            return specs

    def get_ticker_book(self, symbol: str) -> dict:
        """獲取盤口資訊 (最優買賣價)。"""
        try:
            res = self.client.rest_api.ticker_book_ticker(symbol=symbol)
            act = res.data().actual_instance
            return {
                "bid_price": float(act.bid_price),
                "ask_price": float(act.ask_price)
            }
        except Exception as e:
            logging.error(f"獲取 {symbol} 盤口資訊失敗: {e}")
            raise

    def place_limit_maker_order(self, symbol: str, price: float, qty: float) -> str:
        """發送限價掛單 (LIMIT_MAKER) 購買。"""
        formatted_price = f"{price:.{self.symbol_specs[symbol]['price_decimals']}f}"
        formatted_qty = f"{qty:.{self.symbol_specs[symbol]['qty_decimals']}f}"
        
        side_enum = NewOrderSideEnum["BUY"].value
        type_enum = NewOrderTypeEnum["LIMIT_MAKER"].value
        
        logging.info(f"[{symbol}] 發送 LIMIT_MAKER 買單 | 價格: {formatted_price} | 數量: {formatted_qty}")
        
        res = self.client.rest_api.new_order(
            symbol=symbol,
            side=side_enum,
            type=type_enum,
            price=formatted_price,
            quantity=formatted_qty
        )
        data = res.data()
        order_id = str(data.order_id)
        logging.info(f"[{symbol}] 掛單成功。幣安 Order ID: {order_id}")
        return order_id

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """撤銷訂單。"""
        try:
            logging.info(f"[{symbol}] 正在撤銷訂單: {order_id}")
            self.client.rest_api.delete_order(
                symbol=symbol,
                order_id=int(order_id)
            )
            logging.info(f"[{symbol}] 訂單 {order_id} 撤銷成功。")
            return True
        except Exception as e:
            logging.error(f"[{symbol}] 撤銷訂單 {order_id} 失敗: {e}")
            return False

    def query_order(self, symbol: str, order_id: str) -> dict:
        """查詢訂單狀態與成交明細。"""
        try:
            res = self.client.rest_api.get_order(
                symbol=symbol,
                order_id=int(order_id)
            )
            order = res.data()
            exec_qty = float(order.executed_qty) if order.executed_qty else 0.0
            cum_quote = float(order.cummulative_quote_qty) if order.cummulative_quote_qty else 0.0
            avg_price = (cum_quote / exec_qty) if exec_qty > 0 else float(order.price)
            
            return {
                "status": order.status,  # 'NEW' | 'PARTIALLY_FILLED' | 'FILLED' | 'CANCELED' | 'EXPIRED'
                "filled_price": avg_price,
                "filled_qty": exec_qty,
                "cum_quote": cum_quote
            }
        except Exception as e:
            logging.error(f"[{symbol}] 查詢訂單 {order_id} 失敗: {e}")
            raise

    async def run_dca_worker(self, plan: dict):
        """單一交易對定投 Worker 協程。"""
        symbol = plan["symbol"]
        amount_per_time = plan["amount_per_time"]
        times_per_day = plan["times_per_day"]
        interval_seconds = int(86400 / times_per_day)
        
        logging.info(f"[{symbol}] 定投 Worker 已啟動。頻率: 每天 {times_per_day} 次 (每 {interval_seconds} 秒)，每次 {amount_per_time} FDUSD")
        
        # 獲取規格
        specs = await self.update_symbol_specs(symbol)
        min_notional = specs["min_notional"]
        
        while True:
            try:
                # 1. 獲取當前執行中的定投任務
                async with self.db_lock:
                    task = dca_db.get_running_task(symbol)
                    
                    if not task:
                        # 檢查是否到了該定投的時間
                        last_task = dca_db.get_last_completed_task(symbol)
                        should_start = False
                        
                        if not last_task:
                            # 第一次啟動，立即定投
                            logging.info(f"[{symbol}] 找不到定投歷史記錄，建立首個定投任務...")
                            should_start = True
                        else:
                            now = int(time.time())
                            elapsed = now - last_task["created_at"]
                            if elapsed >= interval_seconds:
                                logging.info(f"[{symbol}] 距離上次定投已過去 {elapsed} 秒，到了定投時間。")
                                should_start = True
                        
                        if should_start:
                            now = int(time.time())
                            next_time = now + interval_seconds
                            task_id = dca_db.create_task(symbol, amount_per_time, next_time)
                            task = {
                                "id": task_id,
                                "symbol": symbol,
                                "amount": amount_per_time,
                                "filled_amount": 0.0,
                                "filled_qty": 0.0,
                                "status": "RUNNING"
                            }
                            logging.info(f"[{symbol}] 新建定投任務 ID: {task_id}")
                
                # 2. 如果有執行中的定投任務，處理掛單與追價
                if task:
                    task_id = task["id"]
                    remaining_amount = task["amount"] - task["filled_amount"]
                    
                    # 若剩餘金額小於最小交易金額限制，視為已買齊
                    if remaining_amount < min_notional:
                        logging.info(f"[{symbol}] 剩餘未購買金額 {remaining_amount:.4f} FDUSD 小於交易所最低限制 {min_notional}，本輪定投任務結案！")
                        async with self.db_lock:
                            dca_db.complete_task(task_id)
                        continue
                    
                    # 檢查當前任務是否有掛單
                    active_order = None
                    async with self.db_lock:
                        active_order = dca_db.get_active_order_by_task(task_id)
                    
                    if not active_order:
                        # 沒有掛單，發起新的掛單
                        ticker = await self._run_async(self.get_ticker_book, symbol)
                        bid_price = ticker["bid_price"]
                        
                        # 計算數量
                        qty = remaining_amount / bid_price
                        # 精度修約 (向下捨入以防超支)
                        qty_decimals = specs["qty_decimals"]
                        multiplier = 10 ** qty_decimals
                        qty = math.floor(qty * multiplier) / multiplier
                        
                        actual_notional = qty * bid_price
                        
                        if actual_notional < min_notional:
                            # 如果因為向下修約導致低於名義價值限制，嘗試向上修約
                            qty_ceil = math.ceil((remaining_amount / bid_price) * multiplier) / multiplier
                            actual_notional_ceil = qty_ceil * bid_price
                            
                            # 只要向上修約的金額不超過剩餘金額的 1.15 倍，都可以接受
                            if actual_notional_ceil <= remaining_amount * 1.15:
                                qty = qty_ceil
                                actual_notional = actual_notional_ceil
                            else:
                                logging.warning(f"[{symbol}] 金額受限於交易精度，無法繼續掛單 (剩餘 {remaining_amount:.4f}，向上取整為 {actual_notional_ceil:.4f} 超出容許範圍)，標記任務完成。")
                                async with self.db_lock:
                                    dca_db.complete_task(task_id)
                                continue
                        
                        try:
                            # 發送限價 Maker 買單
                            order_id = await self._run_async(self.place_limit_maker_order, symbol, bid_price, qty)
                            async with self.db_lock:
                                dca_db.add_order(task_id, order_id, bid_price, qty)
                        except Exception as e:
                            logging.error(f"[{symbol}] 掛 LIMIT_MAKER 買單失敗: {e}，將於下次輪詢重試")
                            
                    else:
                        # 有活躍掛單，查詢交易所狀態
                        order_id = active_order["order_id"]
                        try:
                            info = await self._run_async(self.query_order, symbol, order_id)
                            status = info["status"]
                            filled_price = info["filled_price"]
                            filled_qty = info["filled_qty"]
                            cum_quote = info["cum_quote"]
                            
                            if status == "FILLED":
                                logging.info(f"[{symbol}] 掛單已全部成交！Order ID: {order_id} | 成交均價: {filled_price} | 成交數量: {filled_qty}")
                                async with self.db_lock:
                                    dca_db.update_order_status(order_id, "FILLED", filled_price, filled_qty)
                                    dca_db.update_task_progress(task_id, cum_quote, filled_qty)
                                    
                            elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                                logging.warning(f"[{symbol}] 訂單 {order_id} 已在交易所取消/過期 (狀態: {status})")
                                async with self.db_lock:
                                    dca_db.update_order_status(order_id, status, filled_price, filled_qty)
                                    dca_db.update_task_progress(task_id, cum_quote, filled_qty)
                                    
                            elif status in ["NEW", "PARTIALLY_FILLED"]:
                                # 檢查是否掛單超時
                                order_time = active_order["created_at"]
                                now = int(time.time())
                                elapsed = now - order_time
                                
                                if elapsed >= dca_config.ORDER_TIMEOUT:
                                    logging.info(f"[{symbol}] 訂單 {order_id} 掛單已超過 {elapsed} 秒未完全成交，準備撤單重掛追價...")
                                    # 撤單
                                    if await self._run_async(self.cancel_order, symbol, order_id):
                                        # 撤銷成功後，一定要再次查詢，獲取最終成交數據
                                        final_info = await self._run_async(self.query_order, symbol, order_id)
                                        final_qty = final_info["filled_qty"]
                                        final_cum_quote = final_info["cum_quote"]
                                        
                                        logging.info(f"[{symbol}] 訂單已確認撤銷。最終成交數量: {final_qty}，累計金額: {final_cum_quote}")
                                        async with self.db_lock:
                                            dca_db.update_order_status(order_id, "CANCELED", final_info["filled_price"], final_qty)
                                            dca_db.update_task_progress(task_id, final_cum_quote, final_qty)
                                    else:
                                        logging.error(f"[{symbol}] 撤銷訂單 {order_id} 失敗，將在下一輪輪詢重新嘗試")
                        except Exception as e:
                            logging.error(f"[{symbol}] 處理掛單 {order_id} 狀態異常: {e}")
            
            except Exception as e:
                logging.error(f"[{symbol}] Worker 發生異常: {e}")
                logging.error(traceback.format_exc())
            
            # 定期輪詢
            await asyncio.sleep(dca_config.POLL_INTERVAL)

    async def start(self):
        """啟動定投機器人。"""
        logging.info("=== 幣安多幣種免手續費定投 (DCA) 機器人啟動 ===")
        
        # 初始化資料庫
        dca_db.init_db()
        
        # 為每個定投計劃啟動一個 Worker 協程
        tasks = []
        for plan in dca_config.DCA_PLANS:
            tasks.append(self.run_dca_worker(plan))
            
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    bot = DCABot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logging.info("偵測到手動中止訊號，定投機器人正常退出。")
