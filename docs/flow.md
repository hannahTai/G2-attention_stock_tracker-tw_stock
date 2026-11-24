# 執行流程

> 2026-11-13 整理，2026-11-24 更新

| 步驟 | 讀 | 寫 | 做什麼 |
|---|---|---|---|
| crawler | API | `raw_*` | 抓資料，存原文 |
| transform | `raw_*` | `stage_*` | 整理成表 |
| analyze | `stage_*` | `app_*` | 規則引擎算離處置還差幾次 |
| ai | `stage_*`、前面的 AI 結果 | `ai_*` | 公告分類、新聞摘要、新聞 RAG、同一件事、卡片 AI 摘要 |

資料線分兩條，各自排程。網站的 VIEW 再把兩邊的結果合起來：

```
股價線（證交所）  crawler → raw_* → transform → stage_*（注意股、處置股、收盤行情…）→ analyze → app_*
                                                    │
                                          stage_watched_stock（要關注哪些股票）
                                                    ↓
新聞線（觀測站、FinMind、新聞網站）  crawler → raw_* → transform → stage_*（重大訊息、新聞）→ ai → ai_*

app VIEW（把 app_*、ai_* 合起來）→ API → 網頁
```

| | 股價線 | 新聞線 |
|---|---|---|
| 指令 | `python main.py`（daily） | `python main.py news` |
| 排程 | 週一～五 18:00～22:00 每小時 | 每天 08:00、11:00、14:00、17:00、20:00、23:00，加上股價線跑完那一次 |
| crawler | 證交所（`--part twse`） | 觀測站、FinMind、新聞網頁（`--part news`） |
| transform | 注意股、處置股、注意累計名單、收盤行情、市場成交、開休市日期、公司清單 | 重大訊息（清單、全文）、新聞（清單、網頁） |
| 最後一步 | analyze（還差幾次） | ai（公告分類、新聞摘要、新聞 RAG、同一件事、卡片 AI 摘要） |
| 跑完 | 放當天的記號（`data/done/`），接著跑一次新聞線；之後同一天的排程直接結束 | 不放記號，每次都跑（已經抓完整的、30 分鐘內剛抓過的跳過） |
| 鎖 | `data/pipeline.lock` | `data/news.lock` |

為什麼分兩條：證交所一天公布一次（17:30～17:45），公告、新聞隨時都有，FinMind 又限制速度（6 秒一個）。分開以後還差幾次 18:00 很快就算好，不用等新聞；新聞抓失敗也不影響還差幾次。

兩條線怎麼接起來：新聞線讀 `stage_watched_stock`（往前 30 個交易日被列注意過的上市公司，和卡片牆同一批）決定查哪些公司；當天的名單要等股價線整理好，還沒有（早上、週末）就用之前最近一個交易日的名單。卡片 AI 摘要要用當天的注意股、處置股、收盤行情和公告、新聞，所以只寫這三樣證交所資料都整理好的日子：股價線成功的那一輪（18:00，證交所晚公布就是 19:00、20:00…）放完記號就接著跑一次新聞線，當天新被列注意的股票馬上查公告、新聞，當天的卡片也馬上寫（一檔一天寫一次；新聞線正在跑就等它跑完再跑）。每一檔還要那天的新聞清單、那個月的公告清單在注意股抓到以後又抓過、上個月的公告清單也抓過才寫：17:00 那次跑到一半股價線才跑完，當天新被列注意的股票那一輪沒抓，或抓失敗（例如 FinMind 429），那檔就等下一輪抓到了再寫（寫錯了不會自己更正，不能寫成「沒有公告和新聞」；寫完以後只有出現新公告才重寫）。沒寫成的（股價線很晚才跑完、最後一次新聞線失敗）之後的新聞線會補：卡片摘要往前看 7 天，補還沒寫的。

## 每天怎麼跑

期間在 `main.py` 算一次，再傳給每一步（跑過午夜，每一步的「今天」也一樣）。

**股價線**（18:00～22:00 每小時，一輪不到一分鐘）：證交所 17:30～17:45 公布，18:00 通常就跑得完；萬一還沒公布，那次會失敗、下一個小時再試；全部成功就放當天的記號，接著跑一次新聞線（見下面）。開始時建表、更新 VIEW（`schema/*.sql`）：會鎖住很多表，新聞線同時在寫就可能互相卡住，所以只在 `schema/*.sql` 改過時才做（雜湊值記在資料庫 `tw_stock` schema 的 COMMENT，還原舊備份也會重建；當天已經放記號的股價線直接結束，交給新聞線檢查）；回補兩條線都鎖著，每次都做。

新聞線開始時也一樣檢查（週末、股價線已經放記號時更新程式，網站也用得到新的 VIEW）。不管哪條線，要建表時兩條線的鎖一起拿（另一條線不會同時在寫；只拿到一個就放掉再試，不會拿著一個等另一個），做完再拿自己的鎖照常跑。一定要建好才往下跑（網站讀新的欄位才不會出錯）：另一條線正在跑就等它跑完，只有改過 schema 的那次會等。單獨跑某一步的 producer 只檢查 `schema/*.sql` 的表、索引、VIEW 都在，缺了才建。

1. **crawler**（`--part twse`）：證交所分步驟依序：開休市日期、市場成交 → 收盤行情 → 處置股 → 注意股和其他（後面要靠前面判斷那天是不是交易日）。某一份抓失敗就記下來，繼續抓下一份。
2. **transform**（`--part twse`）：每份 raw 整理成 stage。raw 沒抓到就跳過；程式出錯（例如官方改了欄位）就不跑 analyze。
3. **analyze**：先確認往前 30 個交易日的交易日、注意股、處置股都有資料，缺一天那天就不算（會算錯）；再算每檔離處置還差幾次。

**新聞線**（08:00、11:00、14:00、17:00、20:00、23:00，加上股價線跑完那一次）：

1. **crawler**（`--part news`）：讀 `stage_watched_stock` 決定查哪些公司；觀測站（重大訊息清單 → 全文）、新聞（FinMind 清單 → 新聞網頁）兩條同時。新聞網頁是別人家的網站，打開時有限制：只打 http(s)、不打內部網址（每一站轉址都檢查）、只讀網頁、最多 5 MB、60 秒內讀完（太慢算暫時性的失敗，會重試）。
2. **transform**（`--part news`）：重大訊息、新聞整理成 stage。程式出錯就不跑 ai。
3. **ai**：公告分類 → 新聞摘要 → 新聞 RAG → 同一件事 → 卡片 AI 摘要。失敗只影響 AI 的結果，網站照常。沒設 `ANTHROPIC_API_KEY` 就只做不用 Claude 的新聞 RAG、同一件事。

**回補**（`main.py backfill`）：兩條線都跑，先股價線、再新聞線（新聞線要讀股價線整理好的注意股）。股價線有失敗照樣跑新聞線，最後結束代碼 1。

同一條線同一時間只跑一輪：開始前鎖住自己的檔案，上一輪還沒跑完就排隊等（最多 2 小時）。兩條線可以同時跑（寫不同的表）；回補、建表兩個一起鎖。

## 有失敗時怎麼查、怎麼補

1. 有沒有成功：`Get-ScheduledTaskInfo G2-attention_stock_tracker-daily`（股價線）、`G2-attention_stock_tracker-news`（新聞線），`LastTaskResult` 0＝成功、1＝有失敗。
2. 看 `data/logs/daily-YYYY-MM-DD.log`（股價線）、`data/logs/news-YYYY-MM-DD.log`（新聞線）裡標 `✗` 的行，或查任務歷史：`SELECT task, args, error FROM tw_stock.job_run WHERE status = 'failure' AND started_at >= CURRENT_DATE`。

| log 裡看到 | 意思 | 怎麼做 |
|---|---|---|
| `crawl_notice(date_yyyymmdd='20261113') ✗ 連線逾時` | 那一份沒抓到 | 補跑那天 |
| `transform 有任務失敗，不跑 analyze`（或 `不跑 ai`） | 整理時程式出錯 | 修好程式再補跑 |
| `缺少必要的資料，不算：[...]` | 那天需要的資料還沒有 | 補跑那天 |

3. 補跑 11/13：`uv run python main.py backfill --start 20261113 --end 20261113`（兩條線都跑）。重跑不會重複（見下面的冪等）。也可以從出錯的那一步開始一步一步跑（crawler 一定要加 `--part twse` 或 `--part news`：新聞的部分要讀證交所整理好的注意股，中間要先跑 transform；transform 不加就兩個都做，先證交所）：

```
uv run python -m crawler.producer   backfill --start 20261113 --end 20261113 --part twse
uv run python -m transform.producer backfill --start 20261113 --end 20261113 --part twse
uv run python -m analyze.producer   backfill --start 20261113 --end 20261113
uv run python -m crawler.producer   backfill --start 20261113 --end 20261113 --part news
uv run python -m transform.producer backfill --start 20261113 --end 20261113 --part news
uv run python -m ai.producer        backfill --start 20261113 --end 20261113
```

單獨跑 `ai.producer` 時照這個順序：新聞線的 crawler、transform 跑完再跑 ai（卡片只看公告、新聞清單抓過沒，整理成 stage 要在它前面）。

- 改了規則或 transform，要從 raw 重算一段期間：`scripts\run_rebuild.bat 20260701`（先跑測試，再跑 transform、analyze，不打 API）。
- 官方事後更正了某天的資料：`main.py backfill --start <那天>`。還差幾次要往回看 30 個交易日，所以會一起重算到今天。
- 注意累計名單只有最新一份，漏跑要在下一個交易日收盤前補。

## crawler：API → raw

| 資料 | API | 一份涵蓋 | raw_key |
|---|---|---|---|
| 開休市日期 | `holidaySchedule` | 一年 | `2026` |
| 每日市場成交 | `afterTrading/FMTQIK` | 一個月 | `202611` |
| 每日收盤行情 | `afterTrading/MI_INDEX`（`type=ALLBUT0999`） | 一天 | `20261120` |
| 注意股 | `announcement/notice` | 一天 | `20261113` |
| 處置股 | `announcement/punish`（查當天～＋14 天） | 一天 | `20261113` |
| 上市公司清單 | OpenAPI `t187ap03_L` | 快照 | 抓取日期 |
| 注意累計名單 | `announcement/notetrans`（只有最新一份） | 一天 | 名單日期 |
| 重大訊息 | 觀測站 `t05st01` | 一家一個月 | `1303_202611` |
| 重大訊息全文 | 觀測站 `t05st01_detail`（DR 是 `t59sb01_detail`） | 一則公告 | `9908_20261102_1` |
| 個股新聞 | FinMind `TaiwanStockNews` | 一檔一天（UTC） | `2330_20261120` |
| 新聞網頁 | 每則新聞的連結 | 一則新聞 | news_id＝連結的 md5 |

- 重大訊息、新聞查被關注的上市公司：`stage_watched_stock`（往前 30 個交易日被列注意過的上市公司，和卡片牆同一批）；那天還沒有名單（股價線還沒跑、週末）用之前最近一個交易日的。重大訊息查當月和上個月，新聞查那天和往前 7 天（`NEWS_LOOKBACK_DAYS`）。
- 新聞網頁不存全文（著作權），只存 `<head>`（標題、導言）、轉址後的網址、全文字數。論壇貼文不抓；被擋、轉址這類永久失敗記一列，之後不再抓。
- raw 只新增不修改，成功、失敗都留；讀的時候只拿最新一份成功的。
- 已經抓完整的不再抓（加 `--refetch` 全部重抓）。「完整」＝資料不會再變之後抓的：

| 資料 | 什麼時候算完整 |
|---|---|
| 注意股 | 回應裡有那天的日期 |
| 處置股 | 「資訊更新日期」在那天或之後 |
| 收盤行情 | 有個股的表 |
| 注意累計名單 | 名單日期是這一輪的那天（只有最新一份，休市那天照存前一天的） |
| 一個月的資料（市場成交） | 那個月過完以後抓的 |
| FinMind 新聞清單 | 隔天 08:00 以後抓的（FinMind 的一天是 UTC） |

- 還不完整的重大訊息清單（當月）、新聞清單，30 分鐘內剛抓過的也先不抓（`REFETCH_GAP`）：重跑、手動多跑幾次也不會一直打觀測站、FinMind。但在今天的注意股抓到以前抓的清單算舊的，剛抓過也重抓：股價線跑完接著跑的新聞線照樣重抓 17:00 那次抓的清單，當天的卡片不會漏掉傍晚的公告、新聞。

- 看得到那天的資料就算公布了，不管幾點。注意股和處置股一起公布：處置股公布了、注意股是空的，就是那天真的沒人被列。
- 還沒公布就抓的存成失敗，下一輪再抓（空名單不是「沒人被列」）。看不到資料時，先看日曆、再看收盤行情，判斷那天是不是交易日：

| 那天 | 怎麼判斷 | 怎麼做 |
|---|---|---|
| 休市 | 開休市日期寫休市，或週末而且沒寫補行交易 | 沒有資料是正常的，馬上算數 |
| 交易日 | 日曆上有交易，收盤行情也有個股資料 | 一定要等到資料：存成失敗、下一輪再抓；22:00 還沒有就看得到失敗 |
| 不知道（例如颱風假） | 日曆上有交易，但收盤行情一直沒有 | 存成失敗，不用時間猜。analyze 不需要那天（市場成交會標成沒交易），資料不會錯；之後的市場成交出來，就自動確認是休市 |
- 暫時性的失敗（逾時、5xx、429）等 5 秒重試，最多 3 次；內容不對、被擋不重試。

## transform：raw → stage

- 一個 crawl 任務對應一個 transform 任務，例如 `crawl_notice` → `transform_notice`；只讀成功的 raw，任務之間互不依賴。
- 注意股、款次：那天先清掉再寫入（官方拿掉的列也會消失）。其他：用主鍵更新，這批沒有的列不動。
- 重大訊息條款對照表（第 1～51 款）是固定資料，寫在 `schema/stage.sql`。
- 新聞清單只整理和 crawler 同一個範圍的（每天被關注的上市公司，那天和往前 7 天）。一次性的回補（例如全部上市公司的新聞）自己整理，每天的資料線不跟著重做。

## 去重與冪等

同一段期間重跑幾次、分到好幾個 worker 同時跑，結果都一樣：

| 層 | 怎麼寫 |
|---|---|
| raw | 只新增，每抓一次一列（主鍵含 `fetched_at`） |
| stage | `INSERT … ON CONFLICT (主鍵) DO UPDATE`，只讓比較新的抓取（`source_fetched_at`）覆蓋；同一批主鍵重複的先只留最新一列 |
| app | 同一個交易裡先刪那天、再寫入 |
| ai | 同一個 prompt 版本已經有結果的不再問；重算 stage、app 不會刪掉 AI 結果 |

驗證：`uv run pytest -m integration`（另建測試用資料庫，測完刪掉；包含 8 個執行緒同時寫同一筆，最後一定是最新的）。

## ai：stage → ai

| 項目 | 任務 | 一次處理 | 寫入 |
|---|---|---|---|
| 公告分類 | `classify_announcement` | 一則公告 | `ai_announcement_label` |
| 新聞摘要 | `summarize_news` | 一則新聞（被關注的股票、全文至少 200 字） | `ai_news_summary` |
| 新聞 RAG | `embed_news` | 一天的新聞 | `ai_news_embedding` |
| 同一件事 | `group_news_stories` | 整段區間 | `ai_news_story` |
| 卡片 AI 摘要 | `summarize_card` | 一檔一天（卡片牆上的每一張：`stage_watched_stock` 的上市公司；那天的注意股、處置股、收盤行情整理好、這檔的公告和新聞清單抓到了才寫，往前 7 天補沒寫成的；寫完以後又出現新的公告（例如傍晚才發），下一輪重寫） | `ai_card_summary` |

同一筆、同一個 prompt 版本呼叫 Claude 失敗 3 次（例如拒答、回答被截斷）就不再問（`repository.MAX_FAILED_CALLS`，看 `ai_llm_call` 的失敗紀錄）：新聞線一天跑 6 次，不擋的話會一直重問、一直花錢。換 prompt 版本重新算。新聞摘要的網頁打不開、全文太短（不呼叫 Claude）：在 `ai_news_summary` 記一列「跳過」（`model`＝`skipped`、摘要空的、`summary_ok` 為否，網站不顯示），1 天後才再試（一天最多一次，新聞超過 7 天就不在範圍裡），不會一直重抓同一個被擋的網頁；5xx、429、逾時是暫時的，照樣重試。

## 常用指令

```
uv run python main.py                                   # 股價線：今天
uv run python main.py news                              # 新聞線：今天
uv run python main.py backfill --start 20260701         # 兩條線跑一段期間（可加 --end；--refetch 全部重抓）
uv run python -m crawler.producer daily --part twse|news  # 只跑一步（或 backfill --start ... --part …；crawler 一定要給 --part，transform 不給就兩個都做，analyze、ai 沒有）
uv run python -m analyze.backtest                       # 回測
uv run pytest                                           # 測試（連不到 PostgreSQL 的整合測試自動跳過）
uv run python scripts/check_health.py                   # 檢查資料線、網站、備份有沒有問題（排程每天 22:30 跑，有問題跳 Windows 通知）
uv run python scripts/ai_report.py                      # AI 最近 7 天：每個版本問幾筆、重問幾成、失敗幾次、花多少、幾成沒通過檢查（可加 --days 30）
uv run python scripts/check_backup.py                   # 備份還原到另一個資料庫，確認還原得回來（做完刪掉）
```

改了 `schema/` 的欄位：程式不會刪表重建；已經存在的表要加欄位，在 schema 裡寫 `ALTER TABLE … ADD COLUMN IF NOT EXISTS`（例如 `schema/ai.sql` 的 `ai_card_summary.announcement_count`），其他改法要自己下 `ALTER TABLE`。

push 到 GitHub 時，CI（`.github/workflows/ci.yml`）自動跑 ruff 和全部測試（另外開一個用完就丟的 PostgreSQL 跑整合測試），結果在 GitHub 的 Actions 頁。

## 備份

`scripts/backup_db.py`：pg_dump 整個資料庫 → `data/backup/tw_stock_<日期>_<時間>.sql.gz`，留最近 14 份（不鎖表；`.env` 可設 `BACKUP_DIR`）。

確認備份還原得回來（每個月一次，或改了備份的方式以後）：`uv run python scripts/check_backup.py`。把最新一份還原到另一個資料庫 `tw_stock_restore_check`（備份檔裡的 DROP／CREATE DATABASE、`\connect` 拿掉，正式的資料庫不碰），每張表數列數、和正式的比，做完刪掉。

真的要還原（會蓋掉整個 tw_stock）：

```bash
gzip -dc data/backup/tw_stock_20261114_233000.sql.gz | wsl docker exec -i postgres psql -U postgres -d postgres
```

## 排程（Windows 工作排程器）

| 工作 | 時間 | 執行 | log |
|---|---|---|---|
| `G2-attention_stock_tracker-daily` | 週一～五 18:00～22:00 每小時（股價線，當天跑完就跳過） | `scripts\run_daily.bat` | `data/logs/daily-YYYY-MM-DD.log` |
| `G2-attention_stock_tracker-news` | 每天 08:00、11:00、14:00、17:00、20:00、23:00（新聞線） | `scripts\run_news.bat` | `data/logs/news-YYYY-MM-DD.log` |
| `G2-attention_stock_tracker-backup` | 週一～五 23:30 | `scripts\run_backup.bat` | `data/backup.log` |
| `G2-attention_stock_tracker-check` | 每天 22:30（股價線最後一次 22:00 之後） | `scripts\run_check.bat` | `data/logs/check-YYYY-MM-DD.log` |

- 背景執行（`conhost.exe --headless`）；錯過時開機後補跑；關機或睡眠時不會跑。log 留 30 天，永久的紀錄是 `tw_stock.job_run`。
- 每天的檢查（`scripts/check_health.py`）看四件事：今天是交易日，股價線就要有今天的記號（還在跑的先放過，隔天看上一個交易日的記號補報）；最近一次新聞線成功、照排程應該跑完的那一次有跑；資料 API 的 `/health`、網頁打得開；最新的備份在 3.5 天內。有問題就跳 Windows 通知（要登入著才看得到），結束代碼 1：工作排程器的「上次執行結果」也看得到。四個 .bat 都把結束代碼傳出去。
- `Start-ScheduledTask <工作名稱>` 手動跑一次；`Get-ScheduledTaskInfo <工作名稱>` 看結果。

重灌時重建（backup 換檔名、時間 23:30，不用每小時那一行，加 `-ExecutionTimeLimit (New-TimeSpan -Hours 1)`）：上一次還在跑時，下一次不會再開一份（工作排程器預設）。

```powershell
$action   = New-ScheduledTaskAction -Execute conhost.exe -Argument "--headless cmd.exe /c `"$(Resolve-Path scripts\run_daily.bat)`""
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 18:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 18:00 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 4 -Minutes 1)).Repetition   # 每小時，到 22:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName G2-attention_stock_tracker-daily -Action $action -Trigger $trigger -Settings $settings
```

新聞線（每天，08:00 起每 3 小時，到 23:00）：

```powershell
$action   = New-ScheduledTaskAction -Execute conhost.exe -Argument "--headless cmd.exe /c `"$(Resolve-Path scripts\run_news.bat)`""
$trigger  = New-ScheduledTaskTrigger -Daily -At 08:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 08:00 -RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Hours 15 -Minutes 1)).Repetition   # 每 3 小時，到 23:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName G2-attention_stock_tracker-news -Action $action -Trigger $trigger -Settings $settings
```

每天的檢查（每天 22:30）：

```powershell
$action   = New-ScheduledTaskAction -Execute conhost.exe -Argument "--headless cmd.exe /c `"$(Resolve-Path scripts\run_check.bat)`""
$trigger  = New-ScheduledTaskTrigger -Daily -At 22:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName G2-attention_stock_tracker-check -Action $action -Trigger $trigger -Settings $settings
```
