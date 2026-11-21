"""common/worker.py 和 runner 的 Celery 模式：不連 RabbitMQ，用假的送出結果代替。"""
import pytest

from common import runner, worker
from crawler import task as crawler_task


def test_func_path_round_trip():
    """任務函式 → 字串 → 同一個函式（送進佇列只能放字串）。"""
    path = worker.func_path(crawler_task.crawl_notice)
    assert path == "crawler.task.crawl_notice"
    assert worker.load_func(path) is crawler_task.crawl_notice


def test_load_func_only_project_tasks():
    """佇列裡的字串不能拿來執行任意函式。"""
    with pytest.raises(ValueError):
        worker.load_func("os.system")


def test_run_job_turns_errors_into_runtime_error(monkeypatch):
    """worker 上的錯誤換成 RuntimeError（自訂錯誤送回 producer 可能還原不了），訊息保留原本的類型。"""
    def broken(date_yyyymmdd):
        raise ValueError("內容不對")
    monkeypatch.setattr(crawler_task, "crawl_notice", broken)
    with pytest.raises(RuntimeError, match="ValueError: 內容不對"):
        worker.run_job("crawler.task.crawl_notice", {"date_yyyymmdd": "20261120"}, retries=0, sleep=0)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def get(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_run_jobs_sends_to_queue_and_collects_failures(monkeypatch):
    """USE_CELERY=1：整批送到指定的佇列，照順序等結果，失敗的記下來。"""
    sent = []

    def fake_apply_async(args, queue):
        sent.append((args, queue))
        return FakeResult(RuntimeError("HTTP 500") if args[1]["date_yyyymmdd"] == "20261119" else "raw_notice/ok")

    monkeypatch.setattr(runner, "USE_CELERY", True)
    monkeypatch.setattr(worker.run_job, "apply_async", fake_apply_async)
    jobs = [(crawler_task.crawl_notice, {"date_yyyymmdd": d}) for d in ("20261119", "20261120")]
    failed = runner.run_jobs(jobs, sleep=1.0, queue="twse")

    assert failed == ["crawl_notice(date_yyyymmdd='20261119')"]
    assert [queue for _, queue in sent] == ["twse", "twse"]
    assert sent[0][0] == ("crawler.task.crawl_notice", {"date_yyyymmdd": "20261119"}, runner.MAX_RETRIES, 1.0)
