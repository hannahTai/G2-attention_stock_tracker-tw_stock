"""呼叫資料 API（api/）。網頁只打 API，不直接連資料庫：資料庫躲在 API 後面。

API 回的日期是 '2026-11-20'、時間是 '13:41:00' 這種字串，這裡換回 date、time，views.py 才能比大小、算日子。
"""
import datetime
import re
import urllib.parse

import requests

from common import config

DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_TEXT = re.compile(r"\d{2}:\d{2}:\d{2}")


class ApiError(Exception):
    """API 連不上或回錯誤；message 是可以直接顯示給使用者看的中文。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def restore_types(value):
    """把回應裡的 'YYYY-MM-DD' 換回 date、'HH:MM:SS' 換回 time（dict、list 裡的也換）。

    >>> restore_types({"trade_date": "2026-11-20", "days": ["2026-11-19"], "announce_time": "13:41:00"})
    {'trade_date': datetime.date(2026, 11, 20), 'days': [datetime.date(2026, 11, 19)], 'announce_time': datetime.time(13, 41)}
    """
    if isinstance(value, str) and DATE_TEXT.fullmatch(value):
        return datetime.date.fromisoformat(value)
    if isinstance(value, str) and TIME_TEXT.fullmatch(value):
        return datetime.time.fromisoformat(value)
    if isinstance(value, list):
        return [restore_types(v) for v in value]
    if isinstance(value, dict):
        return {k: restore_types(v) for k, v in value.items()}
    return value


def get(path: str, day: datetime.date | None = None) -> dict:
    """GET 一個端點；day 給了就帶 ?date=20261120。錯誤一律變成 ApiError。"""
    params = {"date": day.strftime("%Y%m%d")} if day else {}
    try:
        response = requests.get(config.API_URL + path, params=params, timeout=10)
    except requests.RequestException as exc:
        raise ApiError(f"連不上資料 API（{config.API_URL}），先啟動：uv run uvicorn api.main:app --port 8001") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise ApiError(detail if isinstance(detail, str) else f"資料 API 回應錯誤（{response.status_code}）",
                       response.status_code)
    return restore_types(response.json())


def dates() -> list[datetime.date]:
    """最近 30 個有算的交易日，新的在前。"""
    return get("/dates")["dates"]


def cards(day: datetime.date | None = None) -> dict:
    """{'trade_date': 實際的日子, 'cards': [app_card 一列一檔]}。"""
    return get("/cards", day)


def stock(stock_id: str, day: datetime.date | None = None) -> dict:
    """個股頁要的全部資料（見 api/routes.py 的 /stocks/{stock_id}）。"""
    return get(f"/stocks/{urllib.parse.quote(stock_id, safe='')}", day)
