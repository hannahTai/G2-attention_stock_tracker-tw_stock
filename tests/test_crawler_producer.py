"""crawler/producer.py 決定要抓哪些重大訊息、新聞：用記憶體裡的假 raw 表（raw_store），不連網路、不連資料庫。執行：uv run pytest"""
import pytest

from common import repository
from common.hash_utils import url_md5
from crawler import client
from crawler import producer as pc


def setup_day(raw_store, day="20261113"):
    """今天：2330 被列注意、0050 處置（ETF，不在公司清單）。"""
    raw_store.put("company", day, [{"公司代號": "2330 "}, {"公司代號": "2317"}])
    raw_store.put("notice", day, {"data": [["1", "2330"]]})
    raw_store.put("punish", day, {"data": [["1", "x", "0050"]]})


def keys(jobs):
    return [tuple(kwargs.values()) for _, kwargs in jobs]


def test_client_ids_strip_spaces():
    assert client.notice_stock_ids({"data": [["1", "2330 "]]}) == {"2330"}
    assert client.punish_stock_ids({"data": []}) == set()
    assert client.company_ids([{"公司代號": "2330 "}, {"公司代號": "2317"}]) == {"2330", "2317"}


def test_this_and_prev_month_for_listed_companies_only(raw_store):
    """今天被關注的上市公司：查當月和上個月；ETF（0050）不是上市公司，不查。"""
    setup_day(raw_store)
    assert keys(pc.build_mops_jobs("20261113", "20261113")) == [("2330", 2026, 10), ("2330", 2026, 11)]


def test_range_dedupes_and_skips_finished_months(raw_store):
    """區間內重複的（公司, 月）只查一次；那個月過完以後抓過的不再抓，加 refetch 就重抓。"""
    setup_day(raw_store)
    raw_store.put("notice", "20261020", {"data": [["1", "2317"], ["2", "030039"]]})   # 10/20：2317、權證
    raw_store.put("punish", "20261027", {"data": [["1", "x", "2330"]]})               # 10/27：2330 處置中
    raw_store.put("announcement", "2330_202610", {}, fetched_at="2026-11-13T21:00:00+08:00")   # 10 月過完才抓：完整
    raw_store.put("announcement", "2317_202610", {}, fetched_at="2026-10-20T21:00:00+08:00")   # 10 月還沒過完：要重抓
    raw_store.put("announcement", "2330_202611", {}, fetched_at="2026-11-13T21:00:00+08:00")   # 當月：照樣抓
    assert keys(pc.build_mops_jobs("20261020", "20261113")) == [
        ("2317", 2026, 9), ("2317", 2026, 10),
        ("2330", 2026, 9), ("2330", 2026, 11),
    ]
    assert ("2330", 2026, 10) in keys(pc.build_mops_jobs("20261020", "20261113", refetch=True))


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


def announcement_list(*ids, api_name="t05st01_detail"):
    """做一份重大訊息清單的回應：ids 是 (公司, 民國輸入日期, 序號)。"""
    detail = lambda c, d, n: {"parameters": {"marketKind": "sii", "enterDate": d, "serialNumber": n, "companyId": c},
                              "apiName": api_name}
    return {"result": {"data": [[c, "名稱", "115/11/05", "14:39:07", "主旨", detail(c, d, n)] for c, d, n in ids]}}


def test_client_announcement_ids_convert_roc_date():
    body = announcement_list(("9908 ", "1151102", "1"), ("9908", "991231", "12"))
    assert client.announcement_ids(body) == [("9908", "20261102", 1, "t05st01_detail"),
                                             ("9908", "20101231", 12, "t05st01_detail")]
    assert client.announcement_ids({"result": {"data": []}}) == []


def test_detail_jobs_use_api_from_list(raw_store):
    """存託憑證（DR）的全文是另一支 API（t59sb01_detail），照清單上寫的打。"""
    setup_day(raw_store, day="20261113")
    raw_store.put("notice", "20261113", {"data": [["1", "9103"]]})
    raw_store.put("company", "20261113", [{"公司代號": "9103"}])
    raw_store.put("announcement", "9103_202611", announcement_list(("9103", "1151102", "1"), api_name="t59sb01_detail"))
    assert keys(pc.build_detail_jobs("20261113", "20261113")) == [("9103", "20261102", 1, "t59sb01_detail")]


def test_fetch_detail_rejects_unknown_api():
    with pytest.raises(ValueError, match="不認識的全文 API"):
        client.fetch_announcement_detail("9103", "20261102", 1, "../t05st01")


def test_detail_jobs_skip_fetched(raw_store):
    """第二批清單裡的每則公告查全文；已經抓過全文的不再抓。"""
    setup_day(raw_store)
    raw_store.put("announcement", "2330_202611", announcement_list(("2330", "1151102", "1"), ("2330", "1151105", "2")))
    raw_store.put("announcement", "2317_202611", announcement_list(("2317", "1151102", "1")))   # 2317 今天沒被關注
    raw_store.put("announcement_detail", "2330_20261102_1", {"result": {"data": [["1"]]}})
    assert keys(pc.build_detail_jobs("20261113", "20261113")) == [("2330", "20261105", 2, "t05st01_detail")]


def twse_keys(jobs, name):
    return [tuple(kw.values()) for f, kw in jobs if f.__name__ == name]


def test_twse_skips_days_fetched_after_publish(raw_store):
    """注意股：有成功的就不再抓（公布了才會存成成功，幾點抓的都一樣）；還沒公布就抓的存成失敗，要重抓。refetch 全部重抓。"""
    raw_store.put("notice", "20261112", {"data": []}, fetched_at="2026-11-12T18:00:00+08:00")   # 18:00 看到當天的資料：完整
    raw_store.put("notice", "20261113", {"data": []}, fetched_at="2026-11-13T16:00:00+08:00", ok=False)   # 還沒公布就抓了
    jobs = pc.build_twse_jobs("20261112", "20261113")
    assert twse_keys(jobs, "crawl_notice") == [("20261113",)]
    assert twse_keys(jobs, "crawl_punish") == [("20261112",), ("20261113",)]   # 處置股都還沒抓過
    assert twse_keys(pc.build_twse_jobs("20261112", "20261113", refetch=True), "crawl_notice") == [("20261112",), ("20261113",)]


def test_twse_stock_price_skips_days_fetched_after_publish(raw_store):
    """收盤行情：一天一份，和注意股一樣，有成功的就不再抓。"""
    raw_store.put("stock_price", "20261112", {"tables": []}, fetched_at="2026-11-12T21:00:00+08:00")
    assert twse_keys(pc.build_twse_jobs("20261112", "20261113"), "crawl_stock_price") == [("20261113",)]


def test_twse_skips_finished_months(raw_store):
    """市場成交：那個月最後一天 20:00 以後抓的不再抓；當月照樣抓。"""
    raw_store.put("market_daily", "202610", {"data": []}, fetched_at="2026-11-02T21:00:00+08:00")
    raw_store.put("market_daily", "202611", {"data": []}, fetched_at="2026-11-13T21:00:00+08:00")
    assert twse_keys(pc.build_twse_jobs("20261030", "20261113"), "crawl_market_daily") == [("202611",)]


def test_detail_refetch_includes_fetched(raw_store):
    setup_day(raw_store)
    raw_store.put("announcement", "2330_202611", announcement_list(("2330", "1151102", "1")))
    raw_store.put("announcement_detail", "2330_20261102_1", {"result": {"data": [["1"]]}})
    assert keys(pc.build_detail_jobs("20261113", "20261113")) == []
    assert keys(pc.build_detail_jobs("20261113", "20261113", refetch=True)) == [("2330", "20261102", 1, "t05st01_detail")]


# ---------- 新聞（第四、五批）----------
def test_news_targets_look_back_for_watched_companies(raw_store, monkeypatch):
    """被關注的上市公司：那天和往前 NEWS_LOOKBACK_DAYS 天各抓一次；ETF（0050）不抓。"""
    monkeypatch.setattr(pc, "NEWS_LOOKBACK_DAYS", 2)
    setup_day(raw_store)
    assert keys(pc.build_stock_news_jobs("20261113", "20261113")) == [("2330", "20261111"), ("2330", "20261112"), ("2330", "20261113")]


def test_news_complete_after_next_morning_8am(raw_store, monkeypatch):
    """FinMind 的一天是 UTC（台灣時間 08:00 到隔天 08:00）：隔天 08:00 以後抓的才算完整，之前抓的要再抓。"""
    monkeypatch.setattr(pc, "NEWS_LOOKBACK_DAYS", 1)
    setup_day(raw_store)
    raw_store.put("stock_news", "2330_20261112", {"data": []}, fetched_at="2026-11-13T08:30:00+08:00")   # 隔天 08:30：完整
    raw_store.put("stock_news", "2330_20261113", {"data": []}, fetched_at="2026-11-14T07:00:00+08:00")   # 隔天 07:00：還沒
    assert keys(pc.build_stock_news_jobs("20261113", "20261113")) == [("2330", "20261113")]
    assert len(pc.build_stock_news_jobs("20261113", "20261113", refetch=True)) == 2


def test_news_detail_jobs_from_stock_news(raw_store, monkeypatch):
    """新聞清單裡的每個連結打開一次：論壇貼文不抓、抓過的（成功、永久失敗）不再抓、好幾檔共用的連結只抓一次。"""
    monkeypatch.setattr(pc, "NEWS_LOOKBACK_DAYS", 0)
    setup_day(raw_store)
    raw_store.put("notice", "20261113", {"data": [["1", "2330"], ["2", "2317"]]})
    raw_store.put("stock_news", "2330_20261113", {"data": [
        {"link": "https://udn.com/1"}, {"link": "https://www.cmoney.tw/forum/article/9"}, {"link": "https://ltn.com.tw/2"}]})
    raw_store.put("stock_news", "2317_20261113", {"data": [{"link": "https://udn.com/1"}, {"link": "https://ctee.com.tw/3"}]})
    monkeypatch.setattr(repository, "raw_keys_done", lambda source: {url_md5("https://ltn.com.tw/2")})
    assert [kw["link"] for _, kw in pc.build_news_detail_jobs("20261113", "20261113")] == ["https://ctee.com.tw/3", "https://udn.com/1"]
    assert len(pc.build_news_detail_jobs("20261113", "20261113", refetch=True)) == 3   # --refetch：抓過的也重開
