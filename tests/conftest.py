"""測試共用：用記憶體裡的假 raw 表代替資料庫，測試不會碰到真的 MySQL。"""
import datetime
import json
import types

import pytest

from common import date_utils, repository


class FakeRaw:
    """跟 repository 的 raw 表同樣的存取規則（成功覆蓋、失敗不蓋掉成功的），資料存在 dict 裡。"""

    def __init__(self):
        self.rows = {}   # {(source, raw_key): SimpleNamespace(欄位…)}

    def upsert_raw_row(self, source, raw_key, *, fetched_at, http_status, ok, problem, url, request_body, body):
        old = self.rows.get((source, raw_key))
        if not ok and old is not None and old.ok:
            return False
        self.rows[(source, raw_key)] = types.SimpleNamespace(
            raw_key=raw_key, fetched_at=fetched_at.astimezone(date_utils.TAIPEI).replace(tzinfo=None),
            http_status=http_status, ok=ok, problem=problem, url=url, request_body=request_body, body=body)
        return True

    def read_raws(self, source, raw_keys=None):
        rows = [r for (s, k), r in sorted(self.rows.items()) if s == source and r.ok
                and (raw_keys is None or k in raw_keys)]
        return [repository.to_envelope(source, r) for r in rows]

    def list_raw_keys(self, source):
        return sorted(k for (s, k), r in self.rows.items() if s == source and r.ok)

    def raw_exists(self, source, raw_key):
        r = self.rows.get((source, raw_key))
        return r is not None and r.ok

    def put(self, source, raw_key, body, fetched_at="2026-11-13T21:00:00+08:00", ok=True):
        """測試用：直接放一份回應進去。body 給 dict/list 會轉成 JSON 字串。"""
        self.upsert_raw_row(source, raw_key, fetched_at=datetime.datetime.fromisoformat(fetched_at), http_status=200,
                          ok=ok, problem=None if ok else "測試失敗", url="test", request_body=None,
                          body=body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))


@pytest.fixture
def raw_store(monkeypatch):
    """把 repository 的 raw 表換成記憶體版；回傳 FakeRaw，測試可以用 put() 放資料、用 rows 檢查結果。"""
    fake = FakeRaw()
    for name in ("upsert_raw_row", "read_raws", "list_raw_keys", "raw_exists"):
        monkeypatch.setattr(repository, name, getattr(fake, name))
    return fake
