"""Bound bursts of expensive authenticated requests before route work."""

from collections import OrderedDict, deque
import threading
import time


class RequestLimits:
    def __init__(self):
        self._windows = OrderedDict()
        self._lock = threading.Lock()

    def check(self, peer: str, path: str, method: str) -> int:
        """Return retry seconds, or zero when this request may proceed."""
        if method not in {"POST", "PATCH", "DELETE"}:
            return 0
        if path in {"/api/pairings", "/api/operation-grants", "/api/session-login",
                    "/local/security/login",
                    "/local/security/initial-admin", "/api/commissioning/claim",
                    "/api/commissioning/recover", "/api/commissioning/transfer/accept",
                    "/api/ownership/transfer"}:
            group, maximum = "credentials", 20
        elif path in {"/api/scan/start", "/api/unlock"}:
            group, maximum = "scan", 10
        elif path == "/api/enroll/start":
            group, maximum = "enroll", 6
        elif path == "/api/enroll/sample":
            group, maximum = "sample", 120
        elif path.startswith("/api/users") or path == "/api/settings":
            group, maximum = "management", 30
        else:
            # Cancellation/control has separate authorization; scan load must not
            # consume its quota. Physical interlocks remain a deployment requirement.
            return 0

        now = time.monotonic()
        key = (peer, group)
        with self._lock:
            window = self._windows.pop(key, deque())
            while window and window[0] <= now - 60:
                window.popleft()
            self._windows[key] = window
            if len(self._windows) > 256:
                self._windows.popitem(last=False)
            if len(window) >= maximum:
                return max(1, int(60 - (now - window[0])) + 1)
            window.append(now)
        return 0
