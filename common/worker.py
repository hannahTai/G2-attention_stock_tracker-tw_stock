"""Celery worker：從 RabbitMQ 拿任務來做（USE_CELERY=1 時才用到；預設在本機依序執行，見 common/runner.py）。

    celery -A common.worker worker -Q twse --concurrency 1     # 證交所：一次只做一個，每個任務後等一下
    celery -A common.worker worker -Q mops --concurrency 1     # 觀測站：同上
    celery -A common.worker worker -Q default --concurrency 4  # transform、analyze：可以同時做好幾個

docker-compose.yml 已經把這三種 worker 和 RabbitMQ、Flower 都寫好，平常用 docker compose 啟動就好。

只有一個 Celery 任務 run_job：收到「哪個任務函式＋參數」，照原本的方式執行（runner.run_with_retry，暫時性的失敗重試）。
所以 crawler/、transform/、analyze/ 的任務函式不用改，也不用知道自己是不是在 worker 上跑。

佇列（queue）依「會不會被擋」分：
- twse、mops：打外部網站，太快會被擋。各一個 worker、concurrency 1，一次只做一個，做完等 sleep 秒。
- default：只讀寫自己的資料庫，可以同時做；stage 寫入本來就設計成多個 worker 同時寫也不會錯（見 docs/flow.md）。

結果：用 rpc://（結果經 RabbitMQ 送回給送任務的 producer），producer 等這一批都做完才送下一批。
"""
import importlib
import time

import celery

from common import runner
from common.config import RABBITMQ_HOST, RABBITMQ_PASSWORD, RABBITMQ_PORT, RABBITMQ_USER

app = celery.Celery(
    "tw_stock",
    broker=f"pyamqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{RABBITMQ_HOST}:{RABBITMQ_PORT}//",
    backend="rpc://",
)
app.conf.update(
    task_serializer="json",            # 參數只有字串、數字，用 JSON 就好（不用 pickle，比較安全）
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Taipei",
    task_default_queue="default",
    worker_prefetch_multiplier=1,      # 一次只先拿一個任務：慢的任務不會卡住後面的
    task_acks_late=True,               # 做完才確認；worker 做到一半掛掉，任務會回到佇列給別人做
    broker_connection_retry_on_startup=True,
)


def func_path(func) -> str:
    """任務函式 → 字串（送進佇列只能放字串），例如 crawler.task.crawl_notice。"""
    return f"{func.__module__}.{func.__name__}"


def load_func(path: str):
    """字串 → 任務函式。只接受專案裡三個 task 模組的函式（佇列裡的字串不能拿來執行任意程式）。"""
    module, name = path.rsplit(".", 1)
    if module not in ("crawler.task", "transform.task", "analyze.task"):
        raise ValueError(f"不認識的任務：{path}")
    return getattr(importlib.import_module(module), name)


@app.task(name="run_job")
def run_job(path: str, kwargs: dict, retries: int, sleep: float) -> str:
    """執行一個任務，回傳任務的一行結果（例如 raw_notice/20261113）；失敗就丟出錯誤，producer 會記成失敗。

    錯誤一律換成 RuntimeError（訊息保留原本的類型）：自訂的錯誤（例如 FetchError）送回 producer 時可能還原不了。
    """
    try:
        return runner.run_with_retry(load_func(path), kwargs, retries)
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}") from None
    finally:
        if sleep:
            time.sleep(sleep)   # 打外部網站的佇列：做完一個等一下再拿下一個
