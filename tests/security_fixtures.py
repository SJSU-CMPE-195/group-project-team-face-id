"""Real temporary security state for isolated wireless integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import db
import db_api
from test_pi_device_api import FakeRuntime
from wireless import security as security_module
from wireless.security import Principal, SecurityStore


ADMIN_NAME = "Test Admin"
ADMIN_PIN = "123456"


class AuthorizedFakeRuntime(FakeRuntime):
    """Hardware-free runtime with the authorization hook used by Stage 2."""

    def configure_authorization(self, security: SecurityStore) -> None:
        self.security = security


class SecurityFixture:
    """Own a temporary SQLite database and a paired administrator device."""

    def __init__(self, testcase, root: Path):
        self.db = db_api
        self.pin = ADMIN_PIN
        self.db_path = root / "faceid.db"
        testcase.enterContext(
            mock.patch.object(db, "DB_PATH", str(self.db_path))
        )
        testcase.enterContext(
            mock.patch.object(security_module, "PIN_ITERATIONS", 1_000)
        )
        db.init_db()

        self.security = SecurityStore(db.get_conn, b"s" * 32)
        self.security.ensure_schema()
        self.admin_user = self.security.initial_admin(ADMIN_NAME, self.pin)
        self.local_login = self.security.local_login(ADMIN_NAME, self.pin)
        local_admin = self.security.principal(self.local_login["device_token"])
        invite = self.security.issue_invite(local_admin, self.admin_user["id"])
        paired = self.security.pair(
            invite["invite_token"], self.pin, "Integration test phone"
        )
        self.token = paired["device_token"]
        self.mobile_token = self.token
        self.runtime = AuthorizedFakeRuntime(self.db)

    @property
    def admin(self) -> Principal:
        return self.principal()

    def principal(self, token: str | None = None) -> Principal:
        """Reload the principal so identity changes cannot leave stale authority."""

        return self.security.principal(token or self.token)

    def grant(
        self,
        action: str,
        target: str | None = None,
        *,
        token: str | None = None,
        pin: str | None = None,
    ) -> str:
        result = self.security.authorize(
            self.principal(token), self.pin if pin is None else pin, action, target
        )
        return result["grant_token"]

    def headers(self, grant: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if grant:
            headers["X-BASS-Operation-Grant"] = grant
        return headers


def temporary_security(testcase, root: Path) -> SecurityFixture:
    return SecurityFixture(testcase, root)
