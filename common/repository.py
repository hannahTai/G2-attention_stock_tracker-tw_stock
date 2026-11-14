"""repository 層：只負責把資料存進 MySQL、從 MySQL 讀回來（課程補充 J）。

不知道資料從哪個 API 來、被整理過什麼。連線設定從 .env 讀（見 config.py）。
  - 連線、建表：get_engine()、create_tables()
  - raw 表：upsert_raw()、read_raw()、read_raws()、list_raw_keys()、raw_exists()
  - stage 表：stage_has_day()（analyze 檢查那天的資料到齊了沒）
  - stage、app 表：upload_data_to_mysql()、upsert_rows()、delete_where_in()
"""
import datetime
import functools
import json
import urllib.parse

import pandas as pd
import requests
import sqlalchemy

from common import date_utils
from common.config import MYSQL_ACCOUNT, MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, PROJECT_ROOT
from common.constant import STAGE_KEYS

# 建表的 SQL 檔：專案根目錄 schema/ 底下所有 .sql，依檔名順序執行（app → raw → stage → view；
# 表之間沒有外鍵，順序不影響；view.sql 要用到 stage 表，所以排最後）
SQL_FILES = sorted((PROJECT_ROOT / "schema").glob("*.sql"))

# raw 表的來源：raw_<來源> 一個來源一張表
SOURCES = ("notice", "punish", "punish_watchlist", "market_daily", "holiday", "companies", "announcements")

RAW_COLUMNS = ["raw_key", "fetched_at", "http_status", "ok", "problem", "url", "request_body", "body"]


# ---------- 連線、建表 ----------
@functools.cache   # 同一個 database 只建一次 engine，之後重複使用（engine 自己會管理連線）
def get_engine(database: str = MYSQL_DATABASE) -> sqlalchemy.Engine:
    """建立連線用的 engine。

    database 預設是 .env 設定的資料庫；給空字串 "" 則只連到 MySQL 伺服器（資料庫還不存在時，用來建資料庫）。
    """
    # 密碼裡如果有 @ : / 這些符號，會讓連線字串被切錯，所以先編碼
    password = urllib.parse.quote_plus(MYSQL_PASSWORD)
    # 連線字串格式：mysql+pymysql://帳號:密碼@主機:埠/資料庫?charset=utf8mb4（utf8mb4：完整支援中文）
    url = f"mysql+pymysql://{MYSQL_ACCOUNT}:{password}@{MYSQL_HOST}:{MYSQL_PORT}/{database}?charset=utf8mb4"
    # pool_pre_ping：每次拿連線前先確認還活著，斷線就自動重連
    # time_zone：每條連線都用台灣時間，loaded_at 的 DEFAULT CURRENT_TIMESTAMP 才會是台灣時間（雲端預設是 UTC）
    return sqlalchemy.create_engine(url, pool_pre_ping=True,
                                    connect_args={"init_command": "SET time_zone = '+08:00'"})


def create_tables() -> None:
    """建資料庫，再執行 schema/*.sql 建表。已經存在的就跳過；不會刪任何表（欄位改了要自己 ALTER）。"""
    # 1. 建資料庫：這時資料庫可能還不存在，所以只連到伺服器（database=""）
    with get_engine("").begin() as conn:
        conn.execute(sqlalchemy.text(
            f"CREATE DATABASE IF NOT EXISTS {MYSQL_DATABASE} "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))

    # 2. 連到資料庫，建表
    with get_engine().begin() as conn:
        # 一個 .sql 檔裡有好幾句 SQL，用分號切開，一句一句執行
        for sql_file in SQL_FILES:
            sql_text = sql_file.read_text(encoding="utf-8")
            for statement in sql_text.split(";"):
                if statement.strip():   # 跳過空白（例如最後一個分號後面）
                    conn.execute(sqlalchemy.text(statement))


# ---------- raw 表：存 ----------
def raw_table_name(source: str) -> str:
    """來源 → 表名，例如 notice → raw_notice。只接受 SOURCES 裡的來源，避免拼出奇怪的 SQL。"""
    if source not in SOURCES:
        raise ValueError(f"不認識的來源：{source}")
    return f"raw_{source}"


def parse_body(text: str) -> dict | list | str:
    """JSON 就解析；不是 JSON（例如被擋時的 HTML）就回傳原文。"""
    try:
        return json.loads(text)
    except ValueError:
        return text


def upsert_raw_row(source: str, raw_key: str, *, fetched_at: datetime.datetime, http_status: int, ok: bool,
                 problem: str | None, url: str, request_body: str | None, body: str) -> bool:
    """寫入一份回應。成功一律覆蓋；失敗只在還沒有成功的資料時才寫入。回傳有沒有寫進去。"""
    table = raw_table_name(source)
    row = {
        "raw_key": raw_key,
        "fetched_at": fetched_at.astimezone(date_utils.TAIPEI).replace(tzinfo=None),   # 存台灣時間
        "http_status": http_status,
        "ok": ok,
        "problem": problem,
        "url": url,
        "request_body": request_body,
        "body": body,
    }

    with get_engine().begin() as conn:
        if not ok:
            # 已經有成功的資料就不蓋掉（FOR UPDATE：讀的同時鎖住這一列，避免同時寫入）
            existing_ok = conn.execute(sqlalchemy.text(f"SELECT ok FROM {table} WHERE raw_key = :raw_key FOR UPDATE"),
                                       {"raw_key": raw_key}).scalar()
            if existing_ok:
                return False

        # 新的 key 就新增，已經有就更新（upsert）
        columns = ", ".join(RAW_COLUMNS)
        values = ", ".join(f":{c}" for c in RAW_COLUMNS)
        updates = ", ".join(f"{c} = new.{c}" for c in RAW_COLUMNS if c != "raw_key")
        conn.execute(sqlalchemy.text(
            f"INSERT INTO {table} ({columns}) VALUES ({values}) AS new ON DUPLICATE KEY UPDATE {updates}"
        ), row)
    return True


def upsert_raw(source: str, raw_key: str, resp: requests.Response, problem: str | None = None) -> str:
    """把 API 回應原樣存進 raw 表；失敗時存完再報錯。回傳存到哪裡，例如 raw_notice/20261113。

    problem：呼叫的人檢查內容後發現的問題（例如回傳的年份不對）；有給就當失敗。
    """
    body = parse_body(resp.text)
    ok = resp.ok and not isinstance(body, str) and problem is None

    request_body = resp.request.body   # POST 的參數；GET 為 None
    if isinstance(request_body, bytes):
        request_body = request_body.decode("utf-8")

    upsert_raw_row(source, raw_key, fetched_at=date_utils.now(), http_status=resp.status_code, ok=ok,
                 problem=None if ok else (problem or f"HTTP {resp.status_code}"),
                 url=resp.url, request_body=request_body, body=resp.text)

    where = f"{raw_table_name(source)}/{raw_key}"
    if not ok:
        reason = problem or f"HTTP {resp.status_code}"
        raise RuntimeError(f"{source} {raw_key} 抓取失敗（{reason}），回應已存到 {where}")
    return where


# ---------- raw 表：讀（只讀成功的列）----------
def to_envelope(source: str, row) -> dict:
    """資料庫的一列 → {"meta": {抓取紀錄}, "body": 解析好的 JSON}。"""
    fetched_at = row.fetched_at.replace(tzinfo=date_utils.TAIPEI)
    return {
        "meta": {
            "source": source,
            "key": row.raw_key,
            "url": row.url,
            "request_body": json.loads(row.request_body) if row.request_body else None,
            "http_status": row.http_status,
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
        },
        "body": parse_body(row.body),
    }


def read_raw(source: str, raw_key: str) -> dict | None:
    """讀一份成功的回應；沒有就回傳 None。"""
    raws = read_raws(source, [raw_key])
    return raws[0] if raws else None


def read_raws(source: str, raw_keys: list[str] | None = None) -> list[dict]:
    """讀多份成功的回應，依 raw_key 排序；raw_keys 不給就讀這個來源全部。"""
    sql = f"SELECT {', '.join(RAW_COLUMNS)} FROM {raw_table_name(source)} WHERE ok"
    params = {}
    if raw_keys is not None:
        if not raw_keys:
            return []
        sql += " AND raw_key IN :raw_keys"
        params["raw_keys"] = list(raw_keys)
    statement = sqlalchemy.text(sql + " ORDER BY raw_key")
    if raw_keys is not None:
        statement = statement.bindparams(sqlalchemy.bindparam("raw_keys", expanding=True))   # IN 後面放一串值

    with get_engine().connect() as conn:
        rows = conn.execute(statement, params).fetchall()
    return [to_envelope(source, row) for row in rows]


def list_raw_keys(source: str) -> list[str]:
    """這個來源有哪些成功的 raw_key，排序後回傳。"""
    with get_engine().connect() as conn:
        rows = conn.execute(sqlalchemy.text(f"SELECT raw_key FROM {raw_table_name(source)} WHERE ok ORDER BY raw_key"))
        return [row.raw_key for row in rows]


def raw_exists(source: str, raw_key: str) -> bool:
    """有沒有這份成功的回應（不讀內容，比 read_raw 快）。"""
    with get_engine().connect() as conn:
        found = conn.execute(sqlalchemy.text(f"SELECT 1 FROM {raw_table_name(source)} WHERE raw_key = :raw_key AND ok"),
                             {"raw_key": raw_key}).scalar()
    return found is not None


# ---------- stage 表：讀 ----------
def stage_has_day(table: str, column: str, day: datetime.date) -> bool:
    """stage 表裡有沒有這天的列，例如 stage_has_day("stage_notice_day", "trade_date", date(2026, 11, 13))。"""
    if table not in STAGE_KEYS:
        raise ValueError(f"不認識的 stage 表：{table}")   # 表名拼進 SQL，只接受認識的表
    with get_engine().connect() as conn:
        found = conn.execute(sqlalchemy.text(f"SELECT 1 FROM {table} WHERE {column} = :day LIMIT 1"),
                             {"day": day}).scalar()
    return found is not None


# ---------- stage、app 表：寫 ----------
def upload_data_to_mysql(conn, table: str, df: pd.DataFrame) -> None:
    """把 df 加到表的最後面（不刪舊資料）。

    method="multi"、chunksize=1000：一句 INSERT 帶 1000 列，比一列一句快很多。
    """
    if df.empty:   # 沒資料就不用寫
        return
    df.to_sql(table, conn, if_exists="append", index=False, method="multi", chunksize=1000)


def delete_where_in(conn, table: str, column: str, values: list) -> None:
    """刪掉 column 是這些值的列，例如刪掉某幾天的注意股。"""
    if not values:
        return
    statement = sqlalchemy.text(f"DELETE FROM {table} WHERE {column} IN :values")
    statement = statement.bindparams(sqlalchemy.bindparam("values", expanding=True))   # IN 後面放一串值
    conn.execute(statement, {"values": list(values)})


def upsert_rows(conn, table: str, df: pd.DataFrame, keys: list[str]) -> None:
    """用主鍵更新：先刪掉主鍵相同的舊列，再寫入 df（新的就是新增）。keys：主鍵欄位。"""
    if df.empty:
        return

    # 每一筆要刪的主鍵；.item()：把 numpy 的數字轉成一般的 Python 數字，資料庫套件才看得懂
    key_rows = []
    for record in df[keys].drop_duplicates().to_dict("records"):
        key_rows.append({k: (v.item() if hasattr(v, "item") else v) for k, v in record.items()})

    condition = " AND ".join(f"{k} = :{k}" for k in keys)
    conn.execute(sqlalchemy.text(f"DELETE FROM {table} WHERE {condition}"), key_rows)   # 給一串參數：每筆刪一次
    upload_data_to_mysql(conn, table, df)
