"""common/worker.py 和 runner 的 Celery 模式：不連 RabbitMQ。

在 worker 上執行用 task.apply()（在本機照 worker 的方式跑一次）；送進佇列用假的 apply_async 代替。
"""
import types

import celery.canvas
import celery.exceptions
import pytest
import requests

from ai import producer as ai_producer
from analyze import producer as analyze_producer
from common import config, runner, worker
from crawler import producer as crawler_producer
from crawler import task as crawler_task
from transform import producer as transform_producer


def test_every_task_is_registered_by_module_and_name():
    """Flower、RabbitMQ 上看到的任務名稱＝模組.函式。"""
    worker.app.loader.import_default_modules()   # 和 worker 啟動時一樣，載入 include 的四個模組
    names = set(worker.app.tasks)
    assert {"crawler.task.crawl_notice", "crawler.task.crawl_announcement_detail",
            "transform.task.transform_notice", "analyze.task.analyze_progress"} <= names


def test_crawler_tasks_wait_after_each_call():
    """打外部網站的任務做完等一下；觀測站和證交所各自的間隔。"""
    assert crawler_task.crawl_notice.sleep_after == config.SLEEP_SECONDS
    assert crawler_task.crawl_announcement.sleep_after == config.SLEEP_SECONDS_MOPS


def fake_task(monkeypatch, run):
    """把 crawl_notice 的內容換掉（task 物件不變，名稱、base 都一樣），也不真的等。"""
    monkeypatch.setattr(crawler_task.crawl_notice, "run", run)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)


def test_direct_call_is_the_plain_function(monkeypatch):
    """直接呼叫（本機依序執行、測試）：錯誤原樣丟出，重試交給 runner。"""
    def broken(date_yyyymmdd):
        raise ValueError("內容不對")
    fake_task(monkeypatch, broken)
    with pytest.raises(ValueError, match="內容不對"):
        crawler_task.crawl_notice(date_yyyymmdd="20261120")


def test_on_worker_errors_become_runtime_error(monkeypatch):
    """worker 上的錯誤換成 RuntimeError（自訂錯誤送回 producer 可能還原不了），訊息保留原本的類型；不重試。"""
    calls = []

    def broken(date_yyyymmdd):
        calls.append(date_yyyymmdd)
        raise ValueError("內容不對")
    fake_task(monkeypatch, broken)
    result = crawler_task.crawl_notice.apply(kwargs={"date_yyyymmdd": "20261120"})
    assert isinstance(result.result, RuntimeError) and "ValueError: 內容不對" in str(result.result)
    assert len(calls) == 1


def test_on_worker_temporary_errors_retry(monkeypatch):
    """worker 上的暫時性失敗（逾時等）用 Celery retry 重做，最多 MAX_RETRIES 次。"""
    calls = []

    def flaky(date_yyyymmdd):
        calls.append(date_yyyymmdd)
        if len(calls) < 3:
            raise requests.Timeout("逾時")
        return "raw_notice/20261120"
    fake_task(monkeypatch, flaky)
    result = crawler_task.crawl_notice.apply(kwargs={"date_yyyymmdd": "20261120"})
    assert result.result == "raw_notice/20261120" and len(calls) == 3


class FakeResult:
    """假的 AsyncResult：get() 回傳 value（是 Exception 就丟出來）；pending＝前幾次 get(timeout=...) 還沒好（TimeoutError）。"""
    def __init__(self, value, pending=0):
        self.value = value
        self.pending = pending

    def get(self, timeout=None):
        if self.pending:
            self.pending -= 1
            raise celery.exceptions.TimeoutError("還沒好")
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_run_jobs_sends_to_queue_and_collects_failures(monkeypatch):
    """USE_CELERY=1：整批用 task.s(...).apply_async(queue=...) 送到指定的佇列，照順序等結果，失敗的記下來。"""
    sent = []

    def fake_apply_async(signature, queue):
        sent.append((signature.task, dict(signature.kwargs), queue))
        return FakeResult(RuntimeError("HTTP 500") if signature.kwargs["date_yyyymmdd"] == "20261119" else "raw_notice/ok")

    monkeypatch.setattr(runner, "USE_CELERY", True)
    monkeypatch.setattr(celery.canvas.Signature, "apply_async", fake_apply_async)
    jobs = [(crawler_task.crawl_notice, {"date_yyyymmdd": d}) for d in ("20261119", "20261120")]
    failed = runner.run_jobs(jobs, sleep=1.0, queue="crawl_twse")

    assert failed == ["crawl_notice(date_yyyymmdd='20261119')"]
    assert sent == [("crawler.task.crawl_notice", {"date_yyyymmdd": "20261119"}, "crawl_twse"),
                    ("crawler.task.crawl_notice", {"date_yyyymmdd": "20261120"}, "crawl_twse")]


def chain_step(name):
    """一批：產生一個任務，佇列名稱就用 name（測試看送出的順序）。"""
    return (lambda: [(lambda: "ok", {"name": name})]), 0.0, name


def test_run_chains_sends_first_batches_together(monkeypatch):
    """USE_CELERY=1：每條鏈的第一批同時送出（兩個佇列同時在做），之後輪流等結果、送下一批；同一條鏈裡照順序。失敗的記下來。"""
    sent = []
    values = {"b2": RuntimeError("HTTP 500")}

    def fake_send(jobs, queue):
        sent.append(queue)
        return [(f"job_{queue}", FakeResult(values.get(queue, "ok")))]

    monkeypatch.setattr(runner, "USE_CELERY", True)
    monkeypatch.setattr(runner, "send_jobs", fake_send)
    failed = runner.run_chains([[chain_step("a1"), chain_step("a2")],
                                [chain_step("b1"), chain_step("b2")]])
    assert sent[:2] == ["a1", "b1"]                      # 第一批同時送出，還沒等任何結果
    assert sent.index("a1") < sent.index("a2") and sent.index("b1") < sent.index("b2")
    assert failed == ["job_b2"]


def test_run_chains_skips_empty_batches(monkeypatch):
    """沒有要做的批次（例如全文都抓過了）跳過，不送空的一批；整條都空也不會卡住。"""
    sent = []
    monkeypatch.setattr(runner, "USE_CELERY", True)
    monkeypatch.setattr(runner, "send_jobs", lambda jobs, queue: sent.append(queue) or [(queue, FakeResult("ok"))])
    empty = (lambda: [], 0.0, "empty")
    assert runner.run_chains([[empty, chain_step("a2")], [empty]]) == []
    assert sent == ["a2"]


def test_run_chains_locally_runs_one_chain_after_another(monkeypatch):
    """USE_CELERY=0：在本機一條鏈一條鏈依序執行。"""
    order = []
    monkeypatch.setattr(runner, "run_jobs", lambda jobs, sleep, queue: order.append(queue) or [])
    runner.run_chains([[chain_step("a1"), chain_step("a2")], [chain_step("b1")]])
    assert order == ["a1", "a2", "b1"]


@pytest.mark.parametrize(("part", "expected"), [
    ("twse", ["crawl_twse", "crawl_twse"]),                                          # 證交所分步驟依序（空的步驟跳過）
    ("news", [[["crawl_mops", "crawl_mops"], ["crawl_finmind", "crawl_news"]]]),     # 觀測站、新聞兩條鏈
])
def test_crawler_part_runs_twse_steps_or_two_chains(monkeypatch, part, expected):
    """crawler --part twse：證交所分步驟；--part news：觀測站（清單 → 全文）、新聞（FinMind 清單 → 網頁）兩條鏈。"""
    calls = []
    monkeypatch.setattr(crawler_producer.repository, "ensure_tables", lambda: None)
    monkeypatch.setattr(crawler_producer.cli, "parse_args",
                        lambda: types.SimpleNamespace(start="20261120", end="20261120", refetch=False, part=part))
    monkeypatch.setattr(crawler_producer.runner, "run_jobs", lambda jobs, sleep, queue: calls.append(queue) or [])
    monkeypatch.setattr(crawler_producer.runner, "run_chains",
                        lambda chains: calls.append([[queue for _, _, queue in chain] for chain in chains]) or [])
    monkeypatch.setattr(crawler_producer.runner, "finish", lambda failed: None)
    monkeypatch.setattr(crawler_producer, "build_twse_steps", lambda *a: [["holiday"], [], ["price"]])   # 空的步驟跳過
    crawler_producer.main()
    assert calls == expected


def test_crawler_without_part_stops(monkeypatch):
    """crawler 沒給 --part：直接結束（新聞的部分要先跑 transform --part twse，不能一次做完）。"""
    monkeypatch.setattr(crawler_producer.cli, "parse_args",
                        lambda: types.SimpleNamespace(start="20261120", end="20261120", refetch=False, part=None))
    with pytest.raises(SystemExit, match="--part"):
        crawler_producer.main()


def test_transform_without_part_runs_twse_before_building_news_jobs(monkeypatch):
    """transform 沒給 --part：先整理證交所，做完才算重大訊息、新聞的任務（要讀剛整理好的注意股）。"""
    from transform import producer as transform_producer
    order = []
    monkeypatch.setattr(transform_producer.repository, "ensure_tables", lambda: None)
    monkeypatch.setattr(transform_producer.cli, "parse_args",
                        lambda: types.SimpleNamespace(start="20261120", end="20261120", part=None))
    monkeypatch.setattr(transform_producer, "build_jobs", lambda start, end, part: order.append(f"build {part}") or [part])
    monkeypatch.setattr(transform_producer.runner, "run_jobs", lambda jobs, queue: order.append(f"run {jobs[0]}") or [])
    monkeypatch.setattr(transform_producer.runner, "finish", lambda failed: None)
    transform_producer.main()
    assert order == ["build twse", "run twse", "build news", "run news"]


def test_run_jobs_on_workers_needs_a_queue(monkeypatch):
    """USE_CELERY=1 沒指定佇列：直接報錯，不會送到沒有 worker 在聽的佇列、一直等下去。"""
    monkeypatch.setattr(runner, "USE_CELERY", True)
    with pytest.raises(ValueError, match="要指定 queue"):
        runner.run_jobs([(crawler_task.crawl_notice, {"date_yyyymmdd": "20261120"})])


def test_transform_and_analyze_use_their_own_queues(monkeypatch):
    """transform、analyze 各自一個佇列（各一個 worker）。"""
    queues = []
    monkeypatch.setattr(runner, "run_jobs", lambda jobs, queue=None, **kw: queues.append(queue) or [])
    for producer, _name in ((transform_producer, "transform"), (analyze_producer, "analyze")):
        monkeypatch.setattr(producer.repository, "ensure_tables", lambda: None)
        monkeypatch.setattr(producer, "build_jobs", lambda *span: [])
        monkeypatch.setattr(producer.cli, "parse_args", lambda: types.SimpleNamespace(start="20261120", end="20261120", part="twse"))
        monkeypatch.setattr(producer.runner, "finish", lambda failed: None)
        producer.main()
    assert queues == ["transform", "analyze"]


# ---------- 任務歷史（job_run）----------
@pytest.fixture
def job_rows(monkeypatch):
    """打開任務歷史，寫入的列收進 list（不碰資料庫）。"""
    rows = []
    monkeypatch.setattr(runner, "JOB_LOG", True)
    monkeypatch.setattr(runner.repository, "insert_job_run", rows.append)
    return rows


def test_worker_records_every_attempt(monkeypatch, job_rows):
    """worker 上：逾時 2 次再成功 → retry、retry、success 三列，記下第幾次、哪個模式。"""
    calls = []

    def flaky(date_yyyymmdd):
        calls.append(1)
        if len(calls) < 3:
            raise requests.Timeout("逾時")
        return "raw_notice/20261120"
    fake_task(monkeypatch, flaky)
    crawler_task.crawl_notice.apply(kwargs={"date_yyyymmdd": "20261120"})
    assert [(r["status"], r["attempt"], r["mode"]) for r in job_rows] == [
        ("retry", 1, "celery"), ("retry", 2, "celery"), ("success", 3, "celery")]
    assert job_rows[-1]["task"] == "crawler.task.crawl_notice" and job_rows[-1]["result"] == "raw_notice/20261120"
    assert job_rows[0]["error"] == "Timeout: 逾時" and job_rows[0]["args"] == {"date_yyyymmdd": "20261120"}


def test_local_run_records_too(monkeypatch, job_rows):
    """本機依序執行（USE_CELERY=0）也寫：成功一列、失敗一列。"""
    def good():
        return "ok"

    def bad():
        raise ValueError("內容不對")
    runner.run_jobs([(good, {}), (bad, {})], retries=0)
    assert [(r["task"].rsplit(".", 1)[-1], r["status"], r["mode"]) for r in job_rows] == [
        ("good", "success", "local"), ("bad", "failure", "local")]


def test_job_log_failure_does_not_break_the_task(monkeypatch, capsys):
    """任務歷史寫不進去（資料庫連不上）只印警告，任務照樣成功。"""
    def broken_insert(row):
        raise ConnectionError("連不上")
    monkeypatch.setattr(runner, "JOB_LOG", True)
    monkeypatch.setattr(runner.repository, "insert_job_run", broken_insert)
    assert runner.run_jobs([(lambda: "ok", {})]) == []
    assert "任務歷史沒寫進去" in capsys.readouterr().out


@pytest.mark.parametrize("key, expected", [
    ("key", [("build_classify_jobs", "ai"), ("build_summary_jobs", "ai"), ("build_embed_jobs", "embed"),
             ("build_story_jobs", "ai"), ("build_card_jobs", "embed")]),
    ("", [("build_embed_jobs", "embed"), ("build_story_jobs", "ai")]),
])
def test_ai_producer_order_and_queues(monkeypatch, key, expected):
    """公告分類 → 新聞摘要 → 新聞向量 → 同一件事 → 卡片 AI 摘要；用本機模型的送 embed，其他送 ai。
    沒有 API key：只做不用 Claude 的新聞向量、同一件事。"""
    sent = []
    monkeypatch.setattr(ai_producer, "ANTHROPIC_API_KEY", key)
    monkeypatch.setattr(ai_producer.runner, "run_jobs", lambda jobs, queue: sent.append((jobs[0], queue)) or [])
    for name in ("build_classify_jobs", "build_summary_jobs", "build_embed_jobs", "build_story_jobs", "build_card_jobs"):
        monkeypatch.setattr(ai_producer, name, lambda start, end, *rest, name=name: [name])
    ai_producer.run_all("20261120", "20261120")
    assert sent == expected


def test_run_chains_keeps_going_when_one_chain_cannot_build(monkeypatch):
    """一條鏈產生任務清單出錯（例如資料庫連不上）：記成失敗、這條鏈停下來，其他鏈照做完。"""
    sent = []
    monkeypatch.setattr(runner, "USE_CELERY", True)
    monkeypatch.setattr(runner, "send_jobs", lambda jobs, queue: sent.append(queue) or [(queue, FakeResult("ok"))])

    def broken():
        raise ConnectionError("連不上")
    failed = runner.run_chains([[(broken, 0.0, "a1"), chain_step("a2")], [chain_step("b1"), chain_step("b2")]])
    assert sent == ["b1", "b2"]                       # 第 1 條停下來（a2 沒送），第 2 條照做完
    assert failed == ["第 1 條鏈產生 a1 的任務清單"]


def test_run_chains_slow_chain_does_not_hold_up_others(monkeypatch):
    """慢的鏈（第一批一直還沒好）不會卡住快的：快的鏈做完第一批就送第二批，不用等慢的。"""
    sent = []
    pending = {"a1": 5}   # a1 前 5 次 get 都還沒好

    def fake_send(jobs, queue):
        sent.append(queue)
        return [(f"job_{queue}", FakeResult("ok", pending.get(queue, 0)))]

    monkeypatch.setattr(runner, "USE_CELERY", True)
    monkeypatch.setattr(runner, "send_jobs", fake_send)
    assert runner.run_chains([[chain_step("a1"), chain_step("a2")], [chain_step("b1"), chain_step("b2")]]) == []
    assert sent == ["a1", "b1", "b2", "a2"]   # b2 在 a1 還沒好的時候就送出了


def test_run_chains_locally_keeps_going_when_one_chain_cannot_build(monkeypatch):
    """本機（USE_CELERY=0）也一樣：一條鏈產生清單出錯記成失敗，其他鏈照做。"""
    order = []
    monkeypatch.setattr(runner, "run_jobs", lambda jobs, sleep, queue: order.append(queue) or [])

    def broken():
        raise ConnectionError("連不上")
    failed = runner.run_chains([[(broken, 0.0, "a1"), chain_step("a2")], [chain_step("b1")]])
    assert order == ["b1"] and failed == ["第 1 條鏈產生 a1 的任務清單"]


def test_summary_jobs_skip_news_whose_page_was_skipped_too_often(monkeypatch):
    """網頁打不開、全文太短跳過 MAX_FAILED_CALLS 次的新聞不再排（看任務歷史）。"""
    monkeypatch.setattr(ai_producer.repository, "list_unsummarized_news", lambda *a: ["n1", "n2"])
    monkeypatch.setattr(ai_producer.repository, "repeatedly_skipped", lambda task, mark, arg: {"n2"})
    assert [kw["news_id"] for _, kw in ai_producer.build_summary_jobs("20261120", "20261120")] == ["n1"]
