"""analyze/producer.py 的任務順序：一天一天照順序。不連資料庫。"""
from analyze import producer as analyze_producer
from analyze import task as analyze_task


def test_analyze_jobs_go_day_by_day():
    jobs = analyze_producer.build_jobs("20261112", "20261113")
    assert [(func, kwargs["date_yyyymmdd"]) for func, kwargs in jobs] == [
        (analyze_task.analyze_progress, "20261112"), (analyze_task.analyze_progress, "20261113"),
    ]
