# 幣安現貨交易系統：網格交易與多幣種定投 (DCA) 機器人

這是一個基於幣安 (Binance) Spot API 與 WebSocket Streams 開發的現貨交易系統，包含 **BTC/FDUSD 現貨網格交易機器人** 以及 **多幣種免手續費定投 (DCA) 交易機器人**。

系統均採用限價掛單（LIMIT/LIMIT_MAKER）模式交易，完美避開吃單手續費，並具備強大的自癒與斷線重連機制。同時，網格機器人附帶一個簡易的網頁版監控 Dashboard（實盤公開預覽：[https://grid.cti.app/](https://grid.cti.app/)）。

---

> [!IMPORTANT]
> **FDUSD 手續費優惠提醒**：
> 在幣安上使用 **FDUSD** 對部分交易對進行限價掛單（Maker）享有**零手續費**優惠（例如 BTC/FDUSD 等）。
> - **網格機器人** 與 **定投機器人** 均經過特別設計，只會掛出 **LIMIT_MAKER** 買賣單。
> - 請務必使用 FDUSD 計價交易對，以避免交易手續費蠶食您的投資利潤。

---

## 🌟 核心特色

### 1. 現貨網格機器人 (grid_bot.py)
* **即時價格監聽**：透過 WebSocket Streams 取得即時市場行情。
* **半開/死連線檢測**：加入 120 秒超時心跳檢測機制，自動斷線重連。
* **Rest API 備援自癒**：當 WebSocket 斷訊時，定時同步任務會自動退回使用 Rest API 撈取最新價格，防止以過期價格同步網格導致掛單錯誤。
* **局部同步優化**：僅針對靠近現價的格點進行 Rest 同步，大幅降低幣安 API 權重消耗。
* **交易 Dashboard**：提供簡易的後端伺服器與 HTML 監控介面，方便即時查看累計利潤、網格分佈與近期成交明細。

### 2. 多幣種定投機器人 (dca_bot.py)
* **多幣種平行定投**：支援在一個程式中同時為多個配置的幣種（如 BTC, ETH, BNB 等）進行定投，每個幣種作為獨立的協程平行執行。
* **免手續費掛單策略**：採用 `LIMIT_MAKER` 限價單掛在「買一價 (Bid 1)」進行購買，確保 100% 享有掛單零手續費。
* **超時撤單重掛（追價）**：當市場上漲導致買單無法成交，若掛單超過設定時間（預設 5 分鐘）未成交，機器人會自動撤單，並以最新的買一價重新掛單，直至該輪定投金額完全買齊。
* **重啟自癒與防重複購買**：利用 SQLite 資料庫儲存定投任務歷史與狀態，即便中途重啟，也只會繼續追趕未完成的定投，若已買齊則會嚴格等待下個定投週期，絕不重疊買入。
* **自動交易所規格解析**：啟動時自動向交易所查詢各交易對的價格精度、數量精度以及最小交易額限制，免去手動配置精度的煩惱。

---

## 📂 專案結構

```text
現貨網格/
├── .env.example        # 環境變數設定範本 (API 金鑰)
├── .gitignore          # Git 忽略設定檔案 (已防範金鑰及資料庫外洩)
├── requirements.txt    # 專案依賴 Python 套件
│
├── ── 網格機器人模組 ──
├── config.py           # 網格間距、單筆金額、精度等系統設定
├── db.py               # SQLite 資料庫操作模組 (網格資料庫)
├── grid_bot.py         # 網格交易核心主程式 (包含 Webhook 接收與自癒)
├── run_bot.bat         # 網格機器人 Windows 一鍵啟動指令檔
├── test_grid.py        # 網格邊界與計算邏輯測試檔
│
├── ── 定投機器人模組 ──
├── dca_config.py       # 定投配置檔 (多幣種、定投頻率、每次金額)
├── dca_db.py           # 定投 SQLite 資料庫操作模組
├── dca_bot.py          # 定投交易核心主程式 (Worker 協程、追價邏輯)
├── run_dca_bot.bat     # 定投機器人 Windows 一鍵啟動指令檔
│
└── web/                # 網格 Dashboard 前端與後端
    ├── index.html      # 監控 Dashboard 前端網頁
    └── server.py       # 監控 Dashboard 後端 HTTP 伺服器
```

---

## 🛠️ 安裝與設定說明

> [!NOTE]
> 如果您還沒有幣安 (Binance) 帳戶，歡迎使用邀請碼 **`CLICK168`** 註冊以享有手續費優惠，或直接點擊 [幣安推薦註冊連結](https://accounts.binance.com/register?ref=CLICK168) 進行註冊。

### 1. 準備環境
本專案建議使用 Python 3.10+。請於專案目錄下建立並啟用虛擬環境（建議使用 `uv` 或 `venv`）：
```bash
# 建立虛擬環境
python -m venv .venv

# 啟用虛擬環境 (Windows)
.venv\Scripts\activate
```

### 2. 安裝套件
啟用虛擬環境後，使用下方指令安裝專案所需的 Python 依賴：
```bash
pip install -r requirements.txt
```

### 3. 配置 API 金鑰（⚠️ 重要安全步驟）
1. 複製專案目錄下的 `.env.example` 並重新命名為 `.env`：
   ```bash
   cp .env.example .env
   ```
2. 開啟 `.env` 檔案，填入您在幣安申請的 API 金鑰：
   ```ini
   BINANCE_API_KEY=您的幣安API金鑰
   BINANCE_API_SECRET=您的幣安API私鑰
   ```

---

## 🚀 啟動與參數配置

### A. 運行現貨網格交易

#### 1. 自訂網格參數
您可以編輯 [`config.py`](file:///d:/GitRepo/Binance/現貨網格/config.py) 來調整網格運行參數：
* `GRID_INTERVAL`：網格間距（例如：`20.0` 代表每間隔 20 FDUSD 掛一格）。
* `TRADE_AMOUNT`：單筆掛單金額（例如：`10.0` 代表每筆買/賣單使用等值 10 FDUSD 的資金量）。
* `GRID_NUM`：網格數量（控制基準現價上下要補掛的單邊網格格數）。

#### 2. 啟動機器人
在 Windows 上，直接執行 `run_bot.bat`；或在命令行中執行：
```bash
python grid_bot.py
```

#### 3. 啟動監控 Dashboard
1. 啟動後端伺服器：
   ```bash
   python web/server.py
   ```
2. 使用瀏覽器開啟：[http://localhost:5000](http://localhost:5000) 即可查看即時的收益與網格狀態。

---

### B. 運行多幣種定投 (DCA)

#### 1. 配置定投計劃
您可以編輯 [`dca_config.py`](file:///d:/GitRepo/Binance/現貨網格/dca_config.py) 來調整您的定投策略：
* `ORDER_TIMEOUT`：Maker 單超時時間（秒），預設 `300`（5 分鐘）。超過此時間未成交會自動撤單追價。
* `DCA_PLANS`：定投計劃陣列。您可以新增多個幣種的設定，例如：
```python
DCA_PLANS = [
    {
        "symbol": "BTCFDUSD",
        "times_per_day": 2,          # 一天定投 2 次 (每 12 小時一次)
        "amount_per_time": 10.0,     # 每次買入 10 FDUSD
    },
    {
        "symbol": "ETHFDUSD",
        "times_per_day": 4,          # 一天定投 4 次 (每 6 小時一次)
        "amount_per_time": 10.0,     # 每次買入 10 FDUSD
    }
]
```

#### 2. 啟動定投機器人
在 Windows 上，直接執行 `run_dca_bot.bat`；或在命令行中執行：
```bash
python dca_bot.py
```
定投數據將會寫入 `dca.db` 數據庫中，且定投日誌會儲存在 `log/dca_bot.log`。

---

## 🔒 安全性宣告
* **金鑰保護**：所有的敏感 API 憑證皆讀取自本地的 `.env`。`.gitignore` 檔案中已設定忽略 `.env`、`*.db`（SQLite 庫檔案）以及 `log/`（日誌資料夾）。
* **無公開風險**：請不要修改 `.gitignore` 中關於安全部分的設定，以防將實盤交易的資料庫或金鑰推送到公開的 GitHub 倉庫。
