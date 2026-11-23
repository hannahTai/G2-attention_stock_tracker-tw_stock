"""task 層（轉換）：一個任務整理一份 raw → stage。每個 crawler 任務 crawl_xxx 對應一個 transform_xxx。

每個任務都是同樣四步：
    1. key：要哪一份 raw
    2. raw = repository.read_raw(來源, key)；沒有成功的就跳過
    3. tables = {stage 表名: 整理好的 DataFrame}（transformer 的 xxx_to_frame → clean_xxx）
    4. return save(raw, tables)：一個交易寫進 stage（失敗就不變），每列記上 raw 的抓取時間、只讓比較新的覆蓋
重跑不會重複；任務之間互不依賴，一個任務失敗不影響其他任務；好幾個 worker 同時跑、不照順序，結果都一樣。
transform_xxx 都用 @app.task 註冊成 Celery 任務（見 common/worker.py）；upsert_stage、save 這些是任務共用的小函式，不是任務。
"""
import pandas as pd

from common import date_utils, repository
from common.date_utils import parse_yyyymmdd
from common.worker import Job, app
from transform import transformer
from transform.parsing import fetched_at
from transform.transformer import STAGE_KEYS

# 每張 stage 表都有這個欄位（固定資料的 stage_announcement_clause 除外）：
# 這列來自哪次抓取（raw 的 fetched_at）。寫入時用它比新舊，只讓比較新的覆蓋，
# 好幾個 worker、好幾輪同時整理，誰先寫完都一樣
STAGE_VERSION_COLUMN = "source_fetched_at"


def upsert_stage(tables: dict[str, pd.DataFrame], version, clear_day=None) -> int | None:
    """寫進 stage 表，全部放在同一個交易裡（失敗就全部不變）。回傳整理出的列數；這份比較舊、沒寫就回傳 None。

    version：這批資料來自哪次抓取（raw 的 fetched_at，台灣時間）。每列記在 source_fetched_at，
    寫入時用主鍵更新，只讓比較新（或一樣新）的覆蓋：好幾個 worker、好幾輪同時整理，誰先寫完都一樣。
    clear_day：先把這天的列清掉再寫入，官方拿掉的列也會跟著消失（注意股用）。每張表用自己主鍵的第一欄當日期
    （stage_notice、stage_notice_clause 是 trade_date，stage_notice_day 是 query_date）。
    清之前先比那天現有的版本，資料庫裡比較新就整批不寫，免得舊的名單把新的清掉。
    """
    tables = {name: df.assign(**{STAGE_VERSION_COLUMN: version}) for name, df in tables.items()}

    with repository.get_engine().begin() as conn:
        if clear_day is not None:
            day_column = {name: STAGE_KEYS[name][0] for name in tables}
            if any(repository.has_newer_rows(conn, name, day_column[name], clear_day, STAGE_VERSION_COLUMN, version)
                   for name in tables):
                return None
            for name in tables:
                repository.delete_where_in(conn, name, day_column[name], [clear_day])
        for name, df in tables.items():
            repository.upsert_rows(conn, name, df, STAGE_KEYS[name], STAGE_VERSION_COLUMN)   # 空的就不動
    return sum(len(df) for df in tables.values())


def save(raw: dict, tables: dict[str, pd.DataFrame], clear_day=None) -> str:
    """每個任務的最後一步：用 raw 的抓取時間當版本寫進 stage，回傳給 log 看的一行。"""
    version = fetched_at(raw).astimezone(date_utils.TAIPEI).replace(tzinfo=None)   # 存台灣時間（TIMESTAMP 沒有時區）
    rows = upsert_stage(tables, version, clear_day)
    where = f"raw_{raw['meta']['source']}/{raw['meta']['key']} → {'、'.join(tables)}"
    if rows is None:
        return f"{where}：資料庫裡已經有比較新的，這份較舊，不寫"
    return f"{where}：{rows} 列"


def latest_key(source: str, date_yyyymmdd: str) -> str:
    """那天（含）以前最新的一份 raw_key；一份都沒有就回傳「<=那天」（讀不到，任務會跳過）。只有最新一份的來源用。"""
    return repository.latest_raw_key(source, date_yyyymmdd) or f"<={date_yyyymmdd}"


def skipped(source: str, key) -> str:
    return f"raw_{source}/{key} 沒有成功的資料，跳過"


def transform_one(source: str, key: str, build) -> str:
    """大部分任務都一樣的四步：讀這份 raw（沒有成功的就跳過）→ build(raw) 整理成 {表名: DataFrame} → 寫進 stage。"""
    raw = repository.read_raw(source, key)
    if raw is None:
        return skipped(source, key)
    return save(raw, build(raw))


# ---------- 證交所 ----------
@app.task(base=Job)
def transform_notice(date_yyyymmdd: str) -> str:
    """注意股 → stage_notice、stage_notice_clause、stage_notice_day（那天先清掉再寫入）。"""
    key = date_yyyymmdd
    raw = repository.read_raw("notice", key)
    if raw is None:
        return skipped("notice", key)
    day = parse_yyyymmdd(key)
    notices, clauses = transformer.clean_notice(transformer.notice_to_frame(raw))
    notice_day = pd.DataFrame({"query_date": [day], "notice_count": [len(notices)]})   # 0 檔也寫：表示抓到了、沒人被列
    tables = {"stage_notice": notices, "stage_notice_clause": clauses, "stage_notice_day": notice_day}
    return save(raw, tables, clear_day=day)


@app.task(base=Job)
def transform_punish(date_yyyymmdd: str) -> str:
    """處置股 → stage_punish（同一則公告每天都查得到，用主鍵更新）、stage_punish_day（這天查過了）。"""
    key = date_yyyymmdd
    raw = repository.read_raw("punish", key)
    if raw is None:
        return skipped("punish", key)
    punishes = transformer.clean_punish(transformer.punish_to_frame(raw))
    count = punishes.set_index(["announce_date", "stock_id"]).index.nunique()   # 幾則（主鍵不重複的）；同一則可能出現好幾列
    punish_day = pd.DataFrame({"query_date": [parse_yyyymmdd(key)], "punish_count": [count]})
    tables = {"stage_punish": punishes, "stage_punish_day": punish_day}
    return save(raw, tables)


@app.task(base=Job)
def transform_punish_watchlist(date_yyyymmdd: str) -> str:
    """那天以前最新的注意累計名單 → stage_punish_watchlist。"""
    return transform_one("punish_watchlist", latest_key("punish_watchlist", date_yyyymmdd), lambda raw: {
        "stage_punish_watchlist": transformer.clean_punish_watchlist(transformer.punish_watchlist_to_frame(raw))})


def notice_days(month_yyyymm: str) -> set:
    """這個月有人被列注意的日子（讀 raw_notice，不靠 stage，跟 transform_notice 誰先跑都沒關係）。"""
    keys = [k for k in repository.list_raw_keys("notice") if k.startswith(month_yyyymm)]
    return {parse_yyyymmdd(raw["meta"]["key"]) for raw in repository.read_raws("notice", keys)
            if isinstance(raw["body"], dict) and raw["body"].get("data")}


@app.task(base=Job)
def transform_market_daily(month_yyyymm: str) -> str:
    """每日市場成交（一個月）→ stage_trading_day：涵蓋到的每一天一列，標是不是交易日。"""
    key = month_yyyymm
    raw = repository.read_raw("market_daily", key)
    if raw is None:
        return skipped("market_daily", key)
    through = transformer.covered_through(raw["meta"]["fetched_at"])   # 這份涵蓋到哪天（看抓取時間）
    days = transformer.clean_trading_day(transformer.market_daily_to_frame(raw), key, through, notice_days(key))
    tables = {"stage_trading_day": days}
    return save(raw, tables)


@app.task(base=Job)
def transform_stock_price(date_yyyymmdd: str) -> str:
    """每日收盤行情（一天）→ stage_stock_price。"""
    return transform_one("stock_price", date_yyyymmdd, lambda raw: {
        "stage_stock_price": transformer.clean_stock_price(transformer.stock_price_to_frame(raw), parse_yyyymmdd(date_yyyymmdd))})


@app.task(base=Job)
def transform_holiday(year: int) -> str:
    """開休市日期（一年）→ stage_holiday。"""
    return transform_one("holiday", str(year), lambda raw: {
        "stage_holiday": transformer.clean_holiday(transformer.holiday_to_frame(raw))})


@app.task(base=Job)
def transform_company(date_yyyymmdd: str) -> str:
    """那天以前最新的上市公司清單 → stage_company（依 company_id 更新，下市的留著）。"""
    return transform_one("company", latest_key("company", date_yyyymmdd), lambda raw: {
        "stage_company": transformer.clean_company(transformer.company_to_frame(raw))})


# ---------- 公開資訊觀測站 ----------
@app.task(base=Job)
def transform_announcement(stock_id: str, year: int, month: int) -> str:
    """一家公司一個月的重大訊息 → stage_announcement。"""
    return transform_one("announcement", f"{stock_id}_{year:04d}{month:02d}", lambda raw: {
        "stage_announcement": transformer.clean_announcement(transformer.announcement_to_frame(raw))})


@app.task(base=Job)
def transform_announcement_detail(stock_id: str, enter_yyyymmdd: str, serial_number: int) -> str:
    """一則重大訊息全文 → stage_announcement_detail。"""
    return transform_one("announcement_detail", f"{stock_id}_{enter_yyyymmdd}_{serial_number}", lambda raw: {
        "stage_announcement_detail": transformer.clean_announcement_detail(transformer.announcement_detail_to_frame(raw))})


# ---------- FinMind ----------
@app.task(base=Job)
def transform_stock_news(stock_id: str, date_yyyymmdd: str) -> str:
    """一檔一天的新聞 → stage_news（新聞本身，主鍵 news_id＝md5(link)）、stage_stock_news（這檔有這則新聞）。

    同一則新聞好幾檔都有：stage_news 只有一列，時間、標題以最新一次抓到的為準。
    """
    return transform_one("stock_news", f"{stock_id}_{date_yyyymmdd}", lambda raw: dict(zip(
        ("stage_news", "stage_stock_news"), transformer.clean_stock_news(transformer.stock_news_to_frame(raw)))))


# ---------- 新聞網站 ----------
@app.task(base=Job)
def transform_news_detail(news_id: str) -> str:
    """一則新聞網頁（raw_news_detail 存的 <head>）→ stage_news_detail：導言（meta 摘要）、轉址後的網址、全文有幾個字。"""
    return transform_one("news_detail", news_id, lambda raw: {
        "stage_news_detail": transformer.clean_news_detail(transformer.news_detail_to_frame(raw))})
