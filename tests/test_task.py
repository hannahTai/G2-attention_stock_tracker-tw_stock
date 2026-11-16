"""task.py：抓完存進 raw 表前的檢查（用假的回應和記憶體裡的假 raw 表）。"""
import json

import pytest
import requests

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
    assert raw_store.rows[("holiday", "2026")].ok


def test_holiday_wrong_year_saved_as_failure(raw_store, monkeypatch):
    """API 回了別年（11 月起不帶 date 會回下一年）→ 存成失敗的列，不能當成 2026 年的資料。"""
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2027, "data": []}))

    with pytest.raises(RuntimeError, match="要 2026 年，回傳的是 2027 年"):
        task.crawl_holiday(2026)

    row = raw_store.rows[("holiday", "2026")]
    assert not row.ok and "2027" in row.problem


def test_wrong_year_does_not_overwrite_good_data(raw_store, monkeypatch):
    """已經有正確的 2026 年資料，之後抓到別年 → 好的資料留著。"""
    raw_store.put("holiday", "2026", {"queryYear": 2026, "data": [["2026-01-01", "元旦", ""]]})
    monkeypatch.setattr(client, "fetch_holiday", lambda year: fake_response({"queryYear": 2027, "data": []}))

    with pytest.raises(RuntimeError):
        task.crawl_holiday(2026)

    row = raw_store.rows[("holiday", "2026")]
    assert row.ok and json.loads(row.body)["queryYear"] == 2026


def test_announcement_detail_empty_data_saved_as_failure(raw_store, monkeypatch):
    """全文查不到那則公告（data 空的）→ 存成失敗，下次還會再抓。"""
    monkeypatch.setattr(client, "fetch_announcement_detail",
                        lambda *args: fake_response({"code": 200, "message": "查無資料", "result": {"data": []}}))

    with pytest.raises(RuntimeError, match="查無這則公告"):
        task.crawl_announcement_detail("9908", "20261102", 1)

    assert not raw_store.rows[("announcement_detail", "9908_20261102_1")].ok


def test_announcement_detail_saved(raw_store, monkeypatch):
    monkeypatch.setattr(client, "fetch_announcement_detail",
                        lambda *args: fake_response({"code": 200, "result": {"data": [["1", "115/11/05"]]}}))

    assert task.crawl_announcement_detail("9908", "20261102", 1) == "raw_announcement_detail/9908_20261102_1"
