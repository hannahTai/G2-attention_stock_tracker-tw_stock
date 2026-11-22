"""transform 選哪些 raw 來整理：用記憶體裡的假 raw 表，不連資料庫。"""
from common.hash_utils import url_md5
from transform import producer, task


def jobs_by_task(jobs):
    """{任務名稱: [參數值, ...]}，方便比對。"""
    out = {}
    for func, kwargs in jobs:
        out.setdefault(func.__name__, []).append(tuple(kwargs.values()))
    return out


def test_daily_jobs_cover_what_crawler_fetched(raw_store):
    raw_store.put("announcement", "2330_202609", {})   # 上上個月：不處理
    raw_store.put("announcement", "2330_202610", {})   # 上個月：處理
    raw_store.put("announcement", "2330_202611", {})   # 這個月：處理

    jobs = jobs_by_task(producer.build_jobs("20261113", "20261113"))

    assert jobs["transform_notice"] == [("20261113",)]
    assert jobs["transform_punish"] == [("20261113",)]
    assert jobs["transform_stock_price"] == [("20261113",)]
    assert jobs["transform_market_daily"] == [("202611",)]   # 一份是一整個月
    assert jobs["transform_holiday"] == [(2026,), (2027,)]   # 一份是一整年；11 月起也整理下一年
    assert jobs["transform_punish_watchlist"] == [("20261113",)]
    assert jobs["transform_company"] == [("20261113",)]
    assert jobs["transform_announcement"] == [("2330", 2026, 10), ("2330", 2026, 11)]


def test_detail_jobs_by_enter_month(raw_store):
    """全文：輸入日期可能比發言日期早一點（5/29 輸入、6/1 發言），所以比重大訊息多看一個月。"""
    raw_store.put("announcement_detail", "2330_20260831_1", {})   # 再早一個月：不處理
    raw_store.put("announcement_detail", "2330_20260930_1", {})   # 可能是 10 月發言的
    raw_store.put("announcement_detail", "2330_20261112_3", {})

    jobs = jobs_by_task(producer.build_jobs("20261113", "20261113"))

    assert jobs["transform_announcement_detail"] == [("2330", "20260930", 1), ("2330", "20261112", 3)]


def test_range_covers_every_month_and_year(raw_store):
    jobs = jobs_by_task(producer.build_jobs("20261230", "20270102"))

    assert jobs["transform_market_daily"] == [("202612",), ("202701",)]
    assert jobs["transform_holiday"] == [(2026,), (2027,)]
    assert len(jobs["transform_notice"]) == 4


def test_latest_key_uses_last_one_not_future(raw_store):
    """週六沒有新名單：用週五的；不會拿到之後的快照。"""
    raw_store.put("company", "20261113", [])
    raw_store.put("company", "20261120", [])

    assert task.latest_key("company", "20261114") == "20261113"
    assert task.latest_key("company", "20261112") == "<=20261112"   # 一份都沒有：讀不到，任務會跳過


def test_missing_raw_is_skipped_not_failed(raw_store):
    """crawler 沒抓到（或失敗）的那份：跳過，不寫 stage、不算失敗。"""
    raw_store.put("notice", "20261113", "<html>blocked</html>", ok=False)
    assert "跳過" in task.transform_notice("20261113")


def test_news_jobs_include_lookback_days(raw_store, monkeypatch):
    """crawler 往前多抓 NEWS_LOOKBACK_DAYS 天的新聞，transform 也往前多整理那幾天。"""
    monkeypatch.setattr(producer, "NEWS_LOOKBACK_DAYS", 2)
    for key in ("2330_20261110", "2330_20261111", "2330_20261113", "2330_20261114"):
        raw_store.put("stock_news", key, {"data": []})
    jobs = jobs_by_task(producer.build_jobs("20261113", "20261113"))
    assert jobs["transform_stock_news"] == [("2330", "20261111"), ("2330", "20261113")]


def test_news_detail_jobs_only_for_fetched_pages(raw_store):
    """新聞網頁：區間內新聞清單裡、有抓到網頁的（raw_news_detail 的 key 是 news_id）各一個任務。"""
    raw_store.put("stock_news", "2330_20261113", {"data": [
        {"date": "2026-11-13 01:00:00", "stock_id": "2330", "link": "https://a/1", "source": "UDN", "title": "a"},
        {"date": "2026-11-13 02:00:00", "stock_id": "2330", "link": "https://b/2", "source": "UDN", "title": "b"}]})
    raw_store.put("news_detail", url_md5("https://a/1"), {"head": "", "final_url": "https://a/1", "text_chars": None})
    raw_store.put("news_detail", url_md5("https://z/9"), {"head": "", "final_url": "https://z/9", "text_chars": None})   # 不在這段期間的清單裡
    jobs = jobs_by_task(producer.build_jobs("20261113", "20261113"))
    assert jobs["transform_news_detail"] == [(url_md5("https://a/1"),)]
