"""一次跑完整條資料線：crawler（API → raw）→ transform（raw → stage）→ analyze（stage → app）。

    python main.py                              # 只處理今天（daily）
    python main.py backfill --start 20260701        # 處理一段期間（可加 --end 20261031）
    python main.py backfill --start 20260701 --refetch  # 同上，crawler 已經抓完整的也重抓

期間在這裡算一次（例如 backfill 沒給 --end 就是今天），再明確傳給三步：跑過午夜，三步的「今天」也一樣。
- crawler 有任務失敗：照樣往下跑。transform 會跳過沒抓到的 raw；analyze 會檢查需要的 stage，缺了那天就不算。
- transform 有任務失敗（程式錯誤）：不跑 analyze。
有任何一步失敗，最後結束代碼是 1。每一步也可以單獨執行，見 docs/flow.md。

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


def run_pipeline(args: list[str]) -> None:
    crawler_code = run("crawler.producer", args)
    transform_code = run("transform.producer", args)
    if transform_code:
        print("\ntransform 有任務失敗，不跑 analyze")
        raise SystemExit(1)
    analyze_code = run("analyze.producer", args)

    if crawler_code or analyze_code:
        raise SystemExit(1)


def main() -> None:
    args = cli.parse_args()   # daily：今天；backfill：--start～--end（沒給 --end 就是今天）
    step_args = ["backfill", "--start", args.start, "--end", args.end] + (["--refetch"] if args.refetch else [])
    try:
        with pipeline_lock(LOCK_FILE, PIPELINE_LOCK_WAIT_SECONDS):
            run_pipeline(step_args)
    except TimeoutError as e:
        print(e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
