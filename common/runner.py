"""四個 producer（crawler、transform、analyze、ai）共用的任務執行方式。

    failed = runner.run_jobs(jobs, queue="transform")              # 依序執行（或送進佇列），回傳失敗的任務
    failed = runner.run_jobs(jobs, sleep=1.0, queue="crawl_twse")   # crawler：每個任務之後再等一下（打 API 太快會被擋）
    failed = runner.run_chains([[(build_a1, 0.6, "crawl_mops"), (build_a2, 0.6, "crawl_mops")],
                                [(build_b1, 1.0, "crawl_finmind")]])   # 好幾條任務鏈同時進行（見 run_chains）
    runner.finish(failed)                       # 印出失敗清單；有失敗就結束代碼 1

任務清單的每一項是 (任務函式, 參數 dict)，例如 (task.crawl_notice, {"date_yyyymmdd": "20261113"})。
一個任務失敗不會中斷整批：記下來，繼續下一個。暫時性的失敗整個任務重做（任務是冪等的，重做沒有副作用）。
每個任務每跑一次都寫一列任務歷史（tw_stock.job_run，record()），本機和 worker 都一樣。
USE_CELERY=1 時，同一批任務改成送進 RabbitMQ 給 worker 做：task.s(**參數).apply_async(queue=...)（和課程的 producer 一樣），
等全部做完才回傳；重試、間隔改在 worker 上做（common/worker.py 的 Job）。
"""
import functools
import time

import anthropic
import celery.exceptions
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
    queue：USE_CELERY=1 時送到哪個佇列
    （crawl_twse、crawl_mops、crawl_finmind、crawl_news、transform、analyze、ai，見 common/worker.py），
    一定要給；沒開就在這裡依序執行，不用給。
    """
    if USE_CELERY:   # 重試、間隔都在 worker 那邊做（Job）
        return collect_results(send_jobs(jobs, queue))
    failed = []
    for func, kwargs in jobs:
        report(job_name(func, kwargs), functools.partial(run_with_retry, func, kwargs, retries), failed)
        if sleep:
            time.sleep(sleep)
    return failed


def report(name: str, get_result, failed: list[str]) -> None:
    """拿一個任務的結果印出來（任務回傳一行結果，例如 raw_notice/20261113）；出錯就印出原因、記進 failed。
    get_result 丟出 Celery 的 TimeoutError（還沒做完）時照樣丟出去，讓呼叫的人決定要不要晚點再拿。"""
    try:
        print(name, "→", get_result(), flush=True)
    except celery.exceptions.TimeoutError:
        raise
    except Exception as e:
        print(name, "✗", e, flush=True)
        failed.append(name)


def send_jobs(jobs: list[tuple], queue: str | None) -> list[tuple]:
    """整批送進佇列，回傳 [(任務名稱, AsyncResult), ...]。
    送法和課程一樣：task.s(**參數) 建立簽章（signature），apply_async(queue=...) 指定佇列送出。"""
    if queue is None:
        raise ValueError("USE_CELERY=1 要指定 queue（見 common/worker.py）")   # 程式寫錯，直接停（不會送到沒有 worker 在聽的佇列）
    sent = [(job_name(func, kwargs), func.s(**kwargs).apply_async(queue=queue)) for func, kwargs in jobs]
    print(f"送出 {len(sent)} 個任務到佇列 {queue}", flush=True)
    return sent


def collect_results(sent: list[tuple]) -> list[str]:
    """照送出的順序等每個結果、印出來，回傳失敗的任務名稱。"""
    failed = []
    for name, result in sent:
        report(name, result.get, failed)
    return failed


CHAIN_WAIT_SECONDS = 1.0   # run_chains 每次等一條鏈的結果最多幾秒，沒好就換看下一條


def run_chains(chains: list[list[tuple]]) -> list[str]:
    """好幾條任務鏈同時進行，回傳全部失敗的任務名稱。

    每條鏈是好幾批 [(產生任務清單的函式, sleep, queue), ...]：同一條鏈裡上一批做完，才產生、送出下一批
    （例如重大訊息全文要先有清單才知道要抓哪幾則）；鏈和鏈之間不互等，各自的佇列同時在做。
    任務清單是做到那一批才產生的，所以傳函式，不傳清單。
    USE_CELERY=1：每條鏈的第一批同時送出，之後輪流看每條鏈：用 result.get(timeout=CHAIN_WAIT_SECONDS) 等結果，
    沒好就換看下一條，一條鏈這一批都好了就送它的下一批，慢的鏈（例如 FinMind 6 秒一個）不會卡住快的。
    不用 AsyncResult.ready() 輪詢：rpc 結果後端在任務多、有失敗的時候，輪詢會一直看不到已經做完的結果
    （2026-11-23 每天的資料線卡住）；get() 走的是收結果的 consumer，才可靠。
    產生任務清單出錯（例如資料庫連不上）：記成一個失敗、這條鏈停下來，其他鏈照做完，最後一起列出失敗（本機也一樣）。
    沒開：在本機一條鏈一條鏈依序執行（本機一次只能做一件事）。
    """
    failed = []

    def build_jobs(i: int, build, queue: str) -> list[tuple] | None:
        """產生第 i 條鏈這一批的任務清單；出錯記成一個失敗，回傳 None（這條鏈停下來）。"""
        try:
            return build()
        except Exception as e:
            name = f"第 {i + 1} 條鏈產生 {queue} 的任務清單"
            print(name, "✗", error_text(e), flush=True)
            failed.append(name)
            return None

    if not USE_CELERY:
        for i, chain in enumerate(chains):
            for build, sleep, queue in chain:
                jobs = build_jobs(i, build, queue)
                if jobs is None:
                    break
                failed += run_jobs(jobs, sleep=sleep, queue=queue)
        return failed

    steps = [iter(chain) for chain in chains]
    running = {}   # 第幾條鏈 → 這一批還沒拿到結果的 [(任務名稱, AsyncResult), ...]

    def send_next(i: int) -> None:
        """第 i 條鏈送出下一批；空的批次（沒有要做的）跳過；整條做完、或產生清單出錯，就不再放進 running。"""
        for build, _sleep, queue in steps[i]:   # 間隔在 worker 上做，這裡用不到 sleep
            if queue is None:
                raise ValueError("USE_CELERY=1 要指定 queue（見 common/worker.py）")   # 程式寫錯：空的批次也要抓到
            jobs = build_jobs(i, build, queue)
            if jobs is None:
                return
            if jobs:
                running[i] = send_jobs(jobs, queue)
                return

    for i in range(len(chains)):
        send_next(i)
    while running:
        for i in list(running):
            sent = running[i]
            while sent:                                   # 照送出的順序拿結果，拿不到（還沒好）就換下一條鏈
                name, result = sent[0]
                try:
                    report(name, functools.partial(result.get, timeout=CHAIN_WAIT_SECONDS), failed)
                except celery.exceptions.TimeoutError:
                    break
                sent.pop(0)
            if not sent:
                del running[i]
                send_next(i)
    return failed


def finish(failed: list[str]) -> None:
    """一輪做完：印出失敗清單；有失敗就結束代碼 1（main.py、排程看得到）。"""
    print("\n失敗：", failed or "無")
    if failed:
        raise SystemExit(1)
