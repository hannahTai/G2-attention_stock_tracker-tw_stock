"""transform/transformer.py 的測試：用真實回應的資料列，不連網路、不讀 data/。執行：uv run pytest"""
import datetime
import hashlib
import math
import warnings

import pandas as pd
import pytest

from common.date_utils import roc_to_date, roc_to_dates
from transform.parsing import chinese_number, strip_html, to_number
from transform.transformer import (
    announcement_detail_to_frame, announcement_to_frame, clean_announcement_detail, holiday_to_frame, market_daily_to_frame,
    notice_to_frame, punish_to_frame,
    punish_watchlist_to_frame, clean_announcement, clean_company, company_to_frame, clean_holiday, clean_notice,
    clean_news_detail, clean_punish, clean_punish_watchlist, clean_stock_news, clean_stock_price, clean_trading_day,
    news_detail_to_frame, stock_news_to_frame,
    stock_price_to_frame,
)


# 官方回傳的完整欄位（照真實回應，含沒用到的欄位）
NOTICE_API_FIELDS = ["編號", "證券代號", "證券名稱", "累計次數", "注意交易資訊", "日期", "收盤價", "本益比"]
PUNISH_API_FIELDS = ["編號", "公布日期", "證券代號", "證券名稱", "累計", "處置條件",
                     "處置起迄時間", "處置措施", "處置內容", "備註"]
WATCHLIST_API_FIELDS = ["編號", "證券代號", "證券名稱", "近期達本公司「公布注意交易資訊」標準之情形"]
MARKET_API_FIELDS = ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]
HOLIDAY_API_FIELDS = ["日期", "名稱", "說明"]


def envelope(rows, fields=NOTICE_API_FIELDS, fetched_at="2026-11-09T21:00:00+08:00", **body):
    """組一個最小的 raw 信封。"""
    return {"meta": {"fetched_at": fetched_at}, "body": {"stat": "OK", "fields": fields, "data": rows, **body}}


# 11/06 真實資料（晶心科：一般款次；騰輝：第十款被紅字包起來）
ROW_ANDES = [1, "6533", "晶心科", "38",
             "最近六個營業日累積收盤價跌幅達33.02%。且六個營業日起迄兩個營業日收盤價價差達75.50 元﹝第一款﹞。",
             "115.11.06", "202.00", "-----"]
ROW_TENGHUI = [1, "6672", "騰輝電子-KY", "38",
               "11月06日之本益比為69.94﹝第六款﹞。最近六個營業日之累積週轉率為73.98%。"
               "<font color='#FF0000'>﹝第十款﹞</font>。",
               "115.11.06", "1,463.00", "69.94"]


def run(*rows, fields=NOTICE_API_FIELDS):
    return clean_notice(notice_to_frame(envelope(list(rows), fields)))


def test_clean_helpers():
    assert roc_to_date("115年11月06日") == datetime.date(2026, 11, 6)
    assert roc_to_dates(pd.Series(["115.11.06", "115/10/01"])).tolist() == [datetime.date(2026, 11, 6), datetime.date(2026, 10, 1)]
    nums = to_number(pd.Series(["1,450.00", "-----", ""])).tolist()
    assert nums[0] == 1450.0 and math.isnan(nums[1]) and math.isnan(nums[2])
    assert [chinese_number(s) for s in ["一", "九", "十", "十三", "十四"]] == [1, 9, 10, 13, 14]
    assert strip_html(pd.Series(["<font color='#FF0000'>﹝第十款﹞</font>"])).tolist() == ["﹝第十款﹞"]


def test_to_number_rejects_garbage():
    """看不懂的數字要報錯，不能默默變成 NaN。"""
    with pytest.raises(ValueError):
        to_number(pd.Series(["12.3abc"]))


def test_basic_row():
    notices, clauses = run(ROW_ANDES)
    row = notices.iloc[0]
    assert (row["trade_date"], row["stock_id"], row["stock_name"], row["close_price"]) == (
        datetime.date(2026, 11, 6), "6533", "晶心科", 202.0)
    assert math.isnan(row["pe_ratio"])
    assert clauses.to_dict("records") == [{"trade_date": datetime.date(2026, 11, 6), "stock_id": "6533", "clause": 1}]


def test_html_stripped_and_multiple_clauses():
    notices, clauses = run(ROW_TENGHUI)
    assert "<font" not in notices.iloc[0]["reason_text"]   # 用 ["欄位"] 取值，避免撞到 pandas 方法名
    assert notices.iloc[0]["close_price"] == 1463.0            # 千分位逗號
    assert clauses["clause"].tolist() == [6, 10]


def test_duplicate_clause_in_one_row_kept_for_database():
    """同一段文字寫兩次第三款：兩列都留著；寫進資料庫時主鍵相同，只會剩一列（tests/test_integration.py）。"""
    row = ROW_ANDES.copy()
    row[4] = "﹝第三款﹞﹝第一款﹞﹝第三款﹞"
    _, clauses = run(row)
    assert clauses["clause"].tolist() == [1, 3, 3]


def test_many_rows_keep_their_own_clauses():
    notices, clauses = run(ROW_ANDES, ROW_TENGHUI)
    assert len(notices) == 2
    assert clauses.groupby("stock_id")["clause"].apply(list).to_dict() == {"6533": [1], "6672": [6, 10]}


def test_empty_day_returns_empty_frames():
    """沒開市：stat 還是 OK，但 data 是空的。"""
    notices, clauses = run()
    assert notices.empty and clauses.empty


def test_missing_used_field_raises():
    """用得到的欄位不見了要報錯，不能默默變成空值。"""
    fields = [f for f in NOTICE_API_FIELDS if f != "收盤價"]
    row = [v for f, v in zip(NOTICE_API_FIELDS, ROW_ANDES, strict=True) if f != "收盤價"]
    with pytest.raises(ValueError, match="收盤價"):
        notice_to_frame(envelope([row], fields=fields))


def test_added_field_is_ignored():
    """官方多加一欄：照樣解析，新欄位不影響。"""
    notices, _ = run(ROW_ANDES + ["新欄位的值"], fields=NOTICE_API_FIELDS + ["新欄位"])
    assert notices.iloc[0]["stock_id"] == "6533"


def test_reordered_fields_still_work():
    """官方調整欄位順序：用欄名取，不受影響。"""
    notices, _ = run(ROW_ANDES[::-1], fields=NOTICE_API_FIELDS[::-1])
    assert (notices.iloc[0]["stock_id"], notices.iloc[0]["close_price"]) == ("6533", 202.0)


def test_unused_field_removed_is_fine():
    """沒用到的欄位（編號）拿掉：照樣解析。"""
    notices, _ = run(ROW_ANDES[1:], fields=NOTICE_API_FIELDS[1:])
    assert notices.iloc[0]["stock_id"] == "6533"


def test_company_missing_field_raises():
    """上市公司 API 沒有 fields：少了用得到的 key 也要報錯，不能載入空的公司代號。"""
    raw = {"meta": {"key": "20261109"}, "body": [{"公司名稱": "台灣水泥", "公司簡稱": "台泥", "產業別": "01",
                                                  "上市日期": "19620209"}]}
    with pytest.raises(ValueError, match="公司代號"):
        company_to_frame(raw)


def test_missing_clause_raises():
    row = ROW_ANDES.copy()
    row[4] = "沒有款次的文字"
    with pytest.raises(ValueError, match="款次"):
        run(row)


# ---------- 處置股 ----------
PUNISH_ROW = [1, "115/11/06", "2360", "致茂", 3, "連續三次", "115/11/09～115/11/13", "第一次處置",
              "１處置原因：…\n３處置措施：\nａ以人工管制之撮合終端機執行撮合作業（約每二分鐘撮合一次）。\n", "<a href='x'>連結</a>"]


def test_punish_parses_period_and_minutes():
    df = clean_punish(punish_to_frame(envelope([PUNISH_ROW], PUNISH_API_FIELDS)))
    row = df.iloc[0]
    assert (row["announce_date"], row["period_start"], row["period_end"]) == (
        datetime.date(2026, 11, 6), datetime.date(2026, 11, 9), datetime.date(2026, 11, 13))
    assert row["match_interval_minutes"] == 2 and row["measure_type"] == "第一次處置" and not row["has_star_mark"]


def test_punish_two_in_one_raw_warns():
    """同一份 raw 裡同一檔同一天有兩則不同的處置 → 警告（主鍵只能留一則）；只檢查、不刪。"""
    other = PUNISH_ROW.copy()
    other[7] = "第二次處置"
    with pytest.warns(UserWarning, match="同一天同一檔有多則處置"):
        df = clean_punish(punish_to_frame(envelope([PUNISH_ROW, other], PUNISH_API_FIELDS)))
    assert len(df) == 2


def test_punish_identical_rows_in_one_raw_no_warning():
    """同一份 raw 裡完全相同的兩列只是重複，不警告。"""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        df = clean_punish(punish_to_frame(envelope([PUNISH_ROW, PUNISH_ROW], PUNISH_API_FIELDS)))
    assert len(df) == 2   # 兩列都留著，資料庫只會存一列


def test_punish_star_kept_as_flag():
    row = PUNISH_ROW.copy()
    row[1] = "*115/11/09"
    df = clean_punish(punish_to_frame(envelope([row], PUNISH_API_FIELDS)))
    assert df.iloc[0]["has_star_mark"] and df.iloc[0]["announce_date"] == datetime.date(2026, 11, 9)


# ---------- 注意累計名單 ----------
def test_punish_watchlist_date_from_title_and_br_split():
    raw = envelope([[1, "2455", "全新", "連續二次<br>九個營業日已有五次"]], WATCHLIST_API_FIELDS,
                   title="115年11月06日 公布注意累計次數可能達處置標準之有價證券一覽表")
    df = clean_punish_watchlist(punish_watchlist_to_frame(raw))
    assert df.iloc[0]["data_date"] == datetime.date(2026, 11, 6)
    assert df.iloc[0]["progress_text"] == "連續二次\n九個營業日已有五次"


# ---------- 交易日、休市 ----------
def test_trading_day_numbers():
    rows = [["115/11/02", "10,422,514,115", "799,687,507,911", "4,104,242", "44,920.14", "-291.69"]]
    df = clean_trading_day(market_daily_to_frame(envelope(rows, MARKET_API_FIELDS)), "202611", datetime.date(2026, 11, 2),
                           lambda day: False)
    trading = df[df["is_trading"]]
    assert len(trading) == 1
    assert trading.iloc[0]["volume_shares"] == 10422514115 and trading.iloc[0]["taiex_change_points"] == -291.69


def test_trading_day_calendar_until_covered_day():
    """11/02（一）、11/03（二）有交易，抓到 11/04：11/01（日）、11/04（颱風假）沒交易；11/05 之後不寫（還不知道）。"""
    rows = [["115/11/02", "1", "1", "1", "1", "1"], ["115/11/03", "1", "1", "1", "1", "1"]]
    df = clean_trading_day(market_daily_to_frame(envelope(rows, MARKET_API_FIELDS)), "202611", datetime.date(2026, 11, 4),
                           lambda day: False)
    assert df["trade_date"].tolist() == [datetime.date(2026, 11, d) for d in (1, 2, 3, 4)]
    assert df["is_trading"].tolist() == [False, True, True, False]
    assert df["volume_shares"].isna().tolist() == [True, False, False, True]


def test_holiday_is_western_date():
    df = clean_holiday(holiday_to_frame(envelope([["2026-10-09", "國慶日", "補假"]], HOLIDAY_API_FIELDS)))
    assert df.iloc[0]["holiday_date"] == datetime.date(2026, 10, 9)


# ---------- 公司、重大訊息 ----------
def test_company_snapshot():
    raw = {"meta": {"key": "20261109"},
           "body": [{"公司代號": "1101", "公司簡稱": "台泥", "公司名稱": "臺灣水泥", "產業別": "01", "上市日期": "19620209"}]}
    row = clean_company(company_to_frame(raw)).iloc[0]
    assert (row["company_id"], row["listed_date"], row["snapshot_date"]) == ("1101", datetime.date(1962, 2, 9), datetime.date(2026, 11, 9))


ANNOUNCEMENT_TITLES = [{"main": m, "sub": []} for m in ["公司代號", "公司名稱", "發言日期", "發言時間", "主旨", "詳細資料"]]


def announcement_raw(data: list) -> dict:
    return {"meta": {}, "body": {"code": 200, "result": {"titles": ANNOUNCEMENT_TITLES, "data": data}}}


def test_announcement_serial_and_joined_subject():
    data = [["1303", "南亞", "115/09/01", "15:42:35", "因本公司有價證券達標準，\r\n故公布重大訊息",
             {"parameters": {"enterDate": "1150901", "serialNumber": "2"}}]]
    row = clean_announcement(announcement_to_frame(announcement_raw(data))).iloc[0]
    assert (row["announce_date"], row["enter_date"], row["serial_number"]) == (
        datetime.date(2026, 9, 1), datetime.date(2026, 9, 1), 2)
    assert row["announcement_id"] == hashlib.md5(b"1303_20260901_2").hexdigest()   # 主鍵三欄合成的代號
    assert row["subject"] == "因本公司有價證券達標準，故公布重大訊息"


def test_announcement_same_day_same_serial_both_kept():
    """實際案例：1709 同一天發言的兩則公告序號都是 1（序號依輸入日期編），兩則都要留下。"""
    data = [["1709", "和益", "115/11/11", "15:32:02", "公告本公司董事會通過115年第三季合併財務報告",
             {"parameters": {"enterDate": "1151109", "serialNumber": "1"}}],
            ["1709", "和益", "115/11/11", "15:42:24", "公告本公司向關係人取得不動產使用權資產",
             {"parameters": {"enterDate": "1151111", "serialNumber": "1"}}]]
    out = clean_announcement(announcement_to_frame(announcement_raw(data)))
    assert len(out) == 2
    assert out["enter_date"].tolist() == [datetime.date(2026, 11, 9), datetime.date(2026, 11, 11)]


def test_announcement_no_data_is_empty():
    """code 406「查無相符資料」：那個月沒有公告，result 是 null。"""
    raw = {"meta": {}, "body": {"code": 406, "message": "查無相符資料", "result": None}}
    assert clean_announcement(announcement_to_frame(raw)).empty


def test_trading_day_not_updated_if_prices_exist():
    """11/04 市場成交裡沒有，但那天的收盤行情有個股資料 → 其實有交易，是資料還沒更新：不寫這列（不是颱風假）。
    只問市場成交裡沒有的日子（11/02、11/03 有，不用問）。"""
    rows = [["115/11/02", "1", "1", "1", "1", "1"], ["115/11/03", "1", "1", "1", "1", "1"]]
    asked = []

    def traded(day):
        asked.append(day)
        return day == datetime.date(2026, 11, 4)
    with pytest.warns(UserWarning, match="還沒有"):
        df = clean_trading_day(market_daily_to_frame(envelope(rows, MARKET_API_FIELDS)), "202611", datetime.date(2026, 11, 4), traded)
    assert df["trade_date"].tolist() == [datetime.date(2026, 11, d) for d in (1, 2, 3)]
    assert asked == [datetime.date(2026, 11, 1), datetime.date(2026, 11, 4)]


DETAIL_TITLES = [{"main": m, "sub": []} for m in
                 ["序號", "發言日期", "發言時間", "發言人", "發言人職稱", "發言人電話", "主旨", "符合條款", "事實發生日", "說明"]]


def test_announcement_detail_keys_from_raw_key_and_no_spokesperson():
    """照真實回應（9908，第31款）：主鍵從 raw_key 取；發言人姓名、電話不進 stage。"""
    data = [["1", "115/11/05", "14:39:07", "王小明      ", "協理    ", "02-1234-5678   ", "公告第三季財報   ",
             "第31款", "115/11/05", "1.提報董事會日期:115/11/05\r\n2.其他應敘明事項:無\n"]]
    raw = {"meta": {"key": "9908_20261102_1"}, "body": {"code": 200, "result": {"titles": DETAIL_TITLES, "data": data}}}
    out = clean_announcement_detail(announcement_detail_to_frame(raw))
    assert list(out.columns) == ["company_id", "enter_date", "serial_number", "announcement_id", "announce_clause", "fact_date", "content"]
    assert out.loc[0, "announcement_id"] == hashlib.md5(b"9908_20261102_1").hexdigest()   # ＝ raw_key 的 md5
    row = out.iloc[0]
    assert (row["company_id"], row["enter_date"], row["serial_number"]) == ("9908", datetime.date(2026, 11, 2), 1)
    assert (row["announce_clause"], row["fact_date"]) == ("第31款", datetime.date(2026, 11, 5))
    assert row["content"] == "1.提報董事會日期:115/11/05\n2.其他應敘明事項:無"


def test_announcement_detail_no_data_is_empty():
    raw = {"meta": {"key": "9908_20261102_1"}, "body": {"code": 200, "result": {"data": []}}}
    assert clean_announcement_detail(announcement_detail_to_frame(raw)).empty


DR_TITLES = [{"main": m, "sub": []} for m in
             ["公司代號", "公告序號", "事實發生日", "公司名稱", "主旨", "符合條款",
              "發生依外國發行人所屬國及上市地國法令規定應即申報之重大情事", "其他"]]


def test_announcement_detail_dr_format():
    """存託憑證（9103 美德醫療-DR）：內容在「重大情事」＋「其他」兩欄，日期寫「民國115年6月9日」，條款是第五條。"""
    data = [["9103", 1, "民國115年6月9日", "美德向邦醫療", "主旨", "第五條第28款", "營業收入 42,528\n", "補充說明"]]
    raw = {"meta": {"key": "9103_20260609_1"}, "body": {"code": 200, "result": {"titles": DR_TITLES, "data": data}}}
    row = clean_announcement_detail(announcement_detail_to_frame(raw)).iloc[0]
    assert (row["announce_clause"], row["fact_date"]) == ("第五條第28款", datetime.date(2026, 6, 9))
    assert row["content"] == "營業收入 42,528\n\n其他：\n補充說明"


# ---------- 每日收盤行情 ----------
STOCK_PRICE_API_FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價",
                          "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]


def price_envelope(rows):
    """收盤行情的回應有好幾張表；個股那張在後面。"""
    index_table = {"title": "價格指數", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "27,000.00"]]}
    price_table = {"title": "每日收盤行情", "fields": STOCK_PRICE_API_FIELDS, "data": rows}
    return {"meta": {"fetched_at": "2026-11-20T21:00:00+08:00"}, "body": {"stat": "OK", "tables": [index_table, price_table]}}


def test_stock_price_picks_the_price_table_and_drops_untraded():
    traded = ["2466", "冠西電", "1,234,000", "900", "389,000,000", "310.00", "318.00", "309.50", "315.50",
              "<p style= color:red>+</p>", "5.50", "315.00", "10", "315.50", "3", "25.10"]
    untraded = ["9999", "沒成交", "0", "0", "0", "--", "--", "--", "--", "<p> </p>", "0.00", "--", "0", "--", "0", "0.00"]
    day = datetime.date(2026, 11, 20)
    df = clean_stock_price(stock_price_to_frame(price_envelope([traded, untraded])), day)
    assert df["stock_id"].tolist() == ["2466"]
    row = df.iloc[0]
    assert (row["trade_date"], row["close_price"], row["high_price"], row["volume_shares"]) == (day, 315.5, 318.0, 1234000)
    assert row["price_change"] == 5.5


def test_stock_price_on_closed_day_is_empty():
    raw = {"meta": {"fetched_at": "2026-11-22T21:00:00+08:00"}, "body": {"stat": "很抱歉，沒有符合條件的資料!"}}
    assert clean_stock_price(stock_price_to_frame(raw), datetime.date(2026, 11, 22)).empty


# ---------- 個股新聞（FinMind）----------
def news_envelope(rows):
    return {"meta": {"fetched_at": "2026-11-21T09:00:00+08:00"}, "body": {"msg": "success", "status": 200, "data": rows}}


def test_stock_news_strips_source_and_keeps_earliest_of_same_link():
    """同一個連結出現兩次（時間、標題不同）只留最早的；標題結尾的「 - 來源」去掉。"""
    rows = [
        {"date": "2026-11-20 09:30:00", "stock_id": "2330", "link": "https://a/1", "source": "自由時報",
         "title": "台積電收跌100元（更新） - 自由時報"},
        {"date": "2026-11-20 01:07:00", "stock_id": "2330", "link": " https://a/1\n", "source": "自由時報",   # 前後有空白：去掉後是同一則
         "title": "台積電收跌100元 - 自由時報"},
        {"date": "2026-11-20 00:33:35", "stock_id": "2330", "link": "https://b/2", "source": "CMoney",
         "title": "2330 台積電 - 股市早報 - CMoney"},
    ]
    news, stock_news = clean_stock_news(stock_news_to_frame(news_envelope(rows)))
    ids = [hashlib.md5(b"https://b/2").hexdigest(), hashlib.md5(b"https://a/1").hexdigest()]
    assert news["link"].tolist() == ["https://b/2", "https://a/1"]   # 照時間排
    assert news["news_id"].tolist() == ids and "stock_id" not in news
    assert news["title"].tolist() == ["2330 台積電 - 股市早報", "台積電收跌100元"]
    assert news.loc[1, "news_time"] == pd.Timestamp("2026-11-20 09:07:00")   # FinMind 給 UTC，轉成台灣時間
    assert news.loc[1, "news_date"] == datetime.date(2026, 11, 20)
    assert stock_news.values.tolist() == [["2330", ids[0]], ["2330", ids[1]]]


def test_news_detail_lead_from_saved_head():
    """raw_news_detail 存的是 <head>：導言取 meta 摘要；沒有 head（抽不到）就是 NULL。"""
    def page(key, head):
        return {"meta": {"key": key, "fetched_at": "2026-11-21T09:00:00+08:00"},
                "body": {"final_url": "https://www.ctee.com.tw/news/1", "content_type": "text/html", "head": head, "text_chars": 500}}
    head = "<head><title>健策</title><meta property='og:description' content='健策10月營收30.73億元，年增81％。'></head>"
    df = clean_news_detail(news_detail_to_frame(page("abc", head)))
    assert df.loc[0, "news_id"] == "abc" and df.loc[0, "lead"] == "健策10月營收30.73億元，年增81％。" and df.loc[0, "text_chars"] == 500
    assert clean_news_detail(news_detail_to_frame(page("def", ""))).loc[0, "lead"] is None


def test_stock_news_day_without_news_is_empty():
    news, stock_news = clean_stock_news(stock_news_to_frame(news_envelope([])))
    assert news.empty and stock_news.empty
