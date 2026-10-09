"""BlueZ GATT facade for an opaque, per-peer TLS byte tunnel."""

from __future__ import annotations

from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
import select
import socket
import threading
import time
from typing import Callable

from .ble_protocol import (
    CLOSED_FRAME,
    MAX_DIRECTION_BUFFER,
    PROTOCOL_INFO,
    PROTOCOL_INFO_UUID,
    SERVICE_UUID,
    TUNNEL_UUID,
    WAIT_FRAME,
    ClientOpcode,
    ErrorCode,
    ProtocolViolation,
    data_frame,
    error_frame,
    open_ok_frame,
    parse_client_frame,
    tls_payload_capacity,
)


BLUEZ_SERVICE = "org.bluez"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
GATT_MANAGER_INTERFACE = "org.bluez.GattManager1"
GATT_SERVICE_INTERFACE = "org.bluez.GattService1"
GATT_CHARACTERISTIC_INTERFACE = "org.bluez.GattCharacteristic1"
ADVERTISEMENT_MANAGER_INTERFACE = "org.bluez.LEAdvertisingManager1"
ADVERTISEMENT_INTERFACE = "org.bluez.LEAdvertisement1"

APPLICATION_PATH = "/com/bass/ble"
SERVICE_PATH = f"{APPLICATION_PATH}/service0"
PROTOCOL_INFO_PATH = f"{SERVICE_PATH}/char0"
TUNNEL_PATH = f"{SERVICE_PATH}/char1"
ADVERTISEMENT_PATH = f"{APPLICATION_PATH}/advertisement0"

MAX_CLIENTS = 4
UPSTREAM_HOST = "127.0.0.1"
CALLBACK_TIMEOUT_SECONDS = 10.0
EXCHANGE_TIMEOUT_SECONDS = 60.0
REGISTRATION_TIMEOUT_SECONDS = 10


@dataclass
class _Peer:
    device: str
    generation: int
    mtu: int
    condition: threading.Condition = field(default_factory=threading.Condition)
    uplink: deque[bytes | None] = field(default_factory=deque)
    uplink_bytes: int = 0
    downlink: bytearray = field(default_factory=bytearray)
    controls: deque[bytes] = field(default_factory=deque)
    upstream: socket.socket | None = None
    connector: threading.Thread | None = None
    reader: threading.Thread | None = None
    writer: threading.Thread | None = None
    deadline: threading.Timer | None = None
    state: str = "connecting"
    close_received: bool = False
    upstream_eof: bool = False
    reader_done: bool = False
    writer_done: bool = False
    released: bool = False
    closed_delivered: bool = False
    disconnected: bool = False
    error: ErrorCode | None = None


class Backpressure(RuntimeError):
    """The frame was not accepted; the D-Bus facade must retry it unchanged."""


class BleTunnelGateway:
    """Own independent TCP tunnels; BLE and HTTP remain outside this core."""

    def __init__(self, upstream_port: int):
        if not 1 <= upstream_port <= 65535:
            raise ValueError("upstream port must be between 1 and 65535")
        self._upstream_port = upstream_port
        self._lock = threading.Lock()
        self._peers: dict[str, _Peer] = {}
        self._rejected: dict[str, ErrorCode] = {}
        self._next_generation = 0
        self._closed = False

    def write_frame(self, device: str, mtu: int, value: bytes) -> None:
        if self._closed:
            raise RuntimeError("BLE tunnel gateway is closed")
        try:
            frame = parse_client_frame(value, mtu)
        except ProtocolViolation as exc:
            self._reject_or_poison(device, exc.code)
            return

        if frame.opcode is ClientOpcode.OPEN:
            self._open(device, mtu)
        elif frame.opcode is ClientOpcode.DATA:
            self._write_data(device, frame.payload)
        else:
            self._request_close(device)

    def read_frame(self, device: str, mtu: int) -> bytes:
        tls_payload_capacity(mtu)
        with self._lock:
            rejected = self._rejected.get(device)
            peer = self._peers.get(device)
        if rejected is not None:
            return error_frame(rejected)
        if peer is None:
            return error_frame(ErrorCode.BAD_STATE)

        remove_after_read = False
        with peer.condition:
            if peer.disconnected:
                return error_frame(ErrorCode.BAD_STATE)
            peer.mtu = mtu
            if peer.error is not None:
                return error_frame(peer.error)
            if peer.controls:
                return peer.controls.popleft()
            if peer.downlink:
                size = min(len(peer.downlink), tls_payload_capacity(mtu))
                payload = bytes(peer.downlink[:size])
                del peer.downlink[:size]
                peer.condition.notify_all()
                self._release_if_ready(peer)
                return data_frame(payload, mtu)
            self._release_if_ready(peer)
            if peer.released and not peer.closed_delivered:
                peer.closed_delivered = True
                remove_after_read = True
                response = CLOSED_FRAME
            else:
                response = WAIT_FRAME

        if remove_after_read:
            with self._lock:
                if self._peers.get(device) is peer:
                    self._peers.pop(device, None)
        return response

    def disconnect(self, device: str) -> None:
        with self._lock:
            peer = self._peers.pop(device, None)
            self._rejected.pop(device, None)
        if peer is not None:
            self._terminate(peer)

    def generation(self, device: str) -> int | None:
        with self._lock:
            peer = self._peers.get(device)
            return None if peer is None else peer.generation

    def poison_if_generation(
        self,
        device: str,
        generation: int,
        code: ErrorCode,
    ) -> None:
        with self._lock:
            peer = self._peers.get(device)
        if peer is not None and peer.generation == generation:
            self._poison(peer, code)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            peers = tuple(self._peers.values())
            self._peers.clear()
            self._rejected.clear()
        workers: list[threading.Thread] = []
        for peer in peers:
            if peer.connector is not None:
                workers.append(peer.connector)
            if peer.reader is not None:
                workers.append(peer.reader)
            if peer.writer is not None:
                workers.append(peer.writer)
            self._terminate(peer)
        for worker in workers:
            worker.join(timeout=1)

    def _open(self, device: str, mtu: int) -> None:
        with self._lock:
            if device in self._rejected:
                return
            existing = self._peers.get(device)
            if existing is not None:
                peer = None
            elif len(self._peers) >= MAX_CLIENTS:
                self._rejected[device] = ErrorCode.BUSY
                return
            else:
                self._next_generation += 1
                generation = self._next_generation
                peer = _Peer(device=device, generation=generation, mtu=mtu)
                self._peers[device] = peer
        if existing is not None:
            self._poison(existing, ErrorCode.BAD_STATE)
            return

        connector = threading.Thread(
            target=self._connect_upstream,
            args=(peer,),
            name=f"bass-ble-connect-{peer.generation}",
            daemon=True,
        )
        deadline = threading.Timer(
            EXCHANGE_TIMEOUT_SECONDS,
            self._exchange_timed_out,
            args=(peer,),
        )
        deadline.daemon = True
        with peer.condition:
            peer.connector = connector
            peer.deadline = deadline
        deadline.start()
        connector.start()

    def _connect_upstream(self, peer: _Peer) -> None:
        try:
            upstream = socket.create_connection(
                (UPSTREAM_HOST, self._upstream_port),
                timeout=CALLBACK_TIMEOUT_SECONDS,
            )
            upstream.setblocking(False)
        except OSError:
            self._poison(peer, ErrorCode.UPSTREAM_UNAVAILABLE)
            return

        with self._lock:
            current = self._peers.get(peer.device) is peer
        if not current:
            upstream.close()
            return

        reader = threading.Thread(
            target=self._read_upstream,
            args=(peer,),
            name=f"bass-ble-read-{peer.generation}",
            daemon=True,
        )
        writer = threading.Thread(
            target=self._write_upstream,
            args=(peer,),
            name=f"bass-ble-write-{peer.generation}",
            daemon=True,
        )
        with peer.condition:
            if peer.disconnected or peer.error is not None:
                upstream.close()
                return
            peer.upstream = upstream
            peer.reader = reader
            peer.writer = writer
            peer.state = "active"
            peer.controls.append(open_ok_frame())
        reader.start()
        writer.start()

    def _write_data(self, device: str, payload: bytes) -> None:
        peer = self._peer_or_reject(device)
        if peer is None:
            return
        with peer.condition:
            if peer.state != "active" or peer.error is not None:
                poison = True
            elif peer.uplink_bytes + len(payload) > MAX_DIRECTION_BUFFER:
                raise Backpressure("uplink buffer is full")
            else:
                poison = False
                peer.uplink.append(payload)
                peer.uplink_bytes += len(payload)
                peer.condition.notify_all()
        if poison:
            self._poison(peer, ErrorCode.BAD_STATE)

    def _request_close(self, device: str) -> None:
        peer = self._peer_or_reject(device)
        if peer is None:
            return
        with peer.condition:
            if peer.state != "active" or peer.error is not None:
                poison = True
            else:
                poison = False
                peer.state = "closing"
                peer.close_received = True
                peer.uplink.append(None)
                peer.condition.notify_all()
        if poison:
            self._poison(peer, ErrorCode.BAD_STATE)

    def _peer_or_reject(self, device: str) -> _Peer | None:
        with self._lock:
            if device in self._rejected:
                return None
            peer = self._peers.get(device)
            if peer is None:
                self._rejected[device] = ErrorCode.BAD_STATE
            return peer

    def _reject_or_poison(self, device: str, code: ErrorCode) -> None:
        with self._lock:
            peer = self._peers.get(device)
            if peer is None:
                self._rejected[device] = code
        if peer is not None:
            self._poison(peer, code)

    def _exchange_timed_out(self, peer: _Peer) -> None:
        with peer.condition:
            active = not peer.disconnected and not peer.released and peer.error is None
        if active:
            self._poison(peer, ErrorCode.TIMEOUT)

    def _write_upstream(self, peer: _Peer) -> None:
        try:
            while True:
                with peer.condition:
                    while not peer.uplink and not peer.disconnected and peer.error is None:
                        peer.condition.wait()
                    if peer.disconnected or peer.error is not None:
                        return
                    payload = peer.uplink.popleft()
                    upstream = peer.upstream
                if upstream is None:
                    self._poison(peer, ErrorCode.UPSTREAM_UNAVAILABLE)
                    return
                if payload is None:
                    with suppress(OSError):
                        upstream.shutdown(socket.SHUT_WR)
                    return
                self._send_all(upstream, payload)
                with peer.condition:
                    peer.uplink_bytes -= len(payload)
                    peer.condition.notify_all()
        except TimeoutError:
            self._poison(peer, ErrorCode.TIMEOUT)
        except (OSError, ValueError):
            self._poison(peer, ErrorCode.UPSTREAM_UNAVAILABLE)
        finally:
            with peer.condition:
                peer.writer_done = True
                self._release_if_ready(peer)
                peer.condition.notify_all()

    @staticmethod
    def _send_all(upstream: socket.socket, payload: bytes) -> None:
        view = memoryview(payload)
        deadline = time.monotonic() + CALLBACK_TIMEOUT_SECONDS
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("upstream write timed out")
            _, writable, _ = select.select((), (upstream,), (), remaining)
            if not writable:
                raise TimeoutError("upstream write timed out")
            sent = upstream.send(view)
            if sent <= 0:
                raise OSError("upstream socket closed during write")
            view = view[sent:]

    def _read_upstream(self, peer: _Peer) -> None:
        try:
            while True:
                with peer.condition:
                    while (
                        len(peer.downlink) >= MAX_DIRECTION_BUFFER
                        and not peer.disconnected
                        and peer.error is None
                    ):
                        peer.condition.wait()
                    if peer.disconnected or peer.error is not None:
                        return
                    upstream = peer.upstream
                    room = MAX_DIRECTION_BUFFER - len(peer.downlink)
                if upstream is None:
                    self._poison(peer, ErrorCode.UPSTREAM_UNAVAILABLE)
                    return
                readable, _, _ = select.select((upstream,), (), (), 0.5)
                if not readable:
                    continue
                payload = upstream.recv(min(room, 4096))
                if not payload:
                    with peer.condition:
                        peer.upstream_eof = True
                    return
                with peer.condition:
                    if peer.disconnected or peer.error is not None:
                        return
                    peer.downlink.extend(payload)
                    peer.condition.notify_all()
        except (OSError, ValueError):
            with peer.condition:
                closing = peer.close_received
            if not closing:
                self._poison(peer, ErrorCode.UPSTREAM_UNAVAILABLE)
        finally:
            with peer.condition:
                peer.reader_done = True
                self._release_if_ready(peer)
                peer.condition.notify_all()

    @staticmethod
    def _release_if_ready(peer: _Peer) -> None:
        if (
            peer.released
            or peer.error is not None
            or not peer.close_received
            or not peer.upstream_eof
            or not peer.reader_done
            or not peer.writer_done
            or peer.uplink
            or peer.uplink_bytes
            or peer.downlink
        ):
            return
        upstream = peer.upstream
        peer.upstream = None
        if upstream is not None:
            upstream.close()
        if peer.deadline is not None:
            peer.deadline.cancel()
        peer.released = True
        peer.state = "closed"

    @staticmethod
    def _terminate(peer: _Peer) -> None:
        with peer.condition:
            peer.disconnected = True
            if peer.deadline is not None:
                peer.deadline.cancel()
            upstream = peer.upstream
            peer.upstream = None
            peer.uplink.clear()
            peer.downlink.clear()
            peer.controls.clear()
            peer.condition.notify_all()
        if upstream is not None:
            with suppress(OSError):
                upstream.shutdown(socket.SHUT_RDWR)
            upstream.close()

    @staticmethod
    def _poison(peer: _Peer, code: ErrorCode) -> None:
        with peer.condition:
            if peer.disconnected or peer.error is not None:
                return
            peer.error = code
            peer.state = "poisoned"
            if peer.deadline is not None:
                peer.deadline.cancel()
            upstream = peer.upstream
            peer.upstream = None
            peer.uplink.clear()
            peer.uplink_bytes = 0
            peer.downlink.clear()
            peer.controls.clear()
            peer.condition.notify_all()
        if upstream is not None:
            with suppress(OSError):
                upstream.shutdown(socket.SHUT_RDWR)
            upstream.close()


class BluezBleGateway:
    """Register the tunnel with system BlueZ without importing Pi modules on PC."""

    def __init__(self, *, device_id: str, upstream_port: int):
        compact_id = device_id.replace("-", "")
        self._local_name = f"BASS-{compact_id[-6:].upper()}"
        self._gateway = BleTunnelGateway(upstream_port)
        self._bus = None
        self._bluez_owner: str | None = None
        self._glib = None
        self._loop = None
        self._gatt_manager = None
        self._advertising_manager = None
        self._objects: list[object] = []
        self._signal_match = None
        self._application_registered = False
        self._advertisement_registered = False
        self._registration_started = False
        self._registration_pending = False
        self._shutdown_requested = False
        self._shutdown_source_id: int | None = None
        self._closing = False
        self._pending_writes: dict[
            str,
            tuple[
                object,
                Callable[[Exception], None],
                Callable[[str], Exception],
            ],
        ] = {}

    def __enter__(self):
        try:
            self.start()
        except BaseException as exc:
            self.close()
            if isinstance(exc, RuntimeError) or not isinstance(exc, Exception):
                raise
            raise RuntimeError("BlueZ initialization failed") from exc
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def start(self) -> None:
        try:
            import dbus
            import dbus.mainloop.glib
            import dbus.service
            from gi.repository import GLib
        except ImportError as exc:
            raise RuntimeError(
                "Pi BLE mode requires the Raspberry Pi OS bluez, python3-dbus, "
                "and python3-gi packages."
            ) from exc

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        self._bluez_owner = str(bus.get_name_owner(BLUEZ_SERVICE))
        root = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, "/"),
            OBJECT_MANAGER_INTERFACE,
        )
        managed = root.GetManagedObjects()
        adapter_path = next(
            (
                str(path)
                for path, interfaces in sorted(managed.items(), key=lambda item: str(item[0]))
                if GATT_MANAGER_INTERFACE in interfaces
                and ADVERTISEMENT_MANAGER_INTERFACE in interfaces
            ),
            None,
        )
        if adapter_path is None:
            raise RuntimeError("BlueZ exposes no adapter with GATT and LE advertising support.")
        properties = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, adapter_path),
            PROPERTIES_INTERFACE,
        )
        if not bool(properties.Get(ADAPTER_INTERFACE, "Powered")):
            raise RuntimeError("The Bluetooth adapter is powered off.")

        objects = self._build_objects(dbus, dbus.service, bus)
        self._bus = bus
        self._glib = GLib
        self._loop = GLib.MainLoop()
        self._objects = objects
        gatt_manager = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, adapter_path),
            GATT_MANAGER_INTERFACE,
        )
        advertising_manager = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, adapter_path),
            ADVERTISEMENT_MANAGER_INTERFACE,
        )
        self._gatt_manager = gatt_manager
        self._advertising_manager = advertising_manager
        self._signal_match = bus.add_signal_receiver(
            self._properties_changed,
            bus_name=BLUEZ_SERVICE,
            dbus_interface=PROPERTIES_INTERFACE,
            signal_name="PropertiesChanged",
            path_keyword="path",
        )

    def serve(self, on_ready: Callable[[], None] | None = None) -> None:
        if self._loop is None or not self._objects:
            raise RuntimeError("BLE gateway is not initialized")
        if self._registration_started:
            raise RuntimeError("BLE gateway registration already started")
        self._registration_started = True
        if self._shutdown_requested:
            return
        application, advertisement = self._objects[0], self._objects[-1]
        registration_error: list[BaseException] = []
        timeout_source = [None]

        def fail(exc) -> None:
            self._registration_pending = False
            if not registration_error:
                registration_error.append(exc)
            self._loop.quit()

        def timed_out() -> bool:
            timeout_source[0] = None
            fail(RuntimeError("BlueZ GATT registration timed out"))
            return False

        def advertisement_registered() -> None:
            self._registration_pending = False
            self._advertisement_registered = True
            if timeout_source[0] is not None:
                self._glib.source_remove(timeout_source[0])
                timeout_source[0] = None
            if self._shutdown_requested or self._closing:
                self._loop.quit()
                return
            try:
                if on_ready is not None:
                    on_ready()
            except BaseException as exc:
                fail(exc)

        def application_registered() -> None:
            self._application_registered = True
            if self._shutdown_requested or self._closing:
                self._registration_pending = False
                self._loop.quit()
                return
            try:
                self._advertising_manager.RegisterAdvertisement(
                    advertisement.object_path,
                    {},
                    reply_handler=advertisement_registered,
                    error_handler=fail,
                )
            except BaseException as exc:
                fail(exc)

        timeout_source[0] = self._glib.timeout_add_seconds(
            REGISTRATION_TIMEOUT_SECONDS,
            timed_out,
        )
        self._registration_pending = True
        try:
            self._gatt_manager.RegisterApplication(
                application.object_path,
                {},
                reply_handler=application_registered,
                error_handler=fail,
            )
        except BaseException as exc:
            fail(exc)
        if not registration_error:
            self._loop.run()
        if timeout_source[0] is not None:
            self._glib.source_remove(timeout_source[0])
        if self._shutdown_requested:
            assert not self._registration_pending, (
                "BLE registration must settle before shutdown cleanup"
            )
        if registration_error:
            raise RuntimeError("BlueZ could not register the BASS BLE service") from (
                registration_error[0]
            )

    def request_shutdown(self) -> None:
        """Ask the owning GLib loop to stop without raising across a callback."""

        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        if (
            self._closing
            or self._registration_pending
            or self._glib is None
            or self._shutdown_source_id is not None
        ):
            return
        self._shutdown_source_id = self._glib.idle_add(self._finish_shutdown_request)

    def _finish_shutdown_request(self) -> bool:
        self._shutdown_source_id = None
        if not self._registration_pending and self._loop is not None:
            self._loop.quit()
        return False

    def close(self) -> None:
        self._closing = True
        self._shutdown_requested = True
        loop = self._loop
        if loop is not None:
            loop.quit()
        shutdown_source_id = self._shutdown_source_id
        self._shutdown_source_id = None
        if shutdown_source_id is not None and self._glib is not None:
            with suppress(Exception):
                self._glib.source_remove(shutdown_source_id)
        for device in tuple(self._pending_writes):
            self._cancel_pending_write(device, "BLE gateway is stopping")
        if self._signal_match is not None:
            self._signal_match.remove()
            self._signal_match = None
        if self._objects:
            self._unregister(
                self._gatt_manager,
                self._advertising_manager,
                self._objects[0],
                self._objects[-1],
            )
            for item in reversed(self._objects):
                item.remove_from_connection()
            self._objects.clear()
        self._loop = None
        self._bluez_owner = None
        self._gateway.close()

    def _properties_changed(self, interface, changed, _invalidated, *, path=None):
        if (
            interface == DEVICE_INTERFACE
            and path is not None
            and "Connected" in changed
            and not bool(changed["Connected"])
        ):
            device = str(path)
            self._cancel_pending_write(device, "BLE peer disconnected")
            self._gateway.disconnect(device)

    def _write_value_async(
        self,
        *,
        device: str,
        mtu: int,
        value: bytes,
        reply_handler: Callable[[], None],
        error_handler: Callable[[Exception], None],
        make_error: Callable[[str], Exception],
    ) -> None:
        if device in self._pending_writes:
            error_handler(make_error("another GATT write is pending for this peer"))
            return
        token = object()
        self._pending_writes[device] = (token, error_handler, make_error)
        generation = self._gateway.generation(device)
        deadline = time.monotonic() + CALLBACK_TIMEOUT_SECONDS

        def fail(message: str, *, poison: bool = False) -> None:
            current = self._pending_writes.get(device)
            if current is None or current[0] is not token:
                return
            self._pending_writes.pop(device, None)
            if poison and generation is not None:
                self._gateway.poison_if_generation(
                    device,
                    generation,
                    ErrorCode.TIMEOUT,
                )
            error_handler(make_error(message))

        def attempt() -> bool:
            current = self._pending_writes.get(device)
            if current is None or current[0] is not token:
                return False
            if generation is not None and self._gateway.generation(device) != generation:
                fail("BLE peer generation changed")
                return False
            try:
                self._gateway.write_frame(device, mtu, value)
            except Backpressure:
                if time.monotonic() >= deadline:
                    fail("GATT write backpressure timed out", poison=True)
                else:
                    self._glib.timeout_add(100, attempt)
                return False
            except Exception as exc:
                fail(f"GATT write failed: {exc}")
                return False
            self._pending_writes.pop(device, None)
            reply_handler()
            return False

        attempt()

    def _cancel_pending_write(self, device: str, message: str) -> None:
        pending = self._pending_writes.pop(device, None)
        if pending is not None:
            pending[1](pending[2](message))

    def _unregister(self, gatt_manager, advertising_manager, application, advertisement):
        if self._glib is None or self._loop is None:
            return
        if not self._advertisement_registered and not self._application_registered:
            return
        cleanup_loop = self._glib.MainLoop()
        finished = [False]

        def finish_application(*_args) -> None:
            self._application_registered = False
            finished[0] = True
            cleanup_loop.quit()

        def unregister_application(*_args) -> None:
            self._advertisement_registered = False
            if not self._application_registered or gatt_manager is None:
                finish_application()
                return
            try:
                gatt_manager.UnregisterApplication(
                    application.object_path,
                    reply_handler=finish_application,
                    error_handler=finish_application,
                )
            except Exception:
                finish_application()

        if self._advertisement_registered and advertising_manager is not None:
            try:
                advertising_manager.UnregisterAdvertisement(
                    advertisement.object_path,
                    reply_handler=unregister_application,
                    error_handler=unregister_application,
                )
            except Exception:
                unregister_application()
        else:
            unregister_application()

        if not finished[0]:
            timeout_source = self._glib.timeout_add_seconds(2, cleanup_loop.quit)
            cleanup_loop.run()
            with suppress(Exception):
                self._glib.source_remove(timeout_source)
        self._advertisement_registered = False
        self._application_registered = False

    def _build_objects(self, dbus, service, bus) -> list[object]:
        gateway = self._gateway
        facade = self

        class InvalidArguments(dbus.exceptions.DBusException):
            _dbus_error_name = "org.bluez.Error.InvalidArguments"

        class InvalidOffset(dbus.exceptions.DBusException):
            _dbus_error_name = "org.bluez.Error.InvalidOffset"

        class Failed(dbus.exceptions.DBusException):
            _dbus_error_name = "org.bluez.Error.Failed"

        def authorize_bluez(sender) -> None:
            if sender is None or str(sender) != facade._bluez_owner:
                raise Failed("characteristic methods may only be called by BlueZ")

        def request_context(options) -> tuple[str, int]:
            try:
                offset = int(options.get("offset", 0))
                device = str(options["device"])
                mtu = int(options["mtu"])
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidArguments("device and mtu are required") from exc
            if offset != 0:
                raise InvalidOffset("offset reads and writes are unsupported")
            if not device.startswith("/org/bluez/"):
                raise InvalidArguments("invalid BlueZ device path")
            try:
                tls_payload_capacity(mtu)
            except ValueError as exc:
                raise InvalidArguments(str(exc)) from exc
            return device, mtu

        def byte_array(value: bytes):
            return dbus.Array((dbus.Byte(item) for item in value), signature="y")

        class PropertiesObject(service.Object):
            def __init__(self, path: str, interface: str, values: Callable[[], dict]):
                super().__init__(bus, path)
                self.object_path = dbus.ObjectPath(path)
                self.interface = interface
                self._values = values

            @service.method(
                PROPERTIES_INTERFACE,
                in_signature="s",
                out_signature="a{sv}",
            )
            def GetAll(self, interface):
                if interface != self.interface:
                    raise InvalidArguments("unknown interface")
                return self._values()

            @service.method(
                PROPERTIES_INTERFACE,
                in_signature="ss",
                out_signature="v",
            )
            def Get(self, interface, name):
                values = self.GetAll(interface)
                if name not in values:
                    raise InvalidArguments("unknown property")
                return values[name]

            @service.method(
                PROPERTIES_INTERFACE,
                in_signature="ssv",
                out_signature="",
            )
            def Set(self, _interface, _name, _value):
                raise InvalidArguments("properties are read-only")

        class ProtocolInfoCharacteristic(PropertiesObject):
            def __init__(self):
                super().__init__(
                    PROTOCOL_INFO_PATH,
                    GATT_CHARACTERISTIC_INTERFACE,
                    lambda: {
                        "UUID": dbus.String(PROTOCOL_INFO_UUID),
                        "Service": dbus.ObjectPath(SERVICE_PATH),
                        "Flags": dbus.Array(("read",), signature="s"),
                    },
                )

            @service.method(
                GATT_CHARACTERISTIC_INTERFACE,
                in_signature="a{sv}",
                out_signature="ay",
                sender_keyword="sender",
            )
            def ReadValue(self, options, sender=None):
                authorize_bluez(sender)
                request_context(options)
                return byte_array(PROTOCOL_INFO)

        class TunnelCharacteristic(PropertiesObject):
            def __init__(self):
                super().__init__(
                    TUNNEL_PATH,
                    GATT_CHARACTERISTIC_INTERFACE,
                    lambda: {
                        "UUID": dbus.String(TUNNEL_UUID),
                        "Service": dbus.ObjectPath(SERVICE_PATH),
                        "Flags": dbus.Array(("read", "write"), signature="s"),
                    },
                )

            @service.method(
                GATT_CHARACTERISTIC_INTERFACE,
                in_signature="a{sv}",
                out_signature="ay",
                sender_keyword="sender",
            )
            def ReadValue(self, options, sender=None):
                authorize_bluez(sender)
                device, mtu = request_context(options)
                return byte_array(gateway.read_frame(device, mtu))

            @service.method(
                GATT_CHARACTERISTIC_INTERFACE,
                in_signature="aya{sv}",
                out_signature="",
                async_callbacks=("reply_handler", "error_handler"),
                sender_keyword="sender",
            )
            def WriteValue(
                self,
                value,
                options,
                reply_handler,
                error_handler,
                sender=None,
            ):
                try:
                    authorize_bluez(sender)
                    device, mtu = request_context(options)
                    if "type" in options and str(options["type"]) != "request":
                        raise InvalidArguments("write-without-response is unsupported")
                except dbus.exceptions.DBusException as exc:
                    error_handler(exc)
                    return
                facade._write_value_async(
                    device=device,
                    mtu=mtu,
                    value=bytes(value),
                    reply_handler=reply_handler,
                    error_handler=error_handler,
                    make_error=Failed,
                )

        service_object = PropertiesObject(
            SERVICE_PATH,
            GATT_SERVICE_INTERFACE,
            lambda: {
                "UUID": dbus.String(SERVICE_UUID),
                "Primary": dbus.Boolean(True),
                "Includes": dbus.Array((), signature="o"),
            },
        )
        protocol_info = ProtocolInfoCharacteristic()
        tunnel = TunnelCharacteristic()

        managed_objects = {
            service_object.object_path: {
                GATT_SERVICE_INTERFACE: service_object._values(),
            },
            protocol_info.object_path: {
                GATT_CHARACTERISTIC_INTERFACE: protocol_info._values(),
            },
            tunnel.object_path: {
                GATT_CHARACTERISTIC_INTERFACE: tunnel._values(),
            },
        }

        class Application(service.Object):
            def __init__(self):
                super().__init__(bus, APPLICATION_PATH)
                self.object_path = dbus.ObjectPath(APPLICATION_PATH)

            @service.method(
                OBJECT_MANAGER_INTERFACE,
                in_signature="",
                out_signature="a{oa{sa{sv}}}",
            )
            def GetManagedObjects(self):
                return managed_objects

        class Advertisement(PropertiesObject):
            def __init__(self):
                super().__init__(
                    ADVERTISEMENT_PATH,
                    ADVERTISEMENT_INTERFACE,
                    lambda: {
                        "Type": dbus.String("peripheral"),
                        "ServiceUUIDs": dbus.Array((SERVICE_UUID,), signature="s"),
                        "LocalName": dbus.String(self_local_name),
                        "Discoverable": dbus.Boolean(True),
                    },
                )

            @service.method(
                ADVERTISEMENT_INTERFACE,
                in_signature="",
                out_signature="",
            )
            def Release(self):
                return None

        self_local_name = self._local_name
        application = Application()
        advertisement = Advertisement()
        return [application, service_object, protocol_info, tunnel, advertisement]
