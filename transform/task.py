"""task 層（轉換）：一個任務整理一份 raw → stage，跟 crawler/task.py 的任務一一對應（課程補充 J）。

    crawl_notice(date_yyyymmdd)                  → transform_notice(date_yyyymmdd)
    crawl_punish(date_yyyymmdd)                  → transform_punish(date_yyyymmdd)
    crawl_market_daily(month_yyyymm)              → transform_market_daily(month_yyyymm)
    crawl_holiday(year)                     → transform_holiday(year)
    crawl_announcements(stock_id, y, m)     → transform_announcements(stock_id, y, m)
    crawl_announcement_detail(stock_id, enter_yyyymmdd, serial_number)
                                            → transform_announcement_detail(stock_id, enter_yyyymmdd, serial_number)
    crawl_companies()                       → transform_companies(date_yyyymmdd)         讀那天以前最新的一份
    crawl_punish_watchlist()                → transform_punish_watchlist(date_yyyymmdd)  讀那天以前最新的一份

每個任務：只讀 ok 的 raw（沒有就跳過）、一個交易寫進 stage（失敗就不變）、重跑不會重複。
任務之間互不依賴，一個任務失敗不影響其他任務。
"""
import pandas as pd

from common import repository
from common.constant import STAGE_KEYS
from transform import transformer


def upsert_stage(tables: dict[str, pd.DataFrame], clear_day=None) -> int:
    """用主鍵更新 stage 表（新的就新增），全部放在同一個交易裡。回傳寫入的列數。

    clear_day：先把這天（trade_date）的列清掉再寫入，官方拿掉的列也會跟著消失（注意股用）。
    """
    with repository.get_engine().begin() as conn:
        for name, df in tables.items():
            if clear_day is not None:
                repository.delete_where_in(conn, name, "trade_date", [clear_day])
            repository.upsert_rows(conn, name, df, STAGE_KEYS[name])   # 空的就不動
    return sum(len(df) for df in tables.values())


def latest_key(source: str, date_yyyymmdd: str) -> str | None:
    """那天（含）以前最新的一份 raw_key。只有最新一份的來源用（週末拿到的是週五的）。"""
    keys = [k for k in repository.list_raw_keys(source) if k <= date_yyyymmdd]
    return keys[-1] if keys else None


def skipped(source: str, key) -> str:
    return f"raw_{source}/{key} 沒有成功的資料，跳過"


# ==================== 一天一份 ====================
def transform_notice(date_yyyymmdd: str) -> str:
    """注意股 → stage_notice、stage_notice_clause、stage_notice_day（那天先清掉再寫入）。"""
    raw = repository.read_raw("notice", date_yyyymmdd)
    if raw is None:
        return skipped("notice", date_yyyymmdd)
    day = pd.Timestamp(date_yyyymmdd).date()
    notices, clauses = transformer.clean_notice(transformer.notice_to_frame([raw]))
    notice_day = pd.DataFrame({"trade_date": [day], "notice_count": [len(notices)]})   # 0 檔也寫：表示抓到了、沒人被列
    rows = upsert_stage({"stage_notice": notices, "stage_notice_clause": clauses, "stage_notice_day": notice_day},
                        clear_day=day)
    return f"stage_notice {date_yyyymmdd}：{rows} 列"


def transform_punish(date_yyyymmdd: str) -> str:
    """處置股 → stage_punish（同一則公告每天都查得到，用主鍵更新）、stage_punish_day（這天查過了）。"""
    raw = repository.read_raw("punish", date_yyyymmdd)
    if raw is None:
        return skipped("punish", date_yyyymmdd)
    punishes = transformer.clean_punish(transformer.punish_to_frame([raw]))
    punish_day = pd.DataFrame({"query_date": [pd.Timestamp(date_yyyymmdd).date()], "punish_count": [len(punishes)]})
    rows = upsert_stage({"stage_punish": punishes, "stage_punish_day": punish_day})
    return f"stage_punish {date_yyyymmdd}：{rows} 列"


# ==================== 一份涵蓋一段期間 ====================
def notice_days(month_yyyymm: str) -> set:
    """這個月有人被列注意的日子（讀 raw_notice，不靠 stage，跟 transform_notice 誰先跑都沒關係）。"""
    keys = [k for k in repository.list_raw_keys("notice") if k.startswith(month_yyyymm)]
    return {pd.Timestamp(raw["meta"]["key"]).date() for raw in repository.read_raws("notice", keys)
            if isinstance(raw["body"], dict) and raw["body"].get("data")}


def transform_market_daily(month_yyyymm: str) -> str:
    """每日市場成交（一個月）→ stage_trading_day：涵蓋到的每一天一列，標是不是交易日。"""
    raw = repository.read_raw("market_daily", month_yyyymm)
    if raw is None:
        return skipped("market_daily", month_yyyymm)
    through = transformer.covered_through(raw["meta"]["fetched_at"])   # 這份涵蓋到哪天（看抓取時間）
    days = transformer.clean_trading_day(transformer.market_daily_to_frame([raw]), month_yyyymm, through,
                                         notice_days(month_yyyymm))
    rows = upsert_stage({"stage_trading_day": days})
    return f"stage_trading_day {month_yyyymm}：{rows} 列"


def transform_holiday(year: int) -> str:
    """開休市日期（一年）→ stage_holiday。"""
    raw = repository.read_raw("holiday", str(year))
    if raw is None:
        return skipped("holiday", year)
    rows = upsert_stage({"stage_holiday": transformer.clean_holiday(transformer.holiday_to_frame([raw]))})
    return f"stage_holiday {year}：{rows} 列"


def transform_announcements(stock_id: str, year: int, month: int) -> str:
    """一家公司一個月的重大訊息 → stage_announcement。"""
    key = f"{stock_id}_{year}{month:02d}"
    raw = repository.read_raw("announcements", key)
    if raw is None:
        return skipped("announcements", key)
    rows = upsert_stage({"stage_announcement": transformer.clean_announcement(transformer.announcement_to_frame([raw]))})
    return f"stage_announcement {key}：{rows} 列"


def transform_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int) -> str:
    """一則重大訊息全文 → stage_announcement_detail。"""
    key = f"{stock_id}_{enter_yyyymmdd}_{serial_number}"
    raw = repository.read_raw("announcement_detail", key)
    if raw is None:
        return skipped("announcement_detail", key)
    df = transformer.clean_announcement_detail(transformer.announcement_detail_to_frame([raw]))
    rows = upsert_stage({"stage_announcement_detail": df})
    return f"stage_announcement_detail {key}：{rows} 列"


# ==================== 只有最新一份 ====================
def transform_companies(date_yyyymmdd: str) -> str:
    """那天以前最新的上市公司清單 → stage_company（依 company_id 更新，下市的留著）。"""
    key = latest_key("companies", date_yyyymmdd)
    if key is None:
        return skipped("companies", f"<={date_yyyymmdd}")
    rows = upsert_stage({"stage_company": transformer.clean_company(repository.read_raw("companies", key))})
    return f"stage_company {key}：{rows} 列"


def transform_punish_watchlist(date_yyyymmdd: str) -> str:
    """那天以前最新的注意累計名單 → stage_punish_watchlist。"""
    key = latest_key("punish_watchlist", date_yyyymmdd)
    if key is None:
        return skipped("punish_watchlist", f"<={date_yyyymmdd}")
    raw = repository.read_raw("punish_watchlist", key)
    rows = upsert_stage({"stage_punish_watchlist": transformer.clean_punish_watchlist(transformer.punish_watchlist_to_frame([raw]))})
    return f"stage_punish_watchlist {key}：{rows} 列"
