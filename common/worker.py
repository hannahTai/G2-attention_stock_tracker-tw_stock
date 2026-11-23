"""Celery：app（連 RabbitMQ）和所有任務共用的行為（Job）。和課程的 crawler/worker.py 同一個寫法。

    celery -A common.worker worker -Q crawl_twse --concurrency 1   # 抓證交所：一次只做一個，每個任務後等一下
    celery -A common.worker worker -Q crawl_mops --concurrency 1   # 抓觀測站：同上
    celery -A common.worker worker -Q crawl_finmind --concurrency 1   # 抓 FinMind 新聞清單：同上
    celery -A common.worker worker -Q crawl_news --concurrency 4   # 打開新聞網頁（存 <head>）：分散在十幾個網站，同時做 4 個
    celery -A common.worker worker -Q transform --concurrency 4    # 整理（transform）：可以同時做好幾個
    celery -A common.worker worker -Q analyze --concurrency 2      # 計算（analyze）：同上
    celery -A common.worker worker -Q ai --concurrency 2           # AI（ai/）：呼叫 Claude API（公告分類、新聞摘要）、同一件事
    celery -A common.worker worker -Q embed --concurrency 1        # 用本機 embedding 模型的 AI 任務（新聞 RAG、卡片 AI 摘要）

docker-compose.yml 已經把這八個 worker（服務 celery-crawl-twse、celery-crawl-mops、celery-crawl-finmind、celery-crawl-news、celery-transform、celery-analyze、celery-ai、celery-embed）
和 RabbitMQ、Flower 都寫好，平常用 docker compose 啟動就好。

任務：crawler/task.py、transform/task.py、analyze/task.py、ai/task.py 裡每個函式都用 @app.task 註冊成 Celery 任務
（base 是 Job；打證交所的用 TwseJob、觀測站的用 MopsJob、FinMind 的用 FinmindJob、新聞網站的用 NewsDetailJob），
名稱就是「模組.函式」，例如 crawler.task.crawl_notice（Flower、RabbitMQ 上看到的就是這個名字）。
producer 送任務的寫法和課程一樣：task.crawl_notice.s(date_yyyymmdd="20261113").apply_async(queue="crawl_twse")
（common/runner.py 的 run_jobs 幫四個 producer 做這件事；預設 USE_CELERY=1，設成 0 才改成在本機依序執行）。

佇列（queue）依「會不會被擋」分：
- crawl_twse、crawl_mops、crawl_finmind：打外部網站，太快會被擋（限制是對網站、對 IP，多開幾個 worker 一起打只會更快被擋）。
  各一個 worker、concurrency 1，一次只做一個，做完等一下（TwseJob、MopsJob、FinmindJob）。
- crawl_news：打開新聞網頁，連結分散在十幾個網站（限制是各網站分開算），同時做 4 個；任務照網站輪流排，
  同時打的大多是不同網站，每個做完照樣等一下（NewsDetailJob）。
- transform：raw → stage，只讀寫自己的資料庫，同時做 4 個；stage 寫入本來就設計成多個 worker 同時寫也不會錯（見 docs/flow.md）。
- analyze：stage → app，同時做 2 個（每天讀往前 30 個營業日，天和天之間互不依賴）。
- ai：呼叫 Claude API，同時做 2 個（API 也有每分鐘的次數限制；429 會等一下重做）。同一件事（group_news_stories，不呼叫 API）也在這裡。
- embed：要用本機 embedding 模型的任務（新聞的標題＋新聞摘要轉向量；卡片 AI 摘要要把查詢轉向量，也放這裡），一次做一個。
  一次做一個也讓卡片 AI 摘要照日期先後寫（沿用前一天的摘要要等前一天寫好），不要調高。
  只有這個 worker 載入模型（約 2.3 GB 記憶體），其他 worker 不會多佔一份。

結果：用 rpc://（結果經 RabbitMQ 送回給送任務的 producer），producer 等這一批都做完才送下一批。
紀錄：每個任務跑完寫一列任務歷史（tw_stock.job_run）；文字 log 另外寫到 LOG_DIR/<worker 名稱>-YYYY-MM-DD.log
（crawl_twse、crawl_mops、crawl_finmind、crawl_news、transform、analyze、ai、embed，取自 --hostname 的 @ 前面，例如 crawl_twse@tw_stock；每天一個檔，留 LOG_KEEP_DAYS 天，見 common/logs.py），畫面上（docker compose logs）照樣看得到。
"""
import logging
import time
import urllib.parse

import celery
import celery.signals

from common import date_utils, logs, runner
from common.config import (
    LOG_DIR, LOG_KEEP_DAYS, MAX_RETRIES, RABBITMQ_HOST, RABBITMQ_PASSWORD, RABBITMQ_PORT, RABBITMQ_USER,
    RETRY_DELAY_SECONDS, SLEEP_SECONDS, SLEEP_SECONDS_FINMIND, SLEEP_SECONDS_MOPS, SLEEP_SECONDS_NEWS_DETAIL,
)

app = celery.Celery(
    "tw_stock",
    include=["crawler.task", "transform.task", "analyze.task", "ai.task"],   # worker 啟動時載入這些模組，任務才會註冊
    # 密碼裡的 @ : / 會把網址切錯（例如 P@ssw0rd），先編碼
    broker=f"pyamqp://{RABBITMQ_USER}:{urllib.parse.quote(RABBITMQ_PASSWORD, safe='')}@{RABBITMQ_HOST}:{RABBITMQ_PORT}//",
    backend="rpc://",
)
app.conf.update(
    task_serializer="json",            # 參數只有字串、數字，用 JSON 就好（不用 pickle，比較安全）
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Taipei",
    task_default_queue="transform",     # 沒指定佇列的任務（例如手動送的）送到 transform；producer 都會指定
    worker_prefetch_multiplier=1,      # 一次只先拿一個任務：慢的任務不會卡住後面的
    task_acks_late=True,               # 做完才確認；worker 做到一半掛掉，任務會回到佇列給別人做
    broker_connection_retry_on_startup=True,
    worker_send_task_events=True,      # worker 送出事件（上線、開始、完成、失敗）：Flower 靠這個顯示 worker 和每個任務
    task_send_sent_event=True,         # 送進佇列時也送事件：Flower 看得到還在排隊的任務
)


@celery.signals.celeryd_after_setup.connect
def log_to_daily_file(sender, instance, **kwargs):
    """worker 啟動時：log 多寫一份到每天的檔，檔名用 worker 名稱（sender＝--hostname，例如 crawl_twse@tw_stock → crawl_twse）。"""
    handler = logs.DailyFileHandler(LOG_DIR, sender.split("@")[0], LOG_KEEP_DAYS)
    handler.setFormatter(logging.Formatter(app.conf.worker_log_format))
    logging.getLogger().addHandler(handler)


class Job(celery.Task):
    """所有任務共用的行為（@app.task(base=Job)）。

    直接呼叫（本機依序執行、測試）：就是原本的函式，重試由 runner.run_with_retry 負責。
    在 worker 上執行：
    - 暫時性的失敗（runner.is_retryable）用 Celery 的 retry，RETRY_DELAY_SECONDS 秒後放回佇列重做，最多 MAX_RETRIES 次
      （Flower 上看得到 RETRY）
    - 其他失敗換成 RuntimeError（訊息保留原本的類型）：自訂的錯誤（例如 FetchError）送回 producer 時可能還原不了
    - 做完等 sleep_after 秒再拿下一個：打外部網站的任務用 TwseJob、MopsJob、FinmindJob、NewsDetailJob（間隔在那裡設一次），
      其他任務用 Job（不等）
    - 每次嘗試都寫一列任務歷史（runner.record：success、retry 或 failure，記下是哪個 worker）
    """
    sleep_after = 0.0
    max_retries = MAX_RETRIES

    def __call__(self, *args, **kwargs):
        if self.request.called_directly:
            return self.run(*args, **kwargs)
        started = date_utils.now()
        where = {"attempt": self.request.retries + 1, "mode": "celery", "worker": self.request.hostname}
        try:
            result = self.run(*args, **kwargs)
        except Exception as e:
            retry = runner.is_retryable(e) and self.request.retries < self.max_retries
            runner.record(self, kwargs, "retry" if retry else "failure", started, error=runner.error_text(e), **where)
            if retry:
                raise self.retry(exc=e, countdown=RETRY_DELAY_SECONDS)
            raise RuntimeError(runner.error_text(e)) from None
        else:
            runner.record(self, kwargs, "success", started, result=result, **where)
            return result
        finally:
            if self.sleep_after:
                time.sleep(self.sleep_after)


class TwseJob(Job):
    """打證交所的任務（crawler/task.py 的 crawl_notice 等）：每個做完等 SLEEP_SECONDS 秒，太快會被擋。"""
    sleep_after = SLEEP_SECONDS


class MopsJob(Job):
    """打公開資訊觀測站的任務（crawl_announcement、crawl_announcement_detail）：每個做完等 SLEEP_SECONDS_MOPS 秒。"""
    sleep_after = SLEEP_SECONDS_MOPS


class FinmindJob(Job):
    """打 FinMind 的任務（crawl_stock_news）：每個做完等 SLEEP_SECONDS_FINMIND 秒（FinMind 限制每小時的次數）。"""
    sleep_after = SLEEP_SECONDS_FINMIND


class NewsDetailJob(Job):
    """打新聞網站的任務（crawl_news_detail）：每個做完等 SLEEP_SECONDS_NEWS_DETAIL 秒。"""
    sleep_after = SLEEP_SECONDS_NEWS_DETAIL
