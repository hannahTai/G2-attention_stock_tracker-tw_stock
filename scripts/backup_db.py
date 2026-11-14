"""備份整個資料庫（raw、stage、app 三層）：mysqldump → data/backup/<資料庫>_<時間>.sql.gz，只留最近幾份。

    uv run python scripts/backup_db.py              # 備份一次（排程：scripts/run_backup.bat）
    uv run python scripts/backup_db.py --keep 30    # 留最近 30 份（預設 14）

還原（會整個蓋掉同名資料庫，先確認）：
    gzip -dc data/backup/tw_stock_20261114_2330.sql.gz | mysql -u root -p

連線設定從 .env 讀（common/config.py）。密碼寫進暫存的設定檔交給 mysqldump，不放在命令列上（命令列別人看得到）。
"""
import argparse
import datetime
import gzip
import os
import pathlib
import shutil
import subprocess
import tempfile

from common.config import MYSQL_ACCOUNT, MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, PROJECT_ROOT

BACKUP_DIR = pathlib.Path(os.environ.get("BACKUP_DIR", PROJECT_ROOT / "data" / "backup"))
DEFAULT_MYSQLDUMP = r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe"


def find_mysqldump() -> str:
    """找 mysqldump：.env 的 MYSQLDUMP → PATH → MySQL 8.0 預設安裝位置。"""
    path = os.environ.get("MYSQLDUMP") or shutil.which("mysqldump") or DEFAULT_MYSQLDUMP
    if not pathlib.Path(path).exists() and not shutil.which(path):
        raise SystemExit(f"找不到 mysqldump：{path}（可在 .env 設 MYSQLDUMP=完整路徑）")
    return path


def dump(out_path: pathlib.Path) -> None:
    """mysqldump 的輸出邊讀邊壓縮寫進 out_path；失敗就刪掉不完整的檔案。"""
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False, encoding="utf-8") as cnf:
        cnf.write(f'[client]\npassword="{MYSQL_PASSWORD}"\n')
    try:
        cmd = [
            find_mysqldump(),
            f"--defaults-extra-file={cnf.name}",   # 一定要放第一個參數
            f"--host={MYSQL_HOST}", f"--port={MYSQL_PORT}", f"--user={MYSQL_ACCOUNT}",
            "--single-transaction",                # InnoDB 一致的快照，備份時不鎖表、排程照跑
            "--default-character-set=utf8mb4",
            "--databases", MYSQL_DATABASE,         # 檔案裡含 CREATE DATABASE、USE，還原不用先建庫
        ]
        tmp_path = out_path.with_suffix(".partial")
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc, \
                gzip.open(tmp_path, "wb") as gz:
            shutil.copyfileobj(proc.stdout, gz)
            stderr = proc.stderr.read().decode("utf-8", "replace")
        if proc.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            raise SystemExit(f"mysqldump 失敗（{proc.returncode}）：{stderr.strip()}")
        tmp_path.rename(out_path)
    finally:
        os.unlink(cnf.name)


def remove_old(keep: int) -> list[pathlib.Path]:
    """只留最新的 keep 份，回傳刪掉的檔案。檔名有時間，照名稱排序就是照時間。"""
    backups = sorted(BACKUP_DIR.glob(f"{MYSQL_DATABASE}_*.sql.gz"))
    old = backups[:-keep] if keep > 0 else []
    for path in old:
        path.unlink()
    return old


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=14, help="留最近幾份（預設 14）")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BACKUP_DIR / f"{MYSQL_DATABASE}_{datetime.datetime.now():%Y%m%d_%H%M}.sql.gz"
    dump(out_path)
    print(f"備份完成：{out_path}（{out_path.stat().st_size / 1024 / 1024:.1f} MB）")
    for path in remove_old(args.keep):
        print(f"刪掉舊備份：{path.name}")


if __name__ == "__main__":
    main()
