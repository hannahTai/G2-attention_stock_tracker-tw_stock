"""讀資料庫：只讀 app_* 表和 VIEW（docs/tables.md 的「VIEW（給網頁讀）」）。

用 conn.execute(...).mappings() 直接拿到一列一個 dict 交給 FastAPI，不經過 DataFrame：
API 只是把資料原樣轉成 JSON，不需要 pandas；少了 DataFrame，也就沒有 pd.NA、NaN、numpy 數字要換回來。
一律用參數化查詢（:day、:stock_id），不把網址上的值拼進 SQL。
每個函式收 conn：呼叫的人用 `with get_engine().connect() as conn:` 開一次連線，一個請求的查詢共用。
參數順序都一樣：conn → stock_id（有的話）→ 日期；要幾筆一律叫 count。SELECT 一律列出網頁要用的欄位，不寫 *。
"""
import datetime
import decimal

import sqlalchemy


def plain(value):
    """資料庫讀出來的值，換成 JSON 好用的樣子。NULL 本來就是 None；DATE、TIMESTAMP、TIME 是 Python 的
    date、datetime、time，FastAPI 會轉成 '2026-11-20'、'15:26:49'，不用換。只有一種要換：

    - NUMERIC（收盤價）讀出來是 Decimal，轉成 JSON 會變成字串 '101.5000' → 數字

    >>> plain(decimal.Decimal("101.5000")), plain(None), plain(3)
    (101.5, None, 3)
    >>> plain(datetime.date(2026, 11, 20))
    datetime.date(2026, 11, 20)
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


# app_card 給網頁的欄位：app_progress 的欄位（不含 process_date、loaded_at）＋ VIEW 加上的名稱、收盤價、款次、處置中那次、卡片 AI 摘要
CARD_COLUMNS = """
    trade_date, stock_id, count_start, streak3, streak5, in10, in30,
    streak3_left, streak5_left, in10_left, in30_left, steps_left, closest_path, punished_by, has_full_history,
    period_start, period_end, resume_date,
    next_listed_date, next_punish_round, next_period_start, next_period_end, next_resume_date,
    stock_name, is_listed_today, is_company, close_price, price_change, change_pct, reason_text, clauses, is_counted_today,
    measure_type, trigger_condition, match_interval_minutes, announcement_count, ai_summary, ai_summary_news_ids
"""


def _records(conn: sqlalchemy.Connection, sql: str, **params) -> list[dict]:
    """執行 SQL，一列一個 dict（欄位名稱 → 值），每個值都換成 JSON 好用的樣子。"""
    rows = conn.execute(sqlalchemy.text(sql), params).mappings()
    return [{key: plain(value) for key, value in row.items()} for row in rows]


def _dates(conn: sqlalchemy.Connection, sql: str, **params) -> list[datetime.date]:
    """只查一欄日期時，回傳那一欄（API 要拿來比大小、當查詢參數）；NULL 略過（例如表是空的時 MAX() 是 NULL）。"""
    return [value for value in conn.execute(sqlalchemy.text(sql), params).scalars() if value is not None]


def latest_trade_date(conn: sqlalchemy.Connection, on_or_before: datetime.date | None = None) -> datetime.date | None:
    """規則引擎算到的最後一個交易日；給了日期就找那天（含）以前最近的一天。都沒有為 None。"""
    if on_or_before is None:
        days = _dates(conn, "SELECT MAX(trade_date) FROM app_progress")
    else:
        days = _dates(conn, "SELECT MAX(trade_date) FROM app_progress WHERE trade_date <= :day", day=on_or_before)
    return days[0] if days else None


def recent_trade_dates(conn: sqlalchemy.Connection, count: int = 30) -> list[datetime.date]:
    """最近 count 個有算的交易日（新的在前），給日期選單用。"""
    sql = "SELECT DISTINCT trade_date FROM app_progress ORDER BY trade_date DESC LIMIT :count"
    return _dates(conn, sql, count=count)


def cards(conn: sqlalchemy.Connection, day: datetime.date) -> list[dict]:
    """卡片牆：這一天每檔上市公司一列（不放權證、ETF）。"""
    return _records(conn, f"SELECT {CARD_COLUMNS} FROM app_card WHERE trade_date = :day AND is_company", day=day)


def card(conn: sqlalchemy.Connection, stock_id: str, day: datetime.date) -> dict | None:
    """個股卡片上半部；這一天沒有這檔（或不是上市公司，例如權證）為 None。"""
    sql = f"SELECT {CARD_COLUMNS} FROM app_card WHERE stock_id = :stock_id AND trade_date = :day AND is_company"
    rows = _records(conn, sql, stock_id=stock_id, day=day)
    return rows[0] if rows else None


def counted_dates(conn: sqlalchemy.Connection, stock_id: str, day: datetime.date) -> dict[str, list[datetime.date]]:
    """四條路徑各計入了哪幾天：{'streak3': [日期, ...], ...}。"""
    sql = """
        SELECT path, counted_date FROM app_progress_date
        WHERE stock_id = :stock_id AND trade_date = :day
        ORDER BY counted_date
    """
    result: dict[str, list[datetime.date]] = {}
    for row in _records(conn, sql, stock_id=stock_id, day=day):
        result.setdefault(row["path"], []).append(row["counted_date"])
    return result


def trading_days(conn: sqlalchemy.Connection, day: datetime.date, count: int = 30) -> list[datetime.date]:
    """day（含）往前 count 個交易日，舊的在前。"""
    sql = "SELECT trade_date FROM app_trading_day WHERE trade_date <= :day ORDER BY trade_date DESC LIMIT :count"
    return sorted(_dates(conn, sql, day=day, count=count))


def notice_history(conn: sqlalchemy.Connection, stock_id: str, start: datetime.date,
                   end: datetime.date) -> list[dict]:
    """期間內被列注意的日子：每天一列（trade_date、clauses 例如 '1,2'、is_counted、reason_text 原因全文），舊的在前。"""
    sql = """
        SELECT trade_date, clauses, is_counted, reason_text FROM app_notice_history
        WHERE stock_id = :stock_id AND trade_date BETWEEN :start AND :end
        ORDER BY trade_date
    """
    return _records(conn, sql, stock_id=stock_id, start=start, end=end)


def punish_history(conn: sqlalchemy.Connection, stock_id: str, start: datetime.date,
                   end: datetime.date) -> list[dict]:
    """期間內公布、或處置期間和期間重疊的處置公告（新的在前）。"""
    sql = """
        SELECT announce_date, stock_id, stock_name, trigger_condition, period_start, period_end,
               measure_type, match_interval_minutes, content
        FROM app_punish_history
        WHERE stock_id = :stock_id AND announce_date <= :end AND period_end >= :start
        ORDER BY announce_date DESC
    """
    return _records(conn, sql, stock_id=stock_id, start=start, end=end)


def announcements(conn: sqlalchemy.Connection, stock_id: str, start: datetime.date,
                  end: datetime.date) -> list[dict]:
    """期間內的公司重大訊息（舊的在前）。"""
    sql = """
        SELECT stock_id, announce_date, announce_time, company_name, subject, is_during_session,
               is_correction, is_explanation, category, has_news, is_negative, ai_summary,
               announce_clause, clause_name, fact_date, content
        FROM app_announcement
        WHERE stock_id = :stock_id AND announce_date BETWEEN :start AND :end
        ORDER BY announce_date, announce_time
    """
    return _records(conn, sql, stock_id=stock_id, start=start, end=end)


# 新聞列表不放的：標題有這些字的是論壇貼文，不是新聞
HIDDEN_NEWS_TITLE_WORDS = ("股市爆料同學會",)


def stock_news(conn: sqlalchemy.Connection, stock_id: str, start: datetime.date, end: datetime.date) -> list[dict]:
    """期間內這檔的新聞（新的在前）：標題、連結、媒體、日期。同一件事（story_id 一樣）只列最早的那則，
    also_reported_by 是其他報這件事的媒體。論壇貼文（HIDDEN_NEWS_TITLE_WORDS）不列。"""
    sql = """
        WITH news AS (
            SELECT news_id, news_date, news_time, publisher, title, link, story_id
            FROM app_stock_news
            WHERE stock_id = :stock_id AND news_date BETWEEN :start AND :end
              AND NOT title LIKE ANY(:hidden)
        ), firsts AS (
            SELECT DISTINCT ON (story_id) *
            FROM news
            ORDER BY story_id, news_time, news_id
        )
        SELECT f.news_id, f.news_date, f.news_time, f.publisher, f.title, f.link,
               ARRAY(SELECT DISTINCT o.publisher FROM news o
                     WHERE o.story_id = f.story_id AND o.publisher <> f.publisher ORDER BY o.publisher) AS also_reported_by
        FROM firsts f
        ORDER BY f.news_time DESC
    """
    return _records(conn, sql, stock_id=stock_id, start=start, end=end, hidden=[f"%{w}%" for w in HIDDEN_NEWS_TITLE_WORDS])


def news_by_ids(conn: sqlalchemy.Connection, news_ids: list[str]) -> list[dict]:
    """這幾則新聞的標題、連結、媒體、日期（照給的順序）；卡片 AI 摘要第三句的出處用。"""
    if not news_ids:
        return []
    rows = _records(conn, "SELECT DISTINCT ON (news_id) news_id, news_date, publisher, title, link "
                          "FROM app_stock_news WHERE news_id = ANY(:ids) ORDER BY news_id", ids=news_ids)
    by_id = {row["news_id"]: row for row in rows}
    return [by_id[news_id] for news_id in news_ids if news_id in by_id]
