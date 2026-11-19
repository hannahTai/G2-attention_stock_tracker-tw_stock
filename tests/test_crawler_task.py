"""crawler/task.py：抓完存進 raw 表前的檢查（用假的回應和記憶體裡的假 raw 表）。"""
import datetime
import json

import pytest
import requests

from common import date_utils, repository
from crawler import client, task


def fake_response(body: dict) -> requests.Response:
    """做一個像 requests.get 回來的回應。"""
    url = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&date=20260101"
    resp = requests.Response()
    resp.status_code = 200
    resp._content = json.dumps(body).encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = url
    resp.request = requests.Request("GET", url).prepare()
    return resp


def test_holiday_saved_when_year_matches(raw_store, monkeypatch):
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2026, "data": []}))

    where = task.crawl_holiday(2026)

    assert where == "raw_holiday/2026"
    assert raw_store.last("holiday", "2026").ok


def test_holiday_wrong_year_saved_as_failure(raw_store, monkeypatch):
    """API 回了別年（11 月起不帶 date 會回下一年）→ 存成失敗的列，不能當成 2026 年的資料。"""
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2027, "data": []}))

    with pytest.raises(repository.FetchError, match="要 2026 年，回傳的是 2027 年") as error:
        task.crawl_holiday(2026)

    row = raw_store.last("holiday", "2026")
    assert not row.ok and "2027" in row.problem
    assert not error.value.retryable   # 內容不對，重試也一樣


def test_wrong_year_does_not_overwrite_good_data(raw_store, monkeypatch):
    """已經有正確的 2026 年資料，之後抓到別年 → 兩列都留著（只新增），讀到的還是好的那份。"""
    raw_store.put("holiday", "2026", {"queryYear": 2026, "data": [["2026-01-01", "元旦", ""]]})
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2027, "data": []}))

    with pytest.raises(RuntimeError):
        task.crawl_holiday(2026)

    assert [r.ok for r in raw_store.history("holiday", "2026")] == [True, False]
    assert repository.read_raw("holiday", "2026")["body"]["queryYear"] == 2026


def test_refetch_keeps_old_rows_and_reads_newest(raw_store, monkeypatch):
    """同一個 raw_key 抓兩次都成功 → 兩列都留，讀到的是後抓的。"""
    raw_store.put("holiday", "2026", {"queryYear": 2026, "data": []}, fetched_at="2026-01-02T21:00:00+08:00")
    monkeypatch.setattr(client, "fetch_holiday",
                        lambda year: fake_response({"queryYear": 2026, "data": [["2026-01-01", "元旦", ""]]}))

    task.crawl_holiday(2026)

    assert len(raw_store.history("holiday", "2026")) == 2
    assert repository.read_raw("holiday", "2026")["body"]["data"] == [["2026-01-01", "元旦", ""]]


@pytest.mark.parametrize(("status", "retryable"), [(503, True), (429, True), (404, False)])
def test_http_error_retryable(raw_store, monkeypatch, status, retryable):
    """伺服器錯誤、請求太多 → 可以重試；404 這類 → 不重試。"""
    resp = fake_response({"queryYear": 2026, "data": []})
    resp.status_code = status
    monkeypatch.setattr(client, "fetch_holiday", lambda year: resp)

    with pytest.raises(repository.FetchError) as error:
        task.crawl_holiday(2026)
    assert error.value.retryable is retryable


def test_announcement_detail_empty_data_saved_as_failure(raw_store, monkeypatch):
    """全文查不到那則公告（data 空的）→ 存成失敗，下次還會再抓。"""
    monkeypatch.setattr(client, "fetch_announcement_detail",
                        lambda *args: fake_response({"code": 200, "message": "查無資料", "result": {"data": []}}))

    with pytest.raises(RuntimeError, match="查無這則公告"):
        task.crawl_announcement_detail("9908", "20261102", 1, "t05st01_detail")

    assert not raw_store.last("announcement_detail", "9908_20261102_1").ok


def test_announcement_detail_saved(raw_store, monkeypatch):
    monkeypatch.setattr(client, "fetch_announcement_detail",
                        lambda *args: fake_response({"code": 200, "result": {"data": [["1", "115/11/05"]]}}))

    assert task.crawl_announcement_detail("9908", "20261102", 1, "t05st01_detail") == "raw_announcement_detail/9908_20261102_1"


@pytest.mark.parametrize(("now", "saved_ok"), [("2026-11-13T19:59:00", False), ("2026-11-13T20:00:00", True)])
def test_notice_before_published_saved_as_failure(raw_store, monkeypatch, now, saved_ok):
    """當天 20:00 以前抓的注意股：空名單不是「沒人被列」，存成失敗；20:00 以後才算。"""
    monkeypatch.setattr(date_utils, "now", lambda: datetime.datetime.fromisoformat(now).replace(tzinfo=date_utils.TAIPEI))
    monkeypatch.setattr(client, "fetch_notice", lambda day: fake_response({"stat": "OK", "data": []}))

    if saved_ok:
        task.crawl_notice("20261113")
    else:
        with pytest.raises(repository.FetchError, match="還沒公布完整"):
            task.crawl_notice("20261113")
    assert raw_store.last("notice", "20261113").ok is saved_ok
