# 任務與佇列

> 2026-11-23 整理

## 一張圖看懂

Celery 把「決定要做什麼」和「真的去做」分開，中間用 RabbitMQ 排隊：

```
producer（列出要做的任務）
  → RabbitMQ（8 個佇列，任務在這裡排隊）
  → worker（8 個，各自從自己的佇列拿任務去做）
  → PostgreSQL（寫結果）
  → 做完回報給 producer
```

| 角色 | 在這個專案 | 比喻 |
|---|---|---|
| producer | `crawler/`、`transform/`、`analyze/`、`ai/` 各一個 `producer.py` | 主管：寫工作單 |
| 佇列（RabbitMQ） | 8 個佇列 | 收件匣：工作單在這裡排隊 |
| worker | 8 個 `celery-*` 容器，一個聽一個佇列 | 員工：從自己的收件匣拿工作單去做 |

一個任務＝一件小事，例如「抓 11/23 的注意股」「替這則新聞寫摘要」。

## 所有任務和 worker

後面的任務要用前面的結果，所以 producer 一步做完才送下一步。同一步的任務互不等。

![任務和 worker](images/workers-and-tasks.png)

表裡的「佇列」是任務送進哪個佇列；聽這個佇列的 worker 見下面「8 個 worker」。

| 步驟 | 任務 | 佇列 | 為什麼排在這裡 |
|---|---|---|---|
| 1 | `crawl_holiday` 開休市日期、`crawl_market_daily` 市場成交 | `crawl_twse` | 判斷那天是不是交易日的依據，最先抓 |
| 2 | `crawl_stock_price` 收盤行情 | `crawl_twse` | 要先知道那天該不該有交易 |
| 3 | `crawl_punish` 處置股 | `crawl_twse` | 同上 |
| 4 | `crawl_notice` 注意股、`crawl_punish_watchlist` 注意累計名單、`crawl_company` 公司清單 | `crawl_twse` | 注意股還沒公布時名單是空的；和它一起公布的處置股公布了，空名單才代表真的沒人被列 |
| 5 | `crawl_announcement` 重大訊息清單 → `crawl_announcement_detail` 全文 | `crawl_mops` | 要先知道哪些股票被關注；和下一列同時 |
| 5 | `crawl_stock_news` 新聞清單 → `crawl_news_detail` 新聞網頁 | `crawl_finmind` → `crawl_news` | 同上 |
| 6 | `transform_xxx` 整理（11 種） | `transform` | 要先抓好 |
| 7 | `analyze_progress` 還差幾次 | `analyze` | 只讀整理好的資料；和下面的 AI 同時 |
| 7 | `classify_announcement` 公告分類 | `ai` | AI 照這個順序：後面的用前面的結果；呼叫 Claude |
| 7 | `summarize_news` 新聞摘要 | `ai` | 呼叫 Claude |
| 7 | `embed_news` 新聞向量 | `embed` | 要用 bge-m3 把「標題＋摘要」轉成向量（摘要要先寫好） |
| 7 | `group_news_stories` 同一件事 | `ai` | 只比資料庫裡已經有的向量，不用模型 |
| 7 | `summarize_card` 卡片 AI 摘要 | `embed` | 查詢也要用 bge-m3 轉成向量，才能挑相關的新聞；再呼叫 Claude |

AI 這段在 `ai` 和 `embed` 兩個 worker 之間輪流（圖上一上一下）：要用本機 embedding 模型（bge-m3）的任務都送 `embed`，只有它載入模型（約 2.3 GB 記憶體，只載一份）；其他的送 `ai`。順序不能換：向量要用摘要、歸類要用向量、卡片摘要要用前面全部的結果。

## 每小時一輪

週一～五 18:00～22:00，每個整點跑一次 `main.py`（一輪＝上面全部）。同一時間只跑一輪：上一輪還沒跑完，這一輪排隊等。

![每小時一輪](images/hourly-round.png)

| 哪裡失敗 | 這一輪 |
|---|---|
| crawler 的某個任務 | 其他的照抓；transform、analyze、ai 照跑（用已經抓到的）。少的那份資料：transform 跳過、analyze 那天不算，不會算錯 |
| transform | **停**：不跑 analyze、ai（stage 不完整，算出來會錯） |
| analyze 或 ai | 另一邊照跑 |

- **全部成功**：放當天的完成記號（`data/done/YYYYMMDD`），之後的整點看到就直接結束。
- **有任何失敗**：不放記號，**下一個整點再跑一輪**：做過的跳過，只補失敗、還沒做的。
- 暫時性的失敗（逾時、伺服器錯誤、太多請求）在這一輪裡就先自動重做（5 秒後，最多 3 次）；worker 掛掉，沒做完的任務回到佇列給別人做。

## 重跑不會怎樣

同一天重跑幾次、好幾個 worker 同時做，結果都一樣：

| 層 | 重跑時 |
|---|---|
| raw | 已經抓完整的不再抓；抓了就新增一列，舊的不改 |
| stage | 用主鍵更新，比較新的抓取才蓋掉舊的 |
| app | 先刪那天、再寫入（同一個交易） |
| ai | 同一版 prompt 已經有結果的不再問，不會重複花錢 |

## 8 個 worker

一個 worker 聽一個佇列。三個名字各用在不同地方：Docker 服務（`docker compose restart`）、Celery worker 名稱（Flower、`job_run.worker`）、佇列（RabbitMQ、送任務時指定）。

| Docker 服務 | Celery worker 名稱 | 聽的佇列 | 做什麼 | 同時做幾個 | 為什麼是這個數字 |
|---|---|---|---|---|---|
| `celery-crawl-twse` | `crawl_twse@tw_stock` | `crawl_twse` | 抓證交所 | 1，每個等 1 秒 | 太快會被擋 |
| `celery-crawl-mops` | `crawl_mops@tw_stock` | `crawl_mops` | 抓公開資訊觀測站 | 1，每個等 0.6 秒 | 太快會被擋 |
| `celery-crawl-finmind` | `crawl_finmind@tw_stock` | `crawl_finmind` | 抓 FinMind 新聞清單 | 1，每個等 6 秒 | 每小時上限 600 次 |
| `celery-crawl-news` | `crawl_news@tw_stock` | `crawl_news` | 打開新聞網頁 | 4 | 分散在十幾個網站 |
| `celery-transform` | `transform@tw_stock` | `transform` | raw → stage | 4 | 只碰資料庫 |
| `celery-analyze` | `analyze@tw_stock` | `analyze` | 算還差幾次 | 2 | 只碰資料庫 |
| `celery-ai` | `ai@tw_stock` | `ai` | 呼叫 Claude | 2 | API 有每分鐘的上限 |
| `celery-embed` | `embed@tw_stock` | `embed` | 用 bge-m3 轉向量、寫卡片摘要 | 1 | 模型吃 2.3 GB 記憶體 |

**為什麼要分這麼多個**：Celery 的「同時做幾個」設在 worker 上，所以每個來源要自己的速度，就要自己的佇列和 worker。只用一個佇列會：

- 好幾個 worker 同時打觀測站，被擋好幾個小時；
- FinMind 6 秒一個的任務排在前面，後面的全部跟著等；
- 每個 worker 都可能拿到要用模型的任務，每個都載入一份 2.3 GB。

## 怎麼看

- **Flower**（http://localhost:5556）：每個 worker 在做什麼、成功或失敗。
- **RabbitMQ 管理介面**（http://localhost:15673）：Queues 頁的 Ready＝每個佇列還排幾個。
- **任務歷史**：每個任務每跑一次一列。

```sql
SELECT task, status, count(*), round(avg(seconds), 1) AS avg_seconds
FROM tw_stock.job_run
WHERE started_at >= CURRENT_DATE
GROUP BY task, status
ORDER BY task, status;
```
