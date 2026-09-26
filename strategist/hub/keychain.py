"""機密儲存：macOS Keychain（取代 x-hub 在 Windows 用的 keyring 認證管理員）。

規則
- 機密值只進 Keychain（service = com.randy.hub），資料庫只記「名字」索引，repo 與 .env 一律不落盤。
- owner 決定命名空間：`provider:<id>`（Hub 的 AI 供應商）、`ext:<安裝金鑰>`（某個擴充自己的機密；
  安裝金鑰 = `<擴充 id>@<來源資料夾雜湊>`，別的資料夾冒用同 id 拿不到）。
- 擴充只能拿到自己 namespace 的機密：`ScopedSecrets` 在建構時就綁死 owner，呼叫端無法傳入別人的 id。
- 測試或無 Keychain 環境用 `RANDY_HUB_KEYCHAIN=memory`，避免碰到真實鑰匙圈。
"""
from __future__ import annotations

import os
import re
import threading

SERVICE_NAME = "com.randy.hub"
BACKEND_ENV = "RANDY_HUB_KEYCHAIN"
_OWNER_RE = re.compile(r"^(hub|provider:[a-z0-9][a-z0-9._-]{0,63}|ext:[a-z0-9][a-z0-9._-]{0,127}(@[0-9a-f]{12})?)$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_SECRET_LEN = 16 * 1024


class SecretError(ValueError):
    pass


class MemoryBackend:
    """測試用：只活在行程記憶體裡。"""

    def __init__(self):
        self._items: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def get_password(self, service: str, account: str) -> str | None:
        with self._lock:
            return self._items.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        with self._lock:
            self._items[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        with self._lock:
            self._items.pop((service, account), None)


class KeyringBackend:
    """正式用：keyring 在 macOS 走 Security.framework（login keychain）。"""

    def __init__(self):
        import keyring  # 延遲載入：測試不需要

        self._keyring = keyring

    def get_password(self, service: str, account: str) -> str | None:
        return self._keyring.get_password(service, account)

    def set_password(self, service: str, account: str, value: str) -> None:
        self._keyring.set_password(service, account, value)

    def delete_password(self, service: str, account: str) -> None:
        try:
            self._keyring.delete_password(service, account)
        except self._keyring.errors.PasswordDeleteError:
            pass


def backend_from_env():
    if os.environ.get(BACKEND_ENV, "").strip().lower() == "memory":
        return MemoryBackend()
    return KeyringBackend()


def mask(value: str | None) -> str:
    """只露最後 4 碼，且 key 至少 16 字元才露（短 key 露太多等於半公開）。"""
    if not value:
        return ""
    if len(value) < 16:
        return "••••"
    return f"••••{value[-4:]}"


def _validate(owner: str, name: str) -> str:
    if not _OWNER_RE.match(owner or ""):
        raise SecretError(f"不合法的機密 owner：{owner!r}")
    if not _NAME_RE.match(name or ""):
        raise SecretError("機密名稱只允許英數與 _ . -（最長 64）")
    return f"{owner}/{name}"


class SecretStore:
    def __init__(self, db, backend=None):
        self._db = db
        self._backend = backend or backend_from_env()

    def set(self, owner: str, name: str, value: str) -> None:
        account = _validate(owner, name)
        if not isinstance(value, str) or not value.strip():
            raise SecretError("機密值不可為空")
        if len(value) > MAX_SECRET_LEN:
            raise SecretError("機密值太長")
        self._backend.set_password(SERVICE_NAME, account, value.strip())
        self._db.add_secret_name(owner, name)

    def get(self, owner: str, name: str) -> str | None:
        return self._backend.get_password(SERVICE_NAME, _validate(owner, name))

    def delete(self, owner: str, name: str) -> None:
        self._backend.delete_password(SERVICE_NAME, _validate(owner, name))
        self._db.remove_secret_name(owner, name)

    def names(self, owner: str) -> list[str]:
        _validate(owner, "probe")
        return self._db.secret_names(owner)

    def masked(self, owner: str, name: str) -> str:
        return mask(self.get(owner, name))

    def scoped(self, ext_id: str) -> "ScopedSecrets":
        return ScopedSecrets(self, f"ext:{ext_id}")


class ScopedSecrets:
    """綁定單一擴充的機密視圖：沒有任何參數能改 owner。"""

    def __init__(self, store: SecretStore, owner: str):
        _validate(owner, "probe")
        self._store = store
        self.owner = owner

    def get(self, name: str) -> str | None:
        return self._store.get(self.owner, name)

    def set(self, name: str, value: str) -> None:
        self._store.set(self.owner, name, value)

    def delete(self, name: str) -> None:
        self._store.delete(self.owner, name)

    def names(self) -> list[str]:
        return self._store.names(self.owner)
