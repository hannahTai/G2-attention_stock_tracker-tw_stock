"""確認備份真的還原得回來：把一份備份還原到另一個資料庫（<資料庫>_restore_check），數每張表幾列，和正式的比，做完刪掉。

    uv run python scripts/check_backup.py                                          # 最新一份備份
    uv run python scripts/check_backup.py --file data/backup/tw_stock_20261123_233000.sql.gz

正式的資料庫完全不碰：備份檔裡的 DROP／CREATE DATABASE、\\connect 這幾行拿掉，其他的全部送進 <資料庫>_restore_check。
還原有任何錯誤就停（psql 的 ON_ERROR_STOP），結束代碼 1。每個月、或改了備份的方式以後跑一次。
找 psql 和 scripts/backup_db.py 找 pg_dump 一樣：.env 的 PSQL → PATH；都沒有就用 WSL 裡 Docker 容器（PG_CONTAINER）內建的 psql。
"""
import argparse
import collections.abc
import contextlib
import gzip
import os
import pathlib
import shutil
import subprocess
import threading

import sqlalchemy

from common import repository
from common.config import BACKUP_DIR, DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, PG_CONTAINER
RESTORE_DATABASE = f"{DB_NAME}_restore_check"
# 備份檔裡管整個資料庫的指令（pg_dump --clean --create 寫的）：還原到別的資料庫時拿掉，不然會刪掉、連到正式的資料庫
# \restrict／\unrestrict：新版 pg_dump（17.6 起）寫的，舊版 psql 看不懂；還原到檢查用的資料庫不需要
DATABASE_COMMANDS = ("DROP DATABASE", "CREATE DATABASE", "ALTER DATABASE", "COMMENT ON DATABASE", "\\connect",
                     "\\restrict", "\\unrestrict")


def restore_lines(lines: collections.abc.Iterable[str]) -> collections.abc.Iterator[str]:
    """備份檔的每一行，拿掉管整個資料庫的指令（DATABASE_COMMANDS）；COPY 的資料（到 \\. 為止）原樣留著，
    資料剛好以這些字開頭也不會被拿掉。

    >>> list(restore_lines(["DROP DATABASE tw_stock;\\n", "\\\\connect tw_stock\\n", "CREATE TABLE t (a text);\\n",
    ...                     "COPY t (a) FROM stdin;\\n", "DROP DATABASE 是資料\\n", "\\\\.\\n"]))
    ['CREATE TABLE t (a text);\\n', 'COPY t (a) FROM stdin;\\n', 'DROP DATABASE 是資料\\n', '\\\\.\\n']
    """
    in_copy = False
    for line in lines:
        if in_copy:
            in_copy = line.rstrip("\r\n") != "\\."
            yield line
        elif line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
            in_copy = True
            yield line
        elif not line.startswith(DATABASE_COMMANDS):
            yield line


def psql_command(database: str) -> tuple[list[str], dict | None]:
    """連到 database 的 psql 命令和環境變數（有錯就停、不印每句的結果）。"""
    def options(null_device: str) -> list[str]:
        return [f"--username={DB_USER}", f"--dbname={database}", "-v", "ON_ERROR_STOP=1", "-q", "-o", null_device]
    path = os.environ.get("PSQL") or shutil.which("psql")
    if path:
        return [path, f"--host={DB_HOST}", f"--port={DB_PORT}", *options(os.devnull)], {**os.environ, "PGPASSWORD": DB_PASSWORD}
    if shutil.which("wsl") and DB_HOST in ("127.0.0.1", "localhost"):   # 容器裡連自己的資料庫不用密碼；輸出丟到容器裡的 /dev/null
        # 只有資料庫就在這台電腦（容器）時才用：建、刪還原用的資料庫走 DB_HOST，psql 也要連同一台
        return ["wsl", "docker", "exec", "-i", PG_CONTAINER, "psql", *options("/dev/null")], None
    raise SystemExit("找不到 psql（可在 .env 設 PSQL=完整路徑）；資料庫不在這台電腦時，不能用 WSL 容器裡的 psql")


def restore(backup: pathlib.Path) -> None:
    """備份還原到 RESTORE_DATABASE（先刪掉舊的、建一個空的）。"""
    assert RESTORE_DATABASE != DB_NAME
    repository.drop_database(RESTORE_DATABASE)
    repository.admin_execute(f'CREATE DATABASE "{RESTORE_DATABASE}"')
    cmd, env = psql_command(RESTORE_DATABASE)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    errors = []   # psql 的錯誤訊息：另一條執行緒邊讀，psql 印很多錯誤也不會卡住
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()))
    reader.start()
    try:
        with gzip.open(backup, "rt", encoding="utf-8", newline="") as dump:   # 備份檔壞掉：gzip 的錯誤照樣丟出去
            for line in restore_lines(dump):
                try:
                    proc.stdin.write(line.encode("utf-8"))
                except OSError:   # psql 遇到錯誤先停了（BrokenPipeError；Windows 是 OSError EINVAL）：錯誤訊息在 stderr
                    break
    finally:   # 不管怎樣都關掉 psql 的輸入、等它結束，不會卡住
        with contextlib.suppress(OSError):
            proc.stdin.close()
        proc.wait()
        reader.join()
    stderr = b"".join(errors).decode("utf-8", "replace")
    if proc.returncode != 0:
        raise SystemExit(f"還原失敗（psql 結束代碼 {proc.returncode}）：{stderr.strip()[-2000:]}")


def table_counts(database: str) -> dict[str, int]:
    """{表名: 幾列}：tw_stock schema 的每張表（不含 VIEW）。"""
    engine = repository.get_engine(database)
    try:
        with engine.connect() as conn:
            tables = conn.execute(sqlalchemy.text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = :s AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"), {"s": repository.SCHEMA}).scalars().all()
            return {t: conn.execute(sqlalchemy.text(f'SELECT count(*) FROM {repository.SCHEMA}."{t}"')).scalar() for t in tables}
    finally:
        engine.dispose()


def compare(restored: dict[str, int], live: dict[str, int]) -> list[str]:
    """還原的和正式的比，回傳有問題的：正式的有、還原的沒有的表；正式的有資料、還原的是空的表。
    列數比正式的少是正常的（備份之後又抓了新的）。

    >>> compare({"a": 5, "b": 0}, {"a": 9, "b": 3, "c": 1})
    ['b：正式的有 3 列，還原的是空的', 'c：還原的資料庫沒有這張表']
    """
    problems = []
    for table, count in sorted(live.items()):
        if table not in restored:
            problems.append(f"{table}：還原的資料庫沒有這張表")
        elif count and not restored[table]:
            problems.append(f"{table}：正式的有 {count} 列，還原的是空的")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=pathlib.Path, help="要還原哪一份（預設 data/backup/ 最新的一份）")
    args = parser.parse_args()
    backup = args.file or max(BACKUP_DIR.glob(f"{DB_NAME}_*.sql.gz"), key=lambda path: path.stat().st_mtime, default=None)
    if backup is None:
        raise SystemExit(f"找不到備份（{BACKUP_DIR}）")
    print(f"還原 {backup.name}（{backup.stat().st_size / 1024 / 1024:.1f} MB）到 {RESTORE_DATABASE}…", flush=True)
    try:
        restore(backup)
        restored, live = table_counts(RESTORE_DATABASE), table_counts(DB_NAME)
    finally:
        repository.drop_database(RESTORE_DATABASE)
    print(f"{'表':<28}{'備份':>10}{'正式':>10}")
    for table in sorted(live):
        print(f"{table:<28}{restored.get(table, '—'):>10}{live[table]:>10}")
    problems = compare(restored, live)
    if problems:
        print("有問題：\n" + "\n".join(f"- {p}" for p in problems))
        raise SystemExit(1)
    print(f"還原成功：{len(restored)} 張表都在，有資料的表都還原了（{RESTORE_DATABASE} 已經刪掉）")


if __name__ == "__main__":
    main()
