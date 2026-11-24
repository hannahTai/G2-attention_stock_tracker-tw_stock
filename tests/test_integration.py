"""整合測試：連本機 PostgreSQL，驗證去重與冪等、多個 worker 同時整理也不會錯。

    uv run pytest -m integration -v        # 只跑這個檔案的測試
    uv run pytest -m "not integration"     # 不連資料庫（CI 用）

另外建一個測試用資料庫 <DB_NAME>_test，照 schema/*.sql 建表，測完整個刪掉；正式的資料庫完全不碰。
連不到 PostgreSQL 就自動跳過，不會讓其他測試失敗。
"""
import concurrent.futures
import datetime
import json
import random

import pandas as pd
import pytest
import sqlalchemy

from ai import task as ai_task
from analyze import task as analyze_task
from api import queries
from common import repository, runner
from common.config import DB_NAME, MAX_RETRIES
from common.hash_utils import key_md5, url_md5
from tests.test_transform_transformer import (
    PUNISH_API_FIELDS, PUNISH_ROW, ROW_ANDES, ROW_TENGHUI, envelope,
)
from transform import task as transform_task
from transform import transformer

pytestmark = pytest.mark.integration

TEST_DATABASE = f"{DB_NAME}_test"
V1 = datetime.datetime(2026, 11, 9, 21, 0)    # 兩次抓取的時間（版本）：V2 比較新
V2 = datetime.datetime(2026, 11, 10, 21, 0)


# ---------- 測試用資料庫 ----------
@pytest.fixture(scope="module")
def test_engine():
    """建 <資料庫>_test、照 schema/*.sql 建表；整個檔案測完就刪掉。"""
    assert TEST_DATABASE != DB_NAME
    try:
        repository.drop_database(TEST_DATABASE)
        repository.create_tables(TEST_DATABASE)   # 跟正式的一樣：建資料庫、執行 schema/*.sql
    except sqlalchemy.exc.OperationalError:
        pytest.skip("連不到 PostgreSQL：先啟動 PostgreSQL、確認 .env 的連線設定")

    engine = repository.get_engine(TEST_DATABASE)
    yield engine

    engine.dispose()
    repository.drop_database(TEST_DATABASE)


@pytest.fixture
def db(test_engine, monkeypatch):
    """每個測試前清空資料表，並讓程式裡所有 get_engine() 都連到測試用資料庫。"""
    with test_engine.begin() as conn:
        tables = conn.execute(sqlalchemy.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = :schema AND table_type = 'BASE TABLE'"),
            {"schema": repository.SCHEMA}).scalars().all()
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
                "stage_notice_day": pd.DataFrame({"query_date": [day], "notice_count": [len(notices)]})}

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
        conn.execute(sqlalchemy.text("INSERT INTO stage_trading_day (trade_date, is_trading) VALUES (:d, TRUE)"),
                     [{"d": d} for d in days])
        for table in ["stage_notice_day", "stage_punish_day"]:
            conn.execute(sqlalchemy.text(f"INSERT INTO {table} (query_date, {table.split('_')[1]}_count) VALUES (:d, 0)"),
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
                     [{"d": d, "t": True} for d in days] + [{"d": d, "t": False} for d in weekend])
        conn.execute(sqlalchemy.text("INSERT INTO stage_notice_day (query_date, notice_count) VALUES (:d, 0)"),
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


# ---------- 新聞：raw_stock_news → stage_news、stage_stock_news；raw_news_detail → stage_news_detail ----------
NEWS_LINK = "https://udn.com/news/1"


def stock_news_raw(stock_id):
    return {"msg": "success", "status": 200, "data": [
        {"date": "2026-11-20 01:00:00", "stock_id": stock_id, "link": NEWS_LINK, "source": "UDN", "title": "台積電 - UDN"}]}


def test_news_shared_by_two_stocks_joins_page_in_view(db):
    """同一則新聞兩檔都有：stage_news 一列、stage_stock_news 兩列；網頁的導言接得上（Python 算的 news_id 過得了 CHECK）；重跑筆數不變。"""
    put_raw("stock_news", "2330_20261120", stock_news_raw("2330"), "2026-11-21T09:00:00+08:00")
    put_raw("stock_news", "2317_20261120", stock_news_raw("2317"), "2026-11-21T09:01:00+08:00")
    page = {"final_url": NEWS_LINK, "content_type": "text/html",
            "head": "<head><meta property='og:description' content='台積電第三季財報驚豔。'></head>", "text_chars": 500}
    put_raw("news_detail", url_md5(NEWS_LINK), page, "2026-11-21T09:05:00+08:00")
    for _ in range(2):
        transform_task.transform_stock_news("2330", "20261120")
        transform_task.transform_stock_news("2317", "20261120")
        transform_task.transform_news_detail(url_md5(NEWS_LINK))

    assert (count(db, "stage_news"), count(db, "stage_stock_news"), count(db, "stage_news_detail")) == (1, 2, 1)
    with db.connect() as conn:
        rows = conn.execute(sqlalchemy.text("SELECT stock_id, title, lead FROM app_stock_news ORDER BY stock_id")).all()
    assert [tuple(r) for r in rows] == [("2317", "台積電", "台積電第三季財報驚豔。"), ("2330", "台積電", "台積電第三季財報驚豔。")]


def test_news_detail_done_skips_permanent_failures_only(db):
    """成功、被擋（403）、轉址（沒打）的不再抓；5xx 下次再試。"""
    repository.save_raw_record("news_detail", "ok", url="u1", http_status=200, body={"head": ""})
    repository.save_raw_record("news_detail", "blocked", url="u2", http_status=403, body={}, problem="HTTP 403")
    repository.save_raw_record("news_detail", "redirect", url="u3", http_status=None, body={}, problem="轉址連結")
    repository.save_raw_record("news_detail", "down", url="u4", http_status=503, body={}, problem="HTTP 503")
    assert repository.raw_keys_done("news_detail") == {"ok", "blocked", "redirect"}


def test_news_id_must_be_md5_of_link(db):
    """news_id 和 link 對不上（例如有一邊忘了去空白）：CHECK 擋下，不會默默接不起來。"""
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="stage_news_id_is_md5"), db.begin() as conn:
        conn.execute(sqlalchemy.text(
            "INSERT INTO stage_news (news_id, link, news_time, news_date, title) VALUES (:news_id, :link, :t, :d, 't')"),
            {"news_id": url_md5(NEWS_LINK + "x"), "link": NEWS_LINK, "t": V1, "d": V1.date()})


# ---------- ai_*：只新增 ----------
def test_announcement_label_inserted_twice_keeps_first(db):
    """同一個任務被送了兩次：同一則、同一版只留第一次的結果，不報錯。"""
    label = {"announcement_id": key_md5("6108", "20261114", 3), "prompt_version": "v2",
             "category": "營運", "is_correction": False, "has_news": True, "is_negative": False, "summary": "第一次", "reason": "r",
             "summary_ok": True, "model": "claude-haiku-5-5"}
    repository.insert_announcement_label(label)
    repository.insert_announcement_label({**label, "summary": "第二次"})
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT summary FROM ai_announcement_label")).scalars().all() == ["第一次"]


def test_news_summary_listed_once_and_shown_in_view(db):
    """被關注的股票、有網頁、全文夠長的新聞才列出來摘要；摘要過就不再列；通過檢查的摘要出現在 app_stock_news。"""
    put_raw("stock_news", "2330_20261120", stock_news_raw("2330"), "2026-11-21T09:00:00+08:00")
    put_raw("news_detail", url_md5(NEWS_LINK), {"final_url": NEWS_LINK, "head": "", "text_chars": 500}, "2026-11-21T09:05:00+08:00")
    transform_task.transform_stock_news("2330", "20261120")
    transform_task.transform_news_detail(url_md5(NEWS_LINK))
    day = datetime.date(2026, 11, 20)
    assert repository.list_unsummarized_news(day, day, "v1", 200) == []   # 2330 這段期間沒被關注
    with db.begin() as conn:
        conn.execute(sqlalchemy.text("INSERT INTO stage_trading_day (trade_date, is_trading) VALUES (:d, TRUE)"), {"d": day})
        conn.execute(sqlalchemy.text("INSERT INTO stage_company (company_id, short_name, full_name, industry_code, snapshot_date) "
                                     "VALUES ('2330', '台積電', '台積電', '24', :d)"), {"d": day})
        conn.execute(sqlalchemy.text(
            "INSERT INTO stage_notice (trade_date, stock_id, stock_name, reason_text) VALUES (:d, '2330', '台積電', 'r')"), {"d": day})
        conn.execute(sqlalchemy.text("INSERT INTO stage_notice_clause (trade_date, stock_id, clause) VALUES (:d, '2330', 1)"), {"d": day})
    assert repository.list_unsummarized_news(day, day, "v1", 200) == [url_md5(NEWS_LINK)]
    assert repository.list_unsummarized_news(day, day, "v1", 1000) == []   # 全文太短

    row = {"news_id": url_md5(NEWS_LINK), "prompt_version": "v1", "summary": "台積電財報亮眼", "summary_ok": True, "model": "m"}
    repository.insert_news_summary(row)
    repository.insert_news_summary({**row, "summary": "第二次"})   # 同一版再寫一次：不動
    assert repository.list_unsummarized_news(day, day, "v1", 200) == []
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT ai_summary FROM app_stock_news")).scalar() == "台積電財報亮眼"


def test_announcement_id_matches_sql_and_joins_detail(db):
    """Python 算的 announcement_id 過得了 CHECK（等於 SQL 的 md5）；app_announcement 用它接上全文。"""
    announcement = pd.DataFrame({"company_id": ["9908"], "announce_date": [datetime.date(2026, 11, 2)],
                                 "enter_date": [datetime.date(2026, 11, 2)], "serial_number": [1],
                                 "announce_time": ["14:39:07"], "company_name": ["大台北"], "subject": ["公告第三季財報"]})
    announcement.insert(4, "announcement_id", transformer.announcement_ids(announcement))
    detail = announcement[["company_id", "enter_date", "serial_number", "announcement_id"]].assign(
        announce_clause="第31款", fact_date=datetime.date(2026, 11, 5), content="全文")
    transform_task.upsert_stage({"stage_announcement": announcement, "stage_announcement_detail": detail}, V1)
    with db.connect() as conn:
        row = conn.execute(sqlalchemy.text("SELECT announcement_id, content FROM app_announcement")).one()
    assert row.announcement_id == key_md5("9908", "20261102", 1) and row.content == "全文"
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="stage_announcement_id_is_md5"), db.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE stage_announcement SET announcement_id = :bad"), {"bad": "0" * 32})


def test_app_announcement_labels_come_from_ai_with_keyword_fallback(db):
    """更正、說明的標記：有公告分類的結果就用它；還沒分類（剛發的公告）用主旨關鍵字。"""
    announcement = pd.DataFrame({"company_id": ["9908"], "announce_date": [datetime.date(2026, 11, 2)],
                                 "enter_date": [datetime.date(2026, 11, 2)], "serial_number": [1],
                                 "announce_time": ["14:39:07"], "company_name": ["大台北"], "subject": ["更正第三季財報"]})
    announcement.insert(4, "announcement_id", transformer.announcement_ids(announcement))
    transform_task.upsert_stage({"stage_announcement": announcement}, V1)
    query = sqlalchemy.text("SELECT is_correction, is_explanation, category, ai_summary FROM app_announcement")
    with db.connect() as conn:
        assert tuple(conn.execute(query).one()) == (True, False, None, None)    # 還沒分類：主旨有「更正」
    repository.insert_announcement_label({
        "announcement_id": key_md5("9908", "20261102", 1), "prompt_version": "v2", "category": "澄清／回應媒體",
        "is_correction": False, "has_news": False, "is_negative": True, "summary": "澄清媒體報導", "reason": "r",
        "summary_ok": True, "model": "m"})
    with db.connect() as conn:
        assert tuple(conn.execute(query).one()) == (False, True, "澄清／回應媒體", "澄清媒體報導")   # 用公告分類的判斷
    with db.connect() as conn:   # 壞消息另外一欄
        assert conn.execute(sqlalchemy.text("SELECT is_negative FROM app_announcement")).scalar() is True


def test_news_rag_ranks_by_cosine_and_card_summary_shows_in_app_card(db):
    """pgvector：向量寫得進去（重寫是換掉，不重複）、照 cosine 距離排；卡片 AI 摘要通過檢查的才出現在 app_card。"""
    put_raw("stock_news", "2330_20261120", stock_news_raw("2330"), "2026-11-21T09:00:00+08:00")
    transform_task.transform_stock_news("2330", "20261120")
    vector = "[" + ",".join(["1.0"] + ["0.0"] * 1023) + "]"
    day = datetime.date(2026, 11, 20)
    [todo] = repository.list_news_to_embed(day, day, "m")
    assert todo["news_id"] == url_md5(NEWS_LINK) and todo["input_hash"] is None
    for text_hash in ("a" * 32, "b" * 32):
        repository.save_news_embeddings([{"news_id": url_md5(NEWS_LINK), "model": "m", "embedding": vector, "input_hash": text_hash}])
    assert repository.list_news_to_embed(day, day, "m")[0]["input_hash"] == "b" * 32 and count(db, "ai_news_embedding") == 1
    [news] = repository.stock_news_candidates("2330", day, day, "m", vector)
    assert news["news_id"] == url_md5(NEWS_LINK) and abs(news["similarity"] - 1.0) < 1e-6

    with db.begin() as conn:   # app_card 要有一列（app_progress）
        conn.execute(sqlalchemy.text(
            "INSERT INTO app_progress (trade_date, stock_id, count_start, streak3, streak5, in10, in30, has_full_history, process_date) "
            "VALUES (:d, '2330', :d, 1, 1, 1, 1, TRUE, :d)"), {"d": day})
    row = {"card_id": key_md5("2330", "20261120"), "prompt_version": "v1", "stock_id": "2330", "card_date": day,
           "summary": "因累積漲幅太大被列注意。", "news_ids": [url_md5(NEWS_LINK)], "input_hash": "0" * 32,
           "summary_ok": True, "reused": False, "model": "m"}
    repository.insert_card_summary(row)
    with db.connect() as conn:
        card = conn.execute(sqlalchemy.text("SELECT ai_summary, ai_summary_news_ids FROM app_card")).one()
    assert card.ai_summary == "因累積漲幅太大被列注意。" and card.ai_summary_news_ids == [url_md5(NEWS_LINK)]
    assert repository.previous_card_summary("2330", datetime.date(2026, 11, 21), "v1")["summary"] == row["summary"]


def test_same_story_grouped_and_listed_once(db):
    """三家報同一件事（向量很像）、一則別的事：同一件事歸到最早那則；API 的新聞列表只列一則，寫其他媒體。"""
    same = "[" + ",".join(["1.0"] + ["0.0"] * 1023) + "]"
    other = "[" + ",".join(["0.0", "1.0"] + ["0.0"] * 1022) + "]"
    news = [("https://a.com/1", "09:00", "UDN", "健策接獲大單", same), ("https://b.com/1", "10:00", "自由時報", "健策拿下訂單", same),
            ("https://c.com/1", "11:00", "鉅亨網", "健策接獲大單", same), ("https://d.com/1", "12:00", "UDN", "健策除息", other),
            ("https://g.com/1", "12:30", "CMoney", "3653 健策 - 明天會漲嗎- 股市爆料同學會",
             "[" + ",".join(["0.0"] * 4 + ["1.0"] + ["0.0"] * 1019) + "]"),   # 論壇貼文：新聞列表不列
            ("https://e.com/1", "13:00", "CMoney", "【下週觀察】健策跌近一成仍留3清單",
             "[" + ",".join(["0.0"] * 3 + ["1.0"] + ["0.0"] * 1020) + "]"),
            ("https://f.com/1", "14:00", "CMoney投資網誌", "【下週觀察】健策跌近一成仍留3清單-起漲K線",
             "[" + ",".join(["0.0", "0.0", "1.0"] + ["0.0"] * 1021) + "]")]   # 向量不像，但標題是同一篇
    with db.begin() as conn:
        for link, time, publisher, title, vector in news:
            conn.execute(sqlalchemy.text(
                "INSERT INTO stage_news (news_id, link, news_time, news_date, publisher, title) "
                "VALUES (md5(:link), :link, :t, :d, :p, :title)"),
                {"link": link, "t": datetime.datetime.fromisoformat(f"2026-11-20T{time}"), "d": datetime.date(2026, 11, 20),
                 "p": publisher, "title": title})
            conn.execute(sqlalchemy.text("INSERT INTO stage_stock_news (stock_id, news_id) VALUES ('3653', md5(:link))"), {"link": link})
            conn.execute(sqlalchemy.text(
                "INSERT INTO ai_news_embedding (news_id, model, embedding, input_hash) VALUES (md5(:link), :m, CAST(:v AS vector), :h)"),
                {"link": link, "m": ai_task.EMBED_MODEL, "v": vector, "h": "0" * 32})
    for _ in range(2):   # 重跑：換成一樣的結果，不重複
        assert "3 則和別則是同一件事" in ai_task.group_news_stories("20261120", "20261120")
    assert count(db, "ai_news_story") == 7
    day = datetime.date(2026, 11, 20)
    with db.connect() as conn:
        listed = queries.stock_news(conn, "3653", day, day)
    assert [(n["title"], n["also_reported_by"]) for n in listed] == [
        ("【下週觀察】健策跌近一成仍留3清單", ["CMoney投資網誌"]), ("健策除息", []), ("健策接獲大單", ["自由時報", "鉅亨網"])]


def test_card_summary_reads_only_stage_and_ai(db):
    """卡片 AI 摘要只讀 stage 和公告分類：要寫哪幾張（和卡片牆同一批：往前 30 個交易日被列注意過的上市公司，
    stage_watched_stock）、事實（今天沒被列注意的帶最近一次被列注意）、公告。"""
    day = datetime.date(2026, 11, 20)
    with db.begin() as conn:

        def run(sql, **params):
            return conn.execute(sqlalchemy.text(sql), params)
        for company in ("2330", "2317", "1101", "9910"):
            run("INSERT INTO stage_company (company_id, short_name, full_name, industry_code, snapshot_date) "
                "VALUES (:c, :c, :c, '24', :d)", c=company, d=day)
        for trading in (datetime.date(2026, 11, 18), datetime.date(2026, 11, 19), day):
            run("INSERT INTO stage_trading_day (trade_date, is_trading) VALUES (:d, TRUE)", d=trading)
            run("INSERT INTO stage_notice_day (query_date, notice_count) VALUES (:d, 1)", d=trading)   # 那天的注意股、
            run("INSERT INTO stage_punish_day (query_date, punish_count) VALUES (:d, 1)", d=trading)   # 處置股整理好了
        for stock in ("1101", "2317", "2330"):   # 這幾檔 11/20 的新聞清單、11 月的公告清單抓到了（9910 沒抓：不寫）
            for source, key in (("raw_stock_news", f"{stock}_20261120"), ("raw_announcement", f"{stock}_202611"),
                                ("raw_announcement", f"{stock}_202610")):   # 上個月的公告清單也抓過
                run(f"INSERT INTO {source} (raw_key, fetched_at, http_status, ok, url, body) "
                    "VALUES (:k, LOCALTIMESTAMP, 200, TRUE, 'test', '{}')", k=key)
        run("INSERT INTO stage_notice (trade_date, stock_id, stock_name, reason_text) "   # 1101：前兩天被列注意，今天沒有
            "VALUES (:d, '1101', '台泥', '最近六個營業日累積收盤價漲幅達32.10%')", d=datetime.date(2026, 11, 18))
        run("INSERT INTO stage_notice_clause (trade_date, stock_id, clause) VALUES (:d, '1101', 1)", d=datetime.date(2026, 11, 18))
        run("INSERT INTO stage_notice (trade_date, stock_id, stock_name, reason_text) "   # 2317：昨天被列注意、今天在注意累計名單上
            "VALUES (:d, '2317', '鴻海', '最近六個營業日累積收盤價漲幅達30.00%')", d=datetime.date(2026, 11, 19))
        run("INSERT INTO stage_notice_clause (trade_date, stock_id, clause) VALUES (:d, '2317', 1)", d=datetime.date(2026, 11, 19))
        run("INSERT INTO stage_notice (trade_date, stock_id, stock_name, reason_text) "
            "VALUES (:d, '2330', '台積電', '最近六個營業日累積收盤價跌幅達37.82%')", d=day)
        for stock in ("2317", "084028"):   # 084028 是權證，不是上市公司
            run("INSERT INTO stage_punish_watchlist (data_date, stock_id, stock_name, progress_text) "
                "VALUES (:d, :s, :s, '連續二次')", d=day, s=stock)
        for clause in (13, 1):
            run("INSERT INTO stage_notice_clause (trade_date, stock_id, clause) VALUES (:d, '2330', :c)", d=day, c=clause)
        run("INSERT INTO stage_stock_price (trade_date, stock_id, stock_name, close_price, price_change) "
            "VALUES (:d, '2330', '台積電', 900, -100)", d=day)
        run("INSERT INTO stage_punish (announce_date, stock_id, stock_name, trigger_condition, period_start, period_end, "
            "measure_type, match_interval_minutes, content, has_star_mark) "
            "VALUES (:d, '2330', '台積電', '連續三次', :s, :e, '第一次處置', 5, '…', FALSE)",
            d=day, s=datetime.date(2026, 11, 17), e=datetime.date(2026, 11, 23))
        announcement_id = key_md5("2330", "20261119", 1)
        run("INSERT INTO stage_announcement (company_id, announce_date, enter_date, serial_number, announcement_id, "
            "announce_time, company_name, subject) VALUES ('2330', :d, :d, 1, :a, '15:00', '台積電', '公告子公司破產')",
            d=datetime.date(2026, 11, 19), a=announcement_id)
        run("INSERT INTO ai_announcement_label (announcement_id, prompt_version, category, is_correction, has_news, "
            "is_negative, summary, reason, summary_ok, model) "
            "VALUES (:a, 'v3', '其他', FALSE, TRUE, TRUE, '子公司破產', '…', TRUE, 'm')", a=announcement_id)

    assert repository.list_unsummarized_cards(day, day, "v8") == [("1101", day), ("2317", day), ("2330", day)]
    # 新聞線要查的公司：11/21（週六）沒有名單，用 11/20 的；權證、ETF 不算
    assert repository.watched_companies(day, datetime.date(2026, 11, 21)) == {day: {"1101", "2317", "2330"},
                                                                              datetime.date(2026, 11, 21): {"1101", "2317", "2330"}}
    # 11/19 收盤行情還沒整理好（股價線還在跑）：先不寫那天的卡片，不會寫成漏掉漲跌的摘要
    assert repository.list_unsummarized_cards(datetime.date(2026, 11, 19), day, "v8") == [("1101", day), ("2317", day), ("2330", day)]
    facts = repository.card_facts("2330", day)
    assert facts["is_listed_today"] and facts["reason_text"].startswith("最近六個") and facts["clauses"] == "1,13"
    assert float(facts["change_pct"]) == -10.0   # -100 ÷ (900 + 100)
    assert facts["punish"]["match_interval_minutes"] == 5 and facts["punish"]["period_end"] == datetime.date(2026, 11, 23)
    watch = repository.card_facts("2317", day)
    assert not watch["is_listed_today"] and watch["change_pct"] is None and watch["punish"] is None
    earlier = repository.card_facts("1101", day)   # 今天沒被列注意：帶最近一次被列注意
    assert not earlier["is_listed_today"] and earlier["stock_name"] == "台泥"
    assert earlier["last_notice"]["trade_date"] == datetime.date(2026, 11, 18) and earlier["last_notice"]["clauses"] == "1"
    assert repository.card_facts("9910", day) is None   # 沒被列注意過、沒處置、不在名單上：不是卡片牆上的股票
    [announcement] = repository.recent_announcements("2330", datetime.date(2026, 10, 21), day)
    assert (announcement["category"], announcement["is_negative"], announcement["ai_summary"]) == ("其他", True, "子公司破產")
    # 11/20 的注意股在清單之後才抓到（清單是下午的舊的）：先不寫，等新聞線重抓
    with db.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE stage_notice_day SET source_fetched_at = LOCALTIMESTAMP + INTERVAL '1 hour' "
                                     "WHERE query_date = :d"), {"d": day})
    assert repository.list_unsummarized_cards(day, day, "v8") == []
    # 回補：注意股比清單晚抓，但清單已經完整（新聞清單隔天 08:00 以後抓的、公告清單那個月過完以後抓的）：照樣寫
    with db.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE raw_stock_news SET fetched_at = '2026-11-21 09:00' WHERE raw_key LIKE '%_20261120'"))
        conn.execute(sqlalchemy.text("UPDATE raw_announcement SET fetched_at = '2026-12-01 09:00' WHERE raw_key LIKE '%_202611'"))
        conn.execute(sqlalchemy.text("UPDATE stage_notice_day SET source_fetched_at = '2026-12-02 09:00' WHERE query_date = :d"),
                     {"d": day})
    assert repository.list_unsummarized_cards(day, day, "v8") == [("1101", day), ("2317", day), ("2330", day)]
    with db.begin() as conn:
        conn.execute(sqlalchemy.text("UPDATE stage_notice_day SET source_fetched_at = NULL WHERE query_date = :d"), {"d": day})
    # 同一張卡片這一版失敗 3 次（例如 LLM 拒答）：不再問，不會一直花錢
    with db.begin() as conn:
        for _ in range(repository.MAX_FAILED_CALLS):
            conn.execute(sqlalchemy.text(
                "INSERT INTO ai_llm_call (task, ref_key, model, prompt_version, status, seconds, called_at) "
                "VALUES ('summarize_card', :k, 'm', 'v8', 'failure', 1, LOCALTIMESTAMP)"), {"k": key_md5("2330", "20261120")})
    assert repository.list_unsummarized_cards(day, day, "v8") == [("1101", day), ("2317", day)]
    assert ("2330", day) in repository.list_unsummarized_cards(day, day, "v9")   # 換 prompt 版本重新算


def test_repeatedly_skipped_counts_job_runs(db):
    """新聞摘要跳過（網頁沒辦法摘要）3 次的不再排：看 job_run 的結果數次數。"""
    with db.begin() as conn:
        for times, news_id in ((3, "a"), (2, "b")):
            for _ in range(times):
                conn.execute(sqlalchemy.text(
                    "INSERT INTO job_run (task, args, status, result, attempt, mode, started_at, finished_at, seconds) "
                    "VALUES ('ai.task.summarize_news', CAST(:args AS JSONB), 'success', :result, 1, 'local', "
                    "LOCALTIMESTAMP, LOCALTIMESTAMP, 1)"),
                    {"args": json.dumps({"news_id": news_id}), "result": f"{news_id} {ai_task.PAGE_SKIPPED}：網頁打不開（HTTP 403），跳過"})
    assert repository.repeatedly_skipped("ai.task.summarize_news", ai_task.PAGE_SKIPPED, "news_id") == {"a"}


def test_create_tables_records_the_schema_hash(db):
    """create_tables 把 schema/*.sql 的雜湊值記在資料庫（main.py 比對，改過才再建）。"""
    with db.connect() as conn:
        applied = conn.execute(sqlalchemy.text(
            "SELECT obj_description(to_regnamespace(:s), 'pg_namespace')"), {"s": repository.SCHEMA}).scalar()
    assert applied == repository.schema_hash()


def test_create_tables_can_run_at_the_same_time(db):
    """create_tables 同時跑（例如 docker compose 的 setup 和 main.py 一起開始，都會 DROP、CREATE VIEW）：不會出錯。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(repository.create_tables, TEST_DATABASE) for _ in range(4)]:
            future.result()   # 出錯會在這裡丟出來
    with db.connect() as conn:
        assert conn.execute(sqlalchemy.text("SELECT count(*) FROM app_card")).scalar() == 0


def test_create_database_can_run_at_the_same_time(test_engine):
    """全新的伺服器上股價線、新聞線同時開始：兩邊都查到資料庫不存在、同時建，後建的不算失敗。"""
    name = f"{TEST_DATABASE}_race"
    repository.drop_database(name)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for future in [pool.submit(repository.create_database, name) for _ in range(4)]:
                future.result()
    finally:
        repository.drop_database(name)
