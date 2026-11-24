"""main.py 的鎖：同一時間只跑一輪（檔案鎖，不連資料庫）。"""
import threading
import time
import types

import pytest

import main


def test_second_run_waits_for_first(tmp_path):
    """第一輪鎖著時，第二輪會等到第一輪放掉才開始。"""
    lock_file = tmp_path / "pipeline.lock"
    order = []
    first_has_lock = threading.Event()

    def first_run():
        with main.pipeline_lock(lock_file, wait_seconds=5, poll_seconds=0.05):
            order.append("第一輪開始")
            first_has_lock.set()
            time.sleep(0.5)
            order.append("第一輪結束")

    def second_run():
        first_has_lock.wait()
        with main.pipeline_lock(lock_file, wait_seconds=5, poll_seconds=0.05):
            order.append("第二輪開始")

    threads = [threading.Thread(target=first_run), threading.Thread(target=second_run)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert order == ["第一輪開始", "第一輪結束", "第二輪開始"]


def test_gives_up_after_waiting_too_long(tmp_path):
    lock_file = tmp_path / "pipeline.lock"
    with main.pipeline_lock(lock_file, wait_seconds=5):
        result = []

        def second_run():
            try:
                with main.pipeline_lock(lock_file, wait_seconds=0.2, poll_seconds=0.05):
                    result.append("拿到了")
            except TimeoutError:
                result.append("放棄")

        t = threading.Thread(target=second_run)
        t.start()
        t.join()
    assert result == ["放棄"]


def test_lock_released_after_error(tmp_path):
    """這一輪出錯，鎖也會放掉，下一輪可以馬上開始。"""
    lock_file = tmp_path / "pipeline.lock"
    with pytest.raises(RuntimeError), main.pipeline_lock(lock_file, wait_seconds=1):
        raise RuntimeError("這一輪出錯")
    with main.pipeline_lock(lock_file, wait_seconds=0.1):
        pass


# ---------- 兩條線的順序 ----------
@pytest.fixture
def steps(monkeypatch):
    """不真的執行：記下 run（一步）被叫的順序和 --part；codes 可以指定某一步的結束代碼。"""
    calls, codes = [], {}

    def fake_run(module, args):
        calls.append((module, args[args.index("--part") + 1] if "--part" in args else None))
        return codes.get(module, 0)
    monkeypatch.setattr(main, "run", fake_run)
    return calls, codes


def test_stock_line_is_twse_then_analyze(steps):
    """股價線：crawler 證交所 → transform 證交所的 → analyze（不跑 ai）。"""
    calls, _ = steps
    main.run_stock_line(["daily"])
    assert calls == [("crawler.producer", "twse"), ("transform.producer", "twse"), ("analyze.producer", None)]


def test_news_line_is_mops_news_then_ai(steps):
    """新聞線：crawler 觀測站、新聞 → transform 重大訊息、新聞 → ai（不跑 analyze）。"""
    calls, _ = steps
    main.run_news_line(["daily"])
    assert calls == [("crawler.producer", "news"), ("transform.producer", "news"), ("ai.producer", None)]


def test_transform_failure_stops_the_line(steps):
    calls, codes = steps
    codes["transform.producer"] = 1
    assert main.run_stock_line(["daily"]) and main.run_news_line(["daily"])   # 回傳有失敗
    assert ("analyze.producer", None) not in calls and ("ai.producer", None) not in calls


def test_crawler_failure_still_runs_the_rest_but_exits_1(steps):
    calls, codes = steps
    codes["crawler.producer"] = 1
    assert main.run_news_line(["daily"])
    assert calls[-1] == ("ai.producer", None)


# ---------- main()：股價線的記號、新聞線、回補 ----------
@pytest.fixture
def lines(monkeypatch, tmp_path):
    """main()：鎖和記號放到暫存資料夾；兩條線只記被叫的順序，fail 可以指定哪條線失敗。"""
    state = types.SimpleNamespace(calls=[], fail=set(), mode="daily", applied=None)
    monkeypatch.setattr(main, "LOCK_FILE", tmp_path / "pipeline.lock")
    monkeypatch.setattr(main, "NEWS_LOCK_FILE", tmp_path / "news.lock")
    monkeypatch.setattr(main, "DONE_DIR", tmp_path / "done")
    monkeypatch.setattr(main.repository, "schema_hash", lambda: "h1")
    monkeypatch.setattr(main.repository, "applied_schema_hash", lambda: state.applied)

    def fake_create():
        state.calls.append("schema")
        state.applied = "h1"
    monkeypatch.setattr(main.repository, "create_tables", fake_create)
    monkeypatch.setattr(main.repository, "ensure_tables", lambda: None)
    monkeypatch.setattr(main.cli, "parse_args",
                        lambda **kw: types.SimpleNamespace(mode=state.mode, start="20261123", end="20261123", refetch=False))

    def fake_line(name):
        def run_line(args):
            state.calls.append(name)
            return name in state.fail
        return run_line
    monkeypatch.setattr(main, "run_stock_line", fake_line("stock"))
    monkeypatch.setattr(main, "run_news_line", fake_line("news"))
    return state


def test_daily_runs_the_stock_line_marks_done_and_later_runs_skip(lines, tmp_path):
    """股價線全部成功：放當天的記號；同一天之後的排程看到記號就跳過。"""
    main.main()
    assert (tmp_path / "done" / "20261123").exists()
    main.main()
    # 第一次：schema 還沒記雜湊值，建表、更新 VIEW；股價線放記號後接著跑一次新聞線；第二次看到記號就結束（不再跑新聞線）
    assert lines.calls == ["schema", "stock", "news"]


def test_daily_failure_leaves_no_mark_so_next_hour_runs_again(lines, tmp_path):
    """有失敗（例如證交所還沒公布）：不放記號，下一個小時再跑。"""
    lines.fail = {"stock"}
    with pytest.raises(SystemExit):
        main.main()
    lines.fail = set()
    main.main()
    # 建表、更新 VIEW 只做第一次（schema/*.sql 沒改）：和同時跑的新聞線不會互相鎖住
    # 失敗的那一輪不跑新聞線（當天的資料還沒到齊）；成功的那一輪才接著跑
    assert lines.calls == ["schema", "stock", "stock", "news"] and (tmp_path / "done" / "20261123").exists()


def test_news_mode_runs_every_time_without_a_mark(lines, tmp_path):
    lines.mode = "news"
    main.main()
    main.main()
    # 第一次：schema 還沒記雜湊值，建表、更新 VIEW（股價線沒跑的週末更新程式，網站也會用到新的 VIEW）
    assert lines.calls == ["schema", "news", "news"] and not (tmp_path / "done").exists()


def test_after_the_mark_schema_changes_are_left_to_the_news_line(lines, tmp_path):
    """當天已經放記號，之後才改 schema/*.sql（更新程式）：股價線看到記號直接結束（不等鎖），新聞線開始時建表、更新 VIEW。"""
    main.main()
    lines.applied = "old"   # 例如還原了舊的備份，或改了 schema/*.sql
    main.main()
    assert lines.calls == ["schema", "stock", "news"]
    lines.mode = "news"
    main.main()
    assert lines.calls == ["schema", "stock", "news", "schema", "news"]


def test_backfill_runs_stock_line_then_news_line_and_ignores_the_mark(lines, tmp_path):
    lines.mode = "backfill"
    main.main()
    main.main()
    # 回補兩條線都鎖著：每次都建表、更新 VIEW
    assert lines.calls == ["schema", "stock", "news"] * 2 and not (tmp_path / "done").exists()


def test_backfill_still_runs_news_line_when_stock_line_fails(lines):
    """股價線有失敗：新聞線照樣跑（已經整理好的日子照樣查新聞），最後結束代碼 1。"""
    lines.mode = "backfill"
    lines.fail = {"stock"}
    with pytest.raises(SystemExit):
        main.main()
    assert lines.calls == ["schema", "stock", "news"]


def test_schema_objects_lists_tables_indexes_and_views():
    """ensure_tables 檢查的名單：schema/*.sql 建的表、索引、VIEW 都在裡面，註解裡的不算。"""
    names = main.repository.schema_objects()
    assert {"stage_announcement", "idx_stage_punish_stock", "app_card"} <= set(names)
    assert len(names) == len(set(names))


def test_watched_window_is_defined_once():
    """關注的股票往前看幾個交易日：schema/view.sql 的 LAG(trade_date, 29) 要和 WATCH_WINDOW_DAYS（30）一樣
    （crawler、規則引擎用這個常數）。"""
    view = (main.PROJECT_ROOT / "schema" / "view.sql").read_text(encoding="utf-8")
    watched = view[view.index("CREATE VIEW stage_watched_stock"):]
    from common import rules
    assert f"LAG(trade_date, {rules.WATCH_WINDOW_DAYS - 1})" in watched


def test_schema_change_triggers_refresh_holding_both_locks(monkeypatch, tmp_path):
    """schema/*.sql 改過（雜湊值不一樣）才建表、更新 VIEW，做的時候拿著兩條線的鎖（另一條線不會同時在寫）。"""
    built = []
    monkeypatch.setattr(main, "LOCK_FILE", tmp_path / "pipeline.lock")
    monkeypatch.setattr(main, "NEWS_LOCK_FILE", tmp_path / "news.lock")
    current = {"hash": "a", "applied": None}
    monkeypatch.setattr(main.repository, "schema_hash", lambda: current["hash"])
    monkeypatch.setattr(main.repository, "applied_schema_hash", lambda: current["applied"])

    def fake_create():
        for lock in (main.LOCK_FILE, main.NEWS_LOCK_FILE):   # 兩個鎖都拿著：別人拿不到
            with open(lock, "a+") as f:
                f.seek(0)
                assert not main.try_lock(f)
        built.append(current["hash"])
        current["applied"] = current["hash"]
    monkeypatch.setattr(main.repository, "create_tables", fake_create)
    main.refresh_schema()               # 第一次：建
    main.refresh_schema()               # 沒改：不建
    current["hash"] = "b"
    main.refresh_schema()               # 改了：建
    assert built == ["a", "b"]


def test_schema_refresh_waits_for_a_running_news_line_without_holding_the_stock_lock(monkeypatch, tmp_path):
    """schema/*.sql 改過、新聞線正在跑（拿著它的鎖）：等它跑完才建表（不會用舊的 VIEW 往下跑）；
    等的時候不拿著股價線的鎖（股價線不會被拖住）。"""
    monkeypatch.setattr(main, "LOCK_FILE", tmp_path / "pipeline.lock")
    monkeypatch.setattr(main, "NEWS_LOCK_FILE", tmp_path / "news.lock")
    monkeypatch.setattr(main.repository, "schema_hash", lambda: "new")
    monkeypatch.setattr(main.repository, "applied_schema_hash", lambda: "old")
    built = []
    monkeypatch.setattr(main.repository, "create_tables", lambda: built.append(1))
    started, release = threading.Event(), threading.Event()

    def news_line_running():
        with main.pipeline_lock(main.NEWS_LOCK_FILE, wait_seconds=1):   # 新聞線拿著它的鎖
            started.set()
            release.wait(5)
    news = threading.Thread(target=news_line_running)
    news.start()
    started.wait(5)
    refresher = threading.Thread(target=main.refresh_schema)
    refresher.start()
    time.sleep(0.5)
    with open(main.LOCK_FILE, "a+") as f:   # 等的時候股價線的鎖是空的
        f.seek(0)
        assert main.try_lock(f)
        main.unlock(f)
    assert built == []
    release.set()
    news.join()
    refresher.join(5)
    assert built == [1]


def test_news_line_after_the_stock_line_failing_still_exits_1(lines, tmp_path):
    """股價線成功、接著的新聞線失敗：記號已經放了（股價線不重跑），結束代碼 1 看得出新聞線失敗；下一次排定的新聞線會補。"""
    lines.fail = {"news"}
    with pytest.raises(SystemExit):
        main.main()
    assert lines.calls == ["schema", "stock", "news"] and (tmp_path / "done" / "20261123").exists()
