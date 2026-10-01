"""Explicit, serialized network access for the recorder's external requests."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import requests
from urllib.parse import urlsplit

_guard = threading.RLock()
_local = threading.local()


def managed_proxy_settings():
    unit = os.environ.get("PROXY_SERVICE_NAME", "").strip()
    endpoint = os.environ.get("PROXY_URL", "").strip() or os.environ.get("GOOGLE_HTTP_PROXY", "").strip()
    lock_file = Path(os.environ.get("PROXY_LOCK_FILE") or "/run/lock/recorder-proxy.lock")
    if os.name != "posix":
        raise RuntimeError("Managed proxy mode requires Linux; use a fixed proxy on this platform.")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit) or not endpoint:
        raise RuntimeError("Configure your own PROXY_SERVICE_NAME and PROXY_URL before enabling managed proxy mode.")
    try:
        proxy = urlsplit(endpoint)
        port = proxy.port or (443 if proxy.scheme == "https" else 80)
    except ValueError:
        raise RuntimeError("The managed proxy URL is invalid.") from None
    if proxy.scheme not in ("http", "https") or not proxy.hostname or not lock_file.is_absolute():
        raise RuntimeError("Use an HTTP(S) proxy URL and an absolute PROXY_LOCK_FILE path.")
    return unit, lock_file, proxy.hostname, port


@contextmanager
def network_access():
    if os.environ.get("PROXY_MANAGED", "0") != "1":
        yield
        return
    unit, lock_file, proxy_host, proxy_port = managed_proxy_settings()
    with _guard:
        if getattr(_local, "depth", 0):
            _local.depth += 1
            try:
                yield
            finally:
                _local.depth -= 1
            return
        import fcntl
        with open(lock_file, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _local.depth = 1
            started = False
            try:
                result = subprocess.run(
                    ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", unit],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25,
                )
                if result.returncode:
                    raise RuntimeError("無法啟動網路連線，請聯絡管理員。")
                started = True
                deadline = time.monotonic() + 12
                while True:
                    try:
                        with socket.create_connection((proxy_host, proxy_port), timeout=.5):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("網路連線尚未就緒，請稍後重試。")
                        time.sleep(.15)
                yield
            finally:
                _local.depth = 0
                if started:
                    result = subprocess.run(
                        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", unit],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25,
                    )
                    if result.returncode:
                        raise RuntimeError("網路連線未能正常關閉，請聯絡管理員。")


class ManagedProxySession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False
        endpoint = os.environ.get("PROXY_URL", "").strip()
        if endpoint:
            self.proxies.update(http=endpoint, https=endpoint)

    def request(self, method, url, **kwargs):
        direct_hosts = {host.strip().lower() for host in os.environ.get("DIRECT_API_HOSTS", "").split(",") if host.strip()}
        if (urlsplit(url).hostname or "").lower() in direct_hosts:
            kwargs['proxies'] = {'http': None, 'https': None}
            kwargs.setdefault('timeout', (15, 180))
            return super().request(method, url, **kwargs)
        if kwargs.get("stream") and os.environ.get("PROXY_MANAGED") == "1" and not getattr(_local, "depth", 0):
            raise ValueError("Streaming responses require an enclosing network_access context.")
        kwargs.setdefault("timeout", (15, 180))
        with network_access():
            return super().request(method, url, **kwargs)
