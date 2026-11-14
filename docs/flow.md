# 執行流程

```
crawler    API        → raw_* 表     抓資料，存原文
transform  raw_* 表   → stage_* 表   整理成表
analyze    stage_* 表 → app_* 表     規則引擎算進度
```

## 每天怎麼跑

排程每天 21:00 執行 `main.py`，依序做三件事。以注意股為例。表只有一張，前一步寫入、下一步讀取；讀寫都透過函式：

| 任務 | 讀 | 寫 |
|---|---|---|
| `crawl_notice` | API（`fetch_notice`） | raw 表（`upsert_raw`） |
| `transform_notice` | raw 表（`read_raw`） | stage 表（`upsert_stage`） |
| `analyze_progress` | stage 表（`read_stage`） | app 表（`upsert_app`） |

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
| 注意股 | `announcement/notice` | 一天 | `20261113` |
| 處置股 | `announcement/punish`（查當天～＋14 天） | 一天 | `20261113` |
| 上市公司清單 | OpenAPI `t187ap03_L` | 快照 | 抓取日期 |
| 注意累計名單 | `announcement/notetrans`（只有最新一份） | 一天 | 名單日期 |
| 重大訊息 | 觀測站 `t05st01`（POST） | 一家一個月 | `1303_202611` |

- 先抓證交所，再查重大訊息：當天被關注的上市公司，查當月和上個月。
- 開休市日期一年只抓一次（年初公布整年）；颱風假不在裡面，是從市場成交看出來的。要重抓：`uv run python -c "from crawler import task; print(task.crawl_holiday(2026))"`。
- 單一任務失敗不中斷，最後列出失敗清單。
- 同一個 raw_key 重抓：成功覆蓋；失敗不蓋掉成功的（`ok`＝0、`problem` 寫原因）。

## transform：raw → stage

- 一個任務整理一份 raw，跟 crawler 的任務一一對應，例如 `crawl_notice(date_yyyymmdd)` → `transform_notice(date_yyyymmdd)`。
- 只讀 ok 的 raw，沒有就跳過；用主鍵更新 stage 表，其他日期不動。任務之間互不依賴。
- 注意股、款次：當天先清掉再寫入。上市公司：依 company_id 更新，下市的留著。

## analyze：stage → app

- 算之前先檢查 `stage_trading_day`、`stage_notice_day`、`stage_punish_day` 那天都有一列（每天一列；沒人被列也有，0 檔）。缺了就不算。
  重大訊息、公司清單等只給卡片顯示，缺了不影響進度。
- 讀 stage（往前 30 個營業日），算每檔每天離處置還差幾次，規則見 [`rules.md`](rules.md)。
- 每個交易日每檔一列；重跑只換掉那天，`process_date` 記哪天算的。
- 非營業日沒有東西要算。

## 常用指令

```
uv run python main.py                                   # 三步一次跑完（今天）
uv run python main.py backfill --start 20260701             # 三步一次跑完（一段期間，可加 --end）
uv run python -m crawler.producer [backfill --start ...]    # 只跑一步，transform、analyze 同樣
uv run python -m analyze.backtest                       # 回測
uv run pytest                                           # 測試
```

漏跑：`main.py backfill --start <漏跑那天>`。注意累計名單只有最新一份，要在下一個交易日收盤前補。

改了 `schema/` 的欄位：程式不會刪表重建，要自己對資料庫下 `ALTER TABLE`。

## 備份

`scripts/backup_db.py`：mysqldump 整個資料庫 → `data/backup/tw_stock_<日期>_<時間>.sql.gz`，留最近 14 份。

- 不鎖表，跟每日排程同時跑也沒關係；密碼不出現在命令列。
- `.env` 可設 `BACKUP_DIR`（存到別顆硬碟）、`MYSQLDUMP`（mysqldump 路徑）。

還原（會蓋掉整個 tw_stock）：

```bash
gzip -dc data/backup/tw_stock_20261114_2330.sql.gz | mysql -u root -p
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
