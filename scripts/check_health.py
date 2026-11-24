"""每天檢查一次資料線、網站、備份有沒有問題；有問題就跳 Windows 通知，結束代碼 1（工作排程器的「上次執行結果」也看得到）。

    uv run python scripts/check_health.py            # 檢查，結果印出來
    uv run python scripts/check_health.py --notify   # 有問題就跳 Windows 通知（排程：scripts/run_check.bat，每天 22:30）

檢查四件事：
1. 股價線：今天是交易日（週一～五、不是休市日），資料線也沒在跑了（main.py 的檔案鎖沒鎖著），就要有今天的記號 data/done/YYYYMMDD；
   上一個交易日也要有（那天檢查時還在跑、之後才失敗的，隔天補報）
2. 新聞線：最近一次（data/logs/news-*.log 最後一個 exit code）成功，而且照排程應該跑完的那一次有跑（沒跑就是排程停了）
3. 網站：資料 API 的 /health 回 ok（資料庫連得上）、網頁打得開
4. 備份：data/backup/ 最新一份在 3.5 天內（週一～五 23:30 備份，週末不備份，週一晚上看到的是週五的）
只讀檔案、資料庫、打本機的網址，不改任何東西。
"""
import argparse
import base64
import datetime
import os
import pathlib
import re
import subprocess
import xml.sax.saxutils

import requests

from common import date_utils, repository
from common.config import API_URL, BACKUP_DIR, DB_NAME, PROJECT_ROOT
from crawler import client as crawler_client

DONE_DIR = PROJECT_ROOT / "data" / "done"          # 和 main.py 的 DONE_DIR 一樣
# 兩條線的檔案鎖（和 main.py 的 LOCK_FILE、NEWS_LOCK_FILE 一樣）：鎖著＝那條線正在跑（或正在等另一條線）
LINE_LOCKS = (PROJECT_ROOT / "data" / "pipeline.lock", PROJECT_ROOT / "data" / "news.lock")
LOG_DIR = PROJECT_ROOT / "data" / "logs"           # run_news.bat 的 log（不是 LOG_DIR 設定：那是 Celery worker 的）
WEB_URL = os.environ.get("WEB_URL", "http://127.0.0.1:8502")

STOCK_LINE_DONE_BY = datetime.time(22, 30)          # 股價線最後一次排程 22:00，這時候還沒有記號就是今天都沒成功
NEWS_LINE_HOURS = (8, 11, 14, 17, 20, 23)           # 新聞線的排程（和工作排程器一樣，見 docs/flow.md）
NEWS_LINE_TAKES = datetime.timedelta(minutes=45)    # 一次大約 25 分鐘，多留一點
BACKUP_MAX_AGE = datetime.timedelta(days=3, hours=12)
EXIT_CODE_LINE = re.compile(r"^exit code (-?\d+)", re.MULTILINE)


def file_time(path: pathlib.Path) -> datetime.datetime:
    """檔案最後修改的時間（台灣時間，不帶時區；和 main() 的 now 一樣）。"""
    return datetime.datetime.fromtimestamp(path.stat().st_mtime, date_utils.TAIPEI).replace(tzinfo=None)


def is_trading_day(day: datetime.date) -> bool:
    """這天應該有交易：和 crawler 同一個規則（crawler.client.should_trade；開休市日期上寫休市的沒有、週末的補行交易有）。"""
    known = repository.query_rows("SELECT 1 FROM tw_stock.stage_holiday WHERE EXTRACT(YEAR FROM holiday_date) = :y LIMIT 1",
                                  {"y": day.year})
    if not known:   # 那一年的開休市日期還沒抓到：不知道（例如春節），當成不是交易日，不要誤報
        return False
    rows = repository.query_rows("SELECT holiday_name FROM tw_stock.stage_holiday WHERE holiday_date = :d", {"d": day})
    ymd = date_utils.to_yyyymmdd(day)
    closed = {ymd for row in rows if not date_utils.holiday_is_trading(row.holiday_name)}
    open_ = {ymd for row in rows if date_utils.holiday_is_trading(row.holiday_name)}
    return crawler_client.should_trade(ymd, closed, open_)


def lock_held(path: pathlib.Path) -> bool:
    """main.py 的檔案鎖現在有人鎖著嗎（試著鎖一下，鎖得到就馬上放掉）。程式被關掉、電腦重開，作業系統會自動放掉鎖，
    所以鎖著＝那條線真的還在跑（就算 log 很久沒寫，例如在等鎖、等 Celery 任務）。"""
    if not path.exists():
        return False
    with open(path, "a+") as f:
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            return True
    return False


def stock_line_problem(now: datetime.datetime, trading: bool, done_dir: pathlib.Path = DONE_DIR,
                       locks: tuple[pathlib.Path, ...] = LINE_LOCKS) -> str | None:
    """今天是交易日、過了 STOCK_LINE_DONE_BY，卻沒有今天的記號：股價線今天都沒成功。
    22:00 那次還在跑（兩條線的鎖有一個鎖著：跑得慢、在等鎖）不算，明天的檢查會看到結果；
    沒有鎖著又沒有記號（失敗、被關掉、電腦睡眠）才算。"""
    if not trading or now.weekday() >= 5 or now.time() < STOCK_LINE_DONE_BY:   # 股價線只排週一～五（週六補行交易不跑）
        return None
    if (done_dir / f"{now:%Y%m%d}").exists():
        return None
    if any(lock_held(path) for path in locks):
        print("（資料線還在跑，還沒有今天的記號；明天的檢查會看到結果）")
        return None
    return f"股價線今天（{now:%m/%d}）沒有成功（沒有 data/done/{now:%Y%m%d}），看 data/logs/daily-{now:%Y-%m-%d}.log"


def last_news_run_due(now: datetime.datetime) -> datetime.datetime:
    """照排程，現在最晚應該已經跑完的那一次新聞線是幾點開始的（今天或昨天的 08/11/14/17/20/23 點）。

    >>> last_news_run_due(datetime.datetime(2026, 11, 24, 22, 30)), last_news_run_due(datetime.datetime(2026, 11, 25, 7, 30))
    (datetime.datetime(2026, 11, 24, 20, 0), datetime.datetime(2026, 11, 24, 23, 0))
    """
    starts = [datetime.datetime.combine(now.date() - datetime.timedelta(days=back), datetime.time(hour))
              for back in (1, 0) for hour in NEWS_LINE_HOURS]
    return max(start for start in starts if start + NEWS_LINE_TAKES <= now)


def news_line_problem(now: datetime.datetime, log_dir: pathlib.Path = LOG_DIR) -> str | None:
    """最近一次新聞線失敗，或照排程應該跑完的那一次沒跑（排程停了）。看最新的 news-YYYY-MM-DD.log：
    最後寫入的時間要在那一次開始以後、最後一個 exit code 要是 0。檢查不在 22:30 跑（例如電腦睡著、隔天早上補跑）也對。"""
    logs = sorted(log_dir.glob("news-*.log"))   # 檔名有日期，照名稱排就是照日期
    if not logs:
        return "找不到新聞線的 log（data/logs/news-*.log）：新聞線的排程有註冊嗎？"
    latest = logs[-1]
    written = file_time(latest)
    due = last_news_run_due(now)
    if written < due:
        return f"新聞線 {due:%m/%d %H:%M} 那次沒跑（最後一次 {written:%m/%d %H:%M}）：排程停了嗎？"
    codes = EXIT_CODE_LINE.findall(latest.read_text(encoding="utf-8", errors="replace"))
    if codes and codes[-1] != "0":
        return f"最近一次新聞線失敗（exit code {codes[-1]}），看 data/logs/{latest.name}"
    return None


def backup_problem(now: datetime.datetime, backup_dir: pathlib.Path = BACKUP_DIR) -> str | None:
    """最新一份備份太舊（或一份都沒有）。"""
    latest = max(backup_dir.glob(f"{DB_NAME}_*.sql.gz"), key=lambda path: path.stat().st_mtime, default=None)   # 和 backup_db.py 同樣的檔名
    if latest is None:
        return f"找不到備份（{backup_dir}）"
    written = file_time(latest)
    if now - written > BACKUP_MAX_AGE:
        return f"最新的備份是 {written:%m/%d %H:%M}，太久沒備份了，看 data/backup.log"
    return None


def site_problems() -> list[str]:
    """資料 API 的 /health 要回 ok（資料庫連得上）、網頁要打得開。"""
    problems = []
    try:
        health = requests.get(f"{API_URL}/health", timeout=10).json()
        if health.get("status") != "ok":
            problems.append(f"資料 API 連不上資料庫（{API_URL}/health 回 {health}）")
    except (requests.RequestException, ValueError) as e:
        problems.append(f"資料 API 沒回應（{API_URL}）：{type(e).__name__}")
    try:
        status = requests.get(WEB_URL, timeout=10).status_code
        if status != 200:
            problems.append(f"網頁打不開（{WEB_URL} 回 HTTP {status}）")
    except requests.RequestException as e:
        problems.append(f"網頁沒回應（{WEB_URL}）：{type(e).__name__}")
    return problems


def previous_stock_day(today: datetime.date, is_trading=None) -> datetime.date | None:
    """今天以前最近一個股價線該跑的日子（交易日、週一～五）；往前 14 天都沒有為 None。"""
    is_trading = is_trading or is_trading_day
    for back in range(1, 15):
        day = today - datetime.timedelta(days=back)
        if day.weekday() < 5 and is_trading(day):
            return day
    return None


def has_results(day: datetime.date) -> bool:
    """規則引擎算出這天的還差幾次了嗎（app_progress 有這天）：回補（不放記號）補好的也算。"""
    return bool(repository.query_rows("SELECT 1 FROM tw_stock.app_progress WHERE trade_date = :d LIMIT 1", {"d": day}))


def previous_day_problem(previous: datetime.date | None, done_dir: pathlib.Path = DONE_DIR, done=has_results) -> str | None:
    """上一個交易日沒有記號、規則引擎也沒有那天的結果：那天的股價線沒成功（回補過的不算）。
    那天 22:30 檢查時還在跑（先放過）、之後才失敗的，在這裡補報；那天已經報過、還沒補的，會再提醒一次。"""
    if previous is None or (done_dir / f"{previous:%Y%m%d}").exists() or done(previous):
        return None
    return f"股價線 {previous:%m/%d} 沒有成功（沒有 data/done/{previous:%Y%m%d}），看 data/logs/daily-{previous:%Y-%m-%d}.log"


def calendar_problem(day: datetime.date) -> str | None:
    """那一年的開休市日期還沒抓到（stage_holiday 沒有那一年）：不知道哪天是交易日，股價線的檢查等於關掉了，要說。"""
    known = repository.query_rows("SELECT 1 FROM tw_stock.stage_holiday WHERE EXTRACT(YEAR FROM holiday_date) = :y LIMIT 1",
                                  {"y": day.year})
    return None if known else f"還沒有 {day.year} 年的開休市日期（stage_holiday）：股價線有沒有成功沒辦法檢查"


def find_problems(now: datetime.datetime) -> list[str]:
    """四件事都查，回傳有問題的（沒有就是空的）。某一項查到一半出錯（例如資料庫剛好重啟）也算一個問題，其他照查。
    股價線看今天和上一個交易日：今天的還在跑先放過，下一次檢查會從上一個交易日看到結果。"""
    try:
        trading = is_trading_day(now.date())
        previous = previous_stock_day(now.date())
    except Exception as e:   # 資料庫連不上：當成交易日照樣查記號（寧可多通知）
        trading, previous = now.weekday() < 5, None
        problems = [f"資料庫連不上：{type(e).__name__}"]
    else:
        problems = []
    checks = (("開休市日期", lambda: calendar_problem(now.date())), ("上一個交易日", lambda: previous_day_problem(previous)),
              ("股價線", lambda: stock_line_problem(now, trading)), ("新聞線", lambda: news_line_problem(now)),
              ("備份", lambda: backup_problem(now)), ("網站", lambda: site_problems()))
    for name, check in checks:
        try:
            result = check()
        except Exception as e:   # 一項出錯不要讓整個檢查停掉（停掉就不會跳通知）
            result = f"{name}的檢查出錯：{type(e).__name__}"
        problems += result if isinstance(result, list) else [result]
    return [p for p in problems if p]


def toast_script(title: str, lines: list[str]) -> str:
    """跳 Windows 通知的 PowerShell 指令（用 PowerShell 的身分發，不用另外裝套件）。

    >>> "&lt;b&gt;" in toast_script("t", ["<b>"])
    True
    """
    text = "".join(f"<text>{xml.sax.saxutils.escape(s)}</text>" for s in [title, *lines][:3])   # 通知最多顯示三行
    text = text.replace("'", "''")   # 放進 PowerShell 的 '…' 字串：單引號要寫兩個
    app_id = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    return (
        "$ProgressPreference = 'SilentlyContinue';"   # 不印「Preparing modules」的進度
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null;"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\">{text}</binding></visual></toast>');"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))")


def notify(title: str, lines: list[str]) -> None:
    """跳 Windows 通知。中文用 -EncodedCommand（UTF-16）傳，不會變亂碼；跳不出來（不是 Windows、沒登入）就算了，結果照樣印在 log。"""
    encoded = base64.b64encode(toast_script(title, lines).encode("utf-16-le")).decode("ascii")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                       check=False, timeout=30, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"（通知跳不出來：{type(e).__name__}）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true", help="有問題就跳 Windows 通知")
    args = parser.parse_args()
    now = date_utils.now().replace(tzinfo=None)
    problems = find_problems(now)
    if not problems:
        print(f"{now:%m/%d %H:%M} 檢查完，沒有問題")
        return
    print(f"{now:%m/%d %H:%M} 有 {len(problems)} 個問題：")
    for problem in problems:
        print(f"- {problem}")
    if args.notify:
        notify(f"注意股：有 {len(problems)} 個問題", problems)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
