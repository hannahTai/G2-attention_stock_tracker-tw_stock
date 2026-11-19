"""整合測試：連本機 MySQL，驗證去重與冪等、多個 worker 同時整理也不會錯。

    uv run pytest -m integration -v        # 只跑這個檔案的測試
    uv run pytest -m "not integration"     # 不連資料庫（CI 用）

另外建一個測試用資料庫 <MYSQL_DATABASE>_test，照 schema/*.sql 建表，測完整個刪掉；正式的資料庫完全不碰。
連不到 MySQL 就自動跳過，不會讓其他測試失敗。
"""
import concurrent.futures
import datetime
import json
import random

import pandas as pd
import pytest
import sqlalchemy

from analyze import task as analyze_task
from common import repository, runner
from common.config import MAX_RETRIES, MYSQL_DATABASE
from tests.test_transform_transformer import (
    PUNISH_API_FIELDS, PUNISH_ROW, ROW_ANDES, ROW_TENGHUI, envelope,
)
from transform import task as transform_task
from transform import transformer

pytestmark = pytest.mark.integration

TEST_DATABASE = f"{MYSQL_DATABASE}_test"
V1 = datetime.datetime(2026, 11, 9, 21, 0)    # 兩次抓取的時間（版本）：V2 比較新
V2 = datetime.datetime(2026, 11, 10, 21, 0)


# ---------- 測試用資料庫 ----------
@pytest.fixture(scope="module")
def test_engine():
    """建 <資料庫>_test、照 schema/*.sql 建表；整個檔案測完就刪掉。"""
    assert TEST_DATABASE != MYSQL_DATABASE
    try:
        with repository.get_engine("").begin() as conn:
            conn.execute(sqlalchemy.text(f"DROP DATABASE IF EXISTS {TEST_DATABASE}"))
            conn.execute(sqlalchemy.text(
                f"CREATE DATABASE {TEST_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    except sqlalchemy.exc.OperationalError:
        pytest.skip("連不到 MySQL：先啟動 MySQL、確認 .env 的連線設定")

    engine = repository.get_engine(TEST_DATABASE)
    with engine.begin() as conn:
        for sql_file in repository.SQL_FILES:   # 跟 create_tables() 一樣：一個檔案切成一句一句執行
            for statement in sql_file.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    conn.execute(sqlalchemy.text(statement))
    yield engine

    engine.dispose()
    with repository.get_engine("").begin() as conn:
        conn.execute(sqlalchemy.text(f"DROP DATABASE IF EXISTS {TEST_DATABASE}"))


@pytest.fixture
def db(test_engine, monkeypatch):
    """每個測試前清空資料表，並讓程式裡所有 get_engine() 都連到測試用資料庫。"""
    with test_engine.begin() as conn:
        tables = [row[0] for row in conn.execute(sqlalchemy.text("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'"))]
        for table in tables:
            if table != "stage_announcement_clause":   # 固定資料，建表時就寫好了
                conn.execute(sqlalchemy.text(f"DELETE FROM {table}"))
    monkeypatch.setattr(repository, "get_engine", lambda database=None: test_engine)
    return test_engine


def count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(sqlalchemy.text(f"SELECT COUNT(*) FROM {table}")).scalar()


def put_raw(source, raw_key, body, fetched_at, ok=True):
    """直接寫一份 raw（不打 API）。"""
    repository.insert_raw_row(source, raw_key, fetched_at=datetime.datetime.fromisoformat(fetched_at),
                              http_status=200 if ok else 500, ok=ok, problem=None if ok else "HTTP 500",
                              url="test", request_body=None, body=json.dumps(body, ensure_ascii=False))


# ---------- raw：只新增，讀最新一份成功的 ----------
def test_raw_append_only_and_read_latest_ok(db):
    put_raw("holiday", "2026", {"queryYear": 2026, "v": "A"}, "2026-11-01T21:00:00+08:00")
    put_raw("holiday", "2026", {"queryYear": 2026, "v": "B"}, "2026-11-02T21:00:00+08:00")
    put_raw("holiday", "2026", {"error": "被擋"}, "2026-11-03T21:00:00+08:00", ok=False)   # 最新的是失敗

    assert count(db, "raw_holiday") == 3                                     # 每抓一次一列，舊的都留著
    assert repository.read_raw("holiday", "2026")["body"]["v"] == "B"         # 讀最新一份成功的
    assert repository.list_raw_keys("holiday") == ["2026"]
    assert repository.raw_fetched_times("holiday") == {"2026": datetime.datetime(2026, 11, 2, 21, 0)}


# ---------- stage：重跑筆數不變、值會更新 ----------
def test_stage_upsert_rerun_same_count_and_updates(db):
    holidays = pd.DataFrame({"holiday_date": [datetime.date(2026, 1, 1), datetime.date(2026, 2, 16)],
                             "holiday_name": ["元旦", "春節"], "description": [None, None]})
    transform_task.upsert_stage({"stage_holiday": holidays}, V1)
    transform_task.upsert_stage({"stage_holiday": holidays}, V1)
    assert count(db, "stage_holiday") == 2                                    # 跑兩次還是 2 列

    transform_task.upsert_stage({"stage_holiday": holidays.assign(holiday_name=["元旦", "春節（更正）"])}, V1)
    with db.connect() as conn:
        names = conn.execute(sqlalchemy.text("SELECT holiday_name FROM stage_holiday ORDER BY holiday_date")).scalars()
        assert list(names) == ["元旦", "春節（更正）"]                        # 值更新了，筆數不變
    assert count(db, "stage_holiday") == 2

    with db.connect() as conn:                                                # 沒有重複
        duplicates = conn.execute(sqlalchemy.text(
            "SELECT holiday_date FROM stage_holiday GROUP BY holiday_date HAVING COUNT(*) > 1")).fetchall()
    assert duplicates == []


def test_transform_notice_rerun_and_removed_rows(db):
    """同一天的注意股整理兩次，結果一樣；重抓後官方拿掉一檔，stage 也跟著拿掉。"""
    put_raw("notice", "20261106", envelope([ROW_ANDES, ROW_TENGHUI])["body"], "2026-11-06T21:00:00+08:00")
    transform_task.transform_notice("20261106")
    first = (count(db, "stage_notice"), count(db, "stage_notice_clause"), count(db, "stage_notice_day"))
    transform_task.transform_notice("20261106")
    assert (count(db, "stage_notice"), count(db, "stage_notice_clause"), count(db, "stage_notice_day")) == first
    assert first == (2, 3, 1)                                                 # 2 檔、3 個款次（騰輝第六、十款）

    put_raw("notice", "20261106", envelope([ROW_ANDES])["body"], "2026-11-06T22:00:00+08:00")
    transform_task.transform_notice("20261106")
    assert count(db, "stage_notice") == 1


def test_older_notice_list_does_not_clear_newer(db):
    """兩輪重疊：比較舊的注意股名單晚寫完，不能把那天比較新的名單清掉（整批不寫）。"""
    day = datetime.date(2026, 11, 6)
    newer, _ = transformer.clean_notice(transformer.notice_to_frame(envelope([ROW_ANDES])))
    older, older_clauses = transformer.clean_notice(transformer.notice_to_frame(envelope([ROW_ANDES, ROW_TENGHUI])))
    newer_clauses = transformer.clean_notice(transformer.notice_to_frame(envelope([ROW_ANDES])))[1]

    def tables(notices, clauses):
        return {"stage_notice": notices, "stage_notice_clause": clauses,
                "stage_notice_day": pd.DataFrame({"trade_date": [day], "notice_count": [len(notices)]})}

    assert transform_task.upsert_stage(tables(newer, newer_clauses), V2, clear_day=day) == 3
    assert transform_task.upsert_stage(tables(older, older_clauses), V1, clear_day=day) is None   # 較舊：不寫
    assert count(db, "stage_notice") == 1                                      # 還是比較新的那份（1 檔）


# ---------- 同一批裡主鍵重複：資料庫留最後一列 ----------
def test_duplicate_keys_in_one_batch_last_row_wins(db):
    holidays = pd.DataFrame({"holiday_date": [datetime.date(2026, 1, 1)] * 2,
                             "holiday_name": ["元旦（第一列）", "元旦（第二列）"], "description": [None, None]})
    transform_task.upsert_stage({"stage_holiday": holidays}, V1)
    assert count(db, "stage_holiday") == 1
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT holiday_name FROM stage_holiday")).scalar() == "元旦（第二列）"


def test_transform_punish_duplicate_rows_stored_once(db):
    """同一份 raw 裡同一則處置出現兩次 → stage 只有一列，punish_count 算一則。"""
    body = envelope([PUNISH_ROW, PUNISH_ROW], PUNISH_API_FIELDS)["body"]
    put_raw("punish", "20261109", body, "2026-11-09T21:00:00+08:00")
    transform_task.transform_punish("20261109")
    assert count(db, "stage_punish") == 1
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT punish_count FROM stage_punish_day")).scalar() == 1


def test_duplicate_clause_stored_once(db):
    """同一段文字寫兩次第三款 → 款次表只有一列第三款。"""
    row = ROW_ANDES.copy()
    row[4] = "﹝第三款﹞﹝第一款﹞﹝第三款﹞"
    put_raw("notice", "20261106", envelope([row])["body"], "2026-11-06T21:00:00+08:00")
    transform_task.transform_notice("20261106")
    with db.connect() as conn:
        clauses = conn.execute(sqlalchemy.text("SELECT clause FROM stage_notice_clause ORDER BY clause")).scalars()
        assert list(clauses) == [1, 3]


# ---------- 多個 worker：誰先寫完都一樣，留下最新的 ----------
def punish_named(name: str) -> pd.DataFrame:
    row = PUNISH_ROW.copy()
    row[3] = name
    return transformer.clean_punish(transformer.punish_to_frame(envelope([row], PUNISH_API_FIELDS)))


def stored_punish_name(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(sqlalchemy.text("SELECT stock_name FROM stage_punish")).scalar()


def test_older_does_not_overwrite_newer(db):
    """每張 stage 表都一樣：比較舊的晚寫完，不能蓋掉比較新的；一樣新的可以（手動改壞的重跑會修回來）。"""
    transform_task.upsert_stage({"stage_punish": punish_named("致茂（新）")}, V2)
    transform_task.upsert_stage({"stage_punish": punish_named("致茂（舊）")}, V1)   # 比較舊：不能蓋掉
    assert stored_punish_name(db) == "致茂（新）"

    holidays = pd.DataFrame({"holiday_date": [datetime.date(2026, 1, 1)], "holiday_name": ["元旦"], "description": [None]})
    transform_task.upsert_stage({"stage_holiday": holidays}, V2)
    transform_task.upsert_stage({"stage_holiday": holidays.assign(holiday_name="舊名稱")}, V1)
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT holiday_name FROM stage_holiday")).scalar() == "元旦"
        conn.execute(sqlalchemy.text("UPDATE stage_holiday SET holiday_name = '改壞了'"))
        conn.commit()
    transform_task.upsert_stage({"stage_holiday": holidays}, V2)                    # 一樣新：修回來
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT holiday_name FROM stage_holiday")).scalar() == "元旦"


def test_parallel_punish_writes_keep_newest(db):
    """8 個執行緒同時寫同一則處置公告的不同版本、順序打亂：最後一定是最新的版本，而且沒有任務失敗。"""
    versions = [(punish_named(f"版本{i}"), datetime.datetime(2026, 11, 10 + i, 21, 0)) for i in range(8)]
    random.shuffle(versions)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(runner.run_with_retry, transform_task.upsert_stage,
                               {"tables": {"stage_punish": df}, "version": v}, MAX_RETRIES) for df, v in versions]
        for future in futures:
            future.result()   # 有任務失敗（例如 deadlock 重試完還是失敗）這裡會丟出錯誤
    assert stored_punish_name(db) == "版本7"
    assert count(db, "stage_punish") == 1


# ---------- app：重算同一天，結果一樣 ----------
def test_analyze_rerun_same_result(db):
    days = pd.bdate_range("2026-11-02", "2026-11-06").date
    with db.begin() as conn:
        conn.execute(sqlalchemy.text("INSERT INTO stage_trading_day (trade_date, is_trading) VALUES (:d, 1)"),
                     [{"d": d} for d in days])
        for table, column in [("stage_notice_day", "trade_date"), ("stage_punish_day", "query_date")]:
            conn.execute(sqlalchemy.text(f"INSERT INTO {table} ({column}, {table.split('_')[1]}_count) VALUES (:d, 0)"),
                         [{"d": d} for d in days])
        conn.execute(sqlalchemy.text("INSERT INTO stage_notice_clause (trade_date, stock_id, clause) VALUES (:d, '6533', 1)"),
                     [{"d": d} for d in days[-2:]])   # 最後兩天被列第一款 → 連 3 日還差 1 次

    analyze_task.analyze_progress("20261106")
    first = (count(db, "app_progress"), count(db, "app_progress_date"))
    analyze_task.analyze_progress("20261106")
    assert (count(db, "app_progress"), count(db, "app_progress_date")) == first
    assert first[0] == 1
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT streak3_left FROM app_progress")).scalar() == 1



# ---------- analyze 算之前的檢查：往前 30 個營業日都要有資料 ----------
def test_analyze_needs_every_day_in_window(db):
    days = list(pd.bdate_range("2026-11-02", "2026-11-06").date)   # 11/02～11/06 五個營業日
    weekend = [datetime.date(2026, 11, 7), datetime.date(2026, 11, 8)]
    with db.begin() as conn:
        conn.execute(sqlalchemy.text("INSERT INTO stage_trading_day (trade_date, is_trading) VALUES (:d, :t)"),
                     [{"d": d, "t": 1} for d in days] + [{"d": d, "t": 0} for d in weekend])
        conn.execute(sqlalchemy.text("INSERT INTO stage_notice_day (trade_date, notice_count) VALUES (:d, 0)"),
                     [{"d": d} for d in days if d != datetime.date(2026, 11, 4)])   # 11/04 的注意股沒抓到
        conn.execute(sqlalchemy.text("INSERT INTO stage_punish_day (query_date, punish_count) VALUES (:d, 0)"),
                     [{"d": d} for d in days])

    assert analyze_task.missing_inputs(datetime.date(2026, 11, 6)) == ["stage_notice_day/20261104"]   # 前面缺一天也不算
    assert analyze_task.missing_inputs(datetime.date(2026, 11, 3)) == []                               # 11/04 之前的不受影響
    assert analyze_task.missing_inputs(datetime.date(2026, 11, 9)) == ["stage_trading_day/20261109",
                                                                       "stage_notice_day/20261104"]   # 市場成交還沒到 11/09
    assert analyze_task.missing_inputs(datetime.date(2026, 10, 30)) == ["stage_notice_day/20261030"]  # 資料還沒開始
    with pytest.raises(RuntimeError, match="缺少必要的資料"):
        analyze_task.analyze_progress("20261106")
