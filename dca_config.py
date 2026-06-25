# -*- coding: utf-8 -*-
"""
現貨定投交易系統設定檔
"""
import os
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數
load_dotenv()

# 幣安 API 金鑰設定
API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_API_SECRET')

# 資料庫檔案路徑
DB_FILE = 'dca.db'

# 輪詢檢查與更新狀態的時間間隔 (秒)
POLL_INTERVAL = 10

# Maker 單掛單超時時間 (秒)
# 超過此時間未成交，將撤單並以最新的買一價 (Bid 1) 重新掛單，直到完全成交
ORDER_TIMEOUT = 300  # 5 分鐘

# 多幣種定投計劃配置
# symbol: 交易對名稱 (必須以 FDUSD 結尾)
# times_per_day: 一天定投次數 (例如 24 代表每小時一次，4 代表每 6 小時一次，1 代表每天一次)
# amount_per_time: 每次定投買入金額 (單位: FDUSD，建議設定大於 5 FDUSD)
DCA_PLANS = [
    {
        "symbol": "BTCFDUSD",
        "times_per_day": 2,          # 一天 4 次 (每 6 小時一次)
        "amount_per_time": 10.0,     # 每次買 10 FDUSD
    },
    {
        "symbol": "ETHFDUSD",
        "times_per_day": 2,          # 一天 2 次 (每 12 小時一次)
        "amount_per_time": 10.0,     # 每次買 10 FDUSD
    },
    {
        "symbol": "BNBFDUSD",
        "times_per_day": 2,          # 一天 1 次 (每 24 小時一次)
        "amount_per_time": 10.0,     # 每次買 10 FDUSD
    }
]
