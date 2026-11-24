"""資料線分兩條，各自排程：

股價線（週一～五 18:00～22:00 每小時，跑完放記號，接著跑一次新聞線）：
    python main.py                              # 今天（daily）
    crawler 證交所 → transform 證交所的（注意股、處置股、收盤行情、交易日…）→ analyze（還差幾次）
新聞線（每天 08:00、11:00、14:00、17:00、20:00、23:00，加上股價線跑完那一次）：
    python main.py news                         # 今天
    讀 stage_watched_stock 決定查哪些公司 → crawler 觀測站、新聞 → transform 重大訊息、新聞 → ai
回補一段期間（兩條線都跑，先股價線、再新聞線）：
    python main.py backfill --start 20260701        # 可加 --end 20261031
    python main.py backfill --start 20260701 --refetch  # crawler 已經抓完整的也重抓

為什麼分兩條：證交所一天公布一次（17:30～17:45），新聞、公告隨時都有，FinMind 又限制速度（6 秒一個）。
分開後還差幾次很快就算好，不用等新聞；新聞抓失敗也不影響股價線。新聞線在股價線之後讀 stage_watched_stock，
當天被列注意的股票已經在 stage 裡，所以「要關注哪些股票」只寫在 view 一個地方。
卡片 AI 摘要要用當天的注意股、處置股、收盤行情（股價線）和公告、新聞（新聞線）：新聞線只替這些都整理好的日子寫（見 ai/producer.py）。
所以股價線成功的那一輪（18:00，證交所晚公布就是 19:00、20:00…）放完記號就接著跑一次新聞線：
當天新被列注意的股票馬上查公告、新聞，當天的卡片也馬上寫，不用等下一次排定的新聞線。

期間在這裡算一次（例如 backfill 沒給 --end 就是今天），再明確傳給每一步：跑過午夜，每一步的「今天」也一樣。
- crawler 有任務失敗：照樣往下跑。transform 會跳過沒抓到的 raw；analyze 會檢查需要的 stage，缺了那天就不算。
- transform 有任務失敗（程式錯誤）：不跑後面的（股價線不跑 analyze、新聞線不跑 ai）。
- ai 失敗只影響 AI 的結果，網站照常（規則引擎不讀 AI 的結果）。
有任何一步失敗，最後結束代碼是 1。每一步也可以單獨執行，見 docs/flow.md。

股價線：證交所萬一還沒公布，那次會失敗、下一個小時再試；全部成功就在 data/done/ 放一個當天的記號，之後同一天的排程直接結束。
放記號的那一輪（一天只有一次）接著跑新聞線；新聞線正在跑（例如 17:00 那次還沒跑完）就等它跑完再跑。
建表、更新 VIEW 會鎖住很多表：兩條線都只在 schema/*.sql 改過時才做（資料庫記著上次的雜湊值，見 refresh_schema），回補每次都做。
新聞線沒有記號：每次都跑，已經抓完整的、30 分鐘內剛抓過的跳過（見 crawler/producer.py 的 REFETCH_GAP）。

同一條線同一時間只跑一輪：開始前鎖住檔案（作業系統的檔案鎖，不是資料庫）：股價線 data/pipeline.lock、
新聞線 data/news.lock。兩條線可以同時跑（寫不同的表）；回補、建表兩個一起鎖（pipeline_lock 給兩個檔案）。上一輪還鎖著就排隊等，
最多等 PIPELINE_LOCK_WAIT_SECONDS 秒。之後換到多台機器，改用 Airflow 的 max_active_runs=1。
"""
import contextlib
import os
import pathlib
import subprocess
import sys
import time

from common import cli, repository
from common.config import PIPELINE_LOCK_WAIT_SECONDS, PROJECT_ROOT

LOCK_FILE = PROJECT_ROOT / "data" / "pipeline.lock"   # 股價線
NEWS_LOCK_FILE = PROJECT_ROOT / "data" / "news.lock"   # 新聞線
DONE_DIR = PROJECT_ROOT / "data" / "done"   # 股價線每天跑完的記號，檔名是日期（YYYYMMDD）

if os.name == "nt":   # Windows
    import msvcrt

    def try_lock(f) -> bool:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)   # 鎖住檔案的第 1 個 byte；別人鎖著就馬上失敗
            return True
        except OSError:
            return False

    def unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:                 # Linux、macOS（之後放進 Docker）
    import fcntl

    def try_lock(f) -> bool:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def pipeline_lock(*paths: pathlib.Path, wait_seconds: float, poll_seconds: float = 1.0):
    """鎖住檔案（一個或好幾個），同一時間只讓一輪資料線執行；別人鎖著就每 poll_seconds 秒再試，最多等 wait_seconds 秒。

    好幾個（建表、回補：兩條線的鎖一起拿）要全部拿到才算；只拿到一部分就放掉、等一下再試，不會拿著一個等另一個：
    兩個程序不會各拿一個互等，也不會拿著股價線的鎖等很久、拖住股價線。
    鎖是作業系統管的：跑完會放掉；程式當掉、被關掉，作業系統也會自動放掉，不會卡死。只管得到同一台電腦。
    """
    names = "、".join(path.name for path in paths)
    with contextlib.ExitStack() as stack:   # 檔案一起開，離開時一起關
        files = []
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            files.append(stack.enter_context(open(path, "a+")))
            files[-1].seek(0)
        deadline = time.monotonic() + wait_seconds
        waited = False
        while True:
            got = []
            for f in files:
                if not try_lock(f):
                    break
                got.append(f)
            if len(got) == len(files):
                break
            for f in got:
                unlock(f)
            if not waited:
                print(f"上一輪還在跑（{names} 鎖著），最多等 {wait_seconds / 60:g} 分鐘", flush=True)
                waited = True
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等了 {wait_seconds / 60:g} 分鐘，上一輪還沒跑完（{names}）")
            time.sleep(poll_seconds)
        try:
            yield
        finally:
            for f in files:
                unlock(f)


def run(module: str, args: list[str]) -> int:
    """執行一步（python -m <module> <args>），回傳結束代碼（0＝成功）。
    按 Ctrl+C 中斷時 subprocess.run 會先關掉子程序，不會留著子程序繼續寫資料表。
    工作排程器「結束」工作是直接關掉 main.py，子程序不會跟著關：要等它跑完，或自己關掉 python 程序再重跑。"""
    print(f"===== {module} {' '.join(args)}", flush=True)
    return subprocess.run([sys.executable, "-m", module, *args]).returncode


def run_line(args: list[str], part: str, last: str) -> bool:
    """一條線：crawler（--part）→ transform（--part）→ last（analyze 或 ai），回傳有沒有失敗。
    crawler 有任務失敗照樣往下跑；transform 有任務失敗就不跑 last（stage 不完整，算出來會錯）。"""
    crawler_code = run("crawler.producer", [*args, "--part", part])
    if run("transform.producer", [*args, "--part", part]):
        print(f"\ntransform 有任務失敗，不跑 {last.split('.')[0]}")
        return True
    return bool(run(last, args) or crawler_code)


def run_stock_line(args: list[str]) -> bool:
    """股價線：crawler 證交所 → transform 證交所的 → analyze。回傳有沒有失敗。"""
    return run_line(args, "twse", "analyze.producer")


def run_news_line(args: list[str]) -> bool:
    """新聞線：crawler 觀測站、新聞（查哪些公司讀 stage_watched_stock）→ transform 重大訊息、新聞 → ai。回傳有沒有失敗。
    卡片 AI 摘要只寫公告、新聞清單已經抓到的股票（見 repository.list_unsummarized_cards）：這一輪跑到一半股價線才跑完、
    或抓失敗的，那檔的卡片下一輪抓到了再寫。"""
    return run_line(args, "news", "ai.producer")


def schema_changed() -> bool:
    """schema/*.sql 和資料庫上次建表、更新 VIEW 時用的不一樣（或還沒做過、資料庫是還原的舊備份、全新的資料庫）。"""
    return repository.applied_schema_hash() != repository.schema_hash()


def refresh_schema() -> None:
    """schema/*.sql 改過才建表、更新 VIEW。整個交易會鎖住很多表，另一條線同時在寫就可能互相卡住（deadlock），
    所以要做時兩條線的鎖一起拿，做完放掉，再拿自己那條線的鎖照常跑。
    一定要建好才往下跑（不然網站讀新的欄位會出錯）：另一條線正在跑就等它跑完，等太久這一輪就失敗、下一次再試。
    只有改過 schema 的那次會等；沒改就只比雜湊值，不鎖。全新的資料庫沒有雜湊值，也會在這裡建表。"""
    if not schema_changed():
        return
    with pipeline_lock(LOCK_FILE, NEWS_LOCK_FILE, wait_seconds=PIPELINE_LOCK_WAIT_SECONDS):
        if schema_changed():   # 等鎖的時候另一條線可能已經做好了
            repository.create_tables()


def stock_line_daily(step_args: list[str], day: str) -> None:
    """股價線（每天）：跑完放記號，同一天之後的排程看到記號就直接結束。有失敗不放記號，下一個小時再跑。
    放記號的那一輪接著跑一次新聞線（不管是 18:00 還是證交所晚公布的 19:00、20:00）：當天新被列注意的股票馬上查公告、新聞，
    當天的卡片 AI 摘要也馬上寫。先放掉股價線的鎖再跑：新聞線可能跑很久，不擋下一個小時的股價線（它看到記號就結束）。"""
    done_mark = DONE_DIR / day
    if done_mark.exists():           # 今天跑完了：直接結束（schema 改過的話，新聞線開始時會建表、更新 VIEW）
        print(f"{day} 股價線已經跑完（{done_mark}），跳過")
        return
    refresh_schema()                 # schema/*.sql 改過才建表、更新 VIEW
    with pipeline_lock(LOCK_FILE, wait_seconds=PIPELINE_LOCK_WAIT_SECONDS):
        if done_mark.exists():
            print(f"{day} 股價線已經跑完（{done_mark}），跳過")
            return
        if run_stock_line(step_args):
            raise SystemExit(1)
        done_mark.parent.mkdir(parents=True, exist_ok=True)
        done_mark.touch()
    print(f"\n{day} 股價線跑完，接著跑一次新聞線（當天新被列注意的股票、卡片 AI 摘要）", flush=True)
    try:
        news_line(step_args)
    except (SystemExit, TimeoutError):
        print(f"\n{day} 股價線成功（已經放記號，不會重跑）；接著跑的新聞線有失敗，下一次排定的新聞線會補", flush=True)
        raise


def news_line(step_args: list[str]) -> None:
    """新聞線：沒有記號，每次都跑（已經抓完整的、剛抓過的跳過）。
    schema/*.sql 改過也建表、更新 VIEW（股價線放過記號就不檢查了：週末、晚上更新程式，網站才不會等到下一個交易日）。"""
    refresh_schema()
    with pipeline_lock(NEWS_LOCK_FILE, wait_seconds=PIPELINE_LOCK_WAIT_SECONDS):
        if run_news_line(step_args):
            raise SystemExit(1)


def backfill(step_args: list[str]) -> None:
    """回補：兩條線都跑（先股價線、再新聞線，新聞線要讀股價線整理好的注意股），兩個鎖都拿，不放記號。
    股價線有失敗照樣跑新聞線（已經整理好的日子照樣可以查新聞），最後結束代碼 1。"""
    with pipeline_lock(LOCK_FILE, NEWS_LOCK_FILE, wait_seconds=PIPELINE_LOCK_WAIT_SECONDS):
        repository.create_tables()   # 兩條線都鎖著，沒有別人在寫：每次都建表、更新 VIEW
        stock_failed = run_stock_line(step_args)
        news_failed = run_news_line(step_args)
        if stock_failed or news_failed:
            raise SystemExit(1)


def main() -> None:
    args = cli.parse_args(news_mode=True)   # daily、news：今天；backfill：--start～--end（沒給 --end 就是今天）
    step_args = ["backfill", "--start", args.start, "--end", args.end] + (["--refetch"] if args.refetch else [])
    try:
        if args.mode == "backfill":
            backfill(step_args)
        elif args.mode == "news":
            news_line(step_args)
        else:
            stock_line_daily(step_args, args.end)
    except TimeoutError as e:
        print(e)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
