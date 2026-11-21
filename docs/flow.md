# 執行流程

```
crawler    API        → raw_* 表     抓資料，存原文
transform  raw_* 表   → stage_* 表   整理成表
analyze    stage_* 表 → app_* 表     規則引擎算進度
```

## 每天怎麼跑

排程每天 21:00 執行 `main.py`，依序做三件事（期間在 `main.py` 算一次再傳給三步，跑過午夜也是同一段）。以注意股為例。表只有一張，前一步寫入、下一步讀取；讀寫都透過函式：

| 任務 | 讀 | 寫 |
|---|---|---|
| `crawl_notice` | API（`fetch_notice`） | raw 表（`save_raw`） |
| `transform_notice` | raw 表（`read_raw`） | stage 表（`upsert_stage`） |
| `analyze_progress` | stage 表（`read_stage`） | app 表（`replace_app`） |

**1. crawler：抓資料**

打證交所、觀測站的 API，每份回應原封不動寫入 raw_* 表（例：`crawl_notice` 寫入 `raw_notice`）。

- 某一份抓失敗（網路不穩、被擋）：記下來，繼續抓下一份，最後列出失敗清單。

**2. transform：整理資料**

讀取 raw_* 表，整理後寫入 stage_* 表。每個 crawl 任務都有一個對應的 transform 任務（例：`transform_notice` 讀取 `raw_notice`，寫入 `stage_notice`）。

- 某份 raw 沒抓到：那個任務跳過，stage 表維持原本的資料，其他任務照做。
- 整理時程式出錯（例如證交所改了欄位，解析失敗）：這是程式要修的問題，第 3 步不跑。

**3. analyze：算離處置還差幾次**

讀取三張 stage 表（交易日、注意款次、處置公告），算每檔股票離處置還差幾步，寫入 app_* 表。

- 開始算之前，先確認三張 stage 表今天都有一列：`stage_trading_day`（交易日）、`stage_notice_day`（注意股）、`stage_punish_day`（處置股）。少任何一張，算出來的進度會是錯的，所以今天不算。
- 其他資料（重大訊息、公司清單等）只用在卡片顯示，少了不影響進度。

**有失敗時怎麼查、怎麼補**

1. 看昨晚有沒有成功：`Get-ScheduledTaskInfo G2-attention_stock_tracker-daily`，`LastTaskResult` 是 0 就是成功，1 就是有失敗。
2. 打開 `data/daily.log`，看最後一次執行裡標 `✗` 的那幾行：

| log 裡看到 | 意思 | 怎麼做 |
|---|---|---|
| `crawl_notice(date_yyyymmdd='20261113') ✗ 連線逾時` | 那一份沒抓到 | 補跑那天 |
| `transform 有任務失敗，不跑 analyze` | 整理時程式出錯 | 看上面 `✗` 那行的錯誤，修好程式再補跑 |
| `缺少必要的資料，不算進度：[...]` | 算進度需要的資料那天還沒有 | 補跑那天 |

3. 補跑 11/13：`uv run python main.py backfill --start 20261113 --end 20261113`（不加 `--end` 就從 11/13 補到今天）。只更新那幾天的資料，重跑也不會重複。

   也可以一步一步跑，從出錯的那一步開始，後面的步驟都要跟著跑：

   ```
   uv run python -m crawler.producer   backfill --start 20261113 --end 20261113   # 1. 抓資料（打 API）
   uv run python -m transform.producer backfill --start 20261113 --end 20261113   # 2. 整理資料
   uv run python -m analyze.producer   backfill --start 20261113 --end 20261113   # 3. 算離處置還差幾次
   ```

   例如 raw 都有抓到、只是 transform 出錯：修好程式後跑 2、3，不用重打 API。

   改了規則或 transform 之後要從 raw 重算一段期間：`scripts\run_rebuild.bat 20260701`（先跑測試，沒過就不動資料；再跑 2、3，不打 API；log 在 `data/rebuild.log`）。

## crawler：API → raw

| 資料 | API | 一份涵蓋 | raw_key |
|---|---|---|---|
| 開休市日期 | `holidaySchedule`（用 `date=20260101`） | 一年 | `2026` |
| 每日市場成交 | `afterTrading/FMTQIK` | 一個月 | `202611` |
| 每日收盤行情 | `afterTrading/MI_INDEX`（`type=ALLBUT0999`） | 一天 | `20261120` |
| 注意股 | `announcement/notice` | 一天 | `20261113` |
| 處置股 | `announcement/punish`（查當天～＋14 天） | 一天 | `20261113` |
| 上市公司清單 | OpenAPI `t187ap03_L` | 快照 | 抓取日期 |
| 注意累計名單 | `announcement/notetrans`（只有最新一份） | 一天 | 名單日期 |
| 重大訊息 | 觀測站 `t05st01`（POST） | 一家一個月 | `1303_202611` |
| 重大訊息全文 | 觀測站 `t05st01_detail`（POST；存託憑證 DR 是 `t59sb01_detail`，照清單上寫的打） | 一則公告 | `9908_20261102_1`（公司_輸入日期_序號） |

- 先抓證交所，再查重大訊息：當天被關注的上市公司，查當月和上個月。
- 最後查全文：重大訊息清單裡的每則公告查一次；全文不會變，抓過的不再抓。
- 開休市日期一年一份，抓過就不再抓；期間到了 11 月以後也抓下一年（證交所 11 月起公布）。颱風假不在裡面，是從市場成交看出來的。要重抓：`uv run python -c "from crawler import task; print(task.crawl_holiday(2026))"`。
- 單一任務失敗不中斷，最後列出失敗清單。暫時性的失敗（逾時、連線失敗、HTTP 5xx、429）先等 5 秒重試，最多 3 次（`.env` 的 `MAX_RETRIES`、`RETRY_DELAY_SECONDS`）；內容不對、被擋不重試。
- raw 只新增、不修改：同一個 raw_key 每抓一次多一列，成功、失敗都留（失敗的 `ok`＝0、`problem` 寫原因）；讀的時候只拿最新一份成功的，所以失敗不會蓋掉之前成功的。
- 已經抓完整的不再抓（daily、backfill 都是）；加 `--refetch` 就全部重抓。「完整」＝在資料不會再變之後抓的：
  - 注意股、處置股、收盤行情：那天 20:00 以後抓的（實測收盤後 17:30～17:45 才公布，留點緩衝）。20:00 以前抓的存成失敗（`還沒公布完整`）：那時回傳的空名單不是「沒人被列」。
  - 市場成交：那個月最後一天 20:00 以後抓的；重大訊息清單：那個月過完以後抓的。當月的照樣抓。
  - 開休市日期、重大訊息全文：抓過就算。注意累計名單、上市公司清單只有當下一份，每次都抓。

## transform：raw → stage

- 一個任務整理一份 raw，跟 crawler 的任務一一對應，例如 `crawl_notice(date_yyyymmdd)` → `transform_notice(date_yyyymmdd)`。
- 只讀 ok 的 raw，沒有就跳過；用主鍵更新 stage 表，其他日期不動。任務之間互不依賴。
- 注意股、款次：當天先清掉再寫入。上市公司：依 company_id 更新，下市的留著。
- 重大訊息全文：一則一個任務 `transform_announcement_detail(stock_id, enter_yyyymmdd, serial_number)`，整理輸入日期在當月、上個月和再前一個月的（輸入日期可能比發言日期早，例如 5/29 輸入、6/1 發言）。
- 重大訊息條款對照表（`stage_announcement_clause`，第 1～51 款的名稱）是固定資料，不用爬：寫在 `schema/stage.sql`，transform 開始建表時一併寫入。條文修正時改那裡。

## 去重與冪等

同一段期間重跑幾次，結果都一樣（冪等）；分到多個 worker 同時跑、不照順序，結果也一樣。

| 層 | 去重 | 重跑 |
|---|---|---|
| raw | 不去重：只新增，每抓一次一列（保留證據） | 讀的時候只拿每個 raw_key 最新一份成功的；抓完整的不再抓 |
| stage | 主鍵：同一批裡主鍵重複的列，寫入前只留一列（版本最新的，一樣新留後面的） | 用主鍵更新（整批 `INSERT … ON CONFLICT DO UPDATE`），只讓比較新的覆蓋；注意股先清掉那天，官方拿掉的列也會消失 |
| app | 主鍵（trade_date, stock_id） | 同一個交易裡先刪那天、再寫入 |

stage 的寫法是 `ON CONFLICT (主鍵) DO UPDATE`（upsert），用 `to_sql(method=...)` 讓 pandas 每 1000 列組成一句送出，不是一列一句。
兩種寫入方式依資料分：
- 累積的事實（處置公告、公司、重大訊息、休市日、交易日）：用主鍵更新，這批沒有的列不動。
- 某個範圍的完整名單（某天的注意股、某天的 app）：先清掉那個範圍、再寫入。upsert 不會刪掉這批沒有的列，官方拿掉的、滑出 30 日的要靠先清掉。

同一筆只能有一列、重複時留哪一筆，都由資料庫的主鍵決定：
- 同一批裡主鍵相同的列，寫入前先只留一列（`repository.upsert_rows`）：版本最新的，一樣新就留最後一列（`keep="last"`）。
  PostgreSQL 一句 INSERT 不能改同一列兩次，所以要先去重。transformer 排序用 `kind="stable"`，主鍵相同的列保持原本的先後。
- 處置股「同一份 raw 裡同一檔同一天有兩則不同的處置」只檢查、發出警告，不刪資料（主鍵只能留一則，要人確認）。

raw 是有主鍵又只新增：主鍵含 `fetched_at`（精確到微秒），每次抓都是新的一列，不會撞主鍵。

多個 worker、好幾輪同時跑，分兩層處理：

1. **同一時間只跑一輪**（`main.py`）：開始前鎖住 `data/pipeline.lock` 這個檔案（作業系統的檔案鎖，不鎖資料庫，資料表照常讀寫）。
   上一輪（例如還沒跑完的回補）還鎖著就排隊等，最多等 `PIPELINE_LOCK_WAIT_SECONDS`（預設 2 小時），等太久就放棄、結束代碼 1。
   程式跑完、當掉或被關掉，作業系統都會自動放掉鎖。只管得到同一台電腦。
   鎖只管「輪」，一輪裡面的任務照樣可以分到好幾個 worker。直接跑單一步驟（`python -m transform.producer`）不經過鎖。
   接 Airflow 後改用 DAG 的 `max_active_runs=1`（多台機器也管得到），回補也用同一條 DAG 跑。
2. **就算真的重疊，也是新的贏**（`transform/task.py` 的 `upsert_stage`）：每張 stage 表都有 `source_fetched_at`
   （這列來自哪次抓取，raw 的 `fetched_at`），寫入時只讓比較新（或一樣新）的覆蓋：
   - 寫法：`ON CONFLICT (主鍵) DO UPDATE SET 欄位 = EXCLUDED.欄位 … WHERE 資料庫裡是 NULL OR 新版本 >= 舊版本`。
     比較在資料庫裡、同一句完成，不會兩邊都以為自己比較新。
   - 用 `>=`：一樣新也更新，手動改壞的資料重跑會被修回來。
   - 注意股「先清掉那天再寫入」：清之前先比那天現有的版本（子查詢 `SELECT … FOR UPDATE` 鎖住那天，外面再取 `MAX`），資料庫裡比較新就整批不寫，
     免得舊的名單把新的清掉。
   - 兩個交易互相等鎖時，PostgreSQL 會讓其中一個失敗（deadlock，SQLSTATE 40P01）。失敗的交易已經整個復原，`common/runner.py` 把它當成暫時性的失敗，整個任務重做（任務是冪等的），最多 3 次。

每個 transform 任務都是同樣四步（`transform/task.py`）：`key` → `read_raw`（沒有就跳過）→ `tables = {表名: clean_xxx(xxx_to_frame(raw))}` → `save(raw, tables)`。
版本在 `save` 統一加上，transformer 不用管。

驗證：`uv run pytest -m integration`（`tests/test_integration.py`：另建 `<資料庫>_test` 測試用資料庫，測完刪掉）。
跑兩次筆數不變、值會更新、沒有重複、同批重複留最後一列；比較舊的不會蓋掉比較新的（舊的注意股名單也不會清掉新的）；
8 個執行緒同時寫同一則處置公告的不同版本，最後一定是最新的。鎖：`tests/test_main.py`（第二輪會等第一輪放掉鎖才開始）。

## analyze：stage → app

- 每一天算之前先檢查往前 30 個營業日（資料開始那天以後）都有資料（`analyze/task.py` 的 `missing_inputs`）：
  每一天都有 `stage_trading_day`，其中的營業日都有 `stage_notice_day`、`stage_punish_day`（沒人被列也有一列，0 檔）。
  缺一天，那天會被當成「沒被列」，進度就算錯，所以那天不算、列進失敗清單；其他天照算。
  重大訊息、公司清單等只給卡片顯示，缺了不影響進度。
- 讀 stage（往前 30 個營業日），算每檔每天離處置還差幾次，規則見 [`rules.md`](rules.md)。
- 每個交易日每檔一列；重跑只換掉那天，`process_date` 記哪天算的。
- 非營業日沒有東西要算。

## 常用指令

```
uv run python main.py                                   # 三步一次跑完（今天）
uv run python main.py backfill --start 20260701             # 三步一次跑完（一段期間，可加 --end）；抓完整的跳過
uv run python main.py backfill --start 20260701 --refetch   # 同上，但全部重抓（只影響 crawler）
uv run python -m crawler.producer [backfill --start ...]    # 只跑一步，transform、analyze 同樣
uv run python -m analyze.backtest                       # 回測
uv run pytest                                           # 測試（含整合測試；連不到 PostgreSQL 會自動跳過）
uv run pytest -m "not integration"                      # 只跑不連資料庫的測試
```

漏跑：`main.py backfill --start <漏跑那天>`。注意累計名單只有最新一份，要在下一個交易日收盤前補。

過去的資料有更正（例如官方事後改了注意股、補了處置公告）：`main.py backfill --start <更正的那天>`。每天的排程只算今天，但「還差幾次」要往回看 30 個營業日，所以更正那天之後每一天的 app 都要重算，回補會一起重算到今天。（接 Airflow 時再改成自動從「stage 有變動的最早那天」算起。）

改了 `schema/` 的欄位：程式不會刪表重建，要自己對資料庫下 `ALTER TABLE`。

## 備份

`scripts/backup_db.py`：pg_dump 整個資料庫 → `data/backup/tw_stock_<日期>_<時間>.sql.gz`，留最近 14 份。

- 不鎖表，跟每日排程同時跑也沒關係；密碼不出現在命令列。
- Windows 沒裝 PostgreSQL 時，用 WSL 裡 Docker 容器內建的 pg_dump。
- `.env` 可設 `BACKUP_DIR`（存到別顆硬碟）、`PG_DUMP`（pg_dump 路徑）、`PG_CONTAINER`（容器名稱，預設 postgres）。

還原（會蓋掉整個 tw_stock）：

```bash
gzip -dc data/backup/tw_stock_20261114_233000.sql.gz | wsl docker exec -i postgres psql -U postgres -d postgres
```

## 排程（Windows 工作排程器）

| 工作 | 時間 | 執行 | log |
|---|---|---|---|
| `G2-attention_stock_tracker-daily` | 週一～五 21:00 | `scripts\run_daily.bat` | `data/daily.log` |
| `G2-attention_stock_tracker-backup` | 週一～五 23:30 | `scripts\run_backup.bat` | `data/backup.log` |

- 用 `conhost.exe --headless` 背景執行；錯過時開機後補跑；電腦關機或睡眠時不會跑。
- `Start-ScheduledTask <工作名稱>` 手動跑一次；`Get-ScheduledTaskInfo <工作名稱>` 看結果（0＝成功）。

重灌時重建（backup 把檔名、時間換掉，並加 `-ExecutionTimeLimit (New-TimeSpan -Hours 1)`）：

```powershell
$action   = New-ScheduledTaskAction -Execute conhost.exe -Argument "--headless cmd.exe /c `"$(Resolve-Path scripts\run_daily.bat)`""
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 21:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName G2-attention_stock_tracker-daily -Action $action -Trigger $trigger -Settings $settings
```
