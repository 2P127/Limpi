from __future__ import annotations

import asyncio
import logging
import socket
import sys
from datetime import datetime, timezone

import aiohttp

from .bot_constants import ASYNCIO_RESET_LOG_COOLDOWN_SECONDS
from .clients.x_client import XClientError

LOGGER = logging.getLogger(__name__)
_last_asyncio_reset_log_at = 0.0

def _install_windows_selector_event_loop_policy() -> None:
    if sys.platform != "win32":
        return
    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        return
    try:
        asyncio.set_event_loop_policy(policy_factory())
    except RuntimeError:
        pass


def _is_asyncio_transport_reset_context(context: dict[str, object]) -> bool:
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    if getattr(exc, "winerror", None) != 10054:
        return False
    message = str(context.get("message") or "")
    handle = str(context.get("handle") or "")
    return (
        "Exception in callback" in message
        and "_ProactorBasePipeTransport._call_connection_lost" in handle
    )


def _install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    previous_handler = loop.get_exception_handler()

    def _handle_exception(
        current_loop: asyncio.AbstractEventLoop,
        context: dict[str, object],
    ) -> None:
        global _last_asyncio_reset_log_at
        if _is_asyncio_transport_reset_context(context):
            now = current_loop.time()
            if now - _last_asyncio_reset_log_at >= ASYNCIO_RESET_LOG_COOLDOWN_SECONDS:
                _last_asyncio_reset_log_at = now
                LOGGER.warning(
                    "네트워크 연결이 원격에서 끊겨 비동기 전송을 정리했습니다 "
                    "(WinError 10054)."
                )
            exc = context.get("exception")
            LOGGER.debug(
                "asyncio 연결 종료 콜백 오류 전체 정보: %s",
                context,
                exc_info=(
                    (type(exc), exc, exc.__traceback__)
                    if isinstance(exc, BaseException)
                    else None
                ),
            )
            return

        if previous_handler is not None:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(_handle_exception)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _internet_error_detail(exc: BaseException) -> tuple[str, str] | None:
    dns_error_type = getattr(aiohttp, "ClientConnectorDNSError", None)
    for item in _exception_chain(exc):
        if dns_error_type is not None and isinstance(item, dns_error_type):
            return "DNS 확인 실패", type(item).__name__
        if isinstance(item, socket.gaierror):
            return "DNS 확인 실패", type(item).__name__
        if isinstance(item, (aiohttp.ServerTimeoutError, asyncio.TimeoutError, TimeoutError)):
            return "요청 시간 초과", type(item).__name__
        if isinstance(item, aiohttp.ClientResponseError):
            return "HTTP 응답 오류", type(item).__name__
        if isinstance(item, aiohttp.ClientConnectorError):
            return "서버 연결 실패", type(item).__name__
        if isinstance(item, aiohttp.ClientConnectionError):
            return "네트워크 연결 오류", type(item).__name__
        if isinstance(item, aiohttp.ClientError):
            return "네트워크 요청 오류", type(item).__name__
        if isinstance(item, ConnectionError):
            return "네트워크 연결 오류", type(item).__name__

    message = str(exc)
    if any(
        marker in message
        for marker in (
            "Cannot write to closing transport",
            "closing transport",
            "WinError 10054",
        )
    ):
        return "네트워크 연결 오류", type(exc).__name__
    if isinstance(exc, XClientError) and any(
        marker in message
        for marker in (
            "네트워크",
            "백오프",
            "Cannot connect",
            "getaddrinfo",
            "Name or service not known",
            "Temporary failure in name resolution",
            "timed out",
            "Timeout",
        )
    ):
        return "X API 네트워크 오류", type(exc).__name__
    return None


def _is_internet_exception(exc: BaseException) -> bool:
    return _internet_error_detail(exc) is not None


def _log_internet_exception(
    message: str,
    exc: BaseException,
    *,
    level: int = logging.WARNING,
) -> None:
    reason, error_type = _internet_error_detail(exc) or ("인터넷 관련 오류", type(exc).__name__)
    LOGGER.log(level, "%s: %s (%s)", message, reason, error_type)
    LOGGER.debug(
        "%s 전체 오류: %s",
        message,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

__all__ = [
    "_install_windows_selector_event_loop_policy",
    "_is_asyncio_transport_reset_context",
    "_install_asyncio_exception_handler",
    "_exception_chain",
    "_internet_error_detail",
    "_is_internet_exception",
    "_log_internet_exception",
]
