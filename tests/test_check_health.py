"""scripts/check_health.py：每天的檢查。檔案放在 tmp_path，不碰真的 data/、資料庫、網站。"""
import datetime
import importlib.util
import os

import pytest

from common import date_utils
from common.config import PROJECT_ROOT

# scripts/ 不是套件，照檔案路徑載入
spec = importlib.util.spec_from_file_location("check_health", PROJECT_ROOT / "scripts" / "check_health.py")
check_health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_health)

NIGHT = datetime.datetime(2026, 11, 24, 22, 30)


def touch(path, when: datetime.datetime, text: str = ""):
    """建一個檔案，最後修改時間設成 when（台灣時間；電腦時區不是台北也一樣）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stamp = when.replace(tzinfo=date_utils.TAIPEI).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def hold(f):
    """測試用：像 main.py 一樣鎖住檔案。"""
    f.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def release(f):
    f.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)


def test_stock_line_needs_todays_mark_on_trading_days_after_the_last_run(tmp_path):
    assert "股價線今天（11/24）沒有成功" in check_health.stock_line_problem(NIGHT, True, tmp_path)
    assert check_health.stock_line_problem(NIGHT, False, tmp_path) is None                        # 休市日
    assert check_health.stock_line_problem(NIGHT.replace(hour=20), True, tmp_path) is None         # 22:00 那次還沒跑
    lock = tmp_path / "pipeline.lock"
    assert "沒有成功" in check_health.stock_line_problem(NIGHT, True, tmp_path, (lock,))            # 沒有鎖檔
    with open(lock, "a+") as holder:   # 22:00 那次還在跑（鎖著；就算 log 很久沒寫，例如在等鎖）
        hold(holder)
        assert check_health.stock_line_problem(NIGHT, True, tmp_path, (lock,)) is None
        release(holder)
    assert "沒有成功" in check_health.stock_line_problem(NIGHT, True, tmp_path, (lock,))            # 跑完（或被關掉）、放掉鎖
    touch(tmp_path / "20261124", NIGHT)
    assert check_health.stock_line_problem(NIGHT, True, tmp_path, (lock,)) is None


def test_previous_trading_day_needs_its_mark_too(tmp_path):
    """上一個交易日沒有記號也要報（那天 22:30 還在跑、之後才失敗的）；週末、休市日跳過。"""
    monday = datetime.date(2026, 11, 23)
    assert check_health.previous_stock_day(datetime.date(2026, 11, 24), lambda day: True) == monday
    assert check_health.previous_stock_day(monday, lambda day: True) == datetime.date(2026, 11, 20)     # 跳過週末
    assert check_health.previous_stock_day(monday, lambda day: day != datetime.date(2026, 11, 20)) == datetime.date(2026, 11, 19)
    assert "股價線 11/23 沒有成功" in check_health.previous_day_problem(monday, tmp_path, lambda day: False)
    assert check_health.previous_day_problem(monday, tmp_path, lambda day: True) is None   # 回補過（規則引擎有那天的結果）
    touch(tmp_path / "20261123", NIGHT)
    assert check_health.previous_day_problem(monday, tmp_path, lambda day: False) is None
    assert check_health.previous_day_problem(None, tmp_path) is None


def test_news_line_last_run_must_succeed_and_be_recent(tmp_path):
    assert "找不到新聞線的 log" in check_health.news_line_problem(NIGHT, tmp_path)
    log = tmp_path / "news-2026-11-24.log"
    touch(log, NIGHT - datetime.timedelta(minutes=10), "exit code 1 \n...\nexit code 0 \n")
    assert check_health.news_line_problem(NIGHT, tmp_path) is None                                 # 看最後一次
    touch(log, NIGHT - datetime.timedelta(minutes=10), "exit code 0 \n...\nexit code 1 \n")
    assert "最近一次新聞線失敗（exit code 1）" in check_health.news_line_problem(NIGHT, tmp_path)
    touch(log, datetime.datetime(2026, 11, 24, 17, 25), "exit code 0 \n")   # 20:00 那次沒跑
    assert "新聞線 11/24 20:00 那次沒跑" in check_health.news_line_problem(NIGHT, tmp_path)
    touch(log, datetime.datetime(2026, 11, 24, 23, 20), "exit code 0 \n")   # 隔天早上 07:30 才檢查：23:00 那次有跑就沒事
    assert check_health.news_line_problem(datetime.datetime(2026, 11, 25, 7, 30), tmp_path) is None
    touch(log, datetime.datetime(2026, 11, 24, 17, 25), "exit code 0 \n")
    touch(tmp_path / "news-2026-11-23.log", NIGHT, "exit code 1 \n")                               # 舊的檔案不算
    assert "沒跑" in check_health.news_line_problem(NIGHT, tmp_path)


def test_backup_must_be_recent(tmp_path):
    assert "找不到備份" in check_health.backup_problem(NIGHT, tmp_path)
    touch(tmp_path / "tw_stock_20261120_233000.sql.gz", datetime.datetime(2026, 11, 20, 23, 30))   # 週五的
    assert check_health.backup_problem(datetime.datetime(2026, 11, 23, 22, 30), tmp_path) is None   # 週一晚上：還可以
    assert "太久沒備份" in check_health.backup_problem(NIGHT, tmp_path)                             # 週二晚上：週一沒備份


def test_find_problems_keeps_checking_when_the_database_is_down(monkeypatch):
    def down(day):
        raise ConnectionError("連不上")
    monkeypatch.setattr(check_health, "is_trading_day", down)
    monkeypatch.setattr(check_health, "previous_day_problem", lambda previous: None)
    monkeypatch.setattr(check_health, "stock_line_problem", lambda now, trading: f"股價線 trading={trading}")
    monkeypatch.setattr(check_health, "news_line_problem", lambda now: None)
    monkeypatch.setattr(check_health, "backup_problem", lambda now: None)
    monkeypatch.setattr(check_health, "site_problems", lambda: ["網頁沒回應"])
    monkeypatch.setattr(check_health, "calendar_problem", down)   # 查到一半出錯：記成一個問題，其他照查
    assert check_health.find_problems(NIGHT) == ["資料庫連不上：ConnectionError", "開休市日期的檢查出錯：ConnectionError",
                                                 "股價線 trading=True", "網頁沒回應"]


def test_main_exits_1_and_notifies_only_with_problems(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(check_health, "notify", lambda title, lines: sent.append((title, lines)))
    monkeypatch.setattr(check_health, "find_problems", lambda now: [])
    monkeypatch.setattr("sys.argv", ["check_health.py", "--notify"])
    check_health.main()
    assert "沒有問題" in capsys.readouterr().out and sent == []
    monkeypatch.setattr(check_health, "find_problems", lambda now: ["備份太舊"])
    with pytest.raises(SystemExit) as exit_info:
        check_health.main()
    assert exit_info.value.code == 1 and sent == [("注意股：有 1 個問題", ["備份太舊"])]
