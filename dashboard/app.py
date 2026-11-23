"""注意股，還差幾步？Streamlit 網頁。只打資料 API（api/），不直接連資料庫。

前置：API 要先跑著
    uv run uvicorn api.main:app --port 8001
啟動（專案根目錄）
    uv run streamlit run dashboard/app.py
    # 瀏覽器打開 http://localhost:8502（設定在 .streamlit/config.toml）

這個檔案只做三件事：讀網址參數、打 API（加 cache）、把 render.py 做好的 HTML 交給 st.html 顯示。
畫面用 Jinja2 範本（templates/）＋ style.css 做成，不是 Streamlit 內建的元件。換頁都用網址參數：
    ?date=20261120&tab=one&only=listed   總覽：交易日、分頁、「只看」篩選
    ?q=2330                              總覽只留代號或名稱符合的（搜尋框）
    ?stock=1795&date=20261120            個股頁
Streamlit 每次載入都「整支腳本從頭跑」，所以打 API 的函式都加 cache，60 秒內同樣的參數不重打。
"""
import datetime

import streamlit as st

from common.date_utils import parse_yyyymmdd_strict
from dashboard import client, render


# ---------- 打 API（加 cache） ----------
@st.cache_data(ttl=60, show_spinner=False)
def load_dates() -> list[datetime.date]:
    return client.dates()


@st.cache_data(ttl=60, show_spinner=False)
def load_cards(day: datetime.date | None) -> dict:
    return client.cards(day)


@st.cache_data(ttl=60, show_spinner=False)
def load_stock(stock_id: str, day: datetime.date | None) -> dict:
    return client.stock(stock_id, day)


def parse_date(text: str | None) -> datetime.date | None:
    """網址上的 ?date=：只接受 8 位數字的 20261120（和專案其他地方一樣）；沒給為 None，其他寫法丟 ValueError。

    >>> parse_date("20261110"), parse_date(None)
    (datetime.date(2026, 11, 10), None)
    >>> parse_date("2026-11-10")
    Traceback (most recent call last):
    ValueError: 日期要寫成 8 位數字，例如 20261120
    """
    return parse_yyyymmdd_strict(text) if text else None


def page_html(params) -> str:
    """依網址參數畫出整頁的 HTML。API 連不上、找不到資料，都變成錯誤頁。"""
    try:
        requested = parse_date(params.get("date"))
    except ValueError:
        return render.error_html(f"看不懂日期「{params.get('date')}」；請寫成 20261120。")
    try:
        stock_id = params.get("stock")
        if stock_id:
            back_params = {key: params.get(key, "") for key in ("tab", "only", "q")}   # 從哪個分頁、篩選、搜尋點進來的
            return render.stock_html(load_stock(stock_id, requested), requested, back_params)
        return render.overview_html(load_cards(requested), load_dates(), requested,
                                    params.get("tab", "all"), params.get("only", ""), params.get("q", ""))
    except client.ApiError as exc:
        return render.error_html(exc.message)


def main() -> None:
    st.set_page_config(page_title="注意股，還差幾步？", layout="wide", initial_sidebar_state="collapsed")
    # CSS 用 st.markdown 送：st.html 會把 <style> 拿掉
    st.markdown(render.style_html(), unsafe_allow_html=True)
    st.html(page_html(st.query_params))


if __name__ == "__main__":
    main()
