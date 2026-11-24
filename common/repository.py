"""repository 層：只負責把資料存進 PostgreSQL、從 PostgreSQL 讀回來，不知道資料從哪個 API 來、被整理過什麼。

  連線、建表   get_engine()、create_database()、drop_database()、
               create_tables()（schema/*.sql 改過才做、回補每次做）、ensure_tables()（producer 用）、
               schema_hash()、applied_schema_hash()（比對雜湊值，見 main.py 的 refresh_schema）
  raw 表       寫：save_raw()、save_raw_record()（只新增、不修改）
               讀：read_raw()、read_raws()、list_raw_keys()、raw_fetched_times()（每個 raw_key 只看最新一份成功的）、
                   raw_keys_done()（成功或永久失敗的，不用再抓）
  查詢         query_rows()（AI 讀資料用）
  stage、app   insert_rows()、delete_where_in()、has_newer_rows()、upsert_rows()
  任務歷史     insert_job_run()（job_run 表，只新增）
  一列寫入     insert_one()（任務一次寫一列：raw、ai_* 表）
  AI（ai/）    讀：watched_companies()（新聞線要查哪些公司）、
                   list_unlabeled_announcements()、read_announcement()、has_announcement_label()、
                   list_unsummarized_news()、read_news()、has_news_summary()
                   list_news_to_embed()、news_story_links()、stock_news_candidates()（新聞 RAG，pgvector）、
                   list_unsummarized_cards()、card_facts()、recent_announcements()、has_card_summary()、previous_card_summary()
               寫：insert_announcement_label()、insert_news_summary()、save_news_embeddings()、save_news_stories()、insert_card_summary()、
                   save_llm_call()（ai_* 表只新增，不修改；新聞向量、同一件事例外：內容變了就重轉、重新歸類）

寫入的函式都收 conn：呼叫的人用 `with get_engine().begin() as conn:` 開一個交易，全部成功才寫入。
例外：任務一次只寫一列（或一批，例如一天的新聞向量）的（raw 表、ai_* 表、job_run）自己開交易——沒有別的寫入要跟它一起成功。
連線設定從 .env 讀（見 config.py）。
"""
import datetime
import functools
import hashlib
import json
import re
import urllib.parse

import pandas as pd
import requests
import sqlalchemy
import sqlalchemy.dialects.postgresql

from common import date_utils
from common.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, PROJECT_ROOT

# 建表的 SQL 檔：schema/ 底下所有 .sql，依檔名執行（ai → app → log → raw → stage → view；view 要用到 stage 表，排最後）
SQL_FILES = sorted((PROJECT_ROOT / "schema").glob("*.sql"))

# 每個資料庫裡的表都放在這個 schema；連線時設成 search_path，SQL 直接寫表名就找得到（也可以寫 tw_stock.<表名>）
SCHEMA = "tw_stock"

# raw 表：一個來源一張，表名 raw_<來源>
SOURCES = ("notice", "punish", "punish_watchlist", "market_daily", "stock_price", "holiday", "company", "announcement",
           "announcement_detail", "stock_news", "news_detail")
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
    # 逾時：卡住的不要一直佔著。等鎖最多 1 分鐘（等不到算暫時性的錯誤，會重試）、
    # 交易開著不做事最多 5 分鐘（worker 當掉留下的交易會被收掉）。一句 SQL 的時間不限：回補、analyze 的大查詢可能跑很久
    options = (f"-c timezone=Asia/Taipei -c search_path={SCHEMA} -c lock_timeout=1min "
               "-c idle_in_transaction_session_timeout=5min")
    return sqlalchemy.create_engine(url, pool_pre_ping=True, connect_args={"options": options})


def admin_execute(sql: str) -> None:
    """在伺服器層級執行一句 SQL（CREATE／DROP DATABASE 不能放在交易裡，所以用 AUTOCOMMIT）。"""
    with get_engine("").connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sqlalchemy.text(sql))


# 同時建同一個資料庫時，後建的會得到的 SQLSTATE：42P04＝資料庫已經存在，23505＝系統表的唯一鍵重複（兩邊幾乎同時建）
DATABASE_EXISTS_ERRORS = ("42P04", "23505")


def create_database(database: str = DB_NAME) -> None:
    """資料庫不存在就建（PostgreSQL 沒有 CREATE DATABASE IF NOT EXISTS，先查再建）。
    兩個 producer 同時查到「不存在」、同時建（例如股價線、新聞線同時開始）：
    後建的那個會得到「已經存在」（DATABASE_EXISTS_ERRORS），當成成功。"""
    with get_engine("").connect() as conn:
        exists = conn.execute(sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                              {"name": database}).scalar()
    if not exists:
        try:
            admin_execute(f"CREATE DATABASE \"{database}\" ENCODING 'UTF8' TEMPLATE template0")
        except sqlalchemy.exc.DBAPIError as e:
            if getattr(e.orig, "sqlstate", None) not in DATABASE_EXISTS_ERRORS:
                raise


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


# create_tables 的鎖（PostgreSQL advisory lock 的代號，隨便一個固定的數字）
SCHEMA_LOCK_KEY = 20261123


def create_tables(database: str = DB_NAME) -> None:
    """建資料庫，再執行 schema/*.sql 建表。已經存在的表跳過，不刪任何表（欄位改了要自己 ALTER）；VIEW 每次重建。

    整個交易會鎖住很多表（建索引、COMMENT、DROP VIEW），所以只在 schema/*.sql 改過時跑
    （main.py 比對 COMMENT ON SCHEMA 的雜湊值；回補、docker compose 的 setup 每次跑），
    producer 只用 ensure_tables 確認有表。同一時間只讓一個執行（兩個同時 DROP、CREATE VIEW 會 deadlock）：
    交易一開始先拿 advisory lock，別人拿著就等，交易結束自動放掉。
    """
    create_database(database)
    with get_engine(database).begin() as conn:
        # 等鎖最多 10 分鐘（連線預設 1 分鐘）：DROP VIEW 要等網頁上正在查的 SQL 做完，只有改過 schema 才跑，等久一點沒關係
        conn.execute(sqlalchemy.text("SET LOCAL lock_timeout = '10min'"))
        conn.execute(sqlalchemy.text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK_KEY})
        run_sql_files(conn)
        # 記下這次用的 schema/*.sql（雜湊值是 16 進位字串，直接放進 SQL 沒問題）：main.py 比對，改過才再建
        conn.execute(sqlalchemy.text(f"COMMENT ON SCHEMA {SCHEMA} IS '{schema_hash()}'"))


@functools.cache
def schema_hash() -> str:
    """schema/*.sql 全部內容的雜湊值：改了任何一個檔就不一樣。"""
    digest = hashlib.sha256()
    for sql_file in SQL_FILES:
        # 換行統一成 \n：Windows（CRLF）和 Docker／Linux（LF）看到的同一份檔案算出一樣的值，不會互相以為改過
        digest.update(sql_file.name.encode() + b"\0" + sql_file.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def applied_schema_hash() -> str | None:
    """資料庫上次建表、更新 VIEW 時用的 schema/*.sql 雜湊值（create_tables 記在 SCHEMA 的 COMMENT）。
    記在資料庫、不記在本機檔案：還原舊的備份、換一個資料庫，記的值跟著換，就會重建。
    schema 還不存在，或還沒記過是 None。資料庫不存在就先建（全新安裝時 main.py 第一個查的就是這裡）；
    連不上這類錯誤照樣丟出來，不會當成 schema 改了去建表。"""
    create_database()
    return query_rows("SELECT obj_description(to_regnamespace(:schema), 'pg_namespace') AS h", {"schema": SCHEMA})[0].h


@functools.cache
def schema_objects() -> list[str]:
    """schema/*.sql 會建的表、索引、VIEW 的名稱（CREATE TABLE／INDEX／VIEW 後面的名字，註解裡的不算）。"""
    names = []
    for sql_file in SQL_FILES:
        code = "\n".join(line.split("--")[0] for line in sql_file.read_text(encoding="utf-8").splitlines())
        names += re.findall(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?(?:TABLE|INDEX|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
                            code, re.IGNORECASE)
    return names


def ensure_tables(database: str = DB_NAME) -> None:
    """schema/*.sql 的表、索引、VIEW 有缺（全新的資料庫、加了新的）才 create_tables；都在就只查一句，不鎖任何表。
    producer 開始時用。新加的欄位、改了 VIEW 的內容查不出來（名字都在）：要跑一次 main.py 或 docker compose 的 setup。"""
    try:
        with get_engine(database).connect() as conn:
            missing = conn.execute(sqlalchemy.text(
                "SELECT name FROM unnest(CAST(:names AS text[])) AS name WHERE to_regclass(:schema || '.' || name) IS NULL"),
                {"names": schema_objects(), "schema": SCHEMA}).scalars().all()
    except sqlalchemy.exc.OperationalError:   # 資料庫還沒建（create_tables 會建）
        missing = ["（資料庫）"]
    if missing:
        create_tables(database)


# ---------- raw 表：寫（只新增、不修改）----------
class FetchError(RuntimeError):
    """抓取失敗（回應已經存進 raw 表）。retryable：等一下再試可能會好（伺服器錯誤 5xx、請求太多 429）。"""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def is_temporary_status(status_code: int) -> bool:
    """HTTP 狀態碼是暫時性的失敗（伺服器錯誤 5xx、請求太多 429）：等一下再試可能會好。

    >>> is_temporary_status(503), is_temporary_status(429), is_temporary_status(404)
    (True, True, False)
    """
    return status_code >= 500 or status_code == 429


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
    insert_one(raw_table_name(source), row)


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
        retryable = problem is None and is_temporary_status(resp.status_code)   # 內容不對的，重試也一樣
        raise FetchError(f"{source} {raw_key} 抓取失敗（{reason}），回應已存到 {where}", retryable)
    return where


def save_raw_record(source: str, raw_key: str, *, url: str, http_status: int | None, body: dict,
                    problem: str | None = None) -> str:
    """不是整份回應原樣存的（新聞網頁：只存 <head>，見 crawler/task.py 的 crawl_news_detail）：body 自己組好再存，回傳存到哪裡。

    problem：有給就存成失敗（ok＝否）。和 save_raw 不同，失敗不丟錯誤，要不要重試由呼叫的人決定。
    http_status：沒打（例如轉址連結）為 None。
    """
    insert_raw_row(source, raw_key, fetched_at=date_utils.now(), http_status=http_status, ok=problem is None,
                   problem=problem, url=url, request_body=None, body=json.dumps(body, ensure_ascii=False))
    return f"{raw_table_name(source)}/{raw_key}"


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


def latest_raw_key(source: str, upto: str) -> str | None:
    """這個來源有成功回應、而且 <= upto 的 raw_key 裡最新的一個（只有最新一份的來源用）；沒有就回傳 None。"""
    with get_engine().connect() as conn:
        return conn.execute(sqlalchemy.text(
            f"SELECT MAX(raw_key) FROM {raw_table_name(source)} WHERE ok AND raw_key <= :upto"), {"upto": upto}).scalar()


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


def raw_keys_done(source: str) -> set[str]:
    """不用再抓的 raw_key：有成功的，或失敗但不是暫時性的（被擋 4xx、轉址抓不到、不是網頁；HTTP 5xx、429 下次再試）。"""
    with get_engine().connect() as conn:
        rows = conn.execute(sqlalchemy.text(
            f"SELECT DISTINCT raw_key FROM {raw_table_name(source)} "
            "WHERE ok OR NOT (COALESCE(http_status, 0) >= 500 OR COALESCE(http_status, 0) = 429)"))   # 沒打的 NULL 算永久
        return {row.raw_key for row in rows}


# ---------- 查詢 ----------
def query_rows(sql: str, params: dict) -> list:
    """執行一句 SELECT，回傳每一列（row.欄位 取值）。AI 讀 stage 用。"""
    with get_engine().connect() as conn:
        return conn.execute(sqlalchemy.text(sql), params).fetchall()


# ---------- stage、app 表 ----------
# 表的結構（欄位、型別）讀一次就記住：每次寫入都從資料庫重讀要好幾句查系統表的 SQL，比寫資料本身還多。
# key 是（資料庫網址, 表名），測試用的資料庫和正式的分開記；改了 schema 本來就要重啟 worker
_TABLES: dict[tuple[str, str], sqlalchemy.Table] = {}


def table_of(conn, name: str) -> sqlalchemy.Table:
    """這個連線的資料庫裡，表的結構（第一次從資料庫讀，之後用記住的）。"""
    key = (str(conn.engine.url), name)
    if key not in _TABLES:
        _TABLES[key] = sqlalchemy.Table(name, sqlalchemy.MetaData(), autoload_with=conn)
    return _TABLES[key]


def insert_chunk(pd_table, conn, columns: list[str], data_iter) -> None:
    """df.to_sql 每 1000 列呼叫一次：用資料庫裡表的欄位型別組 INSERT。

    不用 pandas 自己猜的型別：整欄都是 None 的日期欄，pandas 會當成文字，PostgreSQL 不會自動轉成 DATE。
    """
    table = table_of(conn, pd_table.name)
    conn.execute(sqlalchemy.insert(table).values([dict(zip(columns, row, strict=True)) for row in data_iter]))


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
    table = table_of(conn, pd_table.name)
    insert = sqlalchemy.dialects.postgresql.insert(table).values([dict(zip(columns, row, strict=True)) for row in data_iter])
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


# ---------- 任務歷史 ----------
def insert_job_run(row: dict) -> None:
    """任務歷史新增一列（欄位見 schema/log.sql）。自己開一個交易：和任務本身的寫入分開，任務失敗也記得下來。"""
    statement = sqlalchemy.text(
        "INSERT INTO tw_stock.job_run (task, args, status, result, error, attempt, mode, worker, started_at, finished_at, seconds) "
        "VALUES (:task, CAST(:args AS JSONB), :status, :result, :error, :attempt, :mode, :worker, :started_at, :finished_at, :seconds)")
    with get_engine().begin() as conn:
        conn.execute(statement, {**row, "args": json.dumps(row["args"], ensure_ascii=False, default=str)})


# ---------- 一列寫入（任務一次寫一列）----------
# 表名會拼進 SQL：只接受 raw 表和這些
ONE_ROW_TABLES = ("ai_llm_call", "ai_announcement_label", "ai_news_summary", "ai_card_summary")


def insert_one(table: str, row: dict, on_conflict: str = "") -> None:
    """新增一列，自己開一個交易（任務一次只寫一列，沒有別的寫入要跟它一起成功）。

    on_conflict：主鍵撞到時怎麼辦，例如 "ON CONFLICT (...) DO NOTHING"；沒給就是一般 INSERT（撞到會報錯）。
    """
    if table not in ONE_ROW_TABLES and table not in {raw_table_name(s) for s in SOURCES}:
        raise ValueError(f"insert_one 不寫這張表：{table}")
    columns = ", ".join(row)
    values = ", ".join(f":{c}" for c in row)
    with get_engine().begin() as conn:
        conn.execute(sqlalchemy.text(f"INSERT INTO tw_stock.{table} ({columns}) VALUES ({values}) {on_conflict}"), row)


# ---------- AI（ai/）----------
# 同一筆、同一個 prompt 版本呼叫 LLM 失敗（拒答、回答被截斷）幾次以後就不再問：新聞線一天跑 6 次、往前看好幾天，
# 不擋的話同一筆會一直重問、一直花錢，每一輪也都算失敗。改 prompt（換版本）就重新算
MAX_FAILED_CALLS = 3


def not_given_up(task: str, ref_key_sql: str) -> str:
    """WHERE 的一段：這一筆（ref_key＝ref_key_sql）這一版（:version）失敗還沒到 MAX_FAILED_CALLS 次。task、ref_key_sql 是程式寫死的。"""
    return (f"  AND (SELECT count(*) FROM tw_stock.ai_llm_call f WHERE f.status = 'failure' AND f.task = '{task}' "
            f"       AND f.ref_key = {ref_key_sql} AND f.prompt_version = :version) < {MAX_FAILED_CALLS} ")


def repeatedly_skipped(task: str, mark: str, arg: str) -> set[str]:
    """job_run 裡最近 3 天這個任務、結果有 mark 的次數到 MAX_FAILED_CALLS 的那些（args 的 arg 欄，例如 news_id）。
    例如新聞摘要：網頁打不開、全文太短不呼叫 LLM（ai_llm_call 沒有紀錄），用任務歷史數跳過幾次。"""
    rows = query_rows(
        "SELECT args ->> :arg AS key FROM tw_stock.job_run "
        "WHERE task = :task AND status = 'success' AND result LIKE '%' || :mark || '%' "
        "  AND started_at >= LOCALTIMESTAMP - INTERVAL '3 days' "   # 只看最近 3 天：網站暫時擋一下，過幾天照樣再試
        "GROUP BY 1 HAVING count(*) >= :times",
        {"arg": arg, "task": task, "mark": mark, "times": MAX_FAILED_CALLS})
    return {row.key for row in rows}


def list_unlabeled_announcements(start: datetime.date, end: datetime.date, prompt_version: str) -> list[tuple[str, datetime.date, int]]:
    """這個 prompt 版本還沒分類、已經有全文的公告：(公司, 輸入日期, 序號)。

    範圍：區間內發言的，加上全文在區間內才寫進來的（新被關注的公司，crawler 會補抓當月和上個月的公告，發言日期比較早）。
    """
    rows = query_rows(
        "SELECT a.company_id, a.enter_date, a.serial_number "
        "FROM tw_stock.stage_announcement a "
        "JOIN tw_stock.stage_announcement_detail d USING (announcement_id) "
        "WHERE (a.announce_date BETWEEN :start AND :end OR CAST(d.loaded_at AS DATE) BETWEEN :start AND :end) "
        "  AND NOT EXISTS (SELECT 1 FROM tw_stock.ai_announcement_label l "
        "                  WHERE l.announcement_id = a.announcement_id AND l.prompt_version = :version) "
        + not_given_up("classify_announcement", "a.announcement_id") +
        "ORDER BY a.announce_date, a.company_id, a.enter_date, a.serial_number",
        {"start": start, "end": end, "version": prompt_version})
    return [(row.company_id, row.enter_date, row.serial_number) for row in rows]


def read_announcement(company_id: str, enter_date: datetime.date, serial_number: int) -> dict | None:
    """一則公告：主旨、發言日期、符合條款（含簡稱）、事實發生日、全文。還沒有全文就回傳 None。"""
    rows = query_rows(
        "SELECT a.announcement_id, a.company_id, a.company_name, a.announce_date, a.subject, "
        "       d.announce_clause, c.short_name AS clause_name, d.fact_date, d.content "
        "FROM tw_stock.stage_announcement a "
        "JOIN tw_stock.stage_announcement_detail d USING (announcement_id) "
        "LEFT JOIN tw_stock.stage_announcement_clause c ON c.announce_clause = d.announce_clause "
        "WHERE a.company_id = :company_id AND a.enter_date = :enter_date AND a.serial_number = :serial_number",
        {"company_id": company_id, "enter_date": enter_date, "serial_number": serial_number})
    return rows[0]._asdict() if rows else None


def has_announcement_label(announcement_id: str, prompt_version: str) -> bool:
    """這則公告、這個 prompt 版本已經分類過了嗎（分類過就不再花錢問）。"""
    return bool(query_rows(
        "SELECT 1 FROM tw_stock.ai_announcement_label WHERE announcement_id = :announcement_id AND prompt_version = :version",
        {"announcement_id": announcement_id, "version": prompt_version}))


def insert_announcement_label(row: dict) -> None:
    """公告分類的結果新增一列（欄位見 schema/ai.sql）。同一則、同一版已經有了就不動（同一個任務被送了兩次）。"""
    insert_one("ai_announcement_label", row, "ON CONFLICT (announcement_id, prompt_version) DO NOTHING")


def list_unsummarized_news(start: datetime.date, end: datetime.date, prompt_version: str, min_text_chars: int) -> list[str]:
    """這個 prompt 版本還沒摘要的新聞（news_id）：新聞日期在區間內、有抓到網頁、全文至少 min_text_chars 字，
    而且是區間內被關注的上市公司的新聞（stage_watched_stock：往前 30 個交易日被列注意過，和卡片牆同一批）。
    其他公司的新聞（例如一次抓全部上市公司的回補）不摘要。"""
    rows = query_rows(
        "SELECT n.news_id FROM tw_stock.stage_news n "
        "JOIN tw_stock.stage_news_detail d USING (news_id) "
        "WHERE n.news_date BETWEEN :start AND :end AND d.text_chars >= :min_chars "
        "  AND EXISTS (SELECT 1 FROM tw_stock.stage_stock_news sn JOIN tw_stock.stage_watched_stock w ON w.stock_id = sn.stock_id "
        "              WHERE sn.news_id = n.news_id AND w.day BETWEEN :start AND :end AND w.is_company) "
        "  AND NOT EXISTS (SELECT 1 FROM tw_stock.ai_news_summary s "
        "                  WHERE s.news_id = n.news_id AND s.prompt_version = :version) "
        + not_given_up("summarize_news", "n.news_id") +
        "ORDER BY n.news_time, n.news_id",
        {"start": start, "end": end, "min_chars": min_text_chars, "version": prompt_version})
    return [row.news_id for row in rows]


def read_news(news_id: str) -> dict | None:
    """一則新聞：連結、標題、媒體、日期。不在 stage_news 就回傳 None。"""
    rows = query_rows("SELECT news_id, link, title, publisher, news_date FROM tw_stock.stage_news WHERE news_id = :news_id",
                      {"news_id": news_id})
    return rows[0]._asdict() if rows else None


def has_news_summary(news_id: str, prompt_version: str) -> bool:
    """這則新聞、這個 prompt 版本已經摘要過了嗎（摘要過就不再花錢問）。"""
    return bool(query_rows("SELECT 1 FROM tw_stock.ai_news_summary WHERE news_id = :news_id AND prompt_version = :version",
                           {"news_id": news_id, "version": prompt_version}))


def insert_news_summary(row: dict) -> None:
    """新聞摘要的結果新增一列（欄位見 schema/ai.sql）。同一則、同一版已經有了就不動（同一個任務被送了兩次）。"""
    insert_one("ai_news_summary", row, "ON CONFLICT (news_id, prompt_version) DO NOTHING")


# ---------- 新聞 RAG（pgvector）----------
# 新聞 n 最新一版、通過檢查的 AI 摘要（子查詢；新聞要叫 n）
LATEST_NEWS_SUMMARY = ("(SELECT a.summary FROM tw_stock.ai_news_summary a WHERE a.news_id = n.news_id AND a.summary_ok "
                       "ORDER BY a.summarized_at DESC LIMIT 1)")

# 好幾則新聞共用同一句導言（網站介紹、個股概覽頁）不是這則新聞的內容，當成沒有；和 app_stock_news（schema/view.sql）同一個門檻
SHARED_LEAD_MIN_NEWS = 3


def list_news_to_embed(start: datetime.date, end: datetime.date, model: str) -> list[dict]:
    """新聞日期在區間內的新聞：標題、新聞摘要（最新一版、通過檢查的）、導言（網站共用的當成沒有），
    和這個模型上次轉的文字的雜湊值（還沒轉過是 None）。要不要重轉由 task 比對雜湊值決定。
    ai 只讀 stage 和 AI 結果，不讀 app 的 VIEW（ai 在新聞線，和股價線的 analyze 互不等）。"""
    rows = query_rows(
        f"SELECT n.news_id, n.title, {LATEST_NEWS_SUMMARY} AS ai_summary, "
        "       CASE WHEN shared.news_count >= :shared_min THEN NULL ELSE d.lead END AS lead, e.input_hash "
        "FROM tw_stock.stage_news n "
        "LEFT JOIN tw_stock.stage_news_detail d ON d.news_id = n.news_id "
        "LEFT JOIN (SELECT lead, COUNT(*) AS news_count FROM tw_stock.stage_news_detail "
        "           WHERE lead IS NOT NULL GROUP BY lead) shared ON shared.lead = d.lead "
        "LEFT JOIN tw_stock.ai_news_embedding e ON e.news_id = n.news_id AND e.model = :model "
        "WHERE n.news_date BETWEEN :start AND :end "
        "ORDER BY n.news_id",
        {"start": start, "end": end, "model": model, "shared_min": SHARED_LEAD_MIN_NEWS})
    return [row._asdict() for row in rows]


def save_news_embeddings(rows: list[dict]) -> None:
    """一批新聞向量寫進 ai_news_embedding（一個交易）；同一則、同一個模型已經有就換成新的（文字變了才會重轉）。
    embedding 是 pgvector 的字串 "[…]"。"""
    with get_engine().begin() as conn:
        conn.execute(sqlalchemy.text(
            "INSERT INTO tw_stock.ai_news_embedding (news_id, model, embedding, input_hash) "
            "VALUES (:news_id, :model, CAST(:embedding AS vector), :input_hash) "
            "ON CONFLICT (news_id, model) DO UPDATE SET embedding = EXCLUDED.embedding, input_hash = EXCLUDED.input_hash, "
            "  embedded_at = LOCALTIMESTAMP(0)"), rows)


def news_story_links(start: datetime.date, end: datetime.date, model: str, threshold: float, days: int, min_title_chars: int
                     ) -> tuple[list[str], list[tuple[str, str, float]], dict[str, str]]:
    """同一件事的判斷要的資料（retriever.assign_stories）：
    區間內有向量的新聞（照時間排）；每則和 days 天內、同一檔股票、比它早的新聞，
    同一篇（標題一樣，或一則是另一則的開頭、短的至少 min_title_chars 字）或相似度 ≥ threshold 的配對；
    區間之前已經歸好的 story。"""
    params = {"start": start, "end": end, "model": model, "threshold": threshold, "days": days, "min_chars": min_title_chars}
    news = query_rows(
        "SELECT n.news_id FROM tw_stock.stage_news n "
        "JOIN tw_stock.ai_news_embedding e ON e.news_id = n.news_id AND e.model = :model "
        "WHERE n.news_date BETWEEN :start AND :end ORDER BY n.news_time, n.news_id", params)
    links = query_rows(
        "SELECT DISTINCT a.news_id, b.news_id AS earlier_id, 1 - (ea.embedding <=> eb.embedding) AS similarity "
        "FROM tw_stock.stage_news a "
        "JOIN tw_stock.ai_news_embedding ea ON ea.news_id = a.news_id AND ea.model = :model "
        "JOIN tw_stock.stage_stock_news sa ON sa.news_id = a.news_id "
        "JOIN tw_stock.stage_stock_news sb ON sb.stock_id = sa.stock_id AND sb.news_id <> a.news_id "
        "JOIN tw_stock.stage_news b ON b.news_id = sb.news_id "
        "JOIN tw_stock.ai_news_embedding eb ON eb.news_id = b.news_id AND eb.model = :model "
        "WHERE a.news_date BETWEEN :start AND :end "
        "  AND (b.news_time, b.news_id) < (a.news_time, a.news_id) AND b.news_time >= a.news_time - make_interval(days => :days) "
        "  AND (b.title = a.title "
        "       OR (LEAST(length(a.title), length(b.title)) >= :min_chars "
        "           AND (starts_with(a.title, b.title) OR starts_with(b.title, a.title))) "
        "       OR 1 - (ea.embedding <=> eb.embedding) >= :threshold)", params)
    known = query_rows(
        "SELECT st.news_id, st.story_id FROM tw_stock.ai_news_story st JOIN tw_stock.stage_news n USING (news_id) "
        "WHERE st.model = :model AND n.news_date BETWEEN CAST(:start AS DATE) - :days AND CAST(:start AS DATE) - 1", params)
    return ([row.news_id for row in news], [(row.news_id, row.earlier_id, row.similarity) for row in links],
            {row.news_id: row.story_id for row in known})


def save_news_stories(rows: list[dict]) -> None:
    """一批新聞的 story 寫進 ai_news_story（一個交易）；已經有就換成新的（向量重轉、門檻調整、換模型後重新歸類）。"""
    with get_engine().begin() as conn:
        conn.execute(sqlalchemy.text(
            "INSERT INTO tw_stock.ai_news_story (news_id, model, story_id) VALUES (:news_id, :model, :story_id) "
            "ON CONFLICT (news_id) DO UPDATE SET model = EXCLUDED.model, story_id = EXCLUDED.story_id, "
            "  grouped_at = LOCALTIMESTAMP(0)"), rows)


def stock_news_candidates(stock_id: str, start: datetime.date, end: datetime.date, model: str, query: str,
                          limit: int = 50) -> list[dict]:
    """這檔區間內有向量的新聞，照和 query（pgvector 的字串）的 cosine 距離排（<=>，最近的在前）。
    similarity＝1－距離；embedding 帶回去給 retriever 判斷重複的報導。"""
    rows = query_rows(
        "SELECT n.news_id, n.title, n.publisher, n.news_date, n.link, st.story_id, "
        f"       {LATEST_NEWS_SUMMARY} AS ai_summary, "
        "       1 - (e.embedding <=> CAST(:query AS vector)) AS similarity, CAST(e.embedding AS TEXT) AS embedding "
        "FROM tw_stock.stage_stock_news s "
        "JOIN tw_stock.stage_news n ON n.news_id = s.news_id "
        "JOIN tw_stock.ai_news_embedding e ON e.news_id = n.news_id AND e.model = :model "
        "LEFT JOIN tw_stock.ai_news_story st ON st.news_id = n.news_id AND st.model = :model "
        "WHERE s.stock_id = :stock_id AND n.news_date BETWEEN :start AND :end "
        "ORDER BY e.embedding <=> CAST(:query AS vector) "
        "LIMIT :limit",
        {"stock_id": stock_id, "start": start, "end": end, "model": model, "query": query, "limit": limit})
    return [row._asdict() for row in rows]


# ---------- 卡片 AI 摘要 ----------


def notice_fetched_at(day: datetime.date) -> datetime.datetime | None:
    """那天的注意股是什麼時候抓的（stage_notice_day.source_fetched_at，raw 的抓取時間，重跑 transform 不會變）；還沒有是 None。
    這之前抓的重大訊息、新聞清單算舊的：新聞線要重抓，卡片 AI 摘要不能用（見 list_unsummarized_cards）。"""
    rows = query_rows("SELECT source_fetched_at FROM tw_stock.stage_notice_day WHERE query_date = :day", {"day": day})
    return rows[0].source_fetched_at if rows else None


def watched_companies(start: datetime.date, end: datetime.date) -> dict[datetime.date, set[str]]:
    """區間內每天要查重大訊息、新聞的上市公司（新聞線用）：stage_watched_stock 那天的名單。
    那天還沒有名單（股價線還沒跑、注意股還沒整理好、休市）就用之前最近一個有名單的交易日：早上、週末也照樣查新聞。
    「有名單」＝stage_notice_day 有那天：只有交易日、收盤行情整理好了，view 已經有那天，但少了當天新被列的。"""
    rows = query_rows(
        "SELECT w.day, w.stock_id, w.is_company FROM tw_stock.stage_watched_stock w "
        "JOIN tw_stock.stage_notice_day nd ON nd.query_date = w.day "   # 那天的注意股整理好了才算有名單
        "WHERE w.day <= :end AND w.day >= ("
        "  SELECT COALESCE(max(w2.day), :start) FROM tw_stock.stage_watched_stock w2 "
        "  JOIN tw_stock.stage_notice_day nd2 ON nd2.query_date = w2.day WHERE w2.day <= :start)",
        {"start": start, "end": end})
    by_day: dict[datetime.date, set[str]] = {}
    for row in rows:   # 只有權證、ETF 的交易日也是「有名單」（那天沒有要查的公司），不沿用前一天的
        companies = by_day.setdefault(row.day, set())
        if row.is_company:
            companies.add(row.stock_id)
    earlier = [d for d in by_day if d < start]   # 查詢只多拿 start 之前最近的那一天
    latest = by_day[max(earlier)] if earlier else set()
    watched = {}
    day = start
    while day <= end:
        latest = by_day.get(day, latest)
        watched[day] = latest
        day += datetime.timedelta(days=1)
    return watched


def list_unsummarized_cards(start: datetime.date, end: datetime.date, prompt_version: str) -> list[tuple[str, datetime.date]]:
    """區間內每個交易日要寫卡片 AI 摘要的上市公司，這一版還沒寫的：(證券代號, 日期)。
    和卡片牆同一批股票（stage_watched_stock：往前 30 個交易日被列注意過），只讀 stage。
    寫了就不會重寫，所以要的資料都到了才寫：
    - 那天的注意股、處置股、收盤行情都整理好（stage_notice_day、stage_punish_day、stage_stock_price 有那天）：
      不然會把今天被列的寫成「今天沒被列注意」、漏掉處置或今天的漲跌。股價線和新聞線可能同時跑，所以三個都要看。
    - 這檔那天的新聞清單、那個月的公告清單，在那天的注意股抓到以後（stage_notice_day.source_fetched_at）又抓過一次
      或已經完整（之後不會再抓：回補時注意股比清單晚抓也算），上個月的公告清單也抓過（30 天的公告可能跨月）：
      當天新被列注意、新聞線還沒抓到的，抓失敗（例如 FinMind 429）的，
      或只有下午舊清單的，不會寫成「沒有公告和新聞」或漏掉傍晚的公告，下一輪抓到了再寫。
      raw 只拿來看「抓過沒、什麼時候抓的」，摘要的內容照樣只讀 stage 和 AI 結果。"""
    rows = query_rows(
        "SELECT w.stock_id, w.day AS trade_date FROM tw_stock.stage_watched_stock w "
        "JOIN tw_stock.stage_notice_day nd ON nd.query_date = w.day "                                 # 那天的注意股、
        "WHERE w.day BETWEEN :start AND :end AND w.is_company "
        "  AND EXISTS (SELECT 1 FROM tw_stock.stage_punish_day pd WHERE pd.query_date = w.day) "   # 處置股、
        "  AND EXISTS (SELECT 1 FROM tw_stock.stage_stock_price sp WHERE sp.trade_date = w.day) "  # 收盤行情都整理好了，
        # 這檔那天的新聞清單、那個月的公告清單：注意股抓到以後又抓過，或已經完整（之後不會再抓；回補時注意股比清單晚抓也算），
        "  AND EXISTS (SELECT 1 FROM tw_stock.raw_stock_news r "
        "              WHERE r.raw_key = w.stock_id || '_' || to_char(w.day, 'YYYYMMDD') AND r.ok "
        "                AND r.fetched_at >= LEAST(COALESCE(nd.source_fetched_at, '-infinity'), "
        "                                          w.day + INTERVAL '1 day 8 hours')) "   # 隔天 08:00 以後抓的＝完整（FinMind 一天是 UTC）
        "  AND EXISTS (SELECT 1 FROM tw_stock.raw_announcement r "
        "              WHERE r.raw_key = w.stock_id || '_' || to_char(w.day, 'YYYYMM') AND r.ok "
        "                AND r.fetched_at >= LEAST(COALESCE(nd.source_fetched_at, '-infinity'), "
        "                                          date_trunc('month', w.day) + INTERVAL '1 month')) "   # 那個月過完以後抓的＝完整
        "  AND EXISTS (SELECT 1 FROM tw_stock.raw_announcement r "               # 上個月的公告清單也抓過才寫
        "              WHERE r.raw_key = w.stock_id || '_' || to_char(w.day - INTERVAL '1 month', 'YYYYMM') AND r.ok) "
        "  AND NOT EXISTS (SELECT 1 FROM tw_stock.ai_card_summary s "
        "                  WHERE s.stock_id = w.stock_id AND s.card_date = w.day AND s.prompt_version = :version) "
        + not_given_up("summarize_card", "md5(w.stock_id || '_' || to_char(w.day, 'YYYYMMDD'))") +   # card_id（ai/task.py）
        "ORDER BY w.day, w.stock_id",
        {"start": start, "end": end, "version": prompt_version})
    return [(row.stock_id, row.trade_date) for row in rows]


def card_facts(stock_id: str, day: datetime.date) -> dict | None:
    """卡片 AI 摘要那天要的事實（只讀 stage）：名稱、今天有沒有被列注意、款次（例如 "1,13"）和官方寫的情況、
    漲跌幅（%，和參考價比）、處置中的期間；今天沒被列注意的，加上最近一次被列注意（last_notice：日期、款次、那天官方寫的情況）。
    不是卡片牆上的股票（那天不在 stage_watched_stock）回傳 None。"""
    params = {"stock_id": stock_id, "day": day}
    facts = query_rows(
        "SELECT CAST(:day AS DATE) AS trade_date, CAST(:stock_id AS VARCHAR) AS stock_id, ws.stock_id IS NOT NULL AS watched, "
        "       COALESCE(n.stock_name, w.stock_name, sp.stock_name) AS stock_name, "
        "       n.stock_id IS NOT NULL AS in_notice, n.reason_text, "
        "       (SELECT string_agg(CAST(c.clause AS TEXT), ',' ORDER BY c.clause) FROM tw_stock.stage_notice_clause c "
        "        WHERE c.stock_id = :stock_id AND c.trade_date = :day) AS clauses, "
        "       ROUND(sp.price_change / NULLIF(sp.close_price - sp.price_change, 0) * 100, 2) AS change_pct "
        "FROM (SELECT 1) one "
        "LEFT JOIN tw_stock.stage_watched_stock ws ON ws.stock_id = :stock_id AND ws.day = :day "
        "LEFT JOIN tw_stock.stage_notice n ON n.stock_id = :stock_id AND n.trade_date = :day "
        "LEFT JOIN tw_stock.stage_punish_watchlist w ON w.stock_id = :stock_id AND w.data_date = :day "
        "LEFT JOIN tw_stock.stage_stock_price sp ON sp.stock_id = :stock_id AND sp.trade_date = :day",
        params)[0]._asdict()
    if not facts.pop("watched"):
        return None
    # 款次表（和 stage_watched_stock、last_notice 一樣）或注意股表，有一個有那天的列就算今天被列注意
    facts["is_listed_today"] = facts.pop("in_notice") or facts["clauses"] is not None
    punish = query_rows(
        "SELECT period_start, period_end, match_interval_minutes FROM tw_stock.stage_punish "
        "WHERE stock_id = :stock_id AND period_start <= :day AND period_end >= :day "
        "ORDER BY announce_date DESC LIMIT 1", params)
    facts["punish"] = punish[0]._asdict() if punish else None
    facts["last_notice"] = None
    if not facts["is_listed_today"]:
        # 在 stage_watched_stock 裡＝往前 30 個交易日內被列注意過（看款次表，和 view 一樣），最近一次就是那天以前最晚的一次；
        # 原因、名稱在 stage_notice（沒有就是 None，照樣寫）
        last = query_rows(
            "SELECT c.trade_date, string_agg(CAST(c.clause AS TEXT), ',' ORDER BY c.clause) AS clauses, "
            "       max(n.stock_name) AS stock_name, max(n.reason_text) AS reason_text "
            "FROM tw_stock.stage_notice_clause c "
            "LEFT JOIN tw_stock.stage_notice n ON n.stock_id = c.stock_id AND n.trade_date = c.trade_date "
            "WHERE c.stock_id = :stock_id AND c.trade_date < :day "
            "GROUP BY c.trade_date ORDER BY c.trade_date DESC LIMIT 1", params)
        if last:
            facts["last_notice"] = last[0]._asdict()
            facts["stock_name"] = facts["stock_name"] or facts["last_notice"]["stock_name"]
    return facts


def recent_announcements(stock_id: str, start: datetime.date, end: datetime.date) -> list[dict]:
    """區間內的公告（舊的在前），含最新一版公告分類的類型、有新消息、壞消息、白話摘要（沒通過檢查的摘要不帶）。
    只讀 stage 和公告分類的結果（stage_announcement、ai_announcement_label）。"""
    rows = query_rows(
        "SELECT a.announce_date, a.subject, l.category, l.has_news, l.is_negative, "
        "       CASE WHEN l.summary_ok THEN l.summary END AS ai_summary "
        "FROM tw_stock.stage_announcement a "
        "LEFT JOIN LATERAL (SELECT x.category, x.has_news, x.is_negative, x.summary, x.summary_ok "
        "                   FROM tw_stock.ai_announcement_label x WHERE x.announcement_id = a.announcement_id "
        "                   ORDER BY x.labeled_at DESC LIMIT 1) l ON TRUE "
        "WHERE a.company_id = :stock_id AND a.announce_date BETWEEN :start AND :end "
        "ORDER BY a.announce_date, a.announce_time",
        {"stock_id": stock_id, "start": start, "end": end})
    return [row._asdict() for row in rows]


def has_card_summary(card_id: str, prompt_version: str) -> bool:
    """這張卡片、這個 prompt 版本已經寫過摘要了嗎。"""
    return bool(query_rows("SELECT 1 FROM tw_stock.ai_card_summary WHERE card_id = :card_id AND prompt_version = :version",
                           {"card_id": card_id, "version": prompt_version}))


def previous_card_summary(stock_id: str, before: datetime.date, prompt_version: str) -> dict | None:
    """這檔、這一版、before 之前最近一天的摘要（輸入一樣就沿用）；沒有就回傳 None。"""
    rows = query_rows(
        "SELECT card_id, prompt_version, stock_id, card_date, summary, news_ids, input_hash, summary_ok, reused, model, summarized_at "
        "FROM tw_stock.ai_card_summary WHERE stock_id = :stock_id AND card_date < :before AND prompt_version = :version "
        "ORDER BY card_date DESC LIMIT 1",
        {"stock_id": stock_id, "before": before, "version": prompt_version})
    return rows[0]._asdict() if rows else None


def insert_card_summary(row: dict) -> None:
    """卡片 AI 摘要的結果新增一列（欄位見 schema/ai.sql）。同一張卡片、同一版已經有了就不動。"""
    insert_one("ai_card_summary", row, "ON CONFLICT (card_id, prompt_version) DO NOTHING")


def save_llm_call(row: dict, problem: str | None = None) -> str:
    """一次 LLM 呼叫記進 ai_llm_call，回傳記到哪裡；有問題的也記，記完丟 FetchError（和 save_raw 一樣）。

    row：ai/client.py 的 call_record()。
    problem：ai/client.py 的 json_answer_problem() 找到的問題（拒答、被截斷、不是 JSON），重問也一樣，不重試。
    """
    insert_one("ai_llm_call", {**row, "status": "failure" if problem else "success", "error": problem})
    where = f"ai_llm_call/{row['ref_key']}"
    if problem:
        raise FetchError(f"{row['task']} {row['ref_key']} 失敗（{problem}），已記到 {where}", retryable=False)
    return where
