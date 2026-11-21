"""common/worker.py 和 runner 的 Celery 模式：不連 RabbitMQ。

在 worker 上執行用 task.apply()（在本機照 worker 的方式跑一次）；送進佇列用假的 apply_async 代替。
"""
import celery.canvas
import pytest
import requests

from common import config, runner, worker
from crawler import task as crawler_task


def test_every_task_is_registered_by_module_and_name():
    """Flower、RabbitMQ 上看到的任務名稱＝模組.函式。"""
    worker.app.loader.import_default_modules()   # 和 worker 啟動時一樣，載入 include 的三個模組
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
    def __init__(self, value):
        self.value = value

    def get(self):
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
