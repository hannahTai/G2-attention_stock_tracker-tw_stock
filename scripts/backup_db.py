"""備份整個資料庫（raw、stage、app 三層）：pg_dump → data/backup/<資料庫>_<時間>.sql.gz，只留最近幾份。

    uv run python scripts/backup_db.py              # 備份一次（排程：scripts/run_backup.bat）
    uv run python scripts/backup_db.py --keep 30    # 留最近 30 份（預設 14）

還原（會整個蓋掉同名資料庫，先確認）：
    gzip -dc data/backup/tw_stock_20261114_233000.sql.gz | wsl docker exec -i postgres psql -U postgres -d postgres

找 pg_dump：.env 的 PG_DUMP → PATH；都沒有就用 WSL 裡 Docker 容器（PG_CONTAINER，預設 postgres）內建的 pg_dump。
連線設定從 .env 讀（common/config.py）。密碼用環境變數 PGPASSWORD 交給 pg_dump，不放在命令列上（命令列別人看得到）。
"""
import argparse
import datetime
import gzip
import os
import pathlib
import shutil
import subprocess

from common.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, PROJECT_ROOT

BACKUP_DIR = pathlib.Path(os.environ.get("BACKUP_DIR", PROJECT_ROOT / "data" / "backup"))
PG_CONTAINER = os.environ.get("PG_CONTAINER", "postgres")

# --clean --if-exists --create：檔案裡含 DROP／CREATE DATABASE，還原不用先建庫
# --no-owner：還原時不管原本的擁有者是誰
DUMP_OPTIONS = ["--clean", "--if-exists", "--create", "--no-owner", "--encoding=UTF8"]


def dump_command() -> tuple[list[str], dict]:
    """pg_dump 的命令和環境變數。Windows 上有裝 PostgreSQL 就直接用，沒有就到 WSL 的容器裡跑。"""
    path = os.environ.get("PG_DUMP") or shutil.which("pg_dump")
    if path:
        cmd = [path, f"--host={DB_HOST}", f"--port={DB_PORT}", f"--username={DB_USER}", *DUMP_OPTIONS, DB_NAME]
        return cmd, {**os.environ, "PGPASSWORD": DB_PASSWORD}
    if shutil.which("wsl"):   # 容器裡連自己的資料庫不用密碼
        return ["wsl", "docker", "exec", PG_CONTAINER, "pg_dump", f"--username={DB_USER}", *DUMP_OPTIONS, DB_NAME], None
    raise SystemExit("找不到 pg_dump（可在 .env 設 PG_DUMP=完整路徑），也沒有 WSL 可以用容器裡的 pg_dump")


def dump(out_path: pathlib.Path) -> None:
    """pg_dump 的輸出邊讀邊壓縮寫進 out_path；失敗就刪掉不完整的檔案。"""
    cmd, env = dump_command()
    tmp_path = out_path.with_suffix(".partial")
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as proc,             gzip.open(tmp_path, "wb") as gz:
        shutil.copyfileobj(proc.stdout, gz)
        stderr = proc.stderr.read().decode("utf-8", "replace")
    if proc.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise SystemExit(f"pg_dump 失敗（{proc.returncode}）：{stderr.strip()}")
    tmp_path.rename(out_path)


def remove_old(keep: int) -> list[pathlib.Path]:
    """只留最新的 keep 份，回傳刪掉的檔案。檔名有時間，照名稱排序就是照時間。"""
    backups = sorted(BACKUP_DIR.glob(f"{DB_NAME}_*.sql.gz"))
    old = backups[:-keep] if keep > 0 else []
    for path in old:
        path.unlink()
    return old


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=14, help="留最近幾份（預設 14）")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BACKUP_DIR / f"{DB_NAME}_{datetime.datetime.now():%Y%m%d_%H%M%S}.sql.gz"
    dump(out_path)
    print(f"備份完成：{out_path}（{out_path.stat().st_size / 1024 / 1024:.1f} MB）")
    for path in remove_old(args.keep):
        print(f"刪掉舊備份：{path.name}")


if __name__ == "__main__":
    main()
