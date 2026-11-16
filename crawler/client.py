"""client 層：只負責跟 API 拿資料，回傳原始回應，不整理、不存檔（課程補充 J）。存進 raw 表的是 task.py。

xxx_stock_ids()、company_ids()、watchlist_date()：從回應裡取出 producer、task 要用的值，API 格式只有這裡知道。

  證交所：注意股、處置股、注意累計名單、開休市日期、每日市場成交、上市公司清單
  公開資訊觀測站：公司重大訊息（一家公司一個月一個請求）、重大訊息全文（一則一個請求）
"""
import calendar
import re

import pandas as pd
import requests

from common import date_utils
from common.config import MOPS_BASE, TWSE_BASE, TWSE_OPENAPI_BASE
from common.date_utils import DATE_FMT, roc_to_date
from crawler.http_utils import http_get, http_post


# ==================== 證交所 ====================
def fetch_notice(date_yyyymmdd: str) -> requests.Response:
    """注意股，某一天（YYYYMMDD）。沒開市也回 stat="OK"，只是 data 為空。"""
    return http_get(f"{TWSE_BASE}/announcement/notice", {
        "response": "json", "querytype": "1", "startDate": date_yyyymmdd, "endDate": date_yyyymmdd, "sortKind": "DATE",
    })


def notice_stock_ids(body) -> set[str]:
    """注意股回應裡的證券代號（data 每列第 2 欄）。"""
    return {row[1].strip() for row in body.get("data") or []}


def fetch_punish(date_yyyymmdd: str) -> requests.Response:
    """處置股，某一天（YYYYMMDD）處置中＋當天公布的處置。

    篩的是處置期間；當天公布的下一個交易日才開始，連假可能隔 10 天左右，所以結束日多帶 14 天。
    """
    return http_get(f"{TWSE_BASE}/announcement/punish", {
        "response": "json", "startDate": date_yyyymmdd, "endDate": (pd.Timestamp(date_yyyymmdd) + pd.Timedelta(days=14)).strftime("%Y%m%d"),
        "querytype": "3", "stockNo": "", "selectType": "", "proceType": "", "remarkType": "",
        "sortKind": "DATE",
    })


def punish_stock_ids(body) -> set[str]:
    """處置股回應裡的證券代號（data 每列第 3 欄）。"""
    return {row[2].strip() for row in body.get("data") or []}


def fetch_punish_watchlist() -> requests.Response:
    """注意累計可能達處置標準名單，只有最新一天，不接受日期參數。"""
    return http_get(f"{TWSE_BASE}/announcement/notetrans", {"response": "json"})


def watchlist_date(body) -> str | None:
    """從注意累計名單的標題取出資料日期（YYYYMMDD），找不到就回傳 None。

    標題例如「115年11月12日 公布注意累計次數可能達處置標準之有價證券一覽表」
    """
    if not isinstance(body, dict):   # 被擋時回傳的是 HTML 文字，不是 dict
        return None

    found = re.search(r"\d+年\d+月\d+日", body.get("title", ""))
    if found is None:
        return None

    roc_text = found.group()             # "115年11月12日"
    data_date = roc_to_date(roc_text)    # datetime.date(2026, 11, 12)
    return data_date.strftime(DATE_FMT)  # "20261112"


def fetch_holiday(year: int) -> requests.Response:
    """開休市日期，某一年（西元）。不含颱風等臨時休市。

    用 date（那年任一天）指定年份；queryYear 會被忽略，不帶 date 會回傳最新公布的年份。
    """
    return http_get(f"{TWSE_BASE}/holidaySchedule/holidaySchedule",
                    {"response": "json", "date": f"{year}0101"})


def fetch_market_daily(month_yyyymm: str) -> requests.Response:
    """每日市場成交，某個月（YYYYMM）。每一列是一個有交易的日子。"""
    return http_get(f"{TWSE_BASE}/afterTrading/FMTQIK", {"response": "json", "date": f"{month_yyyymm}01"})


def fetch_companies() -> requests.Response:
    """上市公司清單（OpenAPI），只有當下這一份。"""
    return http_get(f"{TWSE_OPENAPI_BASE}/opendata/t187ap03_L", {})


def company_ids(body) -> set[str]:
    """上市公司清單裡的公司代號。"""
    return {company["公司代號"].strip() for company in body}


# ==================== 公開資訊觀測站 ====================
def fetch_announcements(stock_id: str, year: int, month: int) -> requests.Response:
    """公司重大訊息，一家公司一個月（西元年月）。本月只查到今天。"""
    today = date_utils.today()
    if (year, month) == (today.year, today.month):
        last_day = today.day                              # 本月：查到今天
    else:
        last_day = calendar.monthrange(year, month)[1]    # 其他月：查到月底（monthrange 回傳 (星期幾, 天數)）

    return http_post(f"{MOPS_BASE}/t05st01", {
        "companyId": stock_id, "year": str(year - 1911), "month": str(month),
        "firstDay": "1", "lastDay": str(last_day),
    })


def announcement_ids(body) -> list[tuple[str, str, int]]:
    """重大訊息回應裡每則公告的 (公司代號, 輸入日期 YYYYMMDD, 序號)，查全文要用。

    取自每列最後一欄「詳細資料」，例如
    {"parameters": {"marketKind": "sii", "enterDate": "1151102", "serialNumber": "1", "companyId": "9908"}, ...}
    """
    ids = []
    for row in (body.get("result") or {}).get("data") or []:
        params = row[-1]["parameters"]
        roc = params["enterDate"]                                   # "1151102"：民國年＋月日
        enter_yyyymmdd = f"{int(roc[:-4]) + 1911}{roc[-4:]}"        # "20261102"
        ids.append((params["companyId"].strip(), enter_yyyymmdd, int(params["serialNumber"])))
    return ids


def fetch_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int) -> requests.Response:
    """一則重大訊息的全文（主旨、符合條款、事實發生日、說明）。

    用輸入日期（民國年月日連寫）＋當天序號指定哪一則；marketKind sii＝上市。
    """
    roc = f"{int(enter_yyyymmdd[:4]) - 1911}{enter_yyyymmdd[4:]}"   # "20261102" → "1151102"
    return http_post(f"{MOPS_BASE}/t05st01_detail", {
        "marketKind": "sii", "enterDate": roc, "serialNumber": str(serial_number), "companyId": stock_id,
    })
