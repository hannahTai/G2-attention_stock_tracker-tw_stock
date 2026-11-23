"""dashboard/app.py：用 FastAPI 的 TestClient 真的打一次網頁，API 換成假資料（不用開 uvicorn）。

畫面的 HTML 細節在 test_dashboard_render.py 測；這裡只確認網址參數會走到對的頁面、不會出錯、CSS 拿得到。
"""
import fastapi.testclient
import pytest

from dashboard import app as dashboard_app
from dashboard import client
from tests.web_fixtures import CARDS, DAY, card_of, stock_detail


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


@pytest.fixture
def web():
    return fastapi.testclient.TestClient(dashboard_app.app)


def test_pages_are_html(web):
    for url in ("/", "/?stock=2222", "/?date=20261120&tab=one"):
        resp = web.get(url)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html"), url


def test_url_parameters_pick_the_page(web):
    assert 'class="tabs"' in web.get("/").text
    assert 'class="hero' in web.get("/?stock=2222&date=20261120").text


def test_css_is_served(web):
    css = web.get("/static/style.css")
    assert css.status_code == 200 and "--seal" in css.text


def test_unknown_stock_shows_error_page(web):
    resp = web.get("/?stock=9999")
    assert resp.status_code == 404 and "11/20 沒有 9999 的資料" in resp.text


def test_api_down_is_502(web, monkeypatch):
    def down(day=None):
        raise client.ApiError("資料 API 連不上")
    monkeypatch.setattr(client, "cards", down)
    resp = web.get("/")
    assert resp.status_code == 502 and "資料 API 連不上" in resp.text


def test_stock_page_title_has_the_stock(web):
    assert "<title>乙公司 2222・注意股，還差幾步？</title>" in web.get("/?stock=2222").text
    assert "<title>注意股，還差幾步？</title>" in web.get("/").text


def test_news_link_only_http(web, monkeypatch):
    """新聞連結是外部來的：javascript: 這類不放成連結（只顯示標題），點了不會執行程式。"""
    def stock(stock_id, day=None):
        detail = stock_detail(card_of("2222"))
        detail["news"] = [{"title": "壞連結", "link": "javascript:alert(1)", "publisher": "X", "news_date": None},
                          {"title": "好連結", "link": "https://udn.com/1", "publisher": "UDN", "news_date": None}]
        return detail
    monkeypatch.setattr(client, "stock", stock)
    page = web.get("/?stock=2222").text
    assert "javascript:" not in page and 'href="https://udn.com/1"' in page and "壞連結" in page


def test_date_only_accepts_eight_digits(web):
    for text in ("abc", "2026-11-20", "2026/11/20", "1151120", "20261131"):
        resp = web.get("/", params={"date": text})
        assert resp.status_code == 400 and "請寫成 20261120" in resp.text, text


def test_unexpected_error_shows_error_page(monkeypatch):
    """沒預期到的錯誤（例如 API 少了欄位）：500、錯誤頁，不把程式的錯誤訊息給使用者看。"""
    def broken(day=None):
        raise KeyError("trade_date")
    monkeypatch.setattr(client, "cards", broken)
    resp = fastapi.testclient.TestClient(dashboard_app.app, raise_server_exceptions=False).get("/")
    assert resp.status_code == 500 and "網頁出錯了，請稍後再試。" in resp.text and "KeyError" not in resp.text


def test_no_api_docs_on_the_web_page(web):
    assert web.get("/docs").status_code == 404
