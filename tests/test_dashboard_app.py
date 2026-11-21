"""dashboard/app.py：用 Streamlit 的 AppTest 真的跑一次網頁，API 換成假資料（不用開 uvicorn）。

畫面的 HTML 細節在 test_dashboard_render.py 測；這裡只確認網址參數會走到對的頁面、不會出錯。
"""
import pathlib

import pytest
import streamlit as st
import streamlit.testing.v1

from dashboard import app as dashboard_app
from dashboard import client
from tests.web_fixtures import CARDS, DAY, card_of, stock_detail

APP = str(pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "app.py")


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    def stock(stock_id, day=None):
        card = card_of(stock_id)
        if card is None:
            raise client.ApiError(f"11/20 沒有 {stock_id} 的資料", 404)
        return stock_detail(card)

    monkeypatch.setattr(client, "dates", lambda: [DAY])
    monkeypatch.setattr(client, "cards", lambda day=None: {"trade_date": DAY, "cards": [dict(c) for c in CARDS]})
    monkeypatch.setattr(client, "stock", stock)
    st.cache_data.clear()


def test_app_runs_without_errors():
    for params in ({}, {"stock": "2222"}, {"date": "20261120", "tab": "one"}):
        at = streamlit.testing.v1.AppTest.from_file(APP, default_timeout=30)
        for key, value in params.items():
            at.query_params[key] = value
        at.run()
        assert not at.exception, params


def test_url_parameters_pick_the_page():
    assert 'class="tabs"' in dashboard_app.page_html({})
    assert 'class="hero' in dashboard_app.page_html({"stock": "2222", "date": "20261120"})


def test_unknown_stock_shows_error_page():
    assert "11/20 沒有 9999 的資料" in dashboard_app.page_html({"stock": "9999"})


def test_bad_date_shows_error_page():
    assert "看不懂日期「abc」" in dashboard_app.page_html({"date": "abc"})


def test_date_only_accepts_eight_digits():
    for text in ("2026-11-20", "2026/11/20", "1151120", "20261131"):
        at = streamlit.testing.v1.AppTest.from_file(APP, default_timeout=30)
        at.query_params["date"] = text
        at.run()
        assert not at.exception, text
        assert "請寫成 20261120" in at.get("html")[0].proto.body, text
