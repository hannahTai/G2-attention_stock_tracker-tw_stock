"""api/ 的端點：回 JSON、日期找最近的交易日、錯誤訊息、健康檢查。不連資料庫（queries 換成假資料）。"""
import contextlib
import datetime

import fastapi.testclient
import pytest
import sqlalchemy
import sqlalchemy.exc

from api import main, queries, routes
from tests.web_fixtures import ANNOUNCEMENTS, CARDS, DAY, TRADING_DAYS, card_of, stats_response


@pytest.fixture
def client(monkeypatch):
    class FakeEngine:
        def connect(self):
            return contextlib.nullcontext(None)

    def latest(conn, on_or_before=None):
        if on_or_before is None or on_or_before >= DAY:
            return DAY
        return None   # 假資料只有 DAY 這一天

    monkeypatch.setattr(routes, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(queries, "latest_trade_date", latest)
    monkeypatch.setattr(queries, "recent_trade_dates", lambda conn: [DAY])
    monkeypatch.setattr(queries, "cards", lambda conn, day: [dict(c) for c in CARDS])
    monkeypatch.setattr(queries, "card", lambda conn, stock_id, day: card_of(stock_id))
    monkeypatch.setattr(queries, "counted_dates",
                        lambda conn, stock_id, day: {"streak3": [datetime.date(2026, 11, 19), DAY]})
    monkeypatch.setattr(queries, "trading_days", lambda conn, day, count=30: list(TRADING_DAYS))
    monkeypatch.setattr(queries, "notice_history",
                        lambda conn, stock_id, start, end: [{"trade_date": DAY, "clauses": "1", "is_counted": 1}])
    monkeypatch.setattr(queries, "punish_history", lambda conn, stock_id, start, end: [])
    monkeypatch.setattr(queries, "announcements", lambda conn, stock_id, start, end: [dict(a) for a in ANNOUNCEMENTS])
    monkeypatch.setattr(queries, "stock_news", lambda conn, stock_id, start, end: [])
    monkeypatch.setattr(queries, "news_by_ids", lambda conn, news_ids: [])
    return fastapi.testclient.TestClient(main.app)


def test_cards_returns_every_row_as_json(client):
    body = client.get("/cards").json()
    assert body["trade_date"] == "2026-11-20"
    assert [c["stock_id"] for c in body["cards"]] == ["1111", "2222", "3333", "4444", "5555"]
    assert body["cards"][0]["close_price"] == 31.5 and body["cards"][1]["next_listed_date"] == "2026-11-23"


def test_dates(client):
    assert client.get("/dates").json() == {"dates": ["2026-11-20"]}


def test_non_trading_day_snaps_to_latest(client):
    assert client.get("/cards", params={"date": "20261121"}).json()["trade_date"] == "2026-11-20"


def test_date_before_any_data_is_404(client):
    response = client.get("/cards", params={"date": "20200101"})
    assert response.status_code == 404 and "以前沒有資料" in response.json()["detail"]


def test_bad_date_is_422_in_chinese(client):
    for value in ("not-a-date", "2026-11-20", "1151120", "20261131"):   # 只接受 8 位數字、存在的日子
        response = client.get("/cards", params={"date": value})
        assert response.status_code == 422, value
        assert "請寫成 8 位數字，例如 20261120" in response.json()["detail"]


def test_eight_digit_date_works(client):
    response = client.get("/stocks/2222", params={"date": "20261120"})
    assert response.status_code == 200 and response.json()["card"]["stock_name"] == "乙公司"


def test_stock_returns_everything_the_page_needs(client):
    body = client.get("/stocks/2222").json()
    assert set(body) == {"trade_date", "card", "counted_dates", "trading_days", "notice_history",
                         "punish_history", "announcements", "ai_summary", "news"}
    assert body["counted_dates"] == {"streak3": ["2026-11-19", "2026-11-20"]}
    assert body["announcements"][0]["announce_time"] == "15:26:00"
    assert body["ai_summary"] is None and body["news"] == []   # 還沒寫摘要、沒有新聞


def test_stock_ai_summary_comes_with_its_news_sources(client, monkeypatch):
    """卡片 AI 摘要第三句用到的新聞：標題、連結從資料庫查（照 news_ids 的順序），不用 LLM 打的字。"""
    monkeypatch.setattr(queries, "card", lambda conn, stock_id, day: {
        **card_of(stock_id), "ai_summary": "因累積漲幅太大被列注意（新聞 1）", "ai_summary_news_ids": ["n1"],
        "ai_summary_at": datetime.datetime(2026, 11, 20, 19, 5)})
    monkeypatch.setattr(queries, "news_by_ids", lambda conn, news_ids: [
        {"news_id": n, "title": "健策營收創高", "link": "https://udn.com/1", "publisher": "UDN"} for n in news_ids])
    body = client.get("/stocks/2222").json()
    assert body["ai_summary"]["summary"].startswith("因累積漲幅太大")
    assert [s["title"] for s in body["ai_summary"]["sources"]] == ["健策營收創高"]
    assert body["ai_summary"]["summarized_at"] == "2026-11-20T19:05:00"


def test_stats_returns_everything_the_page_needs(client, monkeypatch):
    """統計頁：每一塊都有，產業的期間從近 30 個營業日的第一天起；新制後已結束的處置從 8/10 起算。"""
    fake = stats_response()
    asked = {}
    monkeypatch.setattr(queries, "next_trading_days", lambda conn, day: fake["next_trading_days"])
    monkeypatch.setattr(queries, "first_full_history_day", lambda conn: datetime.date(2026, 6, 12))
    monkeypatch.setattr(queries, "daily_counts",
                        lambda conn, start, day: asked.setdefault("daily_start", start) and fake["daily"])
    monkeypatch.setattr(queries, "new_punish",
                        lambda conn, start, day, period: asked.setdefault("punish", (start, period)) and fake["new_punish"])
    monkeypatch.setattr(queries, "punish_lengths",
                        lambda conn, since, day: asked.setdefault("since", since) and fake["punish_lengths"])
    monkeypatch.setattr(queries, "state_totals", lambda conn, start, day: fake["state_totals"])
    monkeypatch.setattr(queries, "trigger_clauses",
                        lambda conn, start, day: asked.setdefault("trigger_start", start) and fake["trigger_clauses"])
    monkeypatch.setattr(queries, "industry_counts",
                        lambda conn, start, day: asked.setdefault("start", start) and fake["industries"])
    body = client.get("/stats").json()
    assert set(body) == {"trade_date", "next_trading_days", "history", "history_start", "daily", "period", "new_punish",
                         "punish_lengths", "trigger_clauses", "state_totals", "industries"}
    assert body["next_trading_days"][0] == "2026-11-23" and body["daily"][-1]["clause13"] == 5
    # 預設近 30 個交易日（假資料只有 3 天：從 TRADING_DAYS[0] 起）、新增處置每週；全部的期間都一樣
    start = TRADING_DAYS[0]
    assert asked == {"daily_start": start, "punish": (start, "week"), "trigger_start": start, "since": start, "start": start}
    assert client.get("/stats", params={"period": "month"}).status_code == 422   # 只有 day、week
    assert body["history_start"] == TRADING_DAYS[0].isoformat()


def test_unknown_stock_is_404(client):
    response = client.get("/stocks/9999")
    assert response.status_code == 404 and "沒有 9999 的資料" in response.json()["detail"]


def test_unexpected_error_hides_details(client, monkeypatch):
    def broken(conn, day):
        raise RuntimeError("password=secret")

    monkeypatch.setattr(queries, "cards", broken)
    response = fastapi.testclient.TestClient(main.app, raise_server_exceptions=False).get("/cards")
    assert response.status_code == 500 and "secret" not in response.text


def test_health_hides_error_details(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("password=secret"))

    monkeypatch.setattr(routes, "get_engine", lambda: BrokenEngine())
    body = fastapi.testclient.TestClient(main.app).get("/health").json()
    assert body == {"status": "degraded", "database": "error"}
