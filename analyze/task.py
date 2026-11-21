"""task 層（分析）：一個任務算一天，讀 stage、寫 app。

  analyze_progress(date_yyyymmdd)：檢查需要的 stage 都在 → 讀 stage（含之前 30 個營業日）→ engine 算進度
                                   → 換掉 app 那天的列

輸入只有日期字串；只讀資料庫裡已有的 stage；結果直接寫回資料庫。要在 transform 做完之後才執行。
analyze_progress 用 @app.task 註冊成 Celery 任務（見 common/worker.py）。
"""
import datetime

import pandas as pd
import sqlalchemy

from analyze import engine
from common import date_utils, repository
from common.date_utils import parse_yyyymmdd
from common.worker import Job, app

APP_TABLES = ["app_progress", "app_progress_date"]   # analyze_progress 寫入的表
WINDOW_TRADING_DAYS = 30                             # 往前看幾個營業日（30 日 12 次）


def query(sql: str, end: datetime.date, date_columns: list[str]) -> pd.DataFrame:
    """執行一句 SQL（:end 換成 end），日期欄位轉成 datetime.date（才能互相比較）。"""
    df = pd.read_sql(sqlalchemy.text(sql), repository.get_engine(), params={"end": end})
    for col in date_columns:
        df[col] = pd.to_datetime(df[col]).dt.date
    return df


def missing_inputs(day: datetime.date) -> list[str]:
    """算 day 需要、但還沒整理好的 stage 資料，例如 ["stage_notice_day/20261111"]；都有就是空的。

    day 往前 30 個營業日（資料開始那天以後）都會用到：
      - 這段期間的每一天都要有 stage_trading_day（才知道哪幾天是營業日）
      - 其中的營業日都要有 stage_notice_day、stage_punish_day（有這列＝那天抓到也整理好了，沒人被列也有一列）
    缺一天，那天會被當成「沒被列」，進度就算錯了，所以缺了就不算。
    """
    with repository.get_engine().connect() as conn:
        def dates(sql: str, **params) -> list:
            return [row[0] for row in conn.execute(sqlalchemy.text(sql), {"day": day, **params})]

        first_day = conn.execute(sqlalchemy.text("SELECT MIN(query_date) FROM stage_notice_day")).scalar()
        if first_day is None or day < first_day:   # 資料還沒開始
            return [f"stage_notice_day/{day:%Y%m%d}"]
        window = dates("SELECT trade_date FROM stage_trading_day WHERE is_trading AND trade_date <= :day "
                       "ORDER BY trade_date DESC LIMIT :n", n=WINDOW_TRADING_DAYS)
        start = max([first_day, *window[-1:]])   # 第 30 個營業日，但不早於資料開始那天
        rows = conn.execute(sqlalchemy.text(
            "SELECT trade_date, is_trading FROM stage_trading_day WHERE trade_date BETWEEN :start AND :day"),
            {"start": start, "day": day}).fetchall()
        notice_days = set(dates("SELECT query_date FROM stage_notice_day WHERE query_date BETWEEN :start AND :day", start=start))
        punish_days = set(dates("SELECT query_date FROM stage_punish_day WHERE query_date BETWEEN :start AND :day", start=start))

    known = {d for d, _ in rows}
    trading = {d for d, is_trading in rows if is_trading}
    calendar = [start + datetime.timedelta(days=n) for n in range((day - start).days + 1)]
    missing = [f"stage_trading_day/{d:%Y%m%d}" for d in calendar if d not in known]
    missing += [f"stage_notice_day/{d:%Y%m%d}" for d in sorted(trading - notice_days)]
    missing += [f"stage_punish_day/{d:%Y%m%d}" for d in sorted(trading - punish_days)]
    return missing


def read_stage(end: datetime.date) -> tuple[list[datetime.date], pd.DataFrame, pd.DataFrame, set[datetime.date]]:
    """讀算 end 那天需要的 stage 資料。"""
    # 1. 到 end 為止的營業日（30 日 12 次要往前看）
    trading_days = query(
        "SELECT trade_date FROM stage_trading_day WHERE is_trading AND trade_date <= :end ORDER BY trade_date",
        end, ["trade_date"])["trade_date"].tolist()

    # 2. 每天誰被列第幾款
    clauses = query(
        "SELECT trade_date, stock_id, clause FROM stage_notice_clause WHERE trade_date <= :end",
        end, ["trade_date"])

    # 3. 處置公告（決定從哪天開始算、處置中幾號出關）
    punishes = query(
        "SELECT announce_date, stock_id, period_start, period_end FROM stage_punish WHERE announce_date <= :end",
        end, ["announce_date", "period_start", "period_end"])

    # 4. end 之後的休市日（推算「若再列注意」的處置日期）；「開始交易日」「最後交易日」有交易，不算休市
    holidays = query(
        "SELECT holiday_date FROM stage_holiday WHERE holiday_date > :end AND holiday_name NOT LIKE '%交易日'",
        end, ["holiday_date"])["holiday_date"]

    return trading_days, clauses, punishes, set(holidays)


def replace_app(progress: pd.DataFrame, dates: pd.DataFrame, day: datetime.date) -> None:
    """先清掉那天的 app 列，再寫入新算的（記上 process_date＝今天）。放在同一個交易裡，失敗就不變。"""
    process_date = date_utils.today()

    with repository.get_engine().begin() as conn:
        for table in APP_TABLES:
            repository.delete_where_in(conn, table, "trade_date", [day])
        repository.insert_rows(conn, "app_progress", progress.assign(process_date=process_date))
        repository.insert_rows(conn, "app_progress_date", dates.assign(process_date=process_date))


@app.task(base=Job)
def analyze_progress(date_yyyymmdd: str) -> str:
    """任務：算那天（YYYYMMDD）每檔離處置還差幾次，寫進 app 表。不是營業日就沒有東西要算。"""
    day = parse_yyyymmdd(date_yyyymmdd)
    missing = missing_inputs(day)                                                                 # 1. 需要的 stage 都在嗎
    if missing:
        raise RuntimeError(f"缺少必要的資料，不算：{', '.join(missing[:5])}{' …' if len(missing) > 5 else ''}")
    trading_days, clauses, punishes, holidays = read_stage(day)                                   # 2. 讀 stage
    progress, dates = engine.compute_progress(trading_days, clauses, punishes, day, holidays)       # 3. 算
    replace_app(progress, dates, day)                                                              # 4. 寫 app
    return f"app {date_yyyymmdd}：{len(progress)} 檔"
