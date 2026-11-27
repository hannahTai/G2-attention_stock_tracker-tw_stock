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
    measure_type, trigger_condition, match_interval_minutes, announcement_count,
    ai_summary, ai_summary_news_ids, ai_summary_at, ai_summary_reused
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


# ---------- 統計頁 ----------
# 都只算上市公司（JOIN app_company，和 app_card.is_company 同一個判斷），只看 day（含）以前的資料

def next_trading_days(conn: sqlalchemy.Connection, day: datetime.date, count: int = 5) -> list[datetime.date]:
    """day 之後的 count 個交易日。已經有的日子照 app_trading_day；還沒發生的推算：週一～五、扣掉休市日（app_holiday），
    和規則引擎推算出關日的方法一樣（analyze/engine.py 的 add_future_days）。颱風這種臨時休市要等實際資料才知道。"""
    sql = """
        WITH known AS (SELECT MAX(trade_date) AS last_day FROM app_trading_day)
        SELECT g.d::date
        FROM generate_series(CAST(:day AS date) + 1, CAST(:day AS date) + 40, INTERVAL '1 day') AS g(d), known k
        WHERE CASE WHEN g.d::date <= k.last_day
                   THEN g.d::date IN (SELECT trade_date FROM app_trading_day)
                   ELSE EXTRACT(ISODOW FROM g.d) < 6 AND g.d::date NOT IN (SELECT holiday_date FROM app_holiday)
              END
        ORDER BY 1
        LIMIT :count
    """
    return _dates(conn, sql, day=day, count=count)


def first_full_history_day(conn: sqlalchemy.Connection) -> datetime.date | None:
    """規則引擎第一個有完整 30 個營業日資料的交易日（has_full_history）；在那之前「差幾次」不準，每日檔數從這天畫起。"""
    days = _dates(conn, "SELECT MIN(trade_date) FROM app_progress WHERE has_full_history")
    return days[0] if days else None


def daily_counts(conn: sqlalchemy.Connection, start: datetime.date, day: datetime.date) -> list[dict]:
    """start～day 每個交易日一列（舊的在前）：listed 被列注意、clause13 其中被列第 13 款（當沖占比過高）、
    one 聽牌（差 1 次）、punish 在處置期間內（含最後一天）。"""
    sql = """
        WITH notice AS (
            SELECT n.trade_date,
                   COUNT(*) AS listed,
                   COUNT(*) FILTER (WHERE '13' = ANY(string_to_array(n.clauses, ','))) AS clause13
            FROM app_notice_history n
            JOIN app_company c ON c.company_id = n.stock_id
            WHERE n.trade_date BETWEEN :start AND :day
            GROUP BY n.trade_date
        ), progress AS (
            SELECT p.trade_date,
                   COUNT(*) FILTER (WHERE p.steps_left = 1) AS one,
                   COUNT(*) FILTER (WHERE p.period_start <= p.trade_date AND p.period_end >= p.trade_date) AS punish
            FROM app_progress p
            JOIN app_company c ON c.company_id = p.stock_id
            WHERE p.trade_date BETWEEN :start AND :day
            GROUP BY p.trade_date
        )
        SELECT p.trade_date, COALESCE(n.listed, 0) AS listed, COALESCE(n.clause13, 0) AS clause13, p.one, p.punish
        FROM progress p
        LEFT JOIN notice n ON n.trade_date = p.trade_date
        ORDER BY p.trade_date
    """
    return _records(conn, sql, start=start, day=day)


def new_punish(conn: sqlalchemy.Connection, start: datetime.date, day: datetime.date, period: str = "week") -> list[dict]:
    """新增處置幾檔：start 以後開始、day（含）以前公布的處置，依處置開始日分組（period＝day 每天、week 每週，
    period_start＝那天或那週的星期一）。最後一組可能含已公布、還沒開始的。沒有處置的日子、週沒有列。"""
    if period not in ("day", "week"):
        raise ValueError(f"period 只能是 day 或 week：{period}")
    sql = f"""
        SELECT date_trunc('{period}', ph.period_start)::date AS period_start, COUNT(*) AS count
        FROM app_punish_history ph
        JOIN app_company c ON c.company_id = ph.stock_id
        WHERE ph.period_start >= :start AND ph.announce_date <= :day
        GROUP BY 1
        ORDER BY 1
    """
    return _records(conn, sql, start=start, day=day)


def punish_lengths(conn: sqlalchemy.Connection, since: datetime.date, day: datetime.date) -> list[dict]:
    """since 起開始、day（含）以前已經結束的處置，依期間幾個交易日分組：[{days, count}]（例如新制 5 天、延長 7 天）。
    期間的天數用 app_trading_day 數；市場成交還沒抓到的月份數不準，所以結束日要在 app_trading_day 已有的日子內。"""
    sql = """
        SELECT (SELECT COUNT(*) FROM app_trading_day d WHERE d.trade_date BETWEEN ph.period_start AND ph.period_end) AS days,
               COUNT(*) AS count
        FROM app_punish_history ph
        JOIN app_company c ON c.company_id = ph.stock_id
        WHERE ph.period_start >= :since AND ph.period_end <= :day
          AND ph.period_end <= (SELECT MAX(trade_date) FROM app_trading_day)
        GROUP BY 1
        ORDER BY 1
    """
    return _records(conn, sql, since=since, day=day)


def trigger_clauses(conn: sqlalchemy.Connection, start: datetime.date, day: datetime.date) -> dict:
    """觸發處置的注意紀錄：規則引擎判定達標（punished_by）、而且那天真的公布了處置的日子，
    達標的那條路徑計入了哪幾天，一檔一天算一筆（達標但沒公布處置的不算）。
    回傳 {total: 幾筆, by_clause: [{clause, count}]}：每款出現在幾筆裡（一筆可能同時列好幾款，加起來會超過 total）。"""
    trigger = """
        SELECT DISTINCT pd.stock_id, pd.counted_date
        FROM app_progress p
        JOIN app_company c ON c.company_id = p.stock_id
        JOIN app_progress_date pd
          ON pd.trade_date = p.trade_date AND pd.stock_id = p.stock_id
         AND pd.path = ANY(string_to_array(p.punished_by, ','))
        WHERE p.punished_by IS NOT NULL AND p.trade_date BETWEEN :start AND :day
          AND EXISTS (SELECT 1 FROM app_punish_history ph WHERE ph.stock_id = p.stock_id AND ph.announce_date = p.trade_date)
    """
    total = conn.execute(sqlalchemy.text(f"SELECT COUNT(*) FROM ({trigger}) t"), {"start": start, "day": day}).scalar_one()
    by_clause = _records(conn, f"""
        SELECT CAST(x.clause AS INT) AS clause, COUNT(*) AS count
        FROM ({trigger}) t
        JOIN app_notice_history n ON n.stock_id = t.stock_id AND n.trade_date = t.counted_date
        CROSS JOIN unnest(string_to_array(n.clauses, ',')) AS x(clause)
        GROUP BY 1
        ORDER BY 1
    """, start=start, day=day)
    return {"total": total, "by_clause": by_clause}


def industry_counts(conn: sqlalchemy.Connection, start: datetime.date, day: datetime.date) -> list[dict]:
    """每個產業一列：companies 現在的上市公司幾家（最新清單上的，下市的不算）、listed 其中 start～day 被列注意過的幾家
    （比例用，分子分母同一群公司）；listed_all 被列注意過的幾家，含之後下市的（組成用，和 state_totals 的 listed_stocks 一致）。"""
    sql = """
        WITH c AS (
            SELECT c.*, EXISTS (
                SELECT 1 FROM app_notice_history n
                WHERE n.stock_id = c.company_id AND n.trade_date BETWEEN :start AND :day
            ) AS was_listed
            FROM app_company c
        )
        SELECT industry_code,
               COUNT(*) FILTER (WHERE is_listed_now) AS companies,
               COUNT(*) FILTER (WHERE is_listed_now AND was_listed) AS listed,
               COUNT(*) FILTER (WHERE was_listed) AS listed_all
        FROM c
        GROUP BY c.industry_code
        ORDER BY c.industry_code
    """
    return _records(conn, sql, start=start, day=day)


def state_totals(conn: sqlalchemy.Connection, start: datetime.date, day: datetime.date) -> dict:
    """start～day 期間內：listed_stocks 幾檔被列注意過、clause13_stocks 其中幾檔列過第 13 款、
    punished_stocks 幾檔被處置過（公布日在期間內）、punishments 共幾次、
    second_stocks 其中幾檔是第二次處置（measure_type＝第二次處置；人工管制撮合不算）、one_stocks 幾檔聽牌過（差 1 次）。
    每日的圖會重複算同一檔（處置 5 天算 5 次），這裡一檔只算一次。"""
    punished = conn.execute(sqlalchemy.text("""
        SELECT COUNT(DISTINCT ph.stock_id) AS punished_stocks,
               COUNT(*) AS punishments,
               COUNT(DISTINCT ph.stock_id) FILTER (WHERE ph.measure_type = '第二次處置') AS second_stocks
        FROM app_punish_history ph
        JOIN app_company c ON c.company_id = ph.stock_id
        WHERE ph.announce_date BETWEEN :start AND :day
    """), {"start": start, "day": day}).mappings().one()
    one = conn.execute(sqlalchemy.text("""
        SELECT COUNT(DISTINCT p.stock_id)
        FROM app_progress p
        JOIN app_company c ON c.company_id = p.stock_id
        WHERE p.steps_left = 1 AND p.trade_date BETWEEN :start AND :day
    """), {"start": start, "day": day}).scalar_one()
    listed = conn.execute(sqlalchemy.text("""
        SELECT COUNT(DISTINCT n.stock_id) AS listed_stocks,
               COUNT(DISTINCT n.stock_id) FILTER (WHERE '13' = ANY(string_to_array(n.clauses, ','))) AS clause13_stocks
        FROM app_notice_history n
        JOIN app_company c ON c.company_id = n.stock_id
        WHERE n.trade_date BETWEEN :start AND :day
    """), {"start": start, "day": day}).mappings().one()
    return {**{k: int(v) for k, v in listed.items()}, **{k: int(v) for k, v in punished.items()}, "one_stocks": int(one)}
