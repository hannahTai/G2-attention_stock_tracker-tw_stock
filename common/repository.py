"""repository 層：只負責把資料存進 PostgreSQL、從 PostgreSQL 讀回來，不知道資料從哪個 API 來、被整理過什麼。

  連線、建表   get_engine()、create_database()、drop_database()、create_tables()
  raw 表       寫：save_raw()（只新增、不修改）
               讀：read_raw()、read_raws()、list_raw_keys()、raw_fetched_times()（每個 raw_key 只看最新一份成功的）
  stage、app   insert_rows()、delete_where_in()、has_newer_rows()、upsert_rows()

寫入的函式都收 conn：呼叫的人用 `with get_engine().begin() as conn:` 開一個交易，全部成功才寫入。
連線設定從 .env 讀（見 config.py）。
"""
import datetime
import functools
import json
import urllib.parse

import pandas as pd
import requests
import sqlalchemy
import sqlalchemy.dialects.postgresql

from common import date_utils
from common.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, PROJECT_ROOT

# 建表的 SQL 檔：schema/ 底下所有 .sql，依檔名執行（app → raw → stage → view；view 要用到 stage 表，排最後）
SQL_FILES = sorted((PROJECT_ROOT / "schema").glob("*.sql"))

# 每個資料庫裡的表都放在這個 schema；連線時設成 search_path，SQL 直接寫表名就找得到（也可以寫 tw_stock.<表名>）
SCHEMA = "tw_stock"

# raw 表：一個來源一張，表名 raw_<來源>
SOURCES = ("notice", "punish", "punish_watchlist", "market_daily", "stock_price", "holiday", "company", "announcement",
           "announcement_detail")
RAW_COLUMNS = ["raw_key", "fetched_at", "http_status", "ok", "problem", "url", "request_body", "body"]


# ---------- 連線、建表 ----------
@functools.cache   # 同一個資料庫只建一次 engine，之後重複使用（engine 自己管理連線）
def get_engine(database: str = DB_NAME) -> sqlalchemy.Engine:
    """連線用的 engine。database 給空字串連到伺服器內建的 postgres 資料庫（要建、刪資料庫時用）。"""
    password = urllib.parse.quote_plus(DB_PASSWORD)   # 密碼裡的 @ : / 會把連線字串切錯，先編碼
    url = f"postgresql+psycopg://{DB_USER}:{password}@{DB_HOST}:{DB_PORT}/{database or 'postgres'}"
    # pool_pre_ping：拿連線前先確認還活著，斷了就重連
    # timezone：連線用台灣時間，DEFAULT LOCALTIMESTAMP 才是台灣時間（雲端預設 UTC）
    # search_path：SQL 只寫表名時，到 SCHEMA 找
    options = f"-c timezone=Asia/Taipei -c search_path={SCHEMA}"
    return sqlalchemy.create_engine(url, pool_pre_ping=True, connect_args={"options": options})


def admin_execute(sql: str) -> None:
    """在伺服器層級執行一句 SQL（CREATE／DROP DATABASE 不能放在交易裡，所以用 AUTOCOMMIT）。"""
    with get_engine("").connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sqlalchemy.text(sql))


def create_database(database: str = DB_NAME) -> None:
    """資料庫不存在就建（PostgreSQL 沒有 CREATE DATABASE IF NOT EXISTS，先查再建）。"""
    with get_engine("").connect() as conn:
        exists = conn.execute(sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                              {"name": database}).scalar()
    if not exists:
        admin_execute(f"CREATE DATABASE \"{database}\" ENCODING 'UTF8' TEMPLATE template0")


def drop_database(database: str) -> None:
    """刪掉整個資料庫（測試用；還連著的連線一起斷掉）。"""
    admin_execute(f"DROP DATABASE IF EXISTS \"{database}\" WITH (FORCE)")


def run_sql_files(conn) -> None:
    """建 SCHEMA，再執行 schema/*.sql。一個檔案有好幾句，用分號切開一句一句執行；只有註解的片段跳過。"""
    conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    for sql_file in SQL_FILES:
        for statement in sql_file.read_text(encoding="utf-8").split(";"):
            code = [line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--")]
            if code:
                conn.execute(sqlalchemy.text(statement))


def create_tables(database: str = DB_NAME) -> None:
    """建資料庫，再執行 schema/*.sql 建表。已經存在的表跳過，不刪任何表（欄位改了要自己 ALTER）；VIEW 每次重建。"""
    create_database(database)
    with get_engine(database).begin() as conn:
        run_sql_files(conn)


# ---------- raw 表：寫（只新增、不修改）----------
class FetchError(RuntimeError):
    """抓取失敗（回應已經存進 raw 表）。retryable：等一下再試可能會好（伺服器錯誤 5xx、請求太多 429）。"""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def raw_table_name(source: str) -> str:
    """來源 → 表名，例如 notice → raw_notice。只接受 SOURCES 裡的來源（表名會拼進 SQL）。"""
    if source not in SOURCES:
        raise ValueError(f"不認識的來源：{source}")
    return f"raw_{source}"


def parse_body(text: str) -> dict | list | str:
    """JSON 就解析；不是 JSON（例如被擋時的 HTML）就回傳原文。"""
    try:
        return json.loads(text)
    except ValueError:
        return text


def insert_raw_row(source: str, raw_key: str, *, fetched_at: datetime.datetime, http_status: int, ok: bool,
                   problem: str | None, url: str, request_body: str | None, body: str) -> None:
    """新增一份回應。同一個 raw_key 重抓就多一列，舊的留著（主鍵是 raw_key＋fetched_at）。"""
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
    columns = ", ".join(RAW_COLUMNS)
    values = ", ".join(f":{c}" for c in RAW_COLUMNS)
    with get_engine().begin() as conn:
        conn.execute(sqlalchemy.text(f"INSERT INTO {raw_table_name(source)} ({columns}) VALUES ({values})"), row)


def save_raw(source: str, raw_key: str, resp: requests.Response, problem: str | None = None) -> str:
    """把 API 回應原樣存進 raw 表，回傳存到哪裡（例如 raw_notice/20261113）；失敗的也存，存完丟 FetchError。

    problem：呼叫的人檢查內容後發現的問題（例如回傳的年份不對）；有給就當失敗。
    """
    body = parse_body(resp.text)
    ok = resp.ok and not isinstance(body, str) and problem is None
    reason = None if ok else (problem or f"HTTP {resp.status_code}")

    request_body = resp.request.body   # POST 的參數；GET 是 None
    if isinstance(request_body, bytes):
        request_body = request_body.decode("utf-8")

    insert_raw_row(source, raw_key, fetched_at=date_utils.now(), http_status=resp.status_code, ok=ok,
                   problem=reason, url=resp.url, request_body=request_body, body=resp.text)

    where = f"{raw_table_name(source)}/{raw_key}"
    if not ok:
        retryable = problem is None and (resp.status_code >= 500 or resp.status_code == 429)   # 內容不對的，重試也一樣
        raise FetchError(f"{source} {raw_key} 抓取失敗（{reason}），回應已存到 {where}", retryable)
    return where


# ---------- raw 表：讀（每個 raw_key 只看最新一份成功的）----------
def to_envelope(source: str, row) -> dict:
    """資料庫的一列 → {"meta": {抓取紀錄}, "body": 解析好的 JSON}。"""
    return {
        "meta": {
            "source": source,
            "key": row.raw_key,
            "url": row.url,
            "request_body": json.loads(row.request_body) if row.request_body else None,
            "http_status": row.http_status,
            "fetched_at": row.fetched_at.replace(tzinfo=date_utils.TAIPEI).isoformat(timespec="seconds"),
        },
        "body": parse_body(row.body),
    }


def read_raws(source: str, raw_keys: list[str] | None = None) -> list[dict]:
    """讀每個 raw_key 最新一份成功的回應，依 raw_key 排序；raw_keys 不給就讀這個來源全部。"""
    if raw_keys is not None and not raw_keys:
        return []
    table = raw_table_name(source)
    key_filter = " AND raw_key IN :raw_keys" if raw_keys is not None else ""
    # 子查詢：每個 raw_key 成功的列裡最晚的抓取時間；再用 (raw_key, fetched_at)＝主鍵接回那一列
    statement = sqlalchemy.text(
        f"SELECT {', '.join(RAW_COLUMNS)} FROM {table} "
        f"JOIN (SELECT raw_key, MAX(fetched_at) AS fetched_at FROM {table} WHERE ok{key_filter} GROUP BY raw_key) "
        f"AS latest USING (raw_key, fetched_at) ORDER BY raw_key")
    params = {}
    if raw_keys is not None:
        statement = statement.bindparams(sqlalchemy.bindparam("raw_keys", expanding=True))   # IN 後面放一串值
        params["raw_keys"] = list(raw_keys)

    with get_engine().connect() as conn:
        rows = conn.execute(statement, params).fetchall()
    return [to_envelope(source, row) for row in rows]


def read_raw(source: str, raw_key: str) -> dict | None:
    """讀這個 raw_key 最新一份成功的回應；沒有就回傳 None。"""
    raws = read_raws(source, [raw_key])
    return raws[0] if raws else None


def list_raw_keys(source: str) -> list[str]:
    """這個來源有成功回應的 raw_key，排序後回傳。"""
    with get_engine().connect() as conn:
        rows = conn.execute(sqlalchemy.text(
            f"SELECT DISTINCT raw_key FROM {raw_table_name(source)} WHERE ok ORDER BY raw_key"))
        return [row.raw_key for row in rows]


def raw_fetched_times(source: str) -> dict[str, datetime.datetime]:
    """每個 raw_key 最新一份成功回應的抓取時間（台灣時間），例如 {"20261113": datetime(2026, 11, 13, 21, 0, 5)}。"""
    with get_engine().connect() as conn:
        rows = conn.execute(sqlalchemy.text(
            f"SELECT raw_key, MAX(fetched_at) AS fetched_at FROM {raw_table_name(source)} WHERE ok GROUP BY raw_key"))
        return {row.raw_key: row.fetched_at for row in rows}


# ---------- stage、app 表 ----------
def insert_chunk(pd_table, conn, columns: list[str], data_iter) -> None:
    """df.to_sql 每 1000 列呼叫一次：用資料庫裡表的欄位型別組 INSERT。

    不用 pandas 自己猜的型別：整欄都是 None 的日期欄，pandas 會當成文字，PostgreSQL 不會自動轉成 DATE。
    """
    table = sqlalchemy.Table(pd_table.name, sqlalchemy.MetaData(), autoload_with=conn)   # 從資料庫讀表的結構
    conn.execute(sqlalchemy.insert(table).values([dict(zip(columns, row)) for row in data_iter]))


def insert_rows(conn, table: str, df: pd.DataFrame) -> None:
    """把 df 新增到表裡（一般 INSERT，主鍵重複會報錯）。一句 INSERT 帶 1000 列。"""
    if not df.empty:
        df.to_sql(table, conn, if_exists="append", index=False, method=insert_chunk, chunksize=1000)


def delete_where_in(conn, table: str, column: str, values: list) -> None:
    """刪掉 column 是這些值的列，例如刪掉某一天的注意股。"""
    if values:
        statement = sqlalchemy.text(f"DELETE FROM {table} WHERE {column} IN :values")
        statement = statement.bindparams(sqlalchemy.bindparam("values", expanding=True))
        conn.execute(statement, {"values": list(values)})


def has_newer_rows(conn, table: str, column: str, value, version: str, incoming) -> bool:
    """表裡 column = value 的列，有沒有版本（version 欄）比 incoming 新的。「先清掉一個範圍再寫入」之前用。

    FOR UPDATE：讀的同時鎖住這個範圍，別的交易要等這邊做完，不會兩邊都以為自己比較新。
    PostgreSQL 的 FOR UPDATE 不能直接配 MAX()，所以先在子查詢鎖住那些列，外面再取最大值。
    """
    stored = conn.execute(sqlalchemy.text(
        f"SELECT MAX({version}) FROM (SELECT {version} FROM {table} WHERE {column} = :value FOR UPDATE) AS locked"),
        {"value": value}).scalar()
    return stored is not None and stored > incoming


def upsert_chunk(pd_table, conn, columns: list[str], data_iter, *, keys: list[str], version: str) -> None:
    """df.to_sql 每 1000 列呼叫一次：組成一句 INSERT … ON CONFLICT (主鍵) DO UPDATE … WHERE。

    主鍵撞到時，只有「資料庫裡的版本是 NULL，或這批的版本 >= 資料庫的」才整列換成新值，否則留舊值。
    """
    table = sqlalchemy.Table(pd_table.name, sqlalchemy.MetaData(), autoload_with=conn)   # 從資料庫讀表的結構
    insert = sqlalchemy.dialects.postgresql.insert(table).values([dict(zip(columns, row)) for row in data_iter])
    new, old = insert.excluded, table.c                                                 # new＝這次要寫的值（EXCLUDED）
    updates = {c: new[c] for c in columns if c not in keys}
    updates["loaded_at"] = sqlalchemy.text("LOCALTIMESTAMP(0)")
    is_newer = sqlalchemy.or_(old[version].is_(None), new[version] >= old[version])
    conn.execute(insert.on_conflict_do_update(index_elements=keys, set_=updates, where=is_newer))


def upsert_rows(conn, table: str, df: pd.DataFrame, keys: list[str], version: str) -> None:
    """用主鍵更新：主鍵已經有的就更新、沒有的就新增（upsert），這批沒有的列不動。

    keys：主鍵欄位。version：版本欄位，主鍵撞到時只讓比較新（或一樣新）的覆蓋，見 upsert_chunk()。
    同一批裡主鍵相同的列只留一列（PostgreSQL 一句 INSERT 不能改同一列兩次）：版本最新的，一樣新就留後面的。
    """
    if not df.empty:
        df = df.sort_values(version, kind="stable", na_position="first").drop_duplicates(keys, keep="last")
        df.to_sql(table, conn, if_exists="append", index=False, chunksize=1000,
                  method=functools.partial(upsert_chunk, keys=keys, version=version))
