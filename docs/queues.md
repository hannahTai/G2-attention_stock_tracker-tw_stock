# 注意股專題：任務與佇列

> 2026-11-23 整理

每一件事都寫成一個 Celery 任務，producer（`main.py`）送進 RabbitMQ 的佇列，worker 拿出來做、寫進資料庫。任務之間不直接溝通，靠資料表傳資料；先後順序由 producer 控制（一批做完才送下一批）。

## 佇列和 worker

一個佇列守一種限制，一個 worker 聽一個佇列（8 個佇列、8 個 worker，同一份程式，只差 `-Q` 和 `--concurrency`）：

| 佇列 | 同時做幾個 | 任務 | 守的限制 |
|---|---|---|---|
| `crawl_twse` | 1（每個做完等 1 秒） | 注意股、處置股、收盤行情、市場成交、休市日、公司清單、注意累計名單 | 證交所：太快會被擋 IP |
| `crawl_mops` | 1（等 0.6 秒） | 重大訊息清單、全文 | 觀測站：太快會被擋 IP |
| `crawl_finmind` | 1（等 1 秒） | 個股新聞清單 | FinMind：每小時的次數上限 |
| `crawl_news` | 4（等 0.5 秒） | 新聞網頁 | 分散在十幾個網站，各網站分開算 |
| `transform` | 4 | raw → stage | 沒有外部限制 |
| `analyze` | 2 | stage → app（還差幾次） | 沒有外部限制 |
| `ai` | 2 | 公告分類、新聞摘要、同一件事 | Claude API：每分鐘的次數上限 |
| `embed` | 1 | 新聞向量、卡片 AI 摘要 | bge-m3 模型約 2.3 GB 記憶體，只載入一份 |

## 為什麼這樣分

Celery 的「同時做幾個」設在 worker 上，不是設在任務上。所以要讓不同的工作各自限速，標準做法就是分佇列、各自開 worker（Celery 官方文件 Routing Tasks）。只用一個佇列會：

- **被官方網站擋**：好幾個 worker 同時打觀測站，IP 被擋好幾個小時。
- **記憶體不夠**：每個 process 都可能拿到要用模型的任務，每個都載入一份 2.3 GB。
- **快的被慢的卡住**：幾千個 FinMind 任務排在前面，後面的 transform 等十幾個小時。

transform、analyze 其實可以共用一個佇列（producer 本來就不會讓它們搶），分開是讓佇列和資料夾對得起來，Flower、log 上一眼看出是哪一步。

## 什麼時候同時在忙

- crawler 的觀測站、新聞兩條同時（`crawl_mops` 和 `crawl_finmind`、`crawl_news`）。
- analyze 和 ai 同時（`analyze` 和 `ai`、`embed`）。
- 好幾個 producer 同時跑，例如回補和每天的資料線：各排各的佇列，不會互相卡住。

## 所有任務共用的行為（`common/worker.py` 的 `Job`）

- 暫時性的失敗（逾時、429、5xx、資料庫鎖衝突）5 秒後重做，最多 3 次；任務是冪等的，重做沒有副作用。
- 做完才確認（`task_acks_late`）：worker 掛掉，任務回到佇列；一次只先拿一個（`worker_prefetch_multiplier=1`）。
- 每次嘗試寫一列 `tw_stock.job_run`；log 在 `data/logs/<佇列名稱>-YYYY-MM-DD.log`。

## 怎麼看

- Flower：http://localhost:5556（worker 在不在線、每個佇列排了幾個、成功或失敗）。
- 任務歷史：

```sql
SELECT task, status, count(*), round(avg(seconds), 1) AS avg_seconds
FROM tw_stock.job_run
WHERE started_at >= CURRENT_DATE
GROUP BY task, status
ORDER BY task, status;
```
