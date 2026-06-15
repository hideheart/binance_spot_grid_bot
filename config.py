# -*- coding: utf-8 -*-
"""
現貨網格交易系統設定檔
"""

import os
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數
load_dotenv()

# 幣安 API 金鑰設定
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')

# 交易對與參數設定
SYMBOL = 'BTCFDUSD'
BASE_SYMBOL = 'BTC'
QUOTE_SYMBOL = 'FDUSD'

GRID_INTERVAL = 20.0     # 網格間距 (25 美元)
TRADE_AMOUNT = 10.0       # 單筆交易金額 (5 FDUSD)
GRID_NUM = 30            # 基準現價上下各掛 10 格

# 資料庫檔案路徑
DB_FILE = 'grid.db'

# 交易精度設定 (BTCFDUSD)
# 價格精度：小數點後 2 位 (Tick Size = 0.01)
# 數量精度：小數點後 5 位 (Step Size = 0.00001)
PRICE_DECIMALS = 2
QTY_DECIMALS = 5

# 輪詢價格與檢查訂單的時間間隔 (秒)
POLL_INTERVAL = 10
