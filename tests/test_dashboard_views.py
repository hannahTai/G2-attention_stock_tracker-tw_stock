"""dashboard/views.py：總覽的分區、分頁數字、篩選；個股頁的時程、公告、處置進度。不連網路。"""
import datetime

from dashboard import views
from tests.web_fixtures import CARDS, DAY, NEXT, card_of, make_card, stats_response, stock_detail


def section_names(page: dict, key: str) -> list[str]:
    return next(([c["name"] for c in s["cards"]] for s in page["sections"] if s["key"] == key), [])


# ---------- 總覽 ----------
def test_tabs_add_up_to_all():
    counts = views.overview([dict(c) for c in CARDS])["counts"]
    assert [counts[k] for k in ("one", "soon", "punish", "leave_tomorrow", "rest")] == [1, 1, 0, 1, 1]   # 丙公司今天處置最後一天
    assert counts["all"] == 4


def test_all_tab_is_not_grouped_but_follows_the_stages():
    page = views.overview([dict(c) for c in CARDS])
    assert [s["key"] for s in page["sections"]] == ["cards", "rest"]          # 卡片在一起，「其他」的清單在後面
    assert section_names(page, "cards") == ["乙公司", "丁公司", "丙公司"]     # 聽牌 → 即將處置 → 處置中


def test_only_filter_counts_and_filtering():
    page = views.overview([dict(c) for c in CARDS])
    assert page["filter_counts"]["listed"] == 3 and "leave_tomorrow" not in page["filter_counts"]   # 明日出關是分頁，不是篩選
    filtered = views.overview([dict(c) for c in CARDS], only="listed")
    assert section_names(filtered, "cards") == ["乙公司", "丁公司"] and section_names(filtered, "rest") == ["甲公司"]
    old_url = views.overview([dict(c) for c in CARDS], only="leave_tomorrow")   # 舊網址：換成明日出關分頁
    assert old_url["tab"] == "leave_tomorrow" and section_names(old_url, "cards") == ["丙公司"]
    assert filtered["counts"] == views.overview([dict(c) for c in CARDS])["counts"]   # 分頁數字是當天總數，不跟著篩選變


def test_unknown_filter_is_ignored():
    assert views.overview([dict(c) for c in CARDS], only="nope")["counts"]["all"] == 4


def test_quiet_card_is_hidden():
    page = views.overview([dict(c) for c in CARDS])
    assert "戊公司" not in [c["name"] for s in page["sections"] for c in s["cards"]]


def test_announced_disposition_shows_its_dates():
    card = views.wall_card(card_of("4444"))
    assert card["headline"]["text"] == "11/23 起處置"
    assert ("處置", "11/23–11/27 第一次處置") in card["facts"]


def test_one_step_card_says_what_happens_if_listed_again():
    assert "if_next" not in views.wall_card(card_of("2222"))   # 不放推算


def test_punish_card_shows_matching_and_preorder():
    card = views.wall_card(card_of("3333"))
    assert ("撮合", "約每 2 分鐘一次") in card["facts"] and ("預收", "全部委託都要預收") in card["facts"]
    assert card["close"] is None   # 沒有收盤價就不顯示


def test_resume_day_with_one_step_goes_to_one_section():
    resumed = make_card("6666", "己公司", is_listed_today=0, steps_left=1, streak3=2, streak3_left=1,
                        closest_path="streak3", period_start=datetime.date(2026, 11, 13),
                        period_end=datetime.date(2026, 11, 19), resume_date=DAY, measure_type="第一次處置", **NEXT)
    page = views.overview([resumed])
    assert section_names(page, "cards") == ["己公司"] and section_names(page, "rest") == []
    steps = views.stock_page(stock_detail(resumed))["timeline"]
    assert ("11/13–11/19" in [s.when for s in steps]) and ("出關，恢復正常交易" in [s.text for s in steps])


# ---------- 個股頁 ----------
def test_one_step_stock_has_no_predicted_timeline():
    page = views.stock_page(stock_detail(card_of("2222")))
    assert page["timeline"] == [] and not page["has_punishment"]   # 聽牌、沒有處置：不放「若再被列就處置」的推算


def test_stock_page_met_today_with_announced_dates():
    page = views.stock_page(stock_detail(card_of("4444")))
    assert [t["text"] for t in page["wall"]["tags"]][0] == "即將處置"
    assert page["wall"]["headline"]["text"] == "11/23 起處置"
    assert "11/30" in [s.resume_when for s in page["timeline"]]   # 出關日寫在處置那一列
    assert next(p for p in page["paths"] if p["key"] == "streak3")["met"]


def test_stock_page_announcements_not_marked_during_session():
    page = views.stock_page(stock_detail(card_of("2222")))
    items = [a for g in page["announcements_recent"] for a in g["items"]]
    assert sorted(a["subject"] for a in items) == ["更正本公司營收公告 US$ 1,000", "盤前公告"]
    assert [a["time"] for a in items] == ["15:26", "08:30"]   # 同一天照 API 給的順序（API 已依時間排好）
    assert not any(a["during_session"] for a in items)   # 15:26 盤後、08:30 盤前都不標


def test_stock_page_paths_and_notice_record():
    page = views.stock_page(stock_detail(card_of("2222")))
    streak3 = next(p for p in page["paths"] if p["key"] == "streak3")
    assert streak3["dates"] == "計入 11/19、11/20" and streak3["left"] == 1
    assert [c.kind for c in page["cells"]] == ["none", "none", "counted"]


def test_rest_is_sorted_by_progress():
    """一樣差 3 次：進度條（差 3 次的那條）填得多的在前。"""
    low = make_card("1001", "少", streak3=0, streak5=0, in10=0, in30=1, steps_left=3, closest_path="streak3",
                    streak3_left=3, streak5_left=5, in10_left=6, in30_left=11)
    high = make_card("1002", "多", streak3=0, streak5=0, in10=3, in30=3, steps_left=3, closest_path="streak3",
                     streak3_left=3, streak5_left=5, in10_left=3, in30_left=9)   # 10 日內 3/6 也差 3 次：進度條放這條
    assert section_names(views.overview([low, high]), "rest") == ["多", "少"]


def test_search_filters_cards_but_keeps_tab_counts():
    page = views.overview([dict(c) for c in CARDS], search="1111")
    assert [c["stock_id"] for s in page["sections"] for c in s["cards"]] == ["1111"]
    assert page["counts"]["all"] == views.overview([dict(c) for c in CARDS])["counts"]["all"]   # 分頁數字不跟著搜尋變


# ---------- 統計頁 ----------
def test_stats_tomorrow_limits_and_releases_come_from_the_cards():
    """下一個交易日 11/23：丁公司開始處置、丙公司恢復正常交易；3 天內出關只有丙公司（丁公司 11/30 才出關）。"""
    page = views.stats_page([dict(c) for c in CARDS], stats_response())
    assert page["next_day"] == datetime.date(2026, 11, 23)
    assert [(t["label"], t["count"], t["tab"]) for t in page["tomorrow"]] == [
        ("聽牌", 1, "one"), ("即將處置", 1, "soon"), ("處置中", 0, "punish")]
    assert [r["count"] for r in page["releases"]] == [1, 0, 0, 0, 0]   # 固定看 5 個交易日


def test_stats_numbers_for_the_insights():
    """平均、延長成 7 天的次數、款次比例、產業只列 20 家以上。"""
    page = views.stats_page([dict(c) for c in CARDS], stats_response())
    assert (page["listed_avg"], page["clause13_avg"]) == (18.0, 3.0)
    assert (page["punish_ended"], page["punish_extended"], page["extended_days"]) == (12, 3, 7)
    assert page["punish_avg"] == 18 / 16   # 8/03～11/16 共 16 週：12、0、6，其他沒處置的週補 0（連到 11/20 那週）
    assert page["clauses"]["top"]["no"] == 1
    assert [r["name"] for r in page["industries"]["rows"]] == ["玻璃陶瓷", "電子零組件業", "半導體業"]   # 全部列，家數少的也列
    assert page["industries"]["overall"] == 29   # 全體（含玻璃陶瓷）：60／205
    assert sum(s["pct"] for s in page["industry_share"]["slices"]) == 100   # 組成圓餅加起來剛好 100%


def test_stats_page_without_next_trading_days_does_not_break():
    stats = {**stats_response(), "next_trading_days": [], "daily": [], "new_punish": []}
    page = views.stats_page([dict(c) for c in CARDS], stats)
    assert page["next_day"] is None and [t["count"] for t in page["tomorrow"]] == [0, 0, 0]
    assert page["clause13_avg"] is None and page["punish_avg"] == 0   # 沒有處置的週也算，平均 0


def test_one_step_card_warns_when_next_punishment_is_the_second():
    """聽牌、規則引擎算出再處置是 30 個營業日內第二次：多一個「再處置為第二次」小標籤。"""
    texts = [t["text"] for t in views.tags(card_of("2222"))]   # 乙公司：聽牌、next_punish_round＝2
    assert texts == ["聽牌", "再處置為第二次", "11/20 注意股"]
    assert "再處置為第二次" not in [t["text"] for t in views.tags(make_card("7777", "庚公司", steps_left=1))]


def test_industry_share_folds_the_rest_into_other():
    """組成圓餅：家數多的前 4 個產業各一塊，其他合成「其他」放最後；百分比加起來剛好 100。"""
    rows = [{"industry_code": code, "companies": 50, "listed": n} for code, n in
            (("28", 30), ("24", 20), ("26", 10), ("27", 5), ("25", 3), ("05", 2))]
    share = views.industry_share(rows)
    assert share["slices"][-1]["name"] == "其他" and share["slices"][-1]["listed"] == 5 and share["slices"][-1]["other"]
    assert len(share["slices"]) == 5 and sum(s["pct"] for s in share["slices"]) == 100 and share["total"] == 70
    assert views.industry_share([]) == {"total": 0, "slices": []}
