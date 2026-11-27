# 注意股，還差幾步？

> 一句話描述：為收盤後才看盤的短線、融資散戶，解決「自選股被列注意後，還差幾次會被處置、哪天開始受限」查不到的問題。

TibaMe 資料工程專題（NKR204），個人製作。2026/11 起實作，預計 12 月底 Demo。

---

## 為什麼做這個

台股有一套「注意 → 處置」制度：證交所每個交易日收盤後公布注意股，第 1–8 款的注意次數會累計，達到門檻（連 3 日、連 5 日、10 日內 6 次、30 日內 12 次）就成為**處置股**——撮合變慢、大額委託要先預收款券。

自選股被列注意時，散戶最想知道：這次會不會累計？還差幾次會被處置？如果明天再被列注意，從哪天開始受限？這些資訊今天散在 4 個地方（作業要點、證交所兩個公告頁面、觀測站逐家查詢），官方名單只列「只差一次」的股票，而且沒有歷史。

**本專案每個交易日替每檔注意股與處置股產生一張卡片**，只放可指回原文的事實：注意款次、四條累計路徑還差幾次（列出計入的日期）、處置條件與措施、幾號出關、公開資訊（重大訊息）。

**不做的事：** 不預測漲跌、不推薦買賣、不判斷是否涉及炒作。範圍限上市公司股票（不含權證、ETF）。

---

## 系統架構

```
Source → Ingest → Storage → Process → Serve → Observe
```

```
📁 Source：證交所 API、觀測站 API、FinMind 新聞、新聞網站
  ↓
📥 Ingest：Python requests；股價線週一～五 18:00～22:00 每小時、新聞線每天 08:00 起每 3 小時
  ↓
💾 Storage：PostgreSQL（raw / stage / app）
  ↓
⚙️ Process：pandas，清理 + 規則引擎
  ↓
🖥 Serve：FastAPI 資料 API、網頁（FastAPI＋Jinja2）
  ↓
📡 Observe：log + 任務歷史 + 回測、失敗通知
```

| 階段 | 工具 |
|---|---|
| Source | 證交所 API（注意股、處置股、注意累計名單、開休市日期、市場成交、收盤行情、上市公司）、公開資訊觀測站 API（重大訊息、重大訊息全文）、FinMind API（個股新聞）、新聞網站（新聞網頁的 `<head>`） |
| Ingest | Python requests；Celery + RabbitMQ 任務佇列（證交所、觀測站、FinMind 各一個 worker 限速，新聞網頁同時 4 個、照網站輪流，Flower 監看）；Windows 工作排程器（之後 Airflow） |
| Storage | PostgreSQL 三層：`raw_*`（API 原文）→ `stage_*`（清理後）→ `app_*`（引擎結果），另外 `ai_*`（AI 結果、LLM 呼叫紀錄）；之後 Cloud SQL、BigQuery |
| Process | pandas：`transform/` 整理（raw → stage）、`analyze/` 規則引擎——依要點第六條算離處置還差幾次（stage → app）；`ai/` Claude API——公告分類、新聞摘要、卡片 AI 摘要（結構化輸出），新聞 RAG（本機 bge-m3 轉向量，pgvector 檢索） |
| Serve | FastAPI 資料 API（只讀 `app_*` 表和 VIEW，回 JSON）＋ 網頁（FastAPI＋Jinja2 範本，卡片牆、個股頁、統計頁，只打 API、不連資料庫） |
| Observe | `data/logs/`、任務歷史 `tw_stock.job_run`、每天的檢查（資料線、網站、備份，有問題跳 Windows 通知）、AI 品質報表、回測（真實處置 75 筆全部算到，條件也都對上）；GitHub Actions 跑 CI（ruff、全部測試） |

順序是 ELT：原文先存進 raw，再清理、計算；規則改了可以從 raw 重算，不必重抓。每天只處理當天的資料。

地端（11 月）與雲端（12 月）架構圖：[`docs/images/architecture-local.png`](docs/images/architecture-local.png)、[`docs/images/architecture-cloud.png`](docs/images/architecture-cloud.png)。

### 產出

| 畫面 | 內容 |
| --- | --- |
| 個股卡片 | 狀態和收盤價、AI 摘要、近 30 個營業日公告（公開資訊）、新聞資訊、四條路徑還差幾次（處置進度，列出計入的日期）、注意紀錄、處置紀錄（幾號出關） |
| 卡片牆 | 每間上市公司一張卡片（不含權證、ETF）；分頁：全部、聽牌、即將處置、處置中、明日出關、其他；另可只看那天的注意股、出關（標籤寫日期，例如「11/20 注意股」） |
| 統計頁 | 標題列三個分頁：卡片牆／未來幾日／歷史統計（統計頁兩個分頁之間切換不整頁重新載入）。未來幾日：明天的交易限制（聽牌、即將處置、處置中幾檔，點了到卡片牆同名分頁）、未來 5 個交易日哪天出關（每天幾檔，點開看是哪幾檔）。歷史統計（近 30、60 個交易日或 8/10 新制以來），照被盯上到被處置的順序：每天被列注意幾檔（含第 13 款）、每天處置中與聽牌幾檔、開始處置幾檔（每日／每週）、被處置前最常被列哪一款、各產業列注意比例（每 100 家，全部產業都列，含全體上市公司基準）與被列注意公司的產業組成（圓餅，前 4 名＋其他）。每張圖一句結論和期間合計，只陳述數字；折線的圖例可開關，點圖上任一天看當天數字 |

線框圖見 [`docs/images/`](docs/images/)，詳細定義見 [`docs/proposal.md`](docs/proposal.md) 第 5 節。
卡片網站實際畫面：[`docs/images/web-1-card-wall.png`](docs/images/web-1-card-wall.png)（卡片牆）、[`docs/images/web-3-stock-card.png`](docs/images/web-3-stock-card.png)（個股卡片）。

### 專案結構

```
crawler/                    抓資料：API → raw（producer → task → client → repository）
├─ producer.py                producer：決定今天要抓哪些任務；`--part twse` 證交所（股價線）、`--part news` 觀測站、新聞兩條同時（新聞線）
├─ task.py                    task：每個 crawl_xxx 任務抓一份、存進 raw（@app.task 註冊成 Celery 任務，見 common/worker.py）
└─ client.py                  client：打證交所、觀測站、FinMind、新聞網頁（fetch_xxx，不存檔）、檢查回應內容
transform/                  轉換：raw → stage（同樣的 producer → task 寫法）
├─ producer.py                producer：產生任務清單（每份 raw 一個任務）並執行
├─ task.py                    task：transform_xxx，每個 crawl_xxx 對應一個
├─ transformer.py             transformer：每個資料集 xxx_to_frame 攤平、clean_xxx 整理
└─ parsing.py                 transformer 用的解析工具（去 HTML、轉數字、檢查欄位）
analyze/                    分析：stage → app（同樣的 producer → task 寫法）
├─ producer.py                producer：產生任務清單（每個日期一個任務）並依序執行
├─ task.py                    task：analyze_progress(date_yyyymmdd)，一天一個任務
├─ engine.py                  規則引擎：依要點第六條算還差幾次、若再列注意的處置日期、幾號出關
└─ backtest.py                回測（Observe）：引擎預測的處置 vs 真實處置公告
ai/                         AI 項目：只讀 stage 和前面的 AI 結果，呼叫 LLM、存進 ai_* 表（同樣的 producer → task → client → repository 寫法；
                            新聞摘要例外：全文不存，當場用 crawler/client.py 重抓網頁）
├─ producer.py                producer：產生任務清單（公告、新聞、每天的向量、同一件事、卡片，各自還沒做過的）
├─ task.py                    task：classify_announcement、summarize_news、embed_news、group_news_stories、summarize_card
├─ client.py                  client：呼叫 Claude API（fetch_xxx，結構化輸出，不存檔）、檢查回應、算 token 花費
├─ classifier.py              公告分類的 prompt、JSON schema（13 類）、檢查摘要的長度
├─ summarizer.py              新聞摘要的 prompt、JSON schema、檢查摘要的長度
├─ card_summarizer.py         卡片 AI 摘要的 prompt、款次的白話說法、整理輸入、檢查數字和引用的新聞
├─ embedder.py                新聞 RAG：本機跑 bge-m3，把文字轉成向量（pgvector 的寫法）
├─ retriever.py               新聞 RAG：轉向量的文字、挑新聞（去掉沒內容的、合併同一件事的報導、最多 5 則）、同一件事的歸類
└─ verifier.py                檢查 AI 的回答，三個項目共用（摘要裡的數字要出現在原文裡）
api/                        資料 API：FastAPI 讀 app_* 表和 VIEW，回 JSON
├─ main.py                    進入點（uvicorn api.main:app）
├─ routes.py                  端點：/health、/dates、/cards、/stocks/{代號}、/stats（文件在 /docs）
└─ queries.py                 讀資料庫（只讀 app_*，參數化查詢）
dashboard/                  網頁（FastAPI＋Jinja2）：打資料 API，不直接連資料庫
├─ app.py                     進入點（uvicorn dashboard.app:app）：讀網址參數、打 API、回傳整頁 HTML
├─ client.py                  呼叫 API（requests），日期字串換回 date
├─ views.py                   把資料整理成畫面上的文字、分區、篩選（不碰網路）
├─ charts.py                  統計頁的圖：只算 SVG 座標（不碰網路、不寫 HTML）
├─ render.py                  把資料套進 Jinja2 範本，變成 HTML
└─ templates/、static/        網頁範本（卡片牆、個股頁、統計頁）、CSS
common/                     各資料夾共用（crawler、transform、analyze 彼此不互相 import，只用 common；ai 的新聞摘要例外，用 crawler/client.py）
├─ repository.py              repository：連線、建表、raw 表讀寫、stage／app 表讀寫
├─ runner.py                  四個 producer 共用的任務執行：依序執行、好幾條任務鏈同時進行、暫時性失敗重試、列出失敗
├─ cli.py                     四個 producer 共用的指令參數（daily／backfill、--refetch、--part；main.py 多一個 news）
├─ config.py                  設定（會因環境改變的值，從 .env 讀）
├─ worker.py                  Celery app 和任務共用的行為（重試、間隔）；USE_CELERY=1 時任務送進 RabbitMQ（佇列 crawl_twse、crawl_mops、crawl_finmind、crawl_news、transform、analyze、ai）
├─ date_utils.py              日期工具
├─ rules.py                   處置規則的常數（門檻、計入的款次、新制、處置天數、撮合間隔）：規則引擎和網頁共用
└─ hash_utils.py              雜湊工具：url_md5（新聞的 news_id＝連結的 md5）
schema/                     建表 SQL：raw.sql、stage.sql、app.sql、view.sql（給網頁讀的 VIEW）、ai.sql（AI 結果）、log.sql（任務歷史）
scripts/                    .bat：排程（daily＝股價線、news＝新聞線、backup＝備份、check＝每天的檢查）、回補（backfill）、從 raw 重算（rebuild）、網站（web：API ＋ 網頁）；
                            .py：備份（backup_db）、確認備份還原得回來（check_backup）、每天的檢查（check_health）、AI 品質報表（ai_report）
tests/                      測試
docs/                       流程說明（flow.md）、任務與佇列（queues.md）、資料表（tables.md）、處置規則（rules.md）、AI 項目（ai_plan.md）、提案
main.py                     兩條資料線：股價線（證交所 → transform → analyze）、新聞線（`main.py news`：觀測站、新聞 → transform → ai）
```

---

## 快速開始

### Docker Compose（一鍵啟動）

PostgreSQL、RabbitMQ、7 個 Celery worker、Flower、資料 API、網頁全部用 Docker 跑（Docker 裝在 WSL 裡，在 PowerShell 要打 `wsl docker compose …`）：

```bash
cp .env.example .env                                # 填入密碼（資料庫、RabbitMQ 一定要填；AI 項目要 ANTHROPIC_API_KEY，沒填就跳過）
wsl docker compose up -d --build --remove-orphans       # 啟動（第一次會建表；--remove-orphans 順便刪掉 compose 檔已經拿掉的服務，例如舊的 celery-embed）
wsl docker compose run --rm producer python main.py       # 跑一次股價線：任務送進 RabbitMQ，由 worker 執行
wsl docker compose run --rm producer python main.py news  # 跑一次新聞線（要讀股價線整理好的注意股）
```

開發時 `.env` 的 `COMPOSE_FILE` 把 `docker-compose.local.yml` 疊上來：程式碼直接掛進容器，api、web 存檔自動重載；改了任務的程式要重啟 worker：

```bash
wsl docker compose restart celery-crawl-twse celery-crawl-mops celery-crawl-finmind celery-crawl-news   celery-transform celery-analyze celery-ai
```

| 服務 | 網址 |
|---|---|
| 網頁 | http://localhost:8502 |
| 資料 API | http://localhost:8001/docs |
| Flower（worker、任務狀況；`.env` 的 `FLOWER_USER`／`FLOWER_PASSWORD` 登入） | http://localhost:5556 |
| RabbitMQ 管理介面 | http://localhost:15673 |

只有網頁（8502）開給別台電腦（同一個網路的手機、筆電）；資料庫、RabbitMQ、Flower、資料 API 只開在這台電腦。網頁的容器不拿 `.env`（不連資料庫、不用金鑰）。

7 個 worker 各聽一個佇列（`crawl_twse`、`crawl_mops`、`crawl_finmind`、`crawl_news`、`transform`、`analyze`、`ai`），各自控制速度：打外部網站的一次做一個、做完等一下，只碰資料庫的同時做好幾個。
log 在 `data/logs/`，每天一個檔、留 30 天：`daily-YYYY-MM-DD.log`（股價線）、`news-YYYY-MM-DD.log`（新聞線）、`crawl_twse-…`、`crawl_mops-…`、`crawl_finmind-…`、`crawl_news-…`、`transform-…`、`analyze-…`、`ai-…`（七個 worker）。每個任務每跑一次另外記一列在 `tw_stock.job_run`（任務歷史，永久保留）。

### 本機（不用 Docker 跑程式，資料庫還是要有）

預設 `USE_CELERY=1`：任務送進 RabbitMQ 給 Compose 裡的 Celery worker 做（要先 `wsl docker compose up -d`）。沒開 Docker 時，`.env` 設 `USE_CELERY=0` 就改成在這台電腦依序執行，不需要 RabbitMQ。

```bash
git clone https://github.com/hannahTai/G2-attention_stock_tracker-tw_stock.git
cd G2-attention_stock_tracker-tw_stock
cp .env.example .env   # 填入 PostgreSQL 連線資訊
uv sync                # 安裝套件（依 uv.lock）
uv run python main.py       # 股價線：建表、更新 VIEW（schema/*.sql 改過才做）→ 抓證交所 → 整理 → 算還差幾次
uv run python main.py news  # 新聞線：抓重大訊息、新聞 → 整理 → AI 項目
```

回補一段期間：`uv run python main.py backfill --start 20260701`（兩條線都跑，先股價線；已經抓完整的跳過，加 `--refetch` 全部重抓）。AI 任務只有一個 worker、一次做一個，大量回補請挑沒有排程的時段跑，不然排程的新聞線要排在後面等。

網站分兩個程式，開兩個終端機各跑一個（或直接執行 `scripts\run_web.bat`，兩個一起開；Docker Compose 已經在跑就不用，埠號會撞）：

```bash
uv run uvicorn api.main:app --reload --port 8001   # 1. 資料 API，文件 http://localhost:8001/docs
uv run uvicorn dashboard.app:app --reload --port 8502   # 2. 網頁，打開 http://localhost:8502
```

第一次要先跑過 `main.py`（建表、建 VIEW、算出至少一天）。
每一步單獨執行、排程設定見 [`docs/flow.md`](docs/flow.md)；任務和佇列見 [`docs/queues.md`](docs/queues.md)；資料表見 [`docs/tables.md`](docs/tables.md)；規則見 [`docs/rules.md`](docs/rules.md)；AI 項目見 [`docs/ai_plan.md`](docs/ai_plan.md)。

---


## License

MIT
