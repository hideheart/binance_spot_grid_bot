# -*- coding: utf-8 -*-
"""
現貨網格交易系統 - WebSocket 混合架構核心主程式
"""

import time
import logging
import logging.handlers
import math
import traceback
import asyncio
import os
from datetime import datetime
from decimal import Decimal

from binance_common.configuration import ConfigurationRestAPI, ConfigurationWebSocketStreams
from binance_common.constants import SPOT_REST_API_PROD_URL, SPOT_WS_STREAMS_PROD_URL
from binance_sdk_spot.spot import Spot
from binance_sdk_spot.rest_api.models import NewOrderSideEnum, NewOrderTypeEnum, NewOrderTimeInForceEnum

import config
import db

# 建立 log 資料夾
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "grid_bot.log")

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

class GridBot:
    def __init__(self):
        # 1. 初始化 Rest API 客戶端
        self.configuration = ConfigurationRestAPI(
            api_key=config.API_KEY,
            api_secret=config.API_SECRET,
            base_path=SPOT_REST_API_PROD_URL
        )
        self.client = Spot(config_rest_api=self.configuration)
        
        # 2. 初始化 WebSocket Streams 客戶端
        self.configuration_ws = ConfigurationWebSocketStreams(
            stream_url=SPOT_WS_STREAMS_PROD_URL
        )
        self.ws_client = Spot(config_ws_streams=self.configuration_ws)
        
        # 3. 初始化運行變數
        self.current_price = 0.0
        self.last_ws_message_time = 0.0  # 記錄最後一次收到 WebSocket 行情更新的時間
        self.db_lock = asyncio.Lock()  # 用於保護資料庫操作以防協程併發衝突
        logging.info("幣安 Spot API 與 WebSocket 客戶端初始化完成。")

    async def _run_async(self, func, *args, **kwargs):
        """在執行器中運行同步阻塞函數。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def get_latest_price(self) -> float:
        """獲取 BTC/FDUSD 的最新市場價格。"""
        try:
            res = self.client.rest_api.ticker_price(symbol=config.SYMBOL)
            data = res.data().actual_instance
            return float(data.price)
        except Exception as e:
            logging.error(f"獲取最新價格失敗: {e}")
            raise

    def place_limit_order(self, side: str, price: float, qty: float) -> str:
        """發送限價掛單到幣安，回傳 order_id（只做 Maker）。"""
        formatted_price = f"{price:.{config.PRICE_DECIMALS}f}"
        formatted_qty = f"{qty:.{config.QTY_DECIMALS}f}"
        
        side_enum = NewOrderSideEnum[side.upper()].value
        type_enum = NewOrderTypeEnum["LIMIT_MAKER"].value
        
        logging.info(f"發送掛單 (MAKER): {side.upper()} | 價格: {formatted_price} | 數量: {formatted_qty}")
        
        res = self.client.rest_api.new_order(
            symbol=config.SYMBOL,
            side=side_enum,
            type=type_enum,
            price=formatted_price,
            quantity=formatted_qty
        )
        
        data = res.data()
        order_id = str(data.order_id)
        logging.info(f"掛單成功。幣安 Order ID: {order_id}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """撤銷幣安上的指定訂單。"""
        try:
            logging.info(f"正在撤銷幣安訂單: {order_id}")
            self.client.rest_api.delete_order(
                symbol=config.SYMBOL,
                order_id=int(order_id)
            )
            logging.info(f"訂單 {order_id} 撤銷成功。")
            return True
        except Exception as e:
            logging.error(f"撤銷訂單 {order_id} 失敗: {e}")
            return False

    def query_order_status(self, order_id: str) -> dict:
        """查詢訂單狀態與成交明細。"""
        try:
            res = self.client.rest_api.get_order(
                symbol=config.SYMBOL,
                order_id=int(order_id)
            )
            order = res.data()
            
            exec_qty = float(order.executed_qty) if order.executed_qty else 0.0
            cum_quote = float(order.cummulative_quote_qty) if order.cummulative_quote_qty else 0.0
            avg_price = (cum_quote / exec_qty) if exec_qty > 0 else float(order.price)
            
            return {
                "status": order.status,  # 'NEW' | 'PARTIALLY_FILLED' | 'FILLED' | 'CANCELED' | 'EXPIRED'
                "filled_price": avg_price,
                "filled_qty": exec_qty
            }
        except Exception as e:
            logging.error(f"查詢訂單 {order_id} 狀態失敗: {e}")
            raise

    def sync_db_with_exchange(self):
        """[全局同步] 掃描資料庫中所有未結案訂單，同步狀態。"""
        logging.info("開始與幣安交易所進行訂單狀態同步...")
        
        # 1. 同步待成交的買單
        pending_buys = db.get_orders_by_status(['PENDING_BUY'])
        for row in pending_buys:
            buy_id = row['buy_order_id']
            try:
                info = self.query_order_status(buy_id)
                status = info['status']
                if status == 'FILLED':
                    db.confirm_buy_order(buy_id, info['filled_price'], info['filled_qty'])
                    logging.info(f"[買單成交] DB ID: {row['id']} 成交價格: {info['filled_price']}, 數量: {info['filled_qty']}")
                elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                    db.cancel_buy_order(buy_id)
                    logging.info(f"[買單已取消] DB ID: {row['id']} 已於交易所撤銷")
            except Exception as e:
                logging.error(f"同步買單 {buy_id} 失敗: {e}")

        # 2. 同步待成交的賣單
        pending_sells = db.get_orders_by_status(['PENDING_SELL'])
        for row in pending_sells:
            sell_id = row['sell_order_id']
            try:
                info = self.query_order_status(sell_id)
                status = info['status']
                if status == 'FILLED':
                    db.confirm_sell_order(sell_id, info['filled_price'], info['filled_qty'])
                    logging.info(f"[賣單成交✅] DB ID: {row['id']} 賣出價格: {info['filled_price']}, 數量: {info['filled_qty']}")
                elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                    db.revert_sell_order_to_buy(sell_id)
                    logging.info(f"[賣單已取消❌] DB ID: {row['id']} 已撤銷，恢復等待掛賣")
            except Exception as e:
                logging.error(f"同步賣單 {sell_id} 失敗: {e}")
                
        logging.info("訂單狀態同步完成。")

    def calculate_grid_boundaries(self, current_price: float):
        """計算以當前價格為基準的網格格點價格。"""
        if config.GRID_INTERVAL == 25.0:
            k0 = math.floor(current_price / 25.0)
            buy_prices = [25.0 * (k0 - i) for i in range(config.GRID_NUM)]
            sell_prices = [25.0 * (k0 + j) for j in range(1, config.GRID_NUM + 1)]
        else:
            k0 = math.floor((current_price - 25.0) / config.GRID_INTERVAL)
            buy_prices = [config.GRID_INTERVAL * (k0 - i) + 25.0 for i in range(config.GRID_NUM)]
            sell_prices = [config.GRID_INTERVAL * (k0 + j) + 25.0 for j in range(1, config.GRID_NUM + 1)]
            
        return buy_prices, sell_prices

    def rebalance_grid(self, current_price: float):
        """[全局重組] 根據當前價格，對全量網格進行撤單與補單。"""
        buy_range, sell_range = self.calculate_grid_boundaries(current_price)
        min_buy_price = min(buy_range)
        max_buy_price = max(buy_range)
        min_sell_price = min(sell_range)
        max_sell_price = max(sell_range)
        
        logging.info(f"現價: {current_price} | 買單範圍: {min_buy_price} ~ {max_buy_price} | 賣單範圍: {min_sell_price} ~ {max_sell_price}")
        
        # 1. 撤銷超出範圍的買單
        pending_buys = db.get_orders_by_status(['PENDING_BUY'])
        for row in pending_buys:
            grid_price = row['grid_price']
            if grid_price not in buy_range:
                logging.info(f"買單格點 {grid_price} 超出範圍，撤單...")
                if self.cancel_order(row['buy_order_id']):
                    db.cancel_buy_order(row['buy_order_id'])

        # 2. 撤銷超出範圍的賣單
        pending_sells = db.get_orders_by_status(['PENDING_SELL'])
        for row in pending_sells:
            sell_price = row['sell_price']
            if sell_price not in sell_range:
                logging.info(f"賣單格點 {sell_price} 超出範圍，撤單...")
                if self.cancel_order(row['sell_order_id']):
                    db.revert_sell_order_to_buy(row['sell_order_id'])

        active_grids = db.get_active_grids()

        # 3. 補掛買單
        for p_buy in buy_range:
            if p_buy not in active_grids:
                multiplier = 10 ** config.QTY_DECIMALS
                qty = math.ceil((config.TRADE_AMOUNT / p_buy) * multiplier) / multiplier
                if qty <= 0:
                    continue
                try:
                    buy_id = self.place_limit_order('BUY', p_buy, qty)
                    db.insert_buy_order(p_buy, buy_id, p_buy, qty)
                except Exception as e:
                    logging.error(f"在格點 {p_buy} 掛買單失敗: {e}")

        # 4. 補掛賣單
        filled_buys = db.get_orders_by_status(['FILLED_BUY'])
        for row in filled_buys:
            p_sell = row['sell_price'] if row['sell_price'] > 0 else (row['grid_price'] + config.GRID_INTERVAL)
            qty_sell = row['buy_filled_qty']
            if p_sell in sell_range:
                try:
                    sell_id = self.place_limit_order('SELL', p_sell, qty_sell)
                    db.set_pending_sell(row['id'], sell_id)
                except Exception as e:
                    logging.error(f"針對 DB ID: {row['id']} 掛賣單失敗: {e}")

    async def sync_and_rebalance_local(self, current_price: float):
        """[局部優化同步與 Rebalance] 僅同步最靠近現價的上下各 5 格掛單，大幅降低 API 調用量。"""
        buy_range, sell_range = self.calculate_grid_boundaries(current_price)
        
        # 鄰近格點定義（買單最靠近現價的 5 格，賣單最靠近現價的 5 格）
        local_buy_prices = set(buy_range[:5])
        local_sell_prices = set(sell_range[:5])
        
        async with self.db_lock:
            # 1. 局部買單同步 (僅查詢在 local_buy_prices 內的 PENDING_BUY)
            pending_buys = db.get_orders_by_status(['PENDING_BUY'])
            for row in pending_buys:
                grid_price = row['grid_price']
                buy_id = row['buy_order_id']
                if grid_price in local_buy_prices:
                    try:
                        info = await self._run_async(self.query_order_status, buy_id)
                        if info['status'] == 'FILLED':
                            db.confirm_buy_order(buy_id, info['filled_price'], info['filled_qty'])
                            logging.info(f"[局部同步][買單成交] DB ID: {row['id']} 價格: {info['filled_price']}")
                        elif info['status'] in ['CANCELED', 'EXPIRED', 'REJECTED']:
                            db.cancel_buy_order(buy_id)
                            logging.info(f"[局部同步][買單已取消] DB ID: {row['id']}")
                    except Exception as e:
                        logging.error(f"局部同步買單 {buy_id} 失敗: {e}")

            # 2. 局部賣單同步 (僅查詢在 local_sell_prices 內的 PENDING_SELL)
            pending_sells = db.get_orders_by_status(['PENDING_SELL'])
            for row in pending_sells:
                sell_price = row['sell_price']
                sell_id = row['sell_order_id']
                if sell_price in local_sell_prices:
                    try:
                        info = await self._run_async(self.query_order_status, sell_id)
                        if info['status'] == 'FILLED':
                            db.confirm_sell_order(sell_id, info['filled_price'], info['filled_qty'])
                            logging.info(f"[局部同步][賣單成交✅] DB ID: {row['id']} 價格: {info['filled_price']}")
                        elif info['status'] in ['CANCELED', 'EXPIRED', 'REJECTED']:
                            db.revert_sell_order_to_buy(sell_id)
                            logging.info(f"[局部同步][賣單已取消❌] DB ID: {row['id']}")
                    except Exception as e:
                        logging.error(f"局部同步賣單 {sell_id} 失敗: {e}")

            # 3. 撤銷超出全局範圍的買賣單 (純本地資料庫篩選，不消耗 API)
            # 被撤銷的會發送 REST 撤單
            for row in pending_buys:
                if row['grid_price'] not in buy_range:
                    logging.info(f"買單 {row['grid_price']} 超出網格範圍，撤單...")
                    buy_id = row['buy_order_id']
                    if await self._run_async(self.cancel_order, buy_id):
                        db.cancel_buy_order(buy_id)
                    else:
                        # 撤單失敗，可能是訂單已成交或已手動取消，主動進行一次同步自癒
                        try:
                            info = await self._run_async(self.query_order_status, buy_id)
                            status = info['status']
                            logging.info(f"[自癒同步] 買單 {buy_id} 撤單失敗，查詢交易所狀態為: {status}")
                            if status == 'FILLED':
                                db.confirm_buy_order(buy_id, info['filled_price'], info['filled_qty'])
                                logging.info(f"[自癒同步][買單成交] DB ID: {row['id']} 價格: {info['filled_price']}, 數量: {info['filled_qty']}")
                            elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                                db.cancel_buy_order(buy_id)
                                logging.info(f"[自癒同步][買單取消] DB ID: {row['id']}")
                        except Exception as e_sync:
                            logging.error(f"[自癒同步] 查詢買單 {buy_id} 狀態失敗: {e_sync}")
                        
            for row in pending_sells:
                if row['sell_price'] not in sell_range:
                    logging.info(f"賣單 {row['sell_price']} 超出網格範圍，撤單...")
                    sell_id = row['sell_order_id']
                    if await self._run_async(self.cancel_order, sell_id):
                        db.revert_sell_order_to_buy(sell_id)
                    else:
                        # 撤單失敗，可能是訂單已成交或已手動取消，主動進行一次同步自癒
                        try:
                            info = await self._run_async(self.query_order_status, sell_id)
                            status = info['status']
                            logging.info(f"[自癒同步] 賣單 {sell_id} 撤單失敗，查詢交易所狀態為: {status}")
                            if status == 'FILLED':
                                db.confirm_sell_order(sell_id, info['filled_price'], info['filled_qty'])
                                logging.info(f"[自癒同步][賣單成交✅] DB ID: {row['id']} 價格: {info['filled_price']}, 數量: {info['filled_qty']}")
                            elif status in ['CANCELED', 'EXPIRED', 'REJECTED']:
                                db.revert_sell_order_to_buy(sell_id)
                                logging.info(f"[自癒同步][賣單取消❌] DB ID: {row['id']}")
                        except Exception as e_sync:
                            logging.error(f"[自癒同步] 查詢賣單 {sell_id} 狀態失敗: {e_sync}")

            # 獲取最新活躍格點
            active_grids = db.get_active_grids()

            # 4. 局部補掛買單
            for p_buy in buy_range[:5]:  # 僅在最靠近現價的 5 格補掛買單
                if p_buy not in active_grids:
                    multiplier = 10 ** config.QTY_DECIMALS
                    qty = math.ceil((config.TRADE_AMOUNT / p_buy) * multiplier) / multiplier
                    if qty <= 0:
                        continue
                    try:
                        buy_id = await self._run_async(self.place_limit_order, 'BUY', p_buy, qty)
                        db.insert_buy_order(p_buy, buy_id, p_buy, qty)
                        logging.info(f"[局部掛單] 在 {p_buy} 掛入買單成功。")
                    except Exception as e:
                        logging.error(f"在格點 {p_buy} 局部補掛買單失敗: {e}")

            # 5. 局部補掛賣單
            filled_buys = db.get_orders_by_status(['FILLED_BUY'])
            for row in filled_buys:
                p_sell = row['sell_price'] if row['sell_price'] > 0 else (row['grid_price'] + config.GRID_INTERVAL)
                qty_sell = row['buy_filled_qty']
                if p_sell in local_sell_prices:  # 僅在鄰近格點範圍內補掛賣單
                    try:
                        sell_id = await self._run_async(self.place_limit_order, 'SELL', p_sell, qty_sell)
                        db.set_pending_sell(row['id'], sell_id)
                        logging.info(f"[局部掛單] 針對 DB ID: {row['id']} 在 {p_sell} 掛入賣單成功。")
                    except Exception as e:
                        logging.error(f"針對 DB ID: {row['id']} 局部補掛賣單失敗: {e}")

    async def websocket_loop(self):
        """WebSocket 行情行情訂閱循環，支援自動斷線重連。"""
        while True:
            try:
                logging.info("正在建立 WebSocket 連線...")
                # 每次重新建立客戶端，以防內部的連線池或事件迴圈狀態損壞
                ws_client = Spot(config_ws_streams=self.configuration_ws)
                connection = await ws_client.websocket_streams.create_connection()
                # 訂閱 mini_ticker 串流 (小寫交易對名稱)
                stream = await connection.mini_ticker(symbol=config.SYMBOL.lower())
                
                # 記錄這次連線建立成功的時間
                conn_established_time = time.time()
                
                def on_msg(data):
                    try:
                        self.current_price = float(data.c)
                        self.last_ws_message_time = time.time()
                    except Exception as e:
                        logging.error(f"解析 WebSocket 數據失敗: {e}")
                
                stream.on("message", on_msg)
                logging.info("WebSocket 訂閱行情成功。")
                
                # 保持等待直到連接斷開，並加上心跳超時機制 (120秒無數據即視為斷訊)
                while not connection.close_initiated:
                    await asyncio.sleep(2)
                    now = time.time()
                    # 寬限期：如果連線剛建立不到 15 秒，不進行超時判定，給予時間接收第一筆數據
                    if now - conn_established_time < 15:
                        continue
                    
                    if now - self.last_ws_message_time > 120:
                        logging.warning("WebSocket 超過 120 秒未收到行情數據，判定為半開/假連線狀態，主動中斷以觸發重連。")
                        try:
                            await connection.close()
                        except Exception:
                            pass
                        raise Exception("WebSocket 接收行情超時")
                    
            except Exception as e:
                logging.error(f"WebSocket 連線中斷: {e}，將於 5 秒後重連...")
                await asyncio.sleep(5)

    async def get_current_working_price(self) -> float:
        """獲取當前可用價格。如果 WebSocket 正常，返回記憶體中的現價；若斷訊超過 120 秒，則透過 REST API 撈取最新價。"""
        now = time.time()
        if self.current_price > 0 and (now - self.last_ws_message_time) <= 120:
            return self.current_price
        
        # 否則，表示 WebSocket 斷線或尚未初始化完成，改用 REST API 自癒
        logging.warning("WebSocket 價格數據已過期或不可用，改由 REST API 獲取最新價格進行自癒。")
        try:
            latest_price = await self._run_async(self.get_latest_price)
            self.current_price = latest_price
            # 更新時間以避免接下來頻繁調用 API
            self.last_ws_message_time = now
            return latest_price
        except Exception as e:
            logging.error(f"透過 REST API 自癒獲取價格失敗: {e}")
            return self.current_price  # 萬不得已才回傳舊價格


    async def price_handler_loop(self):
        """監聽價格變動並觸發局部同步與掛單。"""
        last_triggered_price = 0.0
        logging.info("啟動價格變動監聽循環。")
        while True:
            try:
                if self.current_price > 0 and self.current_price != last_triggered_price:
                    curr = self.current_price
                    last_triggered_price = curr
                    logging.info(f"[價格變動] 最新價格: {curr}，執行局部同步與重組...")
                    await self.sync_and_rebalance_local(curr)
                    
                    # 印出當前累計利潤與活躍格點數
                    total_profit = db.get_total_profit()
                    active_grids = db.get_active_grids()
                    logging.info(f"當前累計利潤: {total_profit:.4f} FDUSD | 活躍格點數: {len(active_grids)}")
            except Exception as e:
                logging.error(f"價格監聽處理異常: {e}")
                logging.error(traceback.format_exc())
            await asyncio.sleep(0.5)

    async def global_sync_loop(self):
        """全局訂單同步循環，作為防漏單的雙重保險。"""
        logging.info("啟動定時全局同步循環（60 秒一次）。")
        while True:
            try:
                await asyncio.sleep(60)
                curr_price = await self.get_current_working_price()
                if curr_price > 0:
                    logging.info(f"[定時全局同步] 開始與交易所同步所有訂單... 當前參考價格: {curr_price}")
                    async with self.db_lock:
                        await self._run_async(self.sync_db_with_exchange)
                        await self._run_async(self.rebalance_grid, curr_price)
                    logging.info("[定時全局同步] 全局訂單同步與重組完成。")
            except Exception as e:
                logging.error(f"全局同步循環異常: {e}")
                logging.error(traceback.format_exc())

    async def start(self):
        """啟動機器人所有非同步工作。"""
        logging.info("=== BTC/FDUSD WebSocket 網格機器人啟動 ===")
        db.init_db()
        
        # 啟動時先進行一次同步，加鎖
        async with self.db_lock:
            try:
                # 先獲取當前價格以進行初次掛單
                self.current_price = await self._run_async(self.get_latest_price)
                logging.info(f"獲取初始現價: {self.current_price}")
                await self._run_async(self.sync_db_with_exchange)
                await self._run_async(self.rebalance_grid, self.current_price)
            except Exception as e:
                logging.error(f"啟動初始化同步失敗: {e}")
                logging.error(traceback.format_exc())

        # 同步啟動三個後台協程
        await asyncio.gather(
            self.websocket_loop(),
            self.price_handler_loop(),
            self.global_sync_loop()
        )

if __name__ == "__main__":
    bot = GridBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logging.info("偵測到 CTRL+C 手動終止訊號，正在自動撤銷幣安上所有掛單...")
        try:
            bot.client.rest_api.delete_open_orders(symbol=config.SYMBOL)
            logging.info("成功撤銷交易所所有掛單！")
        except Exception as e:
            logging.error(f"撤銷交易所掛單失敗: {e}")
        logging.info("機器人已安全退出。")
