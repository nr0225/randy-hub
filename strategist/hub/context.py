"""Hub 執行期共用物件（Randy 自有模組）。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .db import HubDB
from .extensions import ExtensionRegistry
from .keychain import ScopedSecrets, SecretStore
from .paths import HubPaths, repo_root
from .providers import ProviderManager
from .services import ServiceManager
from .strategist_adapter import DEFAULT_GATEWAY, StrategistAdapter, StrategistError

STRATEGIST_ROOT_KEY = "strategist.root"
STRATEGIST_GATEWAY_KEY = "strategist.gatewayUrl"


def strategist_from_settings(db: HubDB) -> StrategistAdapter:
    import logging
    import os

    root = db.get_setting(STRATEGIST_ROOT_KEY) or os.environ.get("RANDY_STRATEGIST_ROOT") or str(repo_root())
    gateway = db.get_setting(STRATEGIST_GATEWAY_KEY) or os.environ.get("RANDY_STRATEGIST_GATEWAY") or DEFAULT_GATEWAY
    try:
        return StrategistAdapter(Path(root).expanduser(), gateway)
    except StrategistError:  # 壞設定不可讓 Hub 起不來：退回預設本機閘道
        logging.getLogger("randy_hub").warning("Strategist 閘道設定不合法，改用預設 %s", DEFAULT_GATEWAY)
        return StrategistAdapter(Path(root).expanduser(), DEFAULT_GATEWAY)


@dataclass
class HubContext:
    paths: HubPaths
    db: HubDB
    secrets: SecretStore
    registry: ExtensionRegistry
    services: ServiceManager
    providers: ProviderManager
    strategist: StrategistAdapter
    hub_origin: str
    started_at: float = field(default_factory=time.time)
    rate_limits: dict = field(default_factory=dict)

    def ext_secrets(self, ext_id: str) -> ScopedSecrets:
        """某擴充自己的機密視圖（綁安裝金鑰：id + 來源資料夾）。擴充沒載入就丟 KeyError。"""
        loaded = self.registry.get(ext_id)
        if not loaded:
            raise KeyError(ext_id)
        return self.secrets.scoped(loaded.key)

    def shutdown(self) -> None:
        self.services.stop_all()
        self.registry.stop()
        self.db.close()


def build_context(paths: HubPaths, hub_origin: str, secret_backend=None, *, examples: bool | None = None) -> HubContext:
    """examples=None 依設定 hub.loadExamples（預設開）；測試傳 False 以免載入內建範例。"""
    from .capabilities import capability_names

    db = HubDB(paths.db)
    secrets = SecretStore(db, secret_backend)
    registry = ExtensionRegistry(paths, db, hub_origin, capability_names, examples)
    services = ServiceManager(paths, registry, lambda: hub_origin)
    ctx = HubContext(paths, db, secrets, registry, services, ProviderManager(db, secrets),
                     strategist_from_settings(db), hub_origin)
    registry.refresh()
    return ctx
