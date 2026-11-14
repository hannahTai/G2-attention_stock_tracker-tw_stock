"""transformer 層：只負責把 raw 的原始資料整理成 stage 表的樣子（課程補充 J）。

不知道資料怎麼抓來的、整理完存到哪裡（存的是 task.py）。每個資料集兩步：
  xxx_to_frame：把多份 raw 的資料列攤平成一張表
  clean_xxx：清理、轉型別、去重複（民國轉西元、去 HTML、文字轉數字）
只做清洗與型別轉換，不做判斷：官方公告了什麼就留什麼。
"""
import datetime
import re
import warnings

import pandas as pd

from common.date_utils import roc_compact_to_dates, roc_to_date, roc_to_dates
from transform.parsing import chinese_number, fetched_at, require_columns, rows_to_frame, strip_html, to_number


# ---------- 注意股 → stage_notice、stage_notice_clause ----------
# 用得到的官方欄位（沒用到：編號＝全部是 1、累計次數＝查詢回傳的筆數，不是個股累計）
NOTICE_FIELDS = ["證券代號", "證券名稱", "注意交易資訊", "日期", "收盤價", "本益比"]
NOTICE_COLUMNS = ["trade_date", "stock_id", "stock_name", "reason_text", "close_price", "pe_ratio"]
NOTICE_CLAUSE_COLUMNS = ["trade_date", "stock_id", "clause"]


def notice_to_frame(raws: list[dict]) -> pd.DataFrame:
    return rows_to_frame(raws, NOTICE_FIELDS, "注意股")


def clean_notice(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (stage_notice, stage_notice_clause)"""
    if df.empty:
        return pd.DataFrame(columns=NOTICE_COLUMNS), pd.DataFrame(columns=NOTICE_CLAUSE_COLUMNS)

    notices = pd.DataFrame({
        "trade_date": roc_to_dates(df["日期"]),
        "stock_id": df["證券代號"].str.strip(),
        "stock_name": df["證券名稱"].str.strip(),
        "reason_text": strip_html(df["注意交易資訊"]),
        "close_price": to_number(df["收盤價"]),
        "pe_ratio": to_number(df["本益比"]),
    })

    # 每列抓出所有「第X款」，一個款次一列；index 第 0 層是原本的列號
    found = notices["reason_text"].str.extractall(r"第([一二三四五六七八九十]+)款")[0]
    row_ids = found.index.get_level_values(0)
    if row_ids.nunique() < len(notices):
        raise ValueError("有注意股找不到款次，格式可能改了")

    clauses = notices.loc[row_ids, ["trade_date", "stock_id"]].copy()
    clauses["clause"] = found.map(chinese_number).to_numpy()
    clauses = clauses.drop_duplicates().sort_values(NOTICE_CLAUSE_COLUMNS).reset_index(drop=True)
    return notices, clauses


# ---------- 處置股 → stage_punish ----------
# 同一筆公告會出現在好幾天的 raw → 依（公布日期, 代號）去重，留最後抓到的
# 同一份 raw 裡（公布日期, 代號）重複且內容不同 → 同一天有兩則處置，去重只會留一則，所以發出警告
# 用得到的官方欄位（沒用到：編號、累計＝隨查詢區間變動、備註＝只有連結）
# 公布日期偶爾帶 *（意義未確認），存成 has_star_mark
PUNISH_FIELDS = ["公布日期", "證券代號", "證券名稱", "處置條件", "處置起迄時間", "處置措施", "處置內容"]


def punish_to_frame(raws: list[dict]) -> pd.DataFrame:
    raws = sorted(raws, key=fetched_at)   # 舊 → 新
    return rows_to_frame(raws, PUNISH_FIELDS, "處置股", extra={"fetched_at": fetched_at})   # 用來分辨每列來自哪份 raw


def clean_punish(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_punish"""
    period = df["處置起迄時間"].str.extract(r"(\S+)～(\S+)")
    minutes = df["處置內容"].str.extract(r"約每([一二三四五六七八九十]+)分鐘")[0]   # 撮合間隔
    out = pd.DataFrame({
        "announce_date": roc_to_dates(df["公布日期"]),
        "stock_id": df["證券代號"].str.strip(),
        "stock_name": df["證券名稱"].str.strip(),
        "trigger_condition": df["處置條件"].str.strip(),
        "period_start": roc_to_dates(period[0]),
        "period_end": roc_to_dates(period[1]),
        "measure_type": df["處置措施"].str.strip(),
        "match_interval_minutes": minutes.dropna().map(chinese_number).astype("Int64"),
        "content": df["處置內容"].str.strip(),
        "has_star_mark": df["公布日期"].str.startswith("*"),
    })
    same_raw = out.assign(fetched_at=df["fetched_at"]).drop_duplicates()   # 完全相同的列不算
    conflicts = same_raw[same_raw.duplicated(["fetched_at", "announce_date", "stock_id"], keep=False)]
    if not conflicts.empty:
        listed = conflicts[["announce_date", "stock_id", "measure_type"]].drop_duplicates().to_string(index=False)
        warnings.warn(f"處置股：同一天同一檔有多則處置，只會留下一則，請確認：\n{listed}")
    out = out.drop_duplicates(["announce_date", "stock_id"], keep="last")
    return out.sort_values(["announce_date", "stock_id"]).reset_index(drop=True)


# ---------- 注意累計可能達處置標準名單 → stage_punish_watchlist ----------
WATCHLIST_PROGRESS = "近期達本公司「公布注意交易資訊」標準之情形"
PUNISH_WATCHLIST_FIELDS = ["證券代號", "證券名稱", WATCHLIST_PROGRESS]   # 沒用到：編號


def title_date(raw: dict):
    """從 title（例如「115年11月06日 公布…」）取出資料日期。"""
    return roc_to_date(re.search(r"\d+年\d+月\d+日", raw["body"]["title"]).group())


def punish_watchlist_to_frame(raws: list[dict]) -> pd.DataFrame:
    return rows_to_frame(raws, PUNISH_WATCHLIST_FIELDS, "注意累計名單", extra={"data_date": title_date})


def clean_punish_watchlist(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_punish_watchlist"""
    progress = df[WATCHLIST_PROGRESS].str.replace("<br>", "\n")   # 多行情形換成換行
    out = pd.DataFrame({
        "data_date": df["data_date"],
        "stock_id": df["證券代號"].str.strip(),
        "stock_name": df["證券名稱"].str.strip(),
        "progress_text": strip_html(progress),
    })
    return out.drop_duplicates(["data_date", "stock_id"], keep="last").reset_index(drop=True)


# ---------- 每日市場成交 → stage_trading_day ----------
# 證交所收盤後才公布當天的市場成交；這個時間以後抓的才含當天
MARKET_DATA_READY = datetime.time(15, 0)

MARKET_DAILY_FIELDS = ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]


def market_daily_to_frame(raws: list[dict]) -> pd.DataFrame:
    return rows_to_frame(raws, MARKET_DAILY_FIELDS, "市場成交資訊")


def covered_through(fetched_time: str) -> datetime.date:
    """這份市場成交涵蓋到哪一天：收盤資料公布後（15:00）抓的含當天，之前抓的只到前一天。

    >>> covered_through("2026-11-13T21:00:00+08:00"), covered_through("2026-11-13T10:00:00+08:00")
    (datetime.date(2026, 11, 13), datetime.date(2026, 11, 12))
    """
    fetched = datetime.datetime.fromisoformat(fetched_time)
    if fetched.time() >= MARKET_DATA_READY:
        return fetched.date()
    return fetched.date() - datetime.timedelta(days=1)


def clean_trading_day(df: pd.DataFrame, month_yyyymm: str, through: datetime.date) -> pd.DataFrame:
    """→ stage_trading_day：這個月 1 號到 through（資料涵蓋到的那天）的每一天，一天一列。

    資料裡有的那天＝有交易（is_trading=1）；涵蓋範圍內但資料裡沒有＝沒交易（週末、休市、颱風假，is_trading=0）。
    through 之後的日子不寫：還不知道有沒有交易，analyze 看到沒有這列就不算那天。
    """
    trading = clean_trading_rows(df)
    first = pd.Period(month_yyyymm, "M").start_time.date()
    last = min(pd.Period(month_yyyymm, "M").end_time.date(), through)
    calendar = pd.DataFrame({"trade_date": pd.date_range(first, last).date}) if first <= last else trading[["trade_date"]]

    out = calendar.merge(trading, on="trade_date", how="outer")   # outer：資料比 through 新的那天也留著
    out.insert(1, "is_trading", out["trade_date"].isin(set(trading["trade_date"])))
    return out.sort_values("trade_date").reset_index(drop=True)


def clean_trading_rows(df: pd.DataFrame) -> pd.DataFrame:
    """市場成交的每一列（每列是一個有交易的日子），轉型、去重。"""
    out = pd.DataFrame({
        "trade_date": roc_to_dates(df["日期"]),
        "volume_shares": to_number(df["成交股數"]).astype("Int64"),
        "amount_twd": to_number(df["成交金額"]).astype("Int64"),
        "trade_count": to_number(df["成交筆數"]).astype("Int64"),
        "taiex_index": to_number(df["發行量加權股價指數"]),
        "taiex_change_points": to_number(df["漲跌點數"]),
    })
    return out.drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)


# ---------- 開休市日期 → stage_holiday ----------
HOLIDAY_FIELDS = ["日期", "名稱", "說明"]


def holiday_to_frame(raws: list[dict]) -> pd.DataFrame:
    return rows_to_frame(raws, HOLIDAY_FIELDS, "開休市日期")


def clean_holiday(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_holiday（照官方原樣，也含「開始交易日」這類有交易的日子）"""
    out = pd.DataFrame({
        "holiday_date": pd.to_datetime(df["日期"]).dt.date,   # 這支 API 是西元
        "holiday_name": df["名稱"].str.strip(),
        "description": df["說明"].str.strip(),
    })
    return out.drop_duplicates("holiday_date", keep="last").sort_values("holiday_date").reset_index(drop=True)


# ---------- 上市公司 → stage_company ----------
COMPANY_FIELDS = ["公司代號", "公司簡稱", "公司名稱", "產業別", "上市日期"]   # 其餘 28 個欄位沒用到


def clean_company(raw: dict | None) -> pd.DataFrame:
    """最新一份公司清單 → stage_company"""
    if raw and raw["body"]:
        df = require_columns(pd.DataFrame(raw["body"]), COMPANY_FIELDS, "上市公司")   # 每筆是 dict，欄名就是 key
    else:
        df = pd.DataFrame(columns=COMPANY_FIELDS)   # 還沒抓過：空表
    return pd.DataFrame({
        "company_id": df["公司代號"].str.strip(),
        "short_name": df["公司簡稱"].str.strip(),
        "full_name": df["公司名稱"].str.strip(),
        "industry_code": df["產業別"].str.strip(),
        "listed_date": pd.to_datetime(df["上市日期"], format="%Y%m%d").dt.date,
        "snapshot_date": pd.to_datetime(raw["meta"]["key"]).date() if raw else None,
    })


# ---------- 公司重大訊息 → stage_announcement ----------
# 格式和其他來源不同：{"code", "result": {"titles", "data"}}；code 406 = 那個月沒有公告
ANNOUNCEMENT_TITLES = ["公司代號", "公司名稱", "發言日期", "發言時間", "主旨", "詳細資料"]


def announcement_to_frame(raws: list[dict]) -> pd.DataFrame:
    frames = []
    for raw in raws:
        result = raw["body"].get("result") or {}
        if not result.get("data"):
            continue
        titles = [t["main"] for t in result["titles"]]   # 欄名在 titles，依位置對應
        df = pd.DataFrame(result["data"], columns=titles)
        frames.append(require_columns(df, ANNOUNCEMENT_TITLES, "重大訊息"))

    if not frames:
        return pd.DataFrame(columns=ANNOUNCEMENT_TITLES)
    return pd.concat(frames, ignore_index=True)


def clean_announcement(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_announcement

    序號是依「輸入日期」（enterDate）編的，不是依發言日期：
    同一天發言的兩則公告可能序號相同，所以主鍵用（公司, 輸入日期, 序號）。
    """
    keys = ["company_id", "enter_date", "serial_number"]
    params = df["詳細資料"].str["parameters"]
    out = pd.DataFrame({
        "company_id": df["公司代號"].str.strip(),
        "announce_date": roc_to_dates(df["發言日期"]),
        "enter_date": roc_compact_to_dates(params.str["enterDate"]),        # 例如 "1151102"
        "serial_number": params.str["serialNumber"].astype(int),            # 輸入日期當天第幾則
        "announce_time": df["發言時間"].str.strip(),
        "company_name": df["公司名稱"].str.strip(),
        "subject": df["主旨"].str.replace(r"\s*\n\s*", "", regex=True).str.strip(),  # 接回被換行切斷的主旨
    })
    return out.drop_duplicates(keys, keep="last").sort_values(keys).reset_index(drop=True)
