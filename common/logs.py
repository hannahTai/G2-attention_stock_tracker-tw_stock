"""文字 log：每天一個檔，只留最近幾天。

    <資料夾>/<名稱>-YYYY-MM-DD.log   例如 data/logs/twse-2026-11-21.log

Celery worker 用（common/worker.py 在 worker 啟動時接上）；每天的資料線 log 由 scripts/run_daily.bat 用同樣的檔名、同樣的保留天數。
永久的紀錄在任務歷史（tw_stock.job_run）；文字 log 只是看細節、即時看用的，所以只留 LOG_KEEP_DAYS 天。
"""
import datetime
import logging
import pathlib

from common import date_utils


def log_path(folder: pathlib.Path, prefix: str, day: datetime.date) -> pathlib.Path:
    """那天的 log 檔。

    >>> log_path(pathlib.Path("logs"), "crawl_twse", datetime.date(2026, 11, 21)).as_posix()
    'logs/crawl_twse-2026-11-21.log'
    """
    return folder / f"{prefix}-{day:%Y-%m-%d}.log"


def remove_old_logs(folder: pathlib.Path, prefix: str, keep_days: int, today: datetime.date) -> None:
    """刪掉超過 keep_days 天的 <prefix>-YYYY-MM-DD.log（今天算第 1 天）；檔名看不懂的不動。"""
    for path in folder.glob(f"{prefix}-*.log"):
        try:
            day = datetime.date.fromisoformat(path.stem[len(prefix) + 1:])
        except ValueError:
            continue
        if (today - day).days >= keep_days:
            path.unlink(missing_ok=True)


class DailyFileHandler(logging.Handler):
    """logging 的 handler：寫到當天的檔；換日時開新的檔，順便刪掉太舊的檔。

    不改檔名、只開新檔：Celery 的好幾個子行程同時寫同一個檔也不會互相搶（改檔名的輪替方式會）。
    """

    def __init__(self, folder: pathlib.Path, prefix: str, keep_days: int):
        super().__init__()
        self.folder = pathlib.Path(folder)
        self.prefix = prefix
        self.keep_days = keep_days
        self.day = None
        self.stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = date_utils.today()
            if today != self.day:
                self.open_day(today)
            self.stream.write(self.format(record) + "\n")
            self.stream.flush()
        except Exception:
            self.handleError(record)

    def open_day(self, today: datetime.date) -> None:
        if self.stream:
            self.stream.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stream = open(log_path(self.folder, self.prefix, today), "a", encoding="utf-8")   # noqa: SIM115  log 要一直開著寫，換日或 close() 才關
        self.day = today
        remove_old_logs(self.folder, self.prefix, self.keep_days, today)

    def close(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        super().close()
