from __future__ import annotations

from types import SimpleNamespace

import aider.desktop as desktop


class FakeSession:
    def __init__(self, coder):
        self.coder = coder
        self.repo_path = coder.root
        self._shutdown = False
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1
        self._shutdown = True


def test_task_key_binds_distinct_sessions(monkeypatch):
    monkeypatch.setattr(desktop, "DesktopCompanySession", FakeSession)
    desktop._TASK_SESSION_POOL = desktop.TaskSessionPool(max_active=4)

    coder = SimpleNamespace(root="/repo")
    s1 = desktop.get_desktop_company_session(coder, task_key="task:a")
    s2 = desktop.get_desktop_company_session(coder, task_key="task:b")
    s1_again = desktop.get_desktop_company_session(coder, task_key="task:a")

    assert s1 is not s2
    assert s1 is s1_again
