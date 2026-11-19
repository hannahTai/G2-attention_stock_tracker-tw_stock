"""analyze/engine.py（規則引擎）的測試：用手做的小例子，不連資料庫。規則見 docs/rules.md。"""
import datetime

import pandas as pd

from analyze import engine

# 2026/10/01 起的營業日（週一～週五，跳過週末）
DAYS = [d.date() for d in pd.bdate_range("2026-10-01", periods=45)]


def clauses(*rows):
    """clauses(("2424", 0, [1]), ...)：股票、第幾個營業日、款次。"""
    return pd.DataFrame([(s, DAYS[i], c) for s, i, cs in rows for c in cs], columns=["stock_id", "trade_date", "clause"])


def punishes(*rows):
    """punishes(("2424", 2), ...)：股票、第幾個營業日公布處置（隔天起處置 5 個營業日）。"""
    return pd.DataFrame([(s, DAYS[i], DAYS[i + 1], DAYS[i + 5]) for s, i in rows],
                        columns=["stock_id", "announce_date", "period_start", "period_end"])


def progress(clause_df, punish_df, day_index, stock="2424"):
    """算某一天某一檔的進度，回傳 (那一列, 計入日期表)。"""
    day = DAYS[day_index]
    rows, dates = engine.compute_progress(DAYS[:day_index + 1], clause_df, punish_df, day)
    row = rows[rows["stock_id"] == stock].iloc[0]
    return row, dates[dates["stock_id"] == stock]


def counted(dates, path):
    return sorted(dates[dates["path"] == path]["counted_date"])


def test_three_days_of_clause_1_is_punished():
    c = clauses(("2424", 0, [1]), ("2424", 1, [1]), ("2424", 2, [1]))
    row, dates = progress(c, punishes(), 2)
    assert row["streak3"] == 3 and row["punished_by"] == "streak3"
    assert counted(dates, "streak3") == DAYS[0:3]


def test_weekend_does_not_break_a_streak():
    """10/01（四）、10/02（五）、10/05（一）：中間的週末不算，還是連續。"""
    assert DAYS[1].weekday() == 4 and DAYS[2].weekday() == 0
    c = clauses(("2424", 0, [1]), ("2424", 1, [1]), ("2424", 2, [1]))
    row, _ = progress(c, punishes(), 2)
    assert row["streak3"] == 3


def test_streak3_only_clause_1_but_streak5_counts_1_to_8():
    """中間那天只被列第 2 款：連 3 日斷掉，但連 5 日、10 日照算。"""
    c = clauses(("2424", 0, [1]), ("2424", 1, [2]), ("2424", 2, [1]))
    row, _ = progress(c, punishes(), 2)
    assert (row["streak3"], row["streak5"], row["in10"]) == (1, 3, 3)


def test_clauses_9_to_14_do_not_count():
    c = clauses(("2424", 0, [1]), ("2424", 1, [9, 13]), ("2424", 2, [1]))
    row, _ = progress(c, punishes(), 2)
    assert (row["streak3"], row["streak5"], row["in10"]) == (1, 1, 2)


def test_one_day_with_several_clauses_counts_once():
    c = clauses(("2424", 0, [1, 3, 10]))
    row, _ = progress(c, punishes(), 0)
    assert (row["streak3"], row["streak5"], row["in10"], row["in30"]) == (1, 1, 1, 1)


def test_count_restarts_after_punishment_like_2424():
    """實際案例 2424 隴華：前 3 天被列第 1 款 → 第 3 天處置；第 4 天又被列，但只算 1 天；
    第 4、5、6 天湊滿新的 3 天 → 第 6 天第二次處置。"""
    c = clauses(*[("2424", i, [1]) for i in range(6)])
    p = punishes(("2424", 2))

    row, dates = progress(c, p, 3)
    assert row["streak3"] == 1 and row["count_start"] == DAYS[3] and row["punished_by"] is None
    assert counted(dates, "streak3") == [DAYS[3]]

    row, _ = progress(c, p, 5)
    assert row["streak3"] == 3 and row["punished_by"] == "streak3"


def test_ten_day_window_left():
    """10 個營業日內 5 天：還差 1 次。但如果最舊的那天明天掉出窗口，明天再被列也只有 5 天，還差 2 次。"""
    # 第 14 天的窗口是第 5～14 天；明天（第 15 天）的窗口是第 6～15 天，第 5 天會掉出去
    five_recent = clauses(*[("2424", i, [2]) for i in (6, 8, 10, 12, 13)])   # 都在第 6～14 天：明天還在
    row, _ = progress(five_recent, punishes(), 14)
    assert row["in10"] == 5 and row["in10_left"] == 1

    oldest_drops = clauses(*[("2424", i, [2]) for i in (5, 8, 10, 12, 14)])  # 第 5 天明天會掉出去
    row, _ = progress(oldest_drops, punishes(), 14)
    assert row["in10"] == 5 and row["in10_left"] == 2


def test_thirty_day_window_left():
    """30 個營業日內 10 天，但最舊的 3 天（第 1～3 天）接下來 3 天會依序掉出窗口。"""
    c = clauses(*[("2424", i, [2]) for i in (1, 2, 3, 10, 12, 14, 16, 18, 20, 22)])
    row, _ = progress(c, punishes(), 30)   # 窗口是第 1～30 天
    # 再 1 天：剩 9＋1；再 2 天：剩 8＋2；再 3 天：剩 7＋3；第 4 天起不再掉：7＋4，再 5 天：7＋5＝12
    assert row["in30"] == 10 and row["in30_left"] == 5


def test_streak_left():
    c = clauses(("2424", 0, [1]), ("2424", 1, [1]))
    row, _ = progress(c, punishes(), 1)
    assert row["streak3_left"] == 1 and row["streak5_left"] == 3


def test_punished_today_means_no_left():
    c = clauses(("2424", 0, [1]), ("2424", 1, [1]), ("2424", 2, [1]))
    row, _ = progress(c, punishes(), 2)
    assert row["punished_by"] == "streak3"
    assert all(row[f] is None for f in ("streak3_left", "streak5_left", "in10_left", "in30_left"))


def test_only_stocks_noticed_in_last_30_days_get_a_row():
    c = clauses(("1111", 0, [1]), ("2424", 35, [1]))
    rows, _ = engine.compute_progress(DAYS[:36], c, punishes(), DAYS[35])
    assert rows["stock_id"].tolist() == ["2424"]   # 1111 是 35 個營業日前，已經超過 30 天


def test_has_full_history_needs_30_trading_days():
    c = clauses(("2424", 28, [1]), ("2424", 29, [1]))
    assert not progress(c, punishes(), 28)[0]["has_full_history"]
    assert progress(c, punishes(), 29)[0]["has_full_history"]


# ---------- 還差幾次、若再列注意 ----------
def test_steps_left_is_the_closest_path():
    """連 3 日、連 5 日都只差一次：取前面的 streak3。"""
    c = clauses(("2424", 0, [2]), ("2424", 1, [2]), ("2424", 2, [1]), ("2424", 3, [1]))
    row, _ = progress(c, punishes(), 3)
    assert row["streak3_left"] == 1 and row["streak5_left"] == 1
    assert row["steps_left"] == 1 and row["closest_path"] == "streak3"


def test_not_one_step_away_has_no_next_punishment():
    row, _ = progress(clauses(("2424", 0, [1])), punishes(), 0)
    assert row["steps_left"] == 2 and row["closest_path"] == "streak3"
    assert all(pd.isna(row[col]) for col in engine.NEXT_COLUMNS)


def test_next_punishment_first_time():
    """10/02（五）只差一次 → 10/05（一）再列 → 10/06 起 5 個營業日，10/13 恢復。"""
    row, _ = progress(clauses(("2424", 0, [1]), ("2424", 1, [1])), punishes(), 1)
    assert row["next_listed_date"] == DAYS[2] and row["next_punish_round"] == 1
    assert (row["next_punish_start"], row["next_punish_end"], row["next_resume_date"]) == (DAYS[3], DAYS[7], DAYS[8])


def test_next_punishment_second_time():
    """30 個營業日內已處置過一次 → 第二次。"""
    c = clauses(("2424", 0, [1]), ("2424", 1, [1]), ("2424", 2, [1]))
    row, _ = progress(c, punishes(("2424", 0)), 2)
    assert row["steps_left"] == 1 and row["next_punish_round"] == 2


def test_next_punishment_extended_by_clause_13():
    """連 3 日湊成的日子裡有第 13 款 → 7 個營業日。"""
    row, _ = progress(clauses(("2424", 1, [1, 13]), ("2424", 2, [1])), punishes(), 2)
    assert (row["next_punish_start"], row["next_punish_end"]) == (DAYS[4], DAYS[10])


def test_future_trading_days_skip_holidays():
    """資料只到 10/02：之後的營業日用週一～五推算，跳過休市日 10/09。"""
    rows, _ = engine.compute_progress(DAYS[:2], clauses(("2424", 0, [1]), ("2424", 1, [1])), punishes(),
                                      DAYS[1], holidays={datetime.date(2026, 10, 9)})
    row = rows.iloc[0]
    assert row["next_listed_date"] == datetime.date(2026, 10, 5)
    assert row["next_punish_start"] == datetime.date(2026, 10, 6)
    assert row["next_punish_end"] == datetime.date(2026, 10, 13)    # 6、7、8、12、13
    assert row["next_resume_date"] == datetime.date(2026, 10, 14)


def test_old_rule_before_aug_10_is_10_days():
    old = [d.date() for d in pd.bdate_range("2026-07-01", periods=20)]
    c = pd.DataFrame([("2424", old[0], 1), ("2424", old[1], 1)], columns=["stock_id", "trade_date", "clause"])
    rows, _ = engine.compute_progress(old[:2], c, punishes(), old[1])
    row = rows.iloc[0]
    assert (row["next_punish_start"], row["next_punish_end"], row["next_resume_date"]) == (old[3], old[12], old[13])


def test_rule_follows_punish_start_date():
    """8/07（五）再列 → 8/10 起處置，用新制 5 天。"""
    days = [d.date() for d in pd.bdate_range("2026-08-05", periods=20)]   # 8/05（三）起
    c = pd.DataFrame([("2424", days[0], 1), ("2424", days[1], 1)], columns=["stock_id", "trade_date", "clause"])
    rows, _ = engine.compute_progress(days[:2], c, punishes(), days[1])
    row = rows.iloc[0]
    assert row["next_listed_date"] == datetime.date(2026, 8, 7)
    assert (row["next_punish_start"], row["next_punish_end"]) == (datetime.date(2026, 8, 10), datetime.date(2026, 8, 14))


def test_extension_only_looks_at_the_cited_path():
    """連 3、連 5 都只差一次；第 13 款只在連 5 的日子裡 → 引用連 3，不延長。"""
    c = clauses(("2424", 0, [5, 13]), ("2424", 1, [3]), ("2424", 2, [1]), ("2424", 3, [1]))
    row, _ = progress(c, punishes(), 3)
    assert row["closest_path"] == "streak3"
    assert row["next_punish_end"] == DAYS[4 + 5]


def test_current_punishment_and_resume_date():
    """第 2 天公布、第 3～7 天處置、第 8 天出關 → 第 2～8 天都有值（第 8 天＝今日出關）；第 9 天起就沒有了。"""
    c = clauses(("2424", 0, [1]), ("2424", 1, [1]), ("2424", 2, [1]))
    p = pd.DataFrame([("2424", DAYS[2], DAYS[3], DAYS[7])], columns=["stock_id", "announce_date", "period_start", "period_end"])
    for i in (2, 5, 7, 8):
        row, _ = progress(c, p, i)
        assert (row["punish_start"], row["punish_end"], row["resume_date"]) == (DAYS[3], DAYS[7], DAYS[8])
    row, _ = progress(c, p, 9)
    assert pd.isna(row["punish_end"]) and pd.isna(row["resume_date"])
    row, _ = progress(c, p, 1)
    assert pd.isna(row["punish_end"])   # 還沒公布
