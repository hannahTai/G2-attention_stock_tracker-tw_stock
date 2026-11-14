"""task 層（分析）：一個任務計算一天，跟 crawler/task.py、transform/task.py 同樣的寫法（課程補充 J）。

  analyze_progress(date_yyyymmdd)：讀 stage（含之前 30 個營業日）→ engine 算進度 → 更新 app 表

任務的三個條件：輸入都是參數（日期字串）、只依賴資料庫裡已經有的資料、結果直接寫進資料庫。
要用到前幾天的 stage，所以要在 transform 做完之後才執行。
"""
import datetime

import pandas as pd
import sqlalchemy

from analyze import engine
from common import date_utils, repository

APP_TABLES = ["app_progress", "app_progress_date"]   # analyze_progress 寫入的表


def query(sql: str, end: datetime.date, date_columns: list[str]) -> pd.DataFrame:
    """執行一句 SQL（:end 換成 end），日期欄位轉成 datetime.date（才能互相比較）。"""
    df = pd.read_sql(sqlalchemy.text(sql), repository.get_engine(), params={"end": end})
    for col in date_columns:
        df[col] = pd.to_datetime(df[col]).dt.date
    return df


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


def upsert_app(progress: pd.DataFrame, dates: pd.DataFrame, day: datetime.date) -> None:
    """先清掉那天的 app 列，再寫入新算的（記上 process_date＝今天）。放在同一個交易裡，失敗就不變。"""
    process_date = date_utils.today()
    with repository.get_engine().begin() as conn:
        for table in APP_TABLES:
            conn.execute(sqlalchemy.text(f"DELETE FROM {table} WHERE trade_date = :day"), {"day": day})
        repository.upload_data_to_mysql(conn, "app_progress", progress.assign(process_date=process_date))
        repository.upload_data_to_mysql(conn, "app_progress_date", dates.assign(process_date=process_date))


def analyze_progress(date_yyyymmdd: str) -> str:
    """任務：算那天（YYYYMMDD）每檔離處置還差幾次，寫進 app 表。不是營業日就沒有東西要算。"""
    day = datetime.datetime.strptime(date_yyyymmdd, "%Y%m%d").date()
    trading_days, clauses, punishes, holidays = read_stage(day)                                   # 1. 讀 stage
    progress, dates = engine.compute_progress(trading_days, clauses, punishes, day, holidays)       # 2. 算
    upsert_app(progress, dates, day)                                                              # 3. 寫 app
    return f"app {date_yyyymmdd}：{len(progress)} 檔"
