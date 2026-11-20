"""規則引擎（stage → app，analyze/task.py 的 analyze_progress 呼叫）：依要點第六條，算每檔股票每個營業日離處置還差幾次。不碰資料庫。

規則見 docs/rules.md：
  - 連 3 日（streak3）：連續 3 個營業日被列第 1 款
  - 連 5 日（streak5）：連續 5 個營業日被列第 1～8 款
  - 10 日 6 次（in10）：最近 10 個營業日內有 6 天被列第 1～8 款
  - 30 日 12 次（in30）：最近 30 個營業日內有 12 天被列第 1～8 款
  - 只算營業日；一天被列好幾款只算一天；處置公告的隔一個營業日起重新算

主程式是最下面的 compute_progress()：算一天，每檔股票呼叫一次 one_stock_one_day()，裡面依序做 1～5。
"""
import bisect
import datetime

import pandas as pd

# 規則（要點第六條，見 docs/rules.md）
THRESHOLDS = {"streak3": 3, "streak5": 5, "in10": 6, "in30": 12}   # 每一條的門檻
COUNTED_CLAUSES = {1, 2, 3, 4, 5, 6, 7, 8}                          # 第 1～8 款計入（連 3 日只看第 1 款）

# 處置期間（幾個營業日），見 docs/rules.md「處置期間與措施」
NEW_RULE_DATE = datetime.date(2026, 8, 10)     # 這天起新制
PUNISH_DAYS = {"new": 5, "old": 10}            # 一般
PUNISH_DAYS_EXTENDED = {"new": 7, "old": 12}   # 連 3 日、連 5 日且計算期間內有第 13 款
EXTEND_CLAUSE = 13                             # 當沖占比過高

LEFT_COLUMNS = [f"{path}_left" for path in THRESHOLDS]   # streak3_left、streak5_left、in10_left、in30_left
NEXT_COLUMNS = ["next_listed_date", "next_punish_round", "next_period_start", "next_period_end", "next_resume_date"]
PROGRESS_COLUMNS = ["trade_date", "stock_id", "count_start", *THRESHOLDS, *LEFT_COLUMNS, "steps_left", "closest_path",
                    "punished_by", "has_full_history", "period_start", "period_end", "resume_date", *NEXT_COLUMNS]
DATE_COLUMNS = ["trade_date", "stock_id", "path", "counted_date"]


# ---------- 準備資料 ----------
def listed_days_by_stock(clauses: pd.DataFrame) -> dict[str, dict[datetime.date, set[int]]]:
    """stage_notice_clause → {股票: {日期: {款次, ...}}}，例如 {"2424": {date(2026,10,15): {1, 3}}}。"""
    result = {}
    for row in clauses.itertuples(index=False):
        days = result.setdefault(row.stock_id, {})
        days.setdefault(row.trade_date, set()).add(int(row.clause))
    return result


def punishments_by_stock(punishes: pd.DataFrame) -> dict[str, list[tuple]]:
    """stage_punish → {股票: [(公布日, 起日, 迄日), ...]}（由舊到新）。"""
    result = {}
    for row in punishes.itertuples(index=False):
        result.setdefault(row.stock_id, []).append((row.announce_date, row.period_start, row.period_end))
    return {stock: sorted(items, key=lambda item: item[0]) for stock, items in result.items()}


def add_future_days(trading_days: list[datetime.date], holidays: set[datetime.date],
                    n: int = 40) -> list[datetime.date]:
    """已知的營業日後面，再接 n 個推算的營業日（週一～五、不是休市日）。臨時休市要等實際資料才知道。"""
    days = list(trading_days)
    d = days[-1]
    while len(days) < len(trading_days) + n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
    return days


def next_trading_day(days: list[datetime.date], d: datetime.date) -> datetime.date:
    """d 之後的第一個營業日。"""
    return days[bisect.bisect_right(days, d)]


# ---------- 1. 從哪天開始算 ----------
def count_start_index(announce_dates: list[datetime.date], days: list[datetime.date], i: int) -> int:
    """今天（days[i]）之前最後一次處置公告的隔一個營業日；沒處置過就從資料第一天算。回傳在 days 的位置。

    今天公布的處置不算：今天的進度就是讓它被處置的那幾天。
    """
    before = [d for d in announce_dates if d < days[i]]
    if not before:
        return 0
    return bisect.bisect_right(days, before[-1])


# ---------- 2. 四條路徑各計入哪幾天 ----------
def counted_dates(stock_days: dict[datetime.date, set[int]], history: list[datetime.date]) -> dict[str, list]:
    """history：從開始算的那天到今天的營業日。回傳 {"streak3": [日期...], "streak5": [...], "in10": [...], "in30": [...]}。"""
    def has_clause_1(d):
        return 1 in stock_days.get(d, set())

    def has_clause_1_to_8(d):
        return bool(stock_days.get(d, set()) & COUNTED_CLAUSES)

    def streak(check):
        """從今天往回數，遇到沒被列的那天就停。"""
        days = []
        for d in reversed(history):
            if not check(d):
                break
            days.append(d)
        return sorted(days)

    return {
        "streak3": streak(has_clause_1),
        "streak5": streak(has_clause_1_to_8),
        "in10": [d for d in history[-10:] if has_clause_1_to_8(d)],
        "in30": [d for d in history[-30:] if has_clause_1_to_8(d)],
    }


# ---------- 3. 每條還差幾次 ----------
def steps_left_each(stock_days: dict[datetime.date, set[int]], history: list[datetime.date],
                    counts: dict[str, int]) -> dict[str, int]:
    """假設之後每個營業日都被列注意，每一條再幾天會達標。回傳 {"streak3_left": 1, ...}。"""
    def window_left(n, threshold):
        """再 k 天後的窗口＝今天往回 n−k 個營業日 + 之後的 k 天。舊的日子會滑出窗口，所以不一定是「門檻 − 次數」。"""
        for k in range(1, threshold + 1):
            kept = [d for d in history[-(n - k):] if stock_days.get(d, set()) & COUNTED_CLAUSES]
            if len(kept) + k >= threshold:
                return k

    return {
        "streak3_left": THRESHOLDS["streak3"] - counts["streak3"],   # 連續沒斷，每天 +1
        "streak5_left": THRESHOLDS["streak5"] - counts["streak5"],
        "in10_left": window_left(10, THRESHOLDS["in10"]),
        "in30_left": window_left(30, THRESHOLDS["in30"]),
    }


# ---------- 4. 若下一個營業日再被列注意 ----------
def next_punishment(closest_path: str, counted: dict, stock_days: dict[datetime.date, set[int]],
                    announce_dates: list[datetime.date], future: list[datetime.date], i: int) -> dict:
    """只差一次的股票：明天（future[i+1]）再被列 → 明天公布處置 → 後天起處置。

    future：營業日（含推算的未來營業日）；i：今天的位置。
    """
    listed = future[i + 1]
    start = future[i + 2]

    # 處置幾天：新制 5 天、舊制 10 天；因連 3 日／連 5 日處置、且湊成的日子裡有第 13 款 → 延長（7／12 天）
    # 只看會被引用的那條（closest_path）；明天的款次還不知道，只看已計入的日子
    extended = closest_path in ("streak3", "streak5") and any(
        EXTEND_CLAUSE in stock_days.get(d, set()) for d in counted[closest_path])
    rule = "new" if start >= NEW_RULE_DATE else "old"   # 依處置起日：8/07 公布、8/10 起的用新制
    length = (PUNISH_DAYS_EXTENDED if extended else PUNISH_DAYS)[rule]
    end = future[i + 1 + length]

    # 第幾次：公布那天往前 30 個營業日內已經有處置公告 → 第二次
    window_start = future[max(0, i + 1 - 29)]
    punished_recently = any(window_start <= d < listed for d in announce_dates)

    return {
        "next_listed_date": listed,
        "next_punish_round": 2 if punished_recently else 1,
        "next_period_start": start,
        "next_period_end": end,
        "next_resume_date": next_trading_day(future, end),
    }


# ---------- 5. 處置中、幾號出關 ----------
def current_punishment(punishments: list[tuple], day: datetime.date, future: list[datetime.date]) -> dict:
    """那天已公布、還沒出關的處置：起迄和出關（恢復正常交易）的日子。

    公布當天（明天才開始）到出關當天都有值，卡片牆才分得出「處置股」「明日出關」「今日出關」。
    """
    for announce, start, end in reversed(punishments):
        resume = next_trading_day(future, end)
        if announce <= day <= resume:
            return {"period_start": start, "period_end": end, "resume_date": resume}
    return {"period_start": None, "period_end": None, "resume_date": None}


# ---------- 一檔股票一天 ----------
def one_stock_one_day(stock: str, stock_days: dict, punishments: list[tuple],
                      days: list[datetime.date], future: list[datetime.date], i: int) -> tuple[dict, dict]:
    """算 stock 在 days[i] 那天的一列 app_progress，和四條路徑計入的日期。"""
    day = days[i]
    announce_dates = [announce for announce, _, _ in punishments]

    # 1. 從哪天開始算
    first = count_start_index(announce_dates, days, i)
    history = days[first:i + 1]

    # 2. 四條路徑各計入哪幾天、幾次；今天達標的有哪幾條
    counted = counted_dates(stock_days, history)
    counts = {path: len(dates) for path, dates in counted.items()}
    punished_by = [path for path in THRESHOLDS if counts[path] >= THRESHOLDS[path]]

    row = {"trade_date": day, "stock_id": stock, "count_start": days[first], **counts,
           "punished_by": ",".join(punished_by) or None, "has_full_history": i >= 29}

    # 3. 每條還差幾次；今天已經達標的話明天重新算，還差幾次就沒有意義
    if punished_by:
        row.update(dict.fromkeys([*LEFT_COLUMNS, "steps_left", "closest_path"]))
    else:
        lefts = steps_left_each(stock_days, history, counts)
        closest_path = min(THRESHOLDS, key=lambda path: lefts[f"{path}_left"])   # 同分取前面的（streak3 優先）
        row.update(lefts, steps_left=lefts[f"{closest_path}_left"], closest_path=closest_path)

    # 4. 只差一次：若明天再被列，從哪天處置到哪天
    if row["steps_left"] == 1:
        row.update(next_punishment(row["closest_path"], counted, stock_days, announce_dates, future, i))
    else:
        row.update(dict.fromkeys(NEXT_COLUMNS))

    # 5. 處置中的話，幾號出關
    row.update(current_punishment(punishments, day, future))
    return row, counted


# ---------- 主程式 ----------
def compute_progress(trading_days: list[datetime.date], clauses: pd.DataFrame, punishes: pd.DataFrame,
                     day: datetime.date, holidays: set[datetime.date] = frozenset()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """算 day 那天、每檔最近 30 個營業日被列過注意的股票。不是營業日就回傳空的表。

    trading_days：到 day 為止的營業日（要包含之前的，才看得到前面的注意）
    clauses：stage_notice_clause；punishes：stage_punish；holidays：stage_holiday（推算未來的營業日用）
    回傳 (app_progress, app_progress_date) 兩張表。
    """
    progress_rows, date_rows = [], []
    days = sorted(trading_days)
    if day in days:
        i = days.index(day)
        future = add_future_days(days, set(holidays))   # 已知的營業日 + 推算的未來營業日（算處置起訖、出關用）
        listed = listed_days_by_stock(clauses)
        punished = punishments_by_stock(punishes)

        # 最近 30 個營業日被列過注意的股票
        recent = set(days[max(0, i - 29):i + 1])
        stocks = sorted(stock for stock, stock_days in listed.items() if recent & stock_days.keys())

        for stock in stocks:
            row, counted = one_stock_one_day(stock, listed[stock], punished.get(stock, []), days, future, i)
            progress_rows.append(row)
            for path, dates in counted.items():
                for counted_date in dates:
                    date_rows.append({"trade_date": day, "stock_id": stock, "path": path, "counted_date": counted_date})

    return pd.DataFrame(progress_rows, columns=PROGRESS_COLUMNS), pd.DataFrame(date_rows, columns=DATE_COLUMNS)
