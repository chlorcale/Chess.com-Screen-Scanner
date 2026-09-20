from __future__ import annotations

from collections import Counter
from typing import Callable

from ..core.dom_parser import snapshot_from_payload
from ..core.models import DomSnapshot
from ..services.dom_bridge import DomBridgeService


class DomController:
    def __init__(self, host: str, port: int, on_snapshot: Callable[[DomSnapshot], None]):
        self._on_snapshot = on_snapshot
        self.bridge = DomBridgeService(host, port, self._on_payload)
        self.last_snapshot = DomSnapshot()

    def start(self):
        self.bridge.start()

    def stop(self):
        self.bridge.stop()

    def _on_payload(self, payload: dict):
        snapshot = snapshot_from_payload(payload)
        if snapshot.text == self.last_snapshot.text and snapshot.error == self.last_snapshot.error:
            return
        self.last_snapshot = snapshot
        self._on_snapshot(snapshot)

    @property
    def address(self):
        return self.bridge.address()
