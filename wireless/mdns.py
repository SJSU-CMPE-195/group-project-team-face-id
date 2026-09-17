"""Private-LAN IPv4 selection and DNS-SD advertisement for BASS."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import threading
from typing import Callable, Iterable


SERVICE_TYPE = "_bass._tcp.local."
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
VIRTUAL_INTERFACE_PREFIXES = (
    "br-",
    "docker",
    "nordlynx",
    "tap",
    "tun",
    "veth",
    "virbr",
    "vmnet",
    "wg",
)


class MdnsError(RuntimeError):
    """The DNS-SD service cannot be advertised safely."""


def _is_private_lan_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.version == 4 and any(address in network for network in PRIVATE_NETWORKS)


def _is_local_address(value: str) -> bool:
    if not _is_private_lan_address(value):
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.bind((value, 0))
    except OSError:
        return False
    return True


def _windows_physical_addresses() -> list[str]:
    command = (
        "Get-NetIPConfiguration | Where-Object { "
        "$_.NetAdapter.Status -eq 'Up' -and $_.NetAdapter.HardwareInterface -eq $true "
        "} | ForEach-Object { $_.IPv4Address.IPAddress } | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        parsed = json.loads(completed.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    if isinstance(parsed, str):
        return [parsed]
    return [value for value in parsed if isinstance(value, str)]


def _linux_physical_addresses() -> list[str]:
    try:
        completed = subprocess.run(
            ["ip", "-j", "-4", "address", "show", "up"],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
        interfaces = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []

    addresses: list[str] = []
    for interface in interfaces:
        interface_name = str(interface.get("ifname", "")).lower()
        is_virtual = interface_name.startswith(VIRTUAL_INTERFACE_PREFIXES)
        if is_virtual:
            continue
        for address_info in interface.get("addr_info", []):
            if address_info.get("family") == "inet":
                addresses.append(str(address_info.get("local", "")))
    return addresses


def _socket_addresses() -> list[str]:
    try:
        records = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return [record[4][0] for record in records]


def discover_lan_addresses(overrides: Iterable[str] = ()) -> tuple[str, ...]:
    requested = [value.strip() for value in overrides if value.strip()]
    if requested:
        invalid = [value for value in requested if not _is_local_address(value)]
        if invalid:
            raise MdnsError(
                "advertise addresses must be assigned RFC1918 IPv4 addresses: "
                + ", ".join(invalid)
            )
        return tuple(sorted(set(requested)))

    candidates = (
        _windows_physical_addresses()
        if os.name == "nt"
        else _linux_physical_addresses()
    )
    if not candidates:
        candidates = _socket_addresses()
    return tuple(sorted({value for value in candidates if _is_local_address(value)}))


class MdnsAdvertiser:
    def __init__(
        self,
        *,
        device_id: str,
        port: int,
        address_provider: Callable[[], tuple[str, ...]],
        refresh_seconds: float = 10.0,
    ):
        self._device_id = device_id
        self._port = port
        self._address_provider = address_provider
        self._refresh_seconds = max(5.0, refresh_seconds)
        self._addresses: tuple[str, ...] = ()
        self._zeroconf = None
        self._service_info = None
        self._stop = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None

    def _service(self, addresses: tuple[str, ...]):
        from zeroconf import ServiceInfo

        instance_name = f"BASS-{self._device_id}.{SERVICE_TYPE}"
        host_name = f"bass-{self._device_id}.local."
        return ServiceInfo(
            type_=SERVICE_TYPE,
            name=instance_name,
            addresses=[socket.inet_aton(address) for address in addresses],
            port=self._port,
            properties={
                b"device_id": self._device_id.encode("ascii"),
                b"protocol_version": b"1",
            },
            server=host_name,
        )

    def _register(self, addresses: tuple[str, ...]) -> None:
        from zeroconf import IPVersion, Zeroconf

        zeroconf = Zeroconf(interfaces=list(addresses), ip_version=IPVersion.V4Only)
        service_info = self._service(addresses)
        try:
            zeroconf.register_service(service_info, allow_name_change=False)
        except Exception as exc:
            zeroconf.close()
            raise MdnsError(
                "mDNS registration failed; another host may be using this device identity: "
                f"{exc}"
            ) from exc
        self._zeroconf = zeroconf
        self._service_info = service_info
        self._addresses = addresses

    def _unregister(self) -> None:
        zeroconf = self._zeroconf
        service_info = self._service_info
        self._zeroconf = None
        self._service_info = None
        self._addresses = ()
        if zeroconf is None:
            return
        try:
            if service_info is not None:
                zeroconf.unregister_service(service_info)
        except Exception as exc:
            print(f"WARNING: mDNS unregister failed: {exc}", flush=True)
        finally:
            zeroconf.close()

    def start(self) -> tuple[str, ...]:
        addresses = self._address_provider()
        if not addresses:
            raise MdnsError(
                "no physical RFC1918 LAN address is available; connect Wi-Fi/Ethernet "
                "or set BASS_ADVERTISE_ADDRESS"
            )
        with self._state_lock:
            self._register(addresses)
        self._thread = threading.Thread(
            target=self._watch_addresses,
            name="bass-mdns-address-watch",
            daemon=True,
        )
        self._thread.start()
        return addresses

    def _watch_addresses(self) -> None:
        while not self._stop.wait(self._refresh_seconds):
            try:
                addresses = self._address_provider()
                if self._stop.is_set():
                    return
                with self._state_lock:
                    if self._stop.is_set() or addresses == self._addresses:
                        continue
                    self._unregister()
                    if addresses:
                        self._register(addresses)
                        print(
                            "mDNS addresses updated: " + ", ".join(addresses),
                            flush=True,
                        )
                    else:
                        print("mDNS paused: no private LAN address", flush=True)
            except Exception as exc:
                print(f"WARNING: mDNS refresh failed: {exc}", flush=True)

    def close(self) -> None:
        self._stop.set()
        with self._state_lock:
            self._unregister()
        if self._thread is not None:
            self._thread.join(timeout=10)
