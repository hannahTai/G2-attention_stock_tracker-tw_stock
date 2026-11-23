"""一次跑完整條資料線：crawler（API → raw）→ transform（raw → stage）→ analyze（stage → app）和 ai（stage → ai）同時。

    python main.py                              # 只處理今天（daily）
    python main.py backfill --start 20260701        # 處理一段期間（可加 --end 20261031）
    python main.py backfill --start 20260701 --refetch  # 同上，crawler 已經抓完整的也重抓

期間在這裡算一次（例如 backfill 沒給 --end 就是今天），再明確傳給四步：跑過午夜，四步的「今天」也一樣。
- crawler 有任務失敗：照樣往下跑。transform 會跳過沒抓到的 raw；analyze 會檢查需要的 stage，缺了那天就不算。
- transform 有任務失敗（程式錯誤）：不跑 analyze、ai。
- analyze 和 ai 都只讀 stage，互不讀對方的結果，同時跑；網站的 VIEW 再把 app 和 ai 合起來。
- ai 失敗只影響 AI 的結果，網站照常（規則引擎不讀 AI 的結果）。
有任何一步失敗，最後結束代碼是 1。每一步也可以單獨執行，見 docs/flow.md。

每天的排程 18:00～22:00 每小時跑一次（證交所 17:30～17:45 公布；萬一還沒公布，那次會失敗、下一個小時再試）：
daily 全部成功就在 data/done/ 放一個當天的記號，同一天之後的排程看到記號就直接結束。backfill 不看也不放記號。

同一時間只跑一輪：開始前鎖住 data/pipeline.lock 這個檔案（作業系統的檔案鎖，不是資料庫）。
上一輪（例如還沒跑完的回補）還鎖著就排隊等，最多等 PIPELINE_LOCK_WAIT_SECONDS 秒。
一輪裡面的任務可以分到好幾個 worker，鎖只管「輪」。之後換到多台機器，改用 Airflow 的 max_active_runs=1。
"""
import contextlib
import os
import pathlib
import subprocess
import sys
import time

from common import cli
from common.config import PIPELINE_LOCK_WAIT_SECONDS, PROJECT_ROOT

LOCK_FILE = PROJECT_ROOT / "data" / "pipeline.lock"
DONE_DIR = PROJECT_ROOT / "data" / "done"   # 每天全部跑完的記號，檔名是日期（YYYYMMDD）

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
def pipeline_lock(path: pathlib.Path, wait_seconds: float, poll_seconds: float = 1.0):
    """鎖住一個檔案，同一時間只讓一輪資料線執行；別人鎖著就每 poll_seconds 秒再試，最多等 wait_seconds 秒。

    鎖是作業系統管的：跑完會放掉；程式當掉、被關掉，作業系統也會自動放掉，不會卡死。只管得到同一台電腦。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as f:
        f.seek(0)
        deadline = time.monotonic() + wait_seconds
        if not try_lock(f):
            print(f"上一輪還在跑（{path.name} 鎖著），最多等 {wait_seconds / 60:g} 分鐘", flush=True)
            while not try_lock(f):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"等了 {wait_seconds / 60:g} 分鐘，上一輪還沒跑完（{path.name}）")
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            unlock(f)


def run(module: str, args: list[str]) -> int:
    """執行一步（python -m <module> <args>），回傳結束代碼（0＝成功）。"""
    print(f"===== {module} {' '.join(args)}", flush=True)
    return subprocess.run([sys.executable, "-m", module, *args]).returncode


def run_parallel(steps: list[tuple[str, list[str]]]) -> list[int]:
    """同時執行好幾步（每步一個 python -m <module> <args>），全部做完才回傳各自的結束代碼（照傳進來的順序）。
    畫面上的輸出會交錯；每個任務另外記在任務歷史（job_run）和 worker 的 log 檔。
    等的時候被中斷（Ctrl+C、排程停掉）：先把已經開的子程序都關掉再丟出錯誤，
    不然鎖放掉了、子程序還在寫資料表，下一輪會和它們同時跑。"""
    processes = []
    try:
        for module, args in steps:
            print(f"===== {module} {' '.join(args)}（同時）", flush=True)
            processes.append(subprocess.Popen([sys.executable, "-m", module, *args]))
        return [process.wait() for process in processes]
    except BaseException:
        stop_processes(processes)
        raise


def stop_processes(processes: list, wait_seconds: float = 10) -> None:
    """關掉還在跑的子程序：先請它結束（terminate），等不到就強制關掉（kill）。"""
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_pipeline(args: list[str]) -> None:
    crawler_code = run("crawler.producer", args)
    transform_code = run("transform.producer", args)
    if transform_code:
        print("\ntransform 有任務失敗，不跑 analyze、ai")
        raise SystemExit(1)
    # analyze（規則引擎，stage → app）和 ai（stage → ai）都只讀 stage，互不讀對方的結果：同時跑
    analyze_code, ai_code = run_parallel([("analyze.producer", args), ("ai.producer", args)])

    if crawler_code or analyze_code or ai_code:
        raise SystemExit(1)


def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end（沒給 --end 就是今天）
    step_args = ["backfill", "--start", args.start, "--end", args.end] + (["--refetch"] if args.refetch else [])
    done_mark = DONE_DIR / args.end if args.mode != "backfill" else None   # 只有 daily 用記號
    try:
        with pipeline_lock(LOCK_FILE, PIPELINE_LOCK_WAIT_SECONDS):
            if done_mark and done_mark.exists():
                print(f"{args.end} 已經全部跑完（{done_mark}），跳過")
                return
            run_pipeline(step_args)   # 有失敗會丟 SystemExit(1)，不會放記號，下一個小時再跑
            if done_mark:
                done_mark.parent.mkdir(parents=True, exist_ok=True)
                done_mark.touch()
    except TimeoutError as e:
        print(e)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
