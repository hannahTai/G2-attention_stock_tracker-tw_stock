"""日期工具：台北時間的今天、YYYYMMDD 字串、民國轉西元、日期區間。"""
import datetime
import re
import zoneinfo

import pandas as pd

DATE_FMT = "%Y%m%d"                        # 例如 20261106
TAIPEI = zoneinfo.ZoneInfo("Asia/Taipei")  # 「今天」以台北時間為準，不受機器時區影響


# ---------- 今天 ----------
def now() -> datetime.datetime:
    return datetime.datetime.now(TAIPEI)


def now_naive() -> datetime.datetime:
    """現在的台灣時間，不帶時區（寫進 TIMESTAMP 欄位用）。"""
    return now().replace(tzinfo=None)


def today() -> datetime.date:
    return now().date()


def today_yyyymmdd() -> str:
    """今天的日期字串，例如 "20261113"。"""
    return to_yyyymmdd(today())


def to_yyyymmdd(day: datetime.date) -> str:
    """
    >>> to_yyyymmdd(datetime.date(2026, 11, 13))
    '20261113'
    """
    return day.strftime(DATE_FMT)


def parse_yyyymmdd(text: str) -> datetime.date:
    """
    >>> parse_yyyymmdd("20261113")
    datetime.date(2026, 11, 13)
    """
    return datetime.datetime.strptime(text, DATE_FMT).date()


# ---------- 民國 ----------
def roc_to_date(text: str) -> datetime.date:
    """民國日期 → date，分隔符號不拘。

    >>> roc_to_date("115.11.06"), roc_to_date("115年11月6日")
    (datetime.date(2026, 11, 6), datetime.date(2026, 11, 6))
    """
    y, m, d = re.findall(r"\d+", text)
    return datetime.date(int(y) + 1911, int(m), int(d))


def roc_date_in(text: str) -> datetime.date | None:
    """文字裡第一個「115年11月12日」這種日期；沒有就回傳 None。

    >>> roc_date_in("115年11月12日 公布注意累計次數…"), roc_date_in("沒有日期")
    (datetime.date(2026, 11, 12), None)
    """
    found = re.search(r"\d+年\d+月\d+日", text)
    return roc_to_date(found.group()) if found else None


def roc_to_dates(s: pd.Series) -> pd.Series:
    """roc_to_date 的整欄版（比逐筆轉換快）。

    >>> roc_to_dates(pd.Series(["115.11.06", "*115/10/01"])).tolist()
    [datetime.date(2026, 11, 6), datetime.date(2026, 10, 1)]
    """
    ymd = s.str.extract(r"(\d+)\D+(\d+)\D+(\d+)").astype(int)
    return pd.to_datetime(pd.DataFrame({"year": ymd[0] + 1911, "month": ymd[1], "day": ymd[2]})).dt.date


def roc_compact_to_yyyymmdd(roc: str) -> str:
    """民國年月日連寫（觀測站的 enterDate）→ YYYYMMDD。後 4 碼是月日，前面是民國年（2 或 3 碼）。

    >>> roc_compact_to_yyyymmdd("1151102"), roc_compact_to_yyyymmdd("991231")
    ('20261102', '20101231')
    """
    return f"{int(roc[:-4]) + 1911}{roc[-4:]}"


def yyyymmdd_to_roc_compact(text: str) -> str:
    """
    >>> yyyymmdd_to_roc_compact("20261102")
    '1151102'
    """
    return f"{int(text[:4]) - 1911}{text[4:]}"


def roc_compact_to_dates(s: pd.Series) -> pd.Series:
    """roc_compact_to_yyyymmdd 的整欄版，轉成 date。

    >>> roc_compact_to_dates(pd.Series(["1151102", "991231"])).tolist()
    [datetime.date(2026, 11, 2), datetime.date(2010, 12, 31)]
    """
    return pd.to_datetime(s.map(roc_compact_to_yyyymmdd), format=DATE_FMT).dt.date


# ---------- 區間 ----------
def dates_between(start: str, end: str) -> list[str]:
    """start～end（YYYYMMDD）的每一天。

    >>> dates_between("20261112", "20261113")
    ['20261112', '20261113']
    """
    return pd.date_range(start, end).strftime(DATE_FMT).tolist()


def months_between(start: str, end: str) -> list[str]:
    """start～end（YYYYMMDD）經過的每個月。

    >>> months_between("20261230", "20270102")
    ['202612', '202701']
    """
    return pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="M").strftime("%Y%m").tolist()


def prev_month(month_yyyymm: str) -> str:
    """上個月，跨年也對；傳 YYYYMMDD 也可以（只看前 6 碼）。

    >>> prev_month("202611"), prev_month("20260115")
    ('202610', '202512')
    """
    return (pd.Period(f"{month_yyyymm[:4]}-{month_yyyymm[4:6]}", "M") - 1).strftime("%Y%m")


def days_before(date_yyyymmdd: str, days: int) -> str:
    """往前 days 天（YYYYMMDD），跨月、跨年也對。

    >>> days_before("20261103", 7)
    '20261027'
    """
    return to_yyyymmdd(parse_yyyymmdd(date_yyyymmdd) - datetime.timedelta(days=days))


def holiday_years(start: str, end: str) -> list[int]:
    """要抓、要整理哪幾年的開休市日期：期間經過的年份；期間到了 11 月以後，再加下一年。

    證交所 11 月起公布下一年的開休市日期。年底算「若再列注意」的處置日期會推到明年，要有明年的休市日。
    >>> holiday_years("20261001", "20261031"), holiday_years("20261001", "20261119")
    ([2026], [2026, 2027])
    """
    years = {int(m[:4]) for m in months_between(start, end)}
    if int(end[4:6]) >= 11:
        years.add(int(end[:4]) + 1)
    return sorted(years)
