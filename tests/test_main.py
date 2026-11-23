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


# ---------- 一輪的順序 ----------
@pytest.fixture
def steps(monkeypatch):
    """不真的執行：記下 run（一步）、run_parallel（同時好幾步）被叫的順序；codes 可以指定某一步的結束代碼。"""
    calls, codes = [], {}

    def fake_run(module, args):
        calls.append((module, args[-1]))
        return codes.get(module, 0)

    def fake_parallel(group):
        calls.append([(module, args[-1]) for module, args in group])
        return [codes.get(module, 0) for module, _ in group]
    monkeypatch.setattr(main, "run", fake_run)
    monkeypatch.setattr(main, "run_parallel", fake_parallel)
    return calls, codes


def test_analyze_and_ai_run_together_after_transform(steps):
    """crawler → transform → analyze 和 ai 同時（兩邊都只讀 stage）。"""
    calls, _ = steps
    main.run_pipeline(["daily"])
    assert calls == [("crawler.producer", "daily"), ("transform.producer", "daily"),
                     [("analyze.producer", "daily"), ("ai.producer", "daily")]]


def test_transform_failure_stops_before_analyze_and_ai(steps):
    calls, codes = steps
    codes["transform.producer"] = 1
    with pytest.raises(SystemExit):
        main.run_pipeline(["daily"])
    assert calls == [("crawler.producer", "daily"), ("transform.producer", "daily")]


def test_analyze_failure_does_not_stop_ai_but_exits_1(steps):
    """analyze 失敗：ai 照跑完（不讀 app），最後結束代碼 1。"""
    calls, codes = steps
    codes["analyze.producer"] = 1
    with pytest.raises(SystemExit):
        main.run_pipeline(["daily"])
    assert calls[-1] == [("analyze.producer", "daily"), ("ai.producer", "daily")]


def test_run_parallel_returns_each_exit_code():
    """真的同時開兩個 process，照傳進來的順序回傳結束代碼。"""
    assert main.run_parallel([("json.tool", ["--help"]), ("this_module_does_not_exist", [])]) == [0, 1]


def test_run_parallel_stops_started_processes_when_interrupted(monkeypatch):
    """開第二個子程序時出錯（或等的時候被 Ctrl+C）：已經開的子程序要關掉，不能留著繼續寫資料表。"""
    class FakeProcess:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout=None):
            return 0
    started = []

    def fake_popen(command):
        if started:
            raise KeyboardInterrupt
        started.append(FakeProcess())
        return started[-1]
    monkeypatch.setattr(main.subprocess, "Popen", fake_popen)
    with pytest.raises(KeyboardInterrupt):
        main.run_parallel([("analyze.producer", []), ("ai.producer", [])])
    assert started[0].stopped


def test_stop_processes_ends_a_real_running_process():
    process = main.subprocess.Popen([main.sys.executable, "-c", "import time; time.sleep(30)"])
    main.stop_processes([process], wait_seconds=5)
    assert process.poll() is not None


# ---------- 每天全部跑完的記號 ----------
@pytest.fixture
def daily_run(monkeypatch, tmp_path):
    """main()：鎖和記號放到暫存資料夾；run_pipeline 只記被叫了幾次，outcome 可以指定成失敗。"""
    state = types.SimpleNamespace(runs=0, fail=False, mode="daily")
    monkeypatch.setattr(main, "LOCK_FILE", tmp_path / "pipeline.lock")
    monkeypatch.setattr(main, "DONE_DIR", tmp_path / "done")
    monkeypatch.setattr(main.repository, "create_tables", lambda: None)
    monkeypatch.setattr(main.cli, "parse_args",
                        lambda: types.SimpleNamespace(mode=state.mode, start="20261123", end="20261123", refetch=False))

    def fake_pipeline(args):
        state.runs += 1
        if state.fail:
            raise SystemExit(1)
    monkeypatch.setattr(main, "run_pipeline", fake_pipeline)
    return state


def test_daily_marks_done_and_later_runs_skip(daily_run, tmp_path):
    """全部成功：放當天的記號；同一天之後的排程看到記號就跳過。"""
    main.main()
    assert (tmp_path / "done" / "20261123").exists()
    main.main()
    assert daily_run.runs == 1


def test_daily_failure_leaves_no_mark_so_next_hour_runs_again(daily_run, tmp_path):
    """有失敗（例如證交所還沒公布）：不放記號，下一個小時再跑。"""
    daily_run.fail = True
    with pytest.raises(SystemExit):
        main.main()
    daily_run.fail = False
    main.main()
    assert daily_run.runs == 2 and (tmp_path / "done" / "20261123").exists()


def test_backfill_ignores_the_mark(daily_run, tmp_path):
    daily_run.mode = "backfill"
    main.main()
    main.main()
    assert daily_run.runs == 2 and not (tmp_path / "done").exists()


def test_schema_objects_lists_tables_indexes_and_views():
    """ensure_tables 檢查的名單：schema/*.sql 建的表、索引、VIEW 都在裡面，註解裡的不算。"""
    names = main.repository.schema_objects()
    assert {"stage_announcement", "idx_stage_punish_stock", "app_card"} <= set(names)
    assert len(names) == len(set(names))
