"""四個 producer（crawler、transform、analyze、ai）共用的任務執行方式。

    failed = runner.run_jobs(jobs, queue="transform")              # 依序執行（或送進佇列），回傳失敗的任務
    failed = runner.run_jobs(jobs, sleep=1.0, queue="crawl_twse")   # crawler：每個任務之後再等一下（打 API 太快會被擋）
    runner.finish(failed)                       # 印出失敗清單；有失敗就結束代碼 1

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.crawl_notice, {"date_yyyymmdd": "20261113"})。
一個任務失敗不會中斷整批：記下來，繼續下一個。暫時性的失敗整個任務重做（任務是冪等的，重做沒有副作用）。
每個任務每跑一次都寫一列任務歷史（tw_stock.job_run，record()），本機和 worker 都一樣。
USE_CELERY=1 時，同一批任務改成送進 RabbitMQ 給 worker 做：task.s(**參數).apply_async(queue=...)（和課程的 producer 一樣），
等全部做完才回傳；重試、間隔改在 worker 上做（common/worker.py 的 Job）。
"""
import time

import anthropic
import requests
import sqlalchemy

from common import date_utils, repository
from common.config import JOB_LOG, MAX_RETRIES, RETRY_DELAY_SECONDS, USE_CELERY

# PostgreSQL 的鎖衝突（SQLSTATE）：40P01＝deadlock（兩個交易互等，資料庫讓其中一個失敗、整個復原）、
# 40001＝序列化失敗、55P03＝等不到鎖
LOCK_ERRORS = ("40P01", "40001", "55P03")


def job_name(func, kwargs: dict) -> str:
    """給 log 看的任務名稱，例如 crawl_notice(date_yyyymmdd='20261113')。"""
    return f"{func.__name__}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"


def task_name(func) -> str:
    """任務歷史上的名稱（模組.函式），例如 crawler.task.crawl_notice；Celery 任務就是它註冊的名字。"""
    return getattr(func, "name", None) or f"{func.__module__}.{func.__name__}"


def error_text(error: Exception) -> str:
    """錯誤寫成「類型: 訊息」，例如 Timeout: read timed out。"""
    return f"{type(error).__name__}: {error}"


def record(func, kwargs: dict, status: str, started, *, result=None, error=None, attempt: int = 1,
           mode: str = "local", worker: str | None = None) -> None:
    """任務歷史寫一列（tw_stock.job_run）：status 是 success、retry 或 failure。

    寫不進去（資料庫連不上、表還沒建）只印一行警告，不影響任務本身。
    """
    if not JOB_LOG:
        return
    finished = date_utils.now()
    try:
        repository.insert_job_run({
            "task": task_name(func), "args": kwargs, "status": status,
            "result": None if result is None else str(result), "error": error, "attempt": attempt,
            "mode": mode, "worker": worker,
            "started_at": started.replace(tzinfo=None), "finished_at": finished.replace(tzinfo=None),
            "seconds": round((finished - started).total_seconds(), 3),
        })
    except Exception as e:
        print(f"（任務歷史沒寫進去：{error_text(e)}）")


def is_retryable(error: Exception) -> bool:
    """等一下再試可能會好的失敗：

    - 逾時、連線失敗（requests 的錯誤）
    - API 回 5xx、429（repository.FetchError 標成 retryable）
    - Claude API 回 429、5xx、連線失敗（anthropic 的錯誤；ai/ 的任務）
    - 資料庫鎖衝突（好幾個 worker 同時寫，見 LOCK_ERRORS）
    內容不對、被擋（回 HTML）重試也一樣，不重試；太快重打反而更容易被擋。
    """
    if isinstance(error, (requests.RequestException, anthropic.RateLimitError, anthropic.APIConnectionError)):
        return True
    if isinstance(error, anthropic.APIStatusError):
        return error.status_code >= 500
    if isinstance(error, sqlalchemy.exc.OperationalError):
        return getattr(error.orig, "sqlstate", None) in LOCK_ERRORS
    return getattr(error, "retryable", False)


def run_with_retry(func, kwargs: dict, retries: int) -> str:
    """執行一個任務；暫時性的失敗等 RETRY_DELAY_SECONDS 秒再試，最多重試 retries 次，還是失敗就丟出錯誤。"""
    for attempt in range(retries + 1):
        started = date_utils.now()
        try:
            result = func(**kwargs)
        except Exception as e:
            final = attempt == retries or not is_retryable(e)
            record(func, kwargs, "failure" if final else "retry", started, error=error_text(e), attempt=attempt + 1)
            if final:
                raise
            print(f"{job_name(func, kwargs)} ✗ {e}，{RETRY_DELAY_SECONDS:g} 秒後重試（剩 {retries - attempt} 次）")
            time.sleep(RETRY_DELAY_SECONDS)
        else:
            record(func, kwargs, "success", started, result=result, attempt=attempt + 1)
            return result


def run_jobs(jobs: list[tuple], sleep: float = 0.0, retries: int = MAX_RETRIES, queue: str | None = None) -> list[str]:
    """執行一批任務，回傳失敗的任務名稱。

    sleep：每個任務之後等幾秒（打 API 太快會被擋）。retries：暫時性的失敗重試幾次（見 is_retryable）。
    queue：USE_CELERY=1 時送到哪個佇列（crawl_twse、crawl_mops、crawl_finmind、transform、analyze、ai，見 common/worker.py），
    一定要給；沒開就在這裡依序執行，不用給。
    """
    if USE_CELERY:
        if queue is None:
            raise ValueError("USE_CELERY=1 要指定 queue（見 common/worker.py）")
        return run_jobs_on_workers(jobs, queue)
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


def run_jobs_on_workers(jobs: list[tuple], queue: str) -> list[str]:
    """把整批任務送進佇列，再照送出的順序等每個結果，回傳失敗的任務名稱。重試、間隔都在 worker 那邊做（Job）。

    送法和課程一樣：task.s(**參數) 建立簽章（signature），apply_async(queue=...) 指定佇列送出。
    """
    sent = [(job_name(func, kwargs), func.s(**kwargs).apply_async(queue=queue)) for func, kwargs in jobs]
    print(f"送出 {len(sent)} 個任務到佇列 {queue}")
    failed = []
    for name, result in sent:
        try:
            print(name, "→", result.get())
        except Exception as e:
            print(name, "✗", e)
            failed.append(name)
    return failed


def finish(failed: list[str]) -> None:
    """一輪做完：印出失敗清單；有失敗就結束代碼 1（main.py、排程看得到）。"""
    print("\n失敗：", failed or "無")
    if failed:
        raise SystemExit(1)
