"""transform 選哪些 raw 來整理、analyze 檢查必要的 stage 資料：用記憶體裡的假表，不連資料庫。"""
import datetime

from common import repository
from analyze import producer as analyze_producer
from transform import producer, task


def jobs_by_task(jobs):
    """{任務名稱: [參數值, ...]}，方便比對。"""
    out = {}
    for func, kwargs in jobs:
        out.setdefault(func.__name__, []).append(tuple(kwargs.values()))
    return out


def test_daily_jobs_cover_what_crawler_fetched(raw_store):
    raw_store.put("announcements", "2330_202609", {})   # 上上個月：不處理
    raw_store.put("announcements", "2330_202610", {})   # 上個月：處理
    raw_store.put("announcements", "2330_202611", {})   # 這個月：處理

    jobs = jobs_by_task(producer.build_jobs("20261113", "20261113"))

    assert jobs["transform_notice"] == [("20261113",)]
    assert jobs["transform_punish"] == [("20261113",)]
    assert jobs["transform_market_daily"] == [("202611",)]   # 一份是一整個月
    assert jobs["transform_holiday"] == [(2026,)]            # 一份是一整年
    assert jobs["transform_punish_watchlist"] == [("20261113",)]
    assert jobs["transform_companies"] == [("20261113",)]
    assert jobs["transform_announcements"] == [("2330", 2026, 10), ("2330", 2026, 11)]


def test_detail_jobs_by_enter_month(raw_store):
    """全文：輸入日期在當月或上個月的才整理。"""
    raw_store.put("announcement_detail", "2330_20260930_1", {})   # 上上個月：不處理
    raw_store.put("announcement_detail", "2330_20261002_1", {})
    raw_store.put("announcement_detail", "2330_20261112_3", {})

    jobs = jobs_by_task(producer.build_jobs("20261113", "20261113"))

    assert jobs["transform_announcement_detail"] == [("2330", "20261002", 1), ("2330", "20261112", 3)]


def test_range_covers_every_month_and_year(raw_store):
    jobs = jobs_by_task(producer.build_jobs("20261230", "20270102"))

    assert jobs["transform_market_daily"] == [("202612",), ("202701",)]
    assert jobs["transform_holiday"] == [(2026,), (2027,)]
    assert len(jobs["transform_notice"]) == 4


def test_latest_key_uses_last_one_not_future(raw_store):
    """週六沒有新名單：用週五的；不會拿到之後的快照。"""
    raw_store.put("companies", "20261113", [])
    raw_store.put("companies", "20261120", [])

    assert task.latest_key("companies", "20261114") == "20261113"
    assert task.latest_key("companies", "20261112") is None


def test_missing_raw_is_skipped_not_failed(raw_store):
    """crawler 沒抓到（或失敗）的那份：跳過，不寫 stage、不算失敗。"""
    raw_store.put("notice", "20261113", "<html>blocked</html>", ok=False)
    assert "跳過" in task.transform_notice("20261113")


def test_analyze_needs_trading_notice_and_punish_days(monkeypatch):
    """analyze 要三張 stage 表那天都有一列才算。"""
    have = set()   # 假的 stage：{(表, 日期)}
    monkeypatch.setattr(repository, "stage_has_day", lambda table, column, day: (table, day) in have)
    day = datetime.date(2026, 11, 13)

    assert analyze_producer.missing_inputs("20261113") == [
        "stage_trading_day/20261113", "stage_notice_day/20261113", "stage_punish_day/20261113"]

    have |= {("stage_notice_day", day), ("stage_punish_day", day)}
    assert analyze_producer.missing_inputs("20261113") == ["stage_trading_day/20261113"]   # 市場成交還沒更新到 11/13

    have.add(("stage_trading_day", day))
    assert analyze_producer.missing_inputs("20261113") == []
