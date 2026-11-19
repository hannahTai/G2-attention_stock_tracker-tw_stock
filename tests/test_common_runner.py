"""common/runner.py：依序執行任務、暫時性失敗重試、最後回報。不連網路、不連資料庫。"""
import pytest
import pymysql
import requests
import sqlalchemy

from common import repository, runner


class Flaky:
    """前幾次丟出指定的錯誤，之後成功；calls 記錄被呼叫幾次。"""

    def __init__(self, errors, name="flaky"):
        self.errors = list(errors)
        self.calls = 0
        self.__name__ = name

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "raw_notice/20261113"


def deadlock() -> sqlalchemy.exc.OperationalError:
    return sqlalchemy.exc.OperationalError("INSERT …", {}, pymysql.err.OperationalError(1213, "Deadlock found"))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)


def test_retry_then_success():
    """逾時、5xx、資料庫 deadlock 都是暫時性的：重試後成功。"""
    func = Flaky([requests.Timeout("逾時"), repository.FetchError("HTTP 503", retryable=True), deadlock()])
    assert runner.run_with_retry(func, {}, retries=3) == "raw_notice/20261113"
    assert func.calls == 4


def test_no_retry_when_not_retryable():
    """內容不對：只跑一次就丟出錯誤。"""
    func = Flaky([repository.FetchError("年份不對", retryable=False)])
    with pytest.raises(repository.FetchError):
        runner.run_with_retry(func, {}, retries=3)
    assert func.calls == 1


def test_give_up_after_retries():
    """一直逾時：原本 1 次＋重試 3 次，之後放棄。"""
    func = Flaky([requests.ConnectionError("連不上")] * 4)
    with pytest.raises(requests.ConnectionError):
        runner.run_with_retry(func, {}, retries=3)
    assert func.calls == 4


def test_run_jobs_keeps_going_and_lists_failed():
    """重試完還是失敗 → 列進失敗清單，下一個任務照跑。"""
    bad = Flaky([requests.Timeout("逾時")] * 4, name="bad")
    good = Flaky([], name="good")
    assert runner.run_jobs([(bad, {}), (good, {})], retries=3) == ["bad()"]
    assert good.calls == 1


def test_finish_exits_1_when_failed():
    runner.finish([])
    with pytest.raises(SystemExit):
        runner.finish(["bad()"])
