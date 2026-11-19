"""解析工具：去 HTML、文字轉數字、中文數字、檢查欄位、把一份 raw 的資料列變成表。給 transformer.py 用。"""
import datetime

import pandas as pd

CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def strip_html(s: pd.Series) -> pd.Series:
    """去掉 HTML 標籤。"""
    return s.str.replace(r"<[^>]+>", "", regex=True).str.strip()


def to_number(s: pd.Series) -> pd.Series:
    """'1,450.00' → 1450.0；'-----' 或空字串 → NaN；其他看不懂的值直接報錯。"""
    s = s.str.replace(",", "").str.strip()
    s = s.mask(s.str.fullmatch(r"-*"))
    return pd.to_numeric(s)


def chinese_number(text: str) -> int:
    """一～九十九 → int。

    >>> chinese_number("十"), chinese_number("十三"), chinese_number("二十五")
    (10, 13, 25)
    """
    if "十" not in text:
        return CHINESE_DIGITS[text]
    tens, _, ones = text.partition("十")
    return CHINESE_DIGITS.get(tens, 1) * 10 + CHINESE_DIGITS.get(ones, 0)


def require_columns(df: pd.DataFrame, needed: list[str], name: str) -> pd.DataFrame:
    """只留 needed 這幾欄；少了任何一欄就報錯（上游改版，不能默默變成空值）。

    用欄名取，不是用位置：官方多加欄位、調整順序都不影響。
    """
    missing = [col for col in needed if col not in df.columns]
    if missing:
        raise ValueError(f"{name}少了欄位：{missing}（回傳的欄位：{list(df.columns)}）")
    return df[needed]


def twse_rows_to_frame(raw: dict, fields: list[str], name: str) -> pd.DataFrame:
    """證交所的回應 {"fields": [...], "data": [[...], ...]} → 只留 fields 這幾欄的表；沒有資料就是空表。"""
    body = raw["body"]
    if not body.get("data"):
        return pd.DataFrame(columns=fields)
    return require_columns(pd.DataFrame(body["data"], columns=body.get("fields")), fields, name)


def mops_rows_to_frame(raw: dict, titles: list[str], name: str, prepare=None) -> pd.DataFrame:
    """觀測站的回應 {"result": {"titles": [...], "data": [[...], ...]}} → 只留 titles 這幾欄的表；沒有資料就是空表。

    欄名在 titles（依位置對應）。prepare：取欄位之前先調整欄位，例如把存託憑證的欄位換成一般公司的。
    """
    result = raw["body"].get("result") or {}
    if not result.get("data"):
        return pd.DataFrame(columns=titles)
    df = pd.DataFrame(result["data"], columns=[t["main"] for t in result["titles"]])
    if prepare is not None:
        df = prepare(df)
    return require_columns(df, titles, name)


def fetched_at(raw: dict) -> datetime.datetime:
    """raw 的抓取時間（帶時區，可直接比較先後）。"""
    return datetime.datetime.fromisoformat(raw["meta"]["fetched_at"])
