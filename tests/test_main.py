"""main.py 的鎖：同一時間只跑一輪（檔案鎖，不連資料庫）。"""
import threading
import time

import pytest

import main


def test_second_run_waits_for_first(tmp_path):
    """第一輪鎖著時，第二輪會等到第一輪放掉才開始。"""
    lock_file = tmp_path / "pipeline.lock"
    order = []
    first_has_lock = threading.Event()

    def first_run():
        with main.pipeline_lock(lock_file, wait_seconds=5, poll_seconds=0.05):
            order.append("第一輪開始")
            first_has_lock.set()
            time.sleep(0.5)
            order.append("第一輪結束")

    def second_run():
        first_has_lock.wait()
        with main.pipeline_lock(lock_file, wait_seconds=5, poll_seconds=0.05):
            order.append("第二輪開始")

    threads = [threading.Thread(target=first_run), threading.Thread(target=second_run)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert order == ["第一輪開始", "第一輪結束", "第二輪開始"]


def test_gives_up_after_waiting_too_long(tmp_path):
    lock_file = tmp_path / "pipeline.lock"
    with main.pipeline_lock(lock_file, wait_seconds=5):
        result = []

        def second_run():
            try:
                with main.pipeline_lock(lock_file, wait_seconds=0.2, poll_seconds=0.05):
                    result.append("拿到了")
            except TimeoutError:
                result.append("放棄")

        t = threading.Thread(target=second_run)
        t.start()
        t.join()
    assert result == ["放棄"]


def test_lock_released_after_error(tmp_path):
    """這一輪出錯，鎖也會放掉，下一輪可以馬上開始。"""
    lock_file = tmp_path / "pipeline.lock"
    with pytest.raises(RuntimeError):
        with main.pipeline_lock(lock_file, wait_seconds=1):
            raise RuntimeError("這一輪出錯")
    with main.pipeline_lock(lock_file, wait_seconds=0.1):
        pass
