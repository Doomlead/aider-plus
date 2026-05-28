from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from aider.codegraph.core import CodeGraph


@dataclass
class CodeGraphWatcher:
    """Small polling watcher for incremental code graph sync.

    This intentionally avoids a mandatory watchdog dependency. Long-running surfaces can call
    ``run`` with a callback; CLI users can call ``sync_once``.
    """

    graph: CodeGraph
    interval: float = 1.0

    def sync_once(self) -> dict[str, Any]:
        return self.graph.sync()

    def run(
        self, *, once: bool = False, max_cycles: int | None = None, on_sync=None
    ) -> None:
        cycles = 0
        while True:
            result = self.sync_once()
            if on_sync is not None:
                on_sync(result)
            cycles += 1
            if once or (max_cycles is not None and cycles >= max_cycles):
                return
            time.sleep(self.interval)
