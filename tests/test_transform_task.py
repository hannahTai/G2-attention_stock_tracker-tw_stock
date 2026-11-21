"""transform 選哪些 raw 來整理：用記憶體裡的假 raw 表，不連資料庫。"""
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
