"""repository 層：只負責把資料存進 PostgreSQL、從 PostgreSQL 讀回來，不知道資料從哪個 API 來、被整理過什麼。

  連線、建表   get_engine()、create_database()、drop_database()、create_tables()
  raw 表       寫：save_raw()、save_raw_record()（只新增、不修改）
               讀：read_raw()、read_raws()、list_raw_keys()、raw_fetched_times()（每個 raw_key 只看最新一份成功的）、
                   raw_keys_done()（成功或永久失敗的，不用再抓）
  查詢         query_rows()（AI 讀資料用）
  stage、app   insert_rows()、delete_where_in()、has_newer_rows()、upsert_rows()
  任務歷史     insert_job_run()（job_run 表，只新增）
  一列寫入     insert_one()（任務一次寫一列：raw、ai_* 表）
  AI（ai/）    讀：list_unlabeled_announcements()、read_announcement()、has_announcement_label()、
                   list_unsummarized_news()、read_news()、has_news_summary()
               寫：insert_announcement_label()、insert_news_summary()、save_llm_call()（ai_* 表只新增，不修改）

寫入的函式都收 conn：呼叫的人用 `with get_engine().begin() as conn:` 開一個交易，全部成功才寫入。
例外：任務一次只寫一列的（raw 表、ai_* 表、job_run）自己開交易——沒有別的寫入要跟它一起成功。
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
ONE_ROW_TABLES = ("ai_llm_call", "ai_announcement_label", "ai_news_summary")


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
    """這個 prompt 版本還沒摘要的新聞（news_id）：新聞日期在區間內、有抓到網頁、全文至少 min_text_chars 字。"""
    rows = query_rows(
        "SELECT n.news_id FROM tw_stock.stage_news n "
        "JOIN tw_stock.stage_news_detail d USING (news_id) "
        "WHERE n.news_date BETWEEN :start AND :end AND d.text_chars >= :min_chars "
        "  AND NOT EXISTS (SELECT 1 FROM tw_stock.ai_news_summary s "
        "                  WHERE s.news_id = n.news_id AND s.prompt_version = :version) "
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


def save_llm_call(row: dict, problem: str | None = None) -> str:
    """一次 LLM 呼叫記進 ai_llm_call，回傳記到哪裡；有問題的也記，記完丟 FetchError（和 save_raw 一樣）。

    row：ai/client.py 的 call_record()。problem：ai/client.py 的 xxx_problem() 找到的問題（拒答、被截斷、不是 JSON），重問也一樣，不重試。
    """
    insert_one("ai_llm_call", {**row, "status": "failure" if problem else "success", "error": problem})
    where = f"ai_llm_call/{row['ref_key']}"
    if problem:
        raise FetchError(f"{row['task']} {row['ref_key']} 失敗（{problem}），已記到 {where}", retryable=False)
    return where
