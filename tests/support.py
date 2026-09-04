"""Shared test helpers for authenticating against the Device API.

Most route tests care about route behavior, not about how the caller proved
who they are.  These helpers let such a test hold a credential without
repeating the ceremony on every request, while the dedicated authorization
tests still drive the real cookie/session path explicitly.
"""

from __future__ import annotations

from flask.testing import FlaskClient

# Any request whose source address is not loopback; the auth gate has no
# loopback exemption any more, but tests are explicit about being "remote"
# so that intent survives future changes.
REMOTE = {"REMOTE_ADDR": "10.0.0.247"}


class TokenClient(FlaskClient):
    """Test client that attaches an admin bearer token to every request."""

    token: str | None = None

    def open(self, *args, **kwargs):
        if self.token:
            headers = kwargs.setdefault("headers", {})
            try:
                has_auth = "Authorization" in headers
            except TypeError:  # a list of tuples
                has_auth = any(k.lower() == "authorization" for k, _ in headers)
            if not has_auth:
                headers["Authorization"] = f"Bearer {self.token}"
        kwargs.setdefault("environ_base", REMOTE)
        return super().open(*args, **kwargs)


def admin_client(app, token: str):
    """A client that authenticates as an administrator via the legacy token."""
    app.test_client_class = TokenClient
    client = app.test_client()
    client.token = token
    return client


def session_client(app, db_module, user_id: str):
    """A client authenticated the real way: a session cookie for `user_id`."""
    issued = db_module.create_session(user_id, user_agent="pytest")
    app.test_client_class = FlaskClient
    client = app.test_client()
    client.set_cookie("faceid_session", issued["token"], domain="localhost")
    return client
