"""注意股，還差幾步？網頁（FastAPI＋Jinja2）。只打資料 API（api/），不直接連資料庫。

前置：API 要先跑著
    uv run uvicorn api.main:app --port 8001
啟動（專案根目錄）
    uv run uvicorn dashboard.app:app --port 8502
    # 瀏覽器打開 http://localhost:8502

這個檔案只做三件事：讀網址參數、打 API、把 render.py 做好的整頁 HTML 回傳給瀏覽器。
畫面用 Jinja2 範本（templates/）＋ static/style.css。換頁都用網址參數：
    ?date=20261120&tab=one&only=listed   總覽：交易日、分頁、「只看」篩選
    ?q=2330                              總覽只留代號或名稱符合的（搜尋框）
    ?stock=1795&date=20261120            個股頁
伺服器直接回完整的 HTML（CSS 在 <head>），瀏覽器換頁時舊的畫面留到新的出來才換，不會整頁閃一下空白。
API 在同一台、查詢很快（幾十毫秒），所以不另外加 cache。
"""
import datetime
import logging

import fastapi
import fastapi.responses
import fastapi.staticfiles

from common.date_utils import parse_yyyymmdd_strict
from dashboard import client, render

# 網頁不是 API：不開 /docs、/openapi.json（openapi_url=None 連 /docs、/redoc 一起關掉）
app = fastapi.FastAPI(openapi_url=None)
app.mount("/static", fastapi.staticfiles.StaticFiles(directory=render.HERE / "static"), name="static")


def parse_date(text: str | None) -> datetime.date | None:
    """網址上的 ?date=：只接受 8 位數字的 20261120（和專案其他地方一樣）；沒給為 None，其他寫法丟 ValueError。

    >>> parse_date("20261110"), parse_date(None)
    (datetime.date(2026, 11, 10), None)
    >>> parse_date("2026-11-10")
    Traceback (most recent call last):
    ValueError: 日期要寫成 8 位數字，例如 20261120
    """
    return parse_yyyymmdd_strict(text) if text else None


def page_html(params) -> tuple[str, int]:
    """依網址參數畫出整頁的 HTML，回傳 (HTML, HTTP 狀態碼)。
    網址的日期看不懂 400；API 找不到資料用 API 的狀態碼（例如 404）；API 連不上 502。都顯示錯誤頁。"""
    try:
        requested = parse_date(params.get("date"))
    except ValueError:
        return render.error_html(f"看不懂日期「{params.get('date')}」；請寫成 20261120。"), 400
    try:
        stock_id = params.get("stock")
        if stock_id:
            back_params = {key: params.get(key, "") for key in ("tab", "only", "q")}   # 從哪個分頁、篩選、搜尋點進來的
            return render.stock_html(client.stock(stock_id, requested), requested, back_params), 200
        return render.overview_html(client.cards(requested), client.dates(), requested,
                                    params.get("tab", "all"), params.get("only", ""), params.get("q", "")), 200
    except client.ApiError as exc:
        return render.error_html(exc.message), exc.status_code or 502


@app.exception_handler(Exception)
def unexpected_error(request: fastapi.Request, exc: Exception) -> fastapi.responses.HTMLResponse:
    """沒預期到的錯誤（例如 API 改了格式、範本寫錯）：記 log，顯示錯誤頁（500），不顯示程式的錯誤訊息。"""
    logging.getLogger(__name__).exception("網頁出錯：%s", request.url)
    return fastapi.responses.HTMLResponse(render.error_html("網頁出錯了，請稍後再試。"), status_code=500)


@app.get("/")
def page(request: fastapi.Request) -> fastapi.responses.HTMLResponse:
    """唯一的一頁：總覽或個股頁，由網址參數決定。"""
    html, status = page_html(request.query_params)
    return fastapi.responses.HTMLResponse(html, status_code=status)
