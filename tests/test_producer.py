"""producer 決定要抓哪些重大訊息：用記憶體裡的假 raw 表（raw_store），不連網路、不連資料庫。執行：uv run pytest"""
from common.date_utils import prev_month
from crawler import client
from crawler import producer as pc


def setup_day(raw_store, day="20261113"):
    """今天：2330 被列注意、0050 處置（ETF，不在公司清單）。"""
    raw_store.put("companies", day, [{"公司代號": "2330 "}, {"公司代號": "2317"}])
    raw_store.put("notice", day, {"data": [["1", "2330"]]})
    raw_store.put("punish", day, {"data": [["1", "x", "0050"]]})


def keys(jobs):
    return [tuple(kwargs.values()) for _, kwargs in jobs]


def test_prev_month():
    assert prev_month("20260115") == "202512"
    assert prev_month("202611") == "202610"


def test_client_ids_strip_spaces():
    assert client.notice_stock_ids({"data": [["1", "2330 "]]}) == {"2330"}
    assert client.punish_stock_ids({"data": []}) == set()
    assert client.company_ids([{"公司代號": "2330 "}, {"公司代號": "2317"}]) == {"2330", "2317"}


def test_this_and_prev_month_for_listed_companies_only(raw_store):
    """今天被關注的上市公司：查當月和上個月；ETF（0050）不是上市公司，不查。"""
    setup_day(raw_store)
    assert keys(pc.build_mops_jobs("20261113", "20261113")) == [("2330", 2026, 10), ("2330", 2026, 11)]


def test_range_dedupes_and_always_refetches(raw_store):
    """區間內重複的（公司, 月）只查一次；已經抓過的也照樣重抓。"""
    setup_day(raw_store)
    raw_store.put("notice", "20261020", {"data": [["1", "2317"], ["2", "030039"]]})   # 10/20：2317、權證
    raw_store.put("punish", "20261027", {"data": [["1", "x", "2330"]]})               # 10/27：2330 處置中
    raw_store.put("announcements", "2330_202610", {})                                  # 抓過也會重抓
    assert keys(pc.build_mops_jobs("20261020", "20261113")) == [
        ("2317", 2026, 9), ("2317", 2026, 10),
        ("2330", 2026, 9), ("2330", 2026, 10), ("2330", 2026, 11),
    ]


def test_failed_raw_is_ignored(raw_store):
    """注意股那天的回應失敗（被擋）：不算進關注名單。"""
    setup_day(raw_store)
    raw_store.put("notice", "20261112", "<html>blocked</html>", ok=False)
    assert pc.watched_stocks("20261112") == set()


def test_holiday_is_fetched_only_once_per_year(raw_store):
    """開休市日期抓過那年就不再抓；還沒抓過的年份才抓。"""
    holiday_years = lambda: [kw["year"] for f, kw in pc.build_twse_jobs("20261230", "20270102") if f.__name__ == "crawl_holiday"]
    assert holiday_years() == [2026, 2027]
    raw_store.put("holiday", "2026", {"data": []})
    assert holiday_years() == [2027]
