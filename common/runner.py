"""三個 producer（crawler、transform、analyze）共用的任務執行方式。

    failed = runner.run_jobs(jobs)              # 依序執行，回傳失敗的任務
    failed = runner.run_jobs(jobs, sleep=1.0)   # crawler：每個任務之後再等一下（打 API 太快會被擋）
    runner.finish(failed)                       # 印出失敗清單；有失敗就結束代碼 1

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.crawl_notice, {"date_yyyymmdd": "20261113"})。
一個任務失敗不會中斷整批：記下來，繼續下一個。暫時性的失敗整個任務重做（任務是冪等的，重做沒有副作用）。
"""
import time

import requests
import sqlalchemy

from common.config import MAX_RETRIES, RETRY_DELAY_SECONDS

# MySQL 的鎖衝突：1213＝deadlock（兩個交易互等，MySQL 讓其中一個失敗、整個復原）、1205＝等鎖等太久
LOCK_ERRORS = (1213, 1205)


def job_name(func, kwargs: dict) -> str:
    """給 log 看的任務名稱，例如 crawl_notice(date_yyyymmdd='20261113')。"""
    return f"{func.__name__}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"


def is_retryable(error: Exception) -> bool:
    """等一下再試可能會好的失敗：

    - 逾時、連線失敗（requests 的錯誤）
    - API 回 5xx、429（repository.FetchError 標成 retryable）
    - 資料庫鎖衝突（好幾個 worker 同時寫，見 LOCK_ERRORS）
    內容不對、被擋（回 HTML）重試也一樣，不重試；太快重打反而更容易被擋。
    """
    if isinstance(error, requests.RequestException):
        return True
    if isinstance(error, sqlalchemy.exc.OperationalError):
        return bool(error.orig and error.orig.args) and error.orig.args[0] in LOCK_ERRORS
    return getattr(error, "retryable", False)


def run_with_retry(func, kwargs: dict, retries: int) -> str:
    """執行一個任務；暫時性的失敗等 RETRY_DELAY_SECONDS 秒再試，最多重試 retries 次，還是失敗就丟出錯誤。"""
    for attempt in range(retries + 1):
        try:
            return func(**kwargs)
        except Exception as e:
            if attempt == retries or not is_retryable(e):
                raise
            print(f"{job_name(func, kwargs)} ✗ {e}，{RETRY_DELAY_SECONDS:g} 秒後重試（剩 {retries - attempt} 次）")
            time.sleep(RETRY_DELAY_SECONDS)


def run_jobs(jobs: list[tuple], sleep: float = 0.0, retries: int = MAX_RETRIES) -> list[str]:
    """依序執行任務，回傳失敗的任務名稱。

    sleep：每個任務之後等幾秒（打 API 太快會被擋）。retries：暫時性的失敗重試幾次（見 is_retryable）。
    """
    failed = []
    for func, kwargs in jobs:
        name = job_name(func, kwargs)
        try:
            print(name, "→", run_with_retry(func, kwargs, retries))   # 任務回傳一行結果，例如 raw_notice/20261113
        except Exception as e:
            print(name, "✗", e)
            failed.append(name)
        if sleep:
            time.sleep(sleep)
    return failed


def finish(failed: list[str]) -> None:
    """一輪做完：印出失敗清單；有失敗就結束代碼 1（main.py、排程看得到）。"""
    print("\n失敗：", failed or "無")
    if failed:
        raise SystemExit(1)
