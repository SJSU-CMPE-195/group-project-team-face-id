"""Compose ownership, semantic hardware events, and optional local dev tools."""

from pathlib import Path

from .commissioning import CommissioningStore
from .developer_control import DeveloperControl
from .developer_reset import DeveloperReset
from .hardware_events import HardwareEvents
from .security import SecurityError


class HostLifecycle:
    def __init__(self, security, config, runtime):
        self.security = security
        self.config = config
        self.runtime = runtime
        self.events = HardwareEvents(
            security,
            lambda: self.commissioning.status(),
            power_callback=self._set_power,
        )
        self.commissioning = CommissioningStore(security, config, self.events)
        self.commissioning.ensure_schema()
        self.developer_control = None
        self._pending_ownership_change = None

    def enable_developer_controls(self, config_path: Path, port: int):
        reset = DeveloperReset(
            security=self.security,
            get_conn=self.security.get_connection,
            state_dir=config_path.parent,
            backup_sources=(
                config_path,
                config_path.with_suffix(".tls.pem"),
                config_path.with_suffix(".auth.key"),
            ),
            reset_product=self.commissioning.reset_product,
            generation_provider=self.commissioning.status,
            events=self.events,
            quiesce_callback=lambda: self._quiesce_product("developer_reset"),
            reload_callback=self._reload_reset_product,
        )
        self.developer_control = DeveloperControl(
            events=self.events,
            reset=reset,
            card_provider=self._cards,
            port=port,
        )

    def accept_transfer(self, body):
        return self._complete_ownership_change(
            "ownership_transfer", body,
            self.commissioning.validate_transfer_accept,
            self.commissioning.accept_transfer,
        )

    def recover_owner(self, body):
        return self._complete_ownership_change(
            "owner_recovery", body,
            self.commissioning.validate_recovery,
            lambda values: self.commissioning.recover(values, window_verified=True),
        )

    def _complete_ownership_change(self, kind, body, validate, commit):
        """Validate proof, drain old work, commit once, and resume exact retries."""
        with self.events.lifecycle_lock:
            pending = self.commissioning.ownership_request_digest(kind, body)
            if self._pending_ownership_change is not None:
                if pending != self._pending_ownership_change:
                    raise SecurityError(
                        "ownership_recovery_required", 503,
                        "Retry the pending ownership operation to finish recovery.",
                    )
            else:
                with self.security.synchronized():
                    validation = validate(body)
                    if validation["completed"]:
                        return validation["response"]
                    self.events.require_available()
                    self._pending_ownership_change = pending
                    self.events.set_maintenance(True)

            # Worker completion needs the security barrier. Never join a worker
            # while holding it. A drain failure leaves the product unavailable.
            self._quiesce_product(kind)
            try:
                result = commit(body)
            finally:
                self._resume_product()
                self.events.set_maintenance(False)
                self._pending_ownership_change = None
            return result

    def _set_power(self, powered):
        if powered:
            self._resume_product()
        else:
            self._quiesce_product("simulated_power_off")

    def _quiesce_product(self, reason):
        # The event gate is already closed. Waiting for this barrier drains
        # product HTTP handlers, then workers can finish without a lock cycle.
        with self.security.synchronized():
            pass
        try:
            return self.runtime.quiesce(reason)
        except Exception as exc:
            raise SecurityError(
                "runtime_drain_failed", 503,
                "Product work has not stopped. Retry the same operation.",
            ) from exc

    def _resume_product(self):
        try:
            initialize = getattr(self.runtime, "initialize", None)
            if callable(initialize):
                initialize()
            self.runtime.resume_after_maintenance()
        except Exception as exc:
            raise SecurityError(
                "runtime_reload_failed", 503,
                "Product startup has not completed. Retry the same operation.",
            ) from exc

    def _reload_reset_product(self):
        self._resume_product()
        self._pending_ownership_change = None

    def _cards(self):
        with self.security.synchronized():
            public_payload = self.config.pairing_payload
            activation = self.commissioning.activation_material()
            activation_payload = None
            if activation is not None:
                activation_payload = {
                    **public_payload,
                    "purpose": "activation",
                    "activation_secret": activation,
                }
            return {
                "public_payload": public_payload,
                "activation_payload": activation_payload,
            }
