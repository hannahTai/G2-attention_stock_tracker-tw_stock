"""common/logs.py：每天一個檔、刪掉太舊的。"""
import datetime
import logging

from common import logs


def test_writes_to_todays_file_and_removes_old_ones(tmp_path, monkeypatch):
    today = datetime.date(2026, 11, 21)
    (tmp_path / "crawl_twse-2026-10-01.log").write_text("舊的", encoding="utf-8")       # 51 天前：刪掉
    (tmp_path / "crawl_twse-2026-11-20.log").write_text("昨天", encoding="utf-8")       # 留著
    (tmp_path / "crawl_mops-2026-10-01.log").write_text("別的 worker", encoding="utf-8")  # 名稱不同：不動
    monkeypatch.setattr(logs.date_utils, "now", lambda: datetime.datetime(2026, 11, 21, 21, 0))

    handler = logs.DailyFileHandler(tmp_path, "crawl_twse", keep_days=30)
    logger = logging.getLogger("test_daily_file")
    logger.addHandler(handler)
    logger.warning("任務成功")
    handler.close()
    logger.removeHandler(handler)

    assert (tmp_path / "crawl_twse-2026-11-21.log").read_text(encoding="utf-8").strip() == "任務成功"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "crawl_mops-2026-10-01.log", "crawl_twse-2026-11-20.log", "crawl_twse-2026-11-21.log"]
    assert logs.log_path(tmp_path, "crawl_twse", today).name == "crawl_twse-2026-11-21.log"
