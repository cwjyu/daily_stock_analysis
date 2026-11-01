# -*- coding: utf-8 -*-
"""Proxy bootstrap for data sources that ignore the OS proxy settings.

``yfinance`` 1.x reaches Yahoo through ``curl_cffi`` (libcurl). libcurl only
honours the ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``ALL_PROXY`` environment
variables, while browsers (and ``requests``) additionally read the Windows
system proxy from the registry. A desktop behind a local proxy such as
Clash / V2Ray therefore keeps failing on Yahoo even though the system proxy
switch is on, while the very same code works on a server with direct access.

``apply_yfinance_proxy()`` bridges that gap:

1. an explicit ``YFINANCE_PROXY`` (or ``HTTPS_PROXY`` / ``HTTP_PROXY`` /
   ``ALL_PROXY``) environment value always wins;
2. otherwise the Windows system proxy is auto-detected, and only used when the
   endpoint is actually listening (a stale registry entry pointing at a dead
   port must not break working direct connections);
3. the result is handed to ``yfinance.set_config(proxy=...)`` once per process.

Set ``DISABLE_SYSTEM_PROXY_AUTODETECT=true`` to skip step 2 entirely (useful
when the machine's registry proxy belongs to an unrelated tool).
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Explicit override first, then the conventional curl/libcurl variables.
_PROXY_ENV_KEYS = (
    "YFINANCE_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)

_DISABLE_AUTODETECT_ENV = "DISABLE_SYSTEM_PROXY_AUTODETECT"
_DEFAULT_PROXY_SCHEME = "http"
_PROXY_CHECK_TIMEOUT_SECONDS = 1.0

# Process-wide memo: the proxy is resolved and applied at most once.
_applied = False
_resolved_proxy: Optional[str] = None


def _parse_endpoint(value: str) -> Optional[Tuple[str, int]]:
    """Return ``(host, port)`` for a proxy value, or ``None`` when unparsable."""
    text = (value or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"{_DEFAULT_PROXY_SCHEME}://{text}"
    parts = urlsplit(text)
    host = parts.hostname
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = 80 if parts.scheme == "http" else 443
    return host, port


def normalize_proxy_url(value: str) -> Optional[str]:
    """Normalize a proxy value (``127.0.0.1:7897`` / ``http=...;https=...``)."""
    text = (value or "").strip()
    if not text:
        return None
    # WinINET may carry a per-protocol list, e.g. "http=1.2.3.4:80;https=1.2.3.4:443".
    if "=" in text:
        entries = {}
        for chunk in text.split(";"):
            key, _, item = chunk.partition("=")
            if item:
                entries[key.strip().lower()] = item.strip()
        text = entries.get("https") or entries.get("http") or next(
            iter(entries.values()), ""
        )
    endpoint = _parse_endpoint(text)
    if endpoint is None:
        return None
    host, port = endpoint
    scheme = urlsplit(
        text if "://" in text else f"{_DEFAULT_PROXY_SCHEME}://{text}"
    ).scheme
    return f"{scheme}://{host}:{port}"


def _endpoint_is_reachable(host: str, port: int, timeout: float = _PROXY_CHECK_TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_windows_proxy() -> Optional[str]:
    """Read the WinINET proxy (``HKCU\\...\\Internet Settings``) on Windows."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - only relevant on Windows
        return None

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    return normalize_proxy_url(str(server or ""))


def _autodetect_disabled() -> bool:
    return (os.getenv(_DISABLE_AUTODETECT_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_proxy() -> Optional[str]:
    """Return the proxy URL that should be used for Yahoo requests, if any."""
    for env_key in _PROXY_ENV_KEYS:
        candidate = normalize_proxy_url(os.getenv(env_key) or "")
        if candidate:
            return candidate
    if _autodetect_disabled():
        return None

    detected = _read_windows_proxy()
    if not detected:
        return None
    endpoint = _parse_endpoint(detected)
    if endpoint is None or not _endpoint_is_reachable(*endpoint):
        logger.debug(
            "System proxy %s is configured but not reachable; using direct connection",
            detected,
        )
        return None
    return detected


def apply_yfinance_proxy(force: bool = False) -> Optional[str]:
    """Apply the resolved proxy to ``yfinance`` once per process.

    Args:
        force: re-resolve and re-apply even when the proxy was already applied.

    Returns:
        The proxy URL that was applied, or ``None`` when Yahoo is reached
        directly (server deployments, or no live proxy detected).
    """
    global _applied, _resolved_proxy
    if _applied and not force:
        return _resolved_proxy

    proxy = resolve_proxy()
    _applied = True
    _resolved_proxy = proxy

    try:
        if proxy:
            # yfinance 1.x talks to Yahoo through curl_cffi (libcurl), which
            # reads these variables. The alternative yf.config.network.proxy
            # switch is broken in some 1.2 builds (raises TypeError), so the
            # environment is the only reliable channel.
            for env_key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
                os.environ.setdefault(env_key, proxy)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to export the proxy environment variables: %s", exc)
        return proxy

    if proxy:
        logger.info("Yahoo Finance requests will use proxy %s", proxy)
    else:
        logger.debug("Yahoo Finance requests will use a direct connection")
    return proxy


def import_yfinance():
    """Import ``yfinance`` with the proxy already exported to the environment."""
    apply_yfinance_proxy()
    import yfinance as yf  # noqa: WPS433 - intentionally deferred
    return yf


def reset_proxy_state() -> None:
    """Clear the memoized state (test helper)."""
    global _applied, _resolved_proxy
    _applied = False
    _resolved_proxy = None
