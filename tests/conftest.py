"""測試共用：用記憶體裡的假 raw 表代替資料庫，測試不會碰到真的資料庫。"""
import datetime
import ipaddress
import json
import types

import pytest

from common import date_utils, repository, runner


class FakeRaw:
    """跟 repository 的 raw 表同樣的存取規則（只新增；讀每個 raw_key 最新一份成功的），資料存在 list 裡。"""

    def __init__(self):
        self.rows = []   # [(source, SimpleNamespace(欄位…))]，依寫入順序

    def insert_raw_row(self, source, raw_key, *, fetched_at, http_status, ok, problem, url, request_body, body):
        self.rows.append((source, types.SimpleNamespace(
            raw_key=raw_key, fetched_at=fetched_at.astimezone(date_utils.TAIPEI).replace(tzinfo=None),
            http_status=http_status, ok=ok, problem=problem, url=url, request_body=request_body, body=body)))

    def history(self, source, raw_key):
        """測試用：這個 raw_key 的每一列，依寫入順序。"""
        return [r for s, r in self.rows if s == source and r.raw_key == raw_key]

    def last(self, source, raw_key):
        """測試用：這個 raw_key 最後寫入的一列（成功或失敗）。"""
        return self.history(source, raw_key)[-1]

    def latest_ok(self, source):
        """{raw_key: 最新一份成功的列}；抓取時間相同時，後寫入的算新的。"""
        latest = {}
        for s, r in self.rows:
            if s == source and r.ok and (r.raw_key not in latest or r.fetched_at >= latest[r.raw_key].fetched_at):
                latest[r.raw_key] = r
        return latest

    def read_raws(self, source, raw_keys=None):
        latest = self.latest_ok(source)
        return [repository.to_envelope(source, latest[k]) for k in sorted(latest)
                if raw_keys is None or k in raw_keys]

    def list_raw_keys(self, source):
        return sorted(self.latest_ok(source))

    def latest_raw_key(self, source, upto):
        keys = [k for k in self.latest_ok(source) if k <= upto]
        return max(keys) if keys else None

    def raw_fetched_times(self, source):
        return {k: r.fetched_at for k, r in self.latest_ok(source).items()}

    def put(self, source, raw_key, body, fetched_at="2026-11-13T21:00:00+08:00", ok=True):
        """測試用：直接放一份回應進去。body 給 dict/list 會轉成 JSON 字串。"""
        self.insert_raw_row(source, raw_key, fetched_at=datetime.datetime.fromisoformat(fetched_at), http_status=200,
                            ok=ok, problem=None if ok else "測試失敗", url="test", request_body=None,
                            body=body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))


@pytest.fixture
def raw_store(monkeypatch):
    """把 repository 的 raw 表換成記憶體版；回傳 FakeRaw，測試可以用 put() 放資料、用 last()／history() 檢查結果。"""
    fake = FakeRaw()
    for name in ("insert_raw_row", "read_raws", "list_raw_keys", "latest_raw_key", "raw_fetched_times"):
        monkeypatch.setattr(repository, name, getattr(fake, name))
    from crawler import producer   # 這裡才 import：關注名單有快取（一輪算一次），每個測試的 raw 都不一樣，要清掉
    producer.watched_by_day.cache_clear()
    yield fake
    producer.watched_by_day.cache_clear()


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """測試不查 DNS：IP 照原樣，網域一律當成公開網址（93.184.216.34）。要測內部網址的，自己用 IP 或換掉 resolve_host。"""
    from crawler import client   # 這裡才 import，conftest 載入時不用先載 crawler

    def fake_resolve(host):
        try:
            return {str(ipaddress.ip_address(host))}
        except ValueError:
            return {"93.184.216.34"}
    monkeypatch.setattr(client, "resolve_host", fake_resolve)


@pytest.fixture(autouse=True)
def local_runner(monkeypatch):
    """測試一律在本機依序執行、不寫任務歷史（不管 .env 的 USE_CELERY、JOB_LOG 怎麼設）；要測的測試自己打開。"""
    monkeypatch.setattr(runner, "USE_CELERY", False)
    monkeypatch.setattr(runner, "JOB_LOG", False)
