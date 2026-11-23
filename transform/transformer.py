"""transformer 層：只負責把一份 raw 的原始資料整理成 stage 表的樣子，不知道資料怎麼抓來、存到哪裡。

每個資料集兩步：
  xxx_to_frame(raw)：把回應裡的資料列變成一張表，只留用得到的官方欄位
  clean_xxx(df)：清理、轉型別（民國轉西元、去 HTML、文字轉數字），欄位換成 stage 表的名字
只做清洗與型別轉換，不做判斷：官方公告了什麼就留什麼。
同一個主鍵出現好幾列時，寫進資料庫是後面的蓋掉前面的，所以排序用 kind="stable"，保持原本的先後。
"""
import datetime
import warnings

import pandas as pd
import trafilatura

from common.date_utils import DATE_FMT, TAIPEI, roc_compact_to_dates, roc_to_dates
from common.hash_utils import key_md5, url_md5
from crawler import client   # 從回應取值（表、日期、連結）和 crawler 用同一份，兩邊才不會挑到不同的東西
from transform.parsing import (
    chinese_number, mops_rows_to_frame, require_columns, strip_html, to_number, twse_rows_to_frame,
)

# 每張 stage 表的主鍵（寫入時用主鍵更新，見 transform/task.py）
STAGE_KEYS = {
    "stage_notice": ["trade_date", "stock_id"],
    "stage_notice_clause": ["trade_date", "stock_id", "clause"],
    "stage_notice_day": ["query_date"],
    "stage_punish": ["announce_date", "stock_id"],
    "stage_punish_day": ["query_date"],
    "stage_punish_watchlist": ["data_date", "stock_id"],
    "stage_trading_day": ["trade_date"],
    "stage_stock_price": ["trade_date", "stock_id"],
    "stage_holiday": ["holiday_date"],
    "stage_company": ["company_id"],
    "stage_announcement": ["company_id", "enter_date", "serial_number"],
    "stage_announcement_detail": ["company_id", "enter_date", "serial_number"],
    "stage_news": ["news_id"],
    "stage_stock_news": ["stock_id", "news_id"],
    "stage_news_detail": ["news_id"],
}


# ---------- 注意股 → stage_notice、stage_notice_clause ----------
# 用得到的官方欄位（沒用到：編號＝全部是 1、累計次數＝查詢回傳的筆數，不是個股累計）
NOTICE_FIELDS = ["證券代號", "證券名稱", "注意交易資訊", "日期", "收盤價", "本益比"]


def notice_to_frame(raw: dict) -> pd.DataFrame:
    return twse_rows_to_frame(raw, NOTICE_FIELDS, "注意股")


def clean_notice(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (stage_notice, stage_notice_clause)"""
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

    clauses = notices.loc[row_ids, ["trade_date", "stock_id"]].assign(clause=found.map(chinese_number).to_numpy())
    clauses = clauses.sort_values(STAGE_KEYS["stage_notice_clause"], kind="stable").reset_index(drop=True)
    return notices, clauses


# ---------- 處置股 → stage_punish ----------
# 同一則公告會出現在好幾天的 raw 裡：寫入時用主鍵（公布日期, 代號）更新，留最後抓到的（transform/task.py）
# 用得到的官方欄位（沒用到：編號、累計＝隨查詢區間變動、備註＝只有連結）；公布日期偶爾帶 *（意義未確認），存成 has_star_mark
PUNISH_FIELDS = ["公布日期", "證券代號", "證券名稱", "處置條件", "處置起迄時間", "處置措施", "處置內容"]


def punish_to_frame(raw: dict) -> pd.DataFrame:
    return twse_rows_to_frame(raw, PUNISH_FIELDS, "處置股")


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
    # 只檢查、不刪：同一檔同一天有兩則不同的處置（完全相同的列不算），主鍵只能留一則，要人確認
    keys = STAGE_KEYS["stage_punish"]
    conflicts = out[out.duplicated(keys, keep=False) & ~out.duplicated(keep=False)]
    if not conflicts.empty:
        listed = conflicts[keys + ["measure_type"]].to_string(index=False)
        warnings.warn(f"處置股：同一天同一檔有多則處置，只會留下一則，請確認：\n{listed}")
    return out.sort_values(keys, kind="stable").reset_index(drop=True)


# ---------- 注意累計可能達處置標準名單 → stage_punish_watchlist ----------
WATCHLIST_PROGRESS = "近期達本公司「公布注意交易資訊」標準之情形"
PUNISH_WATCHLIST_FIELDS = ["證券代號", "證券名稱", WATCHLIST_PROGRESS]   # 沒用到：編號


def punish_watchlist_to_frame(raw: dict) -> pd.DataFrame:
    """資料日期在標題裡（例如「115年11月06日 公布…」），每列都記上。"""
    return twse_rows_to_frame(raw, PUNISH_WATCHLIST_FIELDS, "注意累計名單").assign(data_date=client.watchlist_day(raw["body"]))


def clean_punish_watchlist(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_punish_watchlist"""
    progress = df[WATCHLIST_PROGRESS].str.replace("<br>", "\n")   # 多行情形換成換行
    return pd.DataFrame({
        "data_date": df["data_date"],
        "stock_id": df["證券代號"].str.strip(),
        "stock_name": df["證券名稱"].str.strip(),
        "progress_text": strip_html(progress),
    })


# ---------- 每日市場成交 → stage_trading_day ----------
MARKET_DATA_READY = datetime.time(15, 0)   # 證交所收盤後才公布當天的市場成交；這個時間以後抓的才含當天
MARKET_DAILY_FIELDS = ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]


def market_daily_to_frame(raw: dict) -> pd.DataFrame:
    return twse_rows_to_frame(raw, MARKET_DAILY_FIELDS, "市場成交資訊")


def covered_through(fetched_time: str) -> datetime.date:
    """這份市場成交涵蓋到哪一天：收盤資料公布後（15:00）抓的含當天，之前抓的只到前一天。

    >>> covered_through("2026-11-13T21:00:00+08:00"), covered_through("2026-11-13T10:00:00+08:00")
    (datetime.date(2026, 11, 13), datetime.date(2026, 11, 12))
    """
    fetched = datetime.datetime.fromisoformat(fetched_time)
    if fetched.time() >= MARKET_DATA_READY:
        return fetched.date()
    return fetched.date() - datetime.timedelta(days=1)


def clean_trading_day(df: pd.DataFrame, month_yyyymm: str, through: datetime.date, traded) -> pd.DataFrame:
    """→ stage_trading_day：這個月 1 號到 through（資料涵蓋到的那天）的每一天，一天一列。

    資料裡有的那天＝有交易（is_trading=1）；涵蓋範圍內但資料裡沒有＝沒交易（週末、休市、颱風假，is_trading=0）。
    through 之後的日子不寫：還不知道有沒有交易，analyze 看到沒有這列就不算那天。
    traded(date)：那天的收盤行情有個股資料（和 crawler 判斷交易日同一個證據）。資料裡沒有、但那天有收盤行情
    → 那天其實有交易，是市場成交還沒更新，不寫這列（當作還不知道），免得把交易日當成沒交易。只問資料裡沒有的日子。
    """
    trading = pd.DataFrame({
        "trade_date": roc_to_dates(df["日期"]),
        "volume_shares": to_number(df["成交股數"]).astype("Int64"),
        "amount_twd": to_number(df["成交金額"]).astype("Int64"),
        "trade_count": to_number(df["成交筆數"]).astype("Int64"),
        "taiex_index": to_number(df["發行量加權股價指數"]),
        "taiex_change_points": to_number(df["漲跌點數"]),
    })
    first = pd.Period(month_yyyymm, "M").start_time.date()
    last = min(pd.Period(month_yyyymm, "M").end_time.date(), through)
    calendar = pd.DataFrame({"trade_date": pd.date_range(first, last).date}) if first <= last else trading[["trade_date"]]

    out = calendar.merge(trading, on="trade_date", how="outer")   # outer：資料比 through 新的那天也留著
    out.insert(1, "is_trading", out["trade_date"].isin(set(trading["trade_date"])))
    not_updated = pd.Series(False, index=out.index)
    missing = ~out["is_trading"]                                      # 只問資料裡沒有的日子（週末、休市的回應很小）
    not_updated[missing] = [bool(traded(day)) for day in out.loc[missing, "trade_date"]]
    if not_updated.any():
        warnings.warn(f"市場成交還沒有 {sorted(out.loc[not_updated, 'trade_date'])}，但那天有收盤行情：先不寫，等下次抓")
    return out[~not_updated].sort_values("trade_date", kind="stable").reset_index(drop=True)


# ---------- 每日收盤行情 → stage_stock_price ----------
# 用得到的官方欄位（沒用到：成交筆數、成交金額、最後揭示買賣價量、本益比）
STOCK_PRICE_FIELDS = ["證券代號", "證券名稱", "成交股數", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"]


def signed_change(sign: pd.Series, amount: pd.Series) -> pd.Series:
    """漲跌(+/-)（HTML，例如 <p style= color:red>+</p>）＋漲跌價差 → 帶正負號的漲跌；平盤是 0，不比價（X）是 NaN。

    >>> signed_change(pd.Series(["<p style= color:red>+</p>", "<p style= color:green>-</p>", "<p> </p>", "<p>X</p>"]),
    ...               pd.Series(["5.50", "100.00", "0.00", "0.00"])).tolist()
    [5.5, -100.0, 0.0, nan]
    """
    factor = strip_html(sign).map({"+": 1.0, "-": -1.0, "": 0.0})   # 其他（X 不比價）→ NaN
    return to_number(amount) * factor


def stock_price_to_frame(raw: dict) -> pd.DataFrame:
    """回應裡個股的那張表（client.stock_price_table，和 crawler 判斷公布了沒看同一張）；沒開市的日子沒有，回傳空表。"""
    return twse_rows_to_frame({"body": client.stock_price_table(raw["body"]) or {}}, STOCK_PRICE_FIELDS, "每日收盤行情")


def clean_stock_price(df: pd.DataFrame, day: datetime.date) -> pd.DataFrame:
    """→ stage_stock_price。回應的列沒有日期欄，用查詢的那天。那天沒成交（收盤價「--」）的不放。"""
    prices = pd.DataFrame({
        "trade_date": [day] * len(df),
        "stock_id": df["證券代號"].str.strip(),
        "stock_name": df["證券名稱"].str.strip(),
        "open_price": to_number(df["開盤價"]),
        "high_price": to_number(df["最高價"]),
        "low_price": to_number(df["最低價"]),
        "close_price": to_number(df["收盤價"]),
        "price_change": signed_change(df["漲跌(+/-)"], df["漲跌價差"]),
        "volume_shares": to_number(df["成交股數"]).astype("Int64"),
    })
    return prices[prices["close_price"].notna()].reset_index(drop=True)


# ---------- 開休市日期 → stage_holiday ----------
HOLIDAY_FIELDS = ["日期", "名稱", "說明"]


def holiday_to_frame(raw: dict) -> pd.DataFrame:
    return twse_rows_to_frame(raw, HOLIDAY_FIELDS, "開休市日期")


def clean_holiday(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_holiday（照官方原樣，也含「開始交易日」這類有交易的日子）"""
    out = pd.DataFrame({
        "holiday_date": pd.to_datetime(df["日期"]).dt.date,   # 這支 API 是西元
        "holiday_name": df["名稱"].str.strip(),
        "description": df["說明"].str.strip(),
    })
    return out.sort_values("holiday_date", kind="stable").reset_index(drop=True)


# ---------- 上市公司 → stage_company ----------
COMPANY_FIELDS = ["公司代號", "公司簡稱", "公司名稱", "產業別", "上市日期"]   # 其餘 28 個欄位沒用到


def company_to_frame(raw: dict) -> pd.DataFrame:
    """這支 OpenAPI 的格式不同：每筆是 dict（欄名就是 key），沒有 fields。快照日期取自 raw_key（抓取日期）。"""
    df = require_columns(pd.DataFrame(raw["body"], columns=None if raw["body"] else COMPANY_FIELDS),
                         COMPANY_FIELDS, "上市公司")
    return df.assign(snapshot_date=pd.to_datetime(raw["meta"]["key"], format=DATE_FMT).date())


def clean_company(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_company"""
    return pd.DataFrame({
        "company_id": df["公司代號"].str.strip(),
        "short_name": df["公司簡稱"].str.strip(),
        "full_name": df["公司名稱"].str.strip(),
        "industry_code": df["產業別"].str.strip(),
        "listed_date": pd.to_datetime(df["上市日期"], format=DATE_FMT).dt.date,
        "snapshot_date": df["snapshot_date"],
    })


# ---------- 公司重大訊息 → stage_announcement ----------
ANNOUNCEMENT_TITLES = ["公司代號", "公司名稱", "發言日期", "發言時間", "主旨", "詳細資料"]


def announcement_to_frame(raw: dict) -> pd.DataFrame:
    return mops_rows_to_frame(raw, ANNOUNCEMENT_TITLES, "重大訊息")   # code 406＝那個月沒有公告，data 是空的


def clean_announcement(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_announcement

    序號是依「輸入日期」（enterDate）編的，不是依發言日期：
    同一天發言的兩則公告可能序號相同，所以主鍵用（公司, 輸入日期, 序號）；announcement_id 是這三欄合成的一個代號。
    """
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
    out.insert(4, "announcement_id", announcement_ids(out))
    return out.sort_values(STAGE_KEYS["stage_announcement"], kind="stable").reset_index(drop=True)


def announcement_ids(df: pd.DataFrame) -> list[str]:
    """（公司, 輸入日期, 序號）→ announcement_id（common/hash_utils.py 的 key_md5）。"""
    return [key_md5(c, d.strftime(DATE_FMT), s) for c, d, s in zip(df["company_id"], df["enter_date"], df["serial_number"])]


# ---------- 重大訊息全文 → stage_announcement_detail ----------
# 一份 raw 是一則公告。主旨、發言日期已經在 stage_announcement；發言人姓名、電話是個資，不放進 stage。
ANNOUNCEMENT_DETAIL_TITLES = ["符合條款", "事實發生日", "說明"]
# 存託憑證（DR，t59sb01_detail）沒有「說明」，內容放在這兩欄；符合條款是另一份規定的「第五條第N款」
DR_CONTENT_TITLE = "發生依外國發行人所屬國及上市地國法令規定應即申報之重大情事"
DR_OTHER_TITLE = "其他"


def dr_to_common(df: pd.DataFrame) -> pd.DataFrame:
    """DR 的全文欄位 → 和一般公司一樣的「說明」：重大情事＋（有的話）其他。一般公司原樣回傳。"""
    if "說明" in df.columns or DR_CONTENT_TITLE not in df.columns:
        return df
    other = df[DR_OTHER_TITLE].astype(str).str.strip() if DR_OTHER_TITLE in df.columns else ""
    content = df[DR_CONTENT_TITLE].astype(str).str.strip()
    return df.assign(說明=content.where(other == "", content + "\n\n其他：\n" + other))


def announcement_detail_to_frame(raw: dict) -> pd.DataFrame:
    """主鍵（公司, 輸入日期, 序號）從 raw_key 取，例如 9908_20261102_1；回應裡沒有輸入日期。"""
    df = mops_rows_to_frame(raw, ANNOUNCEMENT_DETAIL_TITLES, "重大訊息全文", prepare=dr_to_common)
    company_id, enter_yyyymmdd, serial_number = raw["meta"]["key"].split("_")
    return df.assign(company_id=company_id, enter_date=enter_yyyymmdd, serial_number=serial_number)


def clean_announcement_detail(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_announcement_detail（主鍵和 stage_announcement 相同，可以直接 JOIN）"""
    out = pd.DataFrame({
        "company_id": df["company_id"],
        "enter_date": pd.to_datetime(df["enter_date"], format=DATE_FMT).dt.date,
        "serial_number": df["serial_number"].astype(int),
        "announce_clause": df["符合條款"].str.strip(),                      # 例如 "第31款"
        "fact_date": roc_to_dates(df["事實發生日"].str.strip()),             # 例如 "115/11/05"
        "content": df["說明"].str.replace("\r\n", "\n").str.strip(),         # 說明全文
    })
    out.insert(3, "announcement_id", announcement_ids(out))
    return out.sort_values(STAGE_KEYS["stage_announcement_detail"], kind="stable").reset_index(drop=True)


# ---------- 個股新聞（FinMind）→ stage_news、stage_stock_news ----------
STOCK_NEWS_FIELDS = ["date", "stock_id", "link", "source", "title"]


def stock_news_to_frame(raw: dict) -> pd.DataFrame:
    """FinMind 的格式：data 是一串 dict（欄名就是 key）。那天沒有新聞時 data 是空的，回傳空表。"""
    data = raw["body"].get("data") or []
    return require_columns(pd.DataFrame(data, columns=None if data else STOCK_NEWS_FIELDS), STOCK_NEWS_FIELDS, "個股新聞")


def stock_news_ids(raw: dict) -> set[str]:
    """一份個股新聞裡每則新聞的 news_id（transform 的 producer 用來找要整理哪些新聞網頁）。
    連結和 crawler 打開網頁用的是同一份（client.stock_news_links，論壇貼文不算），news_id 才對得上。"""
    return {url_md5(link) for link in client.stock_news_links(raw["body"])}


def strip_publisher_suffix(title: str, publisher: str) -> str:
    """標題結尾的「 - 媒體」去掉（Google 新聞的格式）。

    >>> strip_publisher_suffix("台積電收跌100元 - 自由時報", "自由時報")
    '台積電收跌100元'
    >>> strip_publisher_suffix("台積電收跌100元", "自由時報")
    '台積電收跌100元'
    """
    suffix = f" - {publisher}"
    return title[:-len(suffix)].strip() if publisher and title.endswith(suffix) else title.strip()


def clean_stock_news(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (stage_news, stage_stock_news)：新聞本身一則一列（主鍵 news_id＝md5(link)），和「這檔有這則新聞」。

    同一個連結出現兩次（時間或標題不同）只留最早的一則。
    FinMind 的時間是 UTC（標題寫「09:19 即時新聞」的，date 是 01:20），轉成台灣時間；
    所以查 start_date＝11/20 拿到的是台灣時間 11/20 08:00 到 11/21 08:00 的新聞。
    """
    times = pd.to_datetime(df["date"]).dt.tz_localize("UTC").dt.tz_convert(TAIPEI).dt.tz_localize(None)
    links = df["link"].str.strip()
    out = pd.DataFrame({
        "stock_id": df["stock_id"].astype(str).str.strip(),
        "news_id": [url_md5(link) for link in links],
        "link": links,
        "news_time": times,
        "news_date": times.dt.date,
        "publisher": df["source"].str.strip(),   # FinMind 的 source 是發布的媒體，不是資料來源
        "title": [strip_publisher_suffix(t, p) for t, p in zip(df["title"], df["source"].str.strip())],
    })
    out = out.sort_values("news_time", kind="stable").drop_duplicates(STAGE_KEYS["stage_stock_news"], keep="first")
    out = out.sort_values(["stock_id", "news_time"], kind="stable").reset_index(drop=True)   # news_id 是亂碼，照時間排比較好看
    news = out.drop(columns="stock_id").drop_duplicates("news_id", keep="first").reset_index(drop=True)
    return news, out[STAGE_KEYS["stage_stock_news"]]


# ---------- 新聞網頁（raw_news_detail 只存 <head>）→ stage_news_detail ----------
NEWS_DETAIL_FIELDS = ["final_url", "head", "text_chars"]


def news_detail_to_frame(raw: dict) -> pd.DataFrame:
    """raw_news_detail 的 body 是 crawl_news_detail 組的 dict（不是 API 回應）；news_id 取自 raw_key。"""
    df = require_columns(pd.DataFrame([raw["body"]]), NEWS_DETAIL_FIELDS, "新聞網頁")
    return df.assign(news_id=raw["meta"]["key"])


def lead_from_head(head: str | None) -> str | None:
    """<head> 裡的 meta 摘要（og:description、description）：新聞網站自己給搜尋引擎、社群分享顯示的一兩句，通常就是導言。

    >>> lead_from_head("<head><meta property='og:description' content=' 健策10月營收年增81％。 '></head>")
    '健策10月營收年增81％。'
    >>> lead_from_head("") is None
    True
    """
    metadata = trafilatura.extract_metadata(f"<html>{head}<body></body></html>") if head else None   # 只有 head 解析不出來，要包成整頁
    lead = metadata.description.strip() if metadata and metadata.description else ""
    return lead or None


def clean_news_detail(df: pd.DataFrame) -> pd.DataFrame:
    """→ stage_news_detail"""
    return pd.DataFrame({
        "news_id": df["news_id"],
        "final_url": df["final_url"],
        "lead": df["head"].map(lead_from_head),
        "text_chars": df["text_chars"].astype("Int64"),
    })
