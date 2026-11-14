"""一次跑完整條資料線：crawler（API → raw）→ transform（raw → stage）→ analyze（stage → app）。

    python main.py                              # 只處理今天（daily）
    python main.py init --start 20260701        # 處理一段期間（可加 --end 20261031）

- crawler 有任務失敗：照樣往下跑。transform 會跳過沒抓到的 raw；analyze 會檢查必要的 raw，缺了就不算。
- transform 有任務失敗（程式錯誤）：不跑 analyze。
有任何一步失敗，最後結束代碼是 1。每一步也可以單獨執行，見 docs/flow.md。
"""
import subprocess
import sys


def run(module: str, args: list[str]) -> int:
    """執行一步（python -m <module> <args>），回傳結束代碼（0＝成功）。"""
    print(f"===== {module} {' '.join(args)}", flush=True)
    return subprocess.run([sys.executable, "-m", module, *args]).returncode


def main() -> None:
    args = sys.argv[1:] or ["daily"]   # 沒給參數就是 daily

    crawler_code = run("crawler.producer", args)
    transform_code = run("transform.producer", args)
    if transform_code:
        print("\ntransform 有任務失敗，不跑 analyze")
        raise SystemExit(1)
    analyze_code = run("analyze.producer", args)

    if crawler_code or analyze_code:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
