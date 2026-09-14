"""PT 站点定时访问业务内核：与 V2 插件保持并行逻辑，供 V3 适配器委托调用。

本模块不导入任何 ``app.*`` 宿主符号，仅依赖标准库与 apscheduler，
便于单元测试、生产内置 V2 插件的影子恢复以及跨代复用。
本内核与 ``plugins.v2/ptsiteopener`` 的业务行为保持一致；任何一端
修改业务规则时必须同步另一端，并在两侧 ``tests/`` 下保留覆盖。
"""

from __future__ import annotations

import json
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from apscheduler.triggers.cron import CronTrigger


DEFAULT_CDP_URL = ""
CDP_URL_PLACEHOLDER = "例如：http://127.0.0.1:16002/json/version"
DEFAULT_SCHEDULE = "0 */6 * * *"
DEFAULT_TTL_MINUTES = 5
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "cdp_url": DEFAULT_CDP_URL,
    "schedule": DEFAULT_SCHEDULE,
    "ttl_minutes": DEFAULT_TTL_MINUTES,
    "notify_enabled": False,
    "reuse_site_cookie": True,
    "manual_site_cookies": "",
    "site_mode": "all",
    "site_ids": [],
}


def parse_site_cookie(cookie: Any) -> List[Tuple[str, str]]:
    """Parse MoviePilot's semicolon-separated site cookie string."""
    if not isinstance(cookie, str):
        return []

    pairs = []
    for segment in cookie.split(";"):
        name, separator, value = segment.strip().partition("=")
        if not separator or not name:
            continue
        pairs.append((name.strip(), value.strip()))
    return pairs


def parse_manual_site_cookies(raw_config: Any, logger: Any = None) -> Dict[str, str]:
    """Parse one manual Cookie header per site name without exposing values."""
    if not isinstance(raw_config, str):
        return {}

    manual_cookies: Dict[str, str] = {}
    for line_number, raw_line in enumerate(raw_config.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        site_name, separator, cookie = line.partition(":")
        site_name = site_name.strip()
        cookie = cookie.strip()
        if not separator or not site_name or not parse_site_cookie(cookie):
            if logger is not None:
                logger.warning(f"忽略第 {line_number} 行无效手动站点 Cookie 配置")
            continue
        manual_cookies[site_name] = cookie
    return manual_cookies


def resolve_site_cookie_pairs(
    site: Any,
    manual_cookies: Dict[str, str],
    reuse_site_cookie: bool,
) -> List[Tuple[str, str]]:
    """Choose managed Cookie first, then named manual Cookie as fallback."""
    if not reuse_site_cookie:
        return []

    managed_pairs = parse_site_cookie(getattr(site, "cookie", None))
    if managed_pairs:
        return managed_pairs

    site_name = str(getattr(site, "name", "") or "").strip()
    return parse_site_cookie(manual_cookies.get(site_name))


def select_sites(
    sites: Iterable[Any],
    site_mode: str = "all",
    selected_site_ids: Optional[Iterable[Any]] = None,
) -> List[Any]:
    """Return active, unique HTTP(S) site objects in MoviePilot order."""
    selected = {str(site_id) for site_id in (selected_site_ids or [])}
    selected_sites: List[Any] = []
    seen = set()

    for site in sites or []:
        if not getattr(site, "is_active", False):
            continue
        if site_mode == "selected" and str(getattr(site, "id", "")) not in selected:
            continue

        raw_url = getattr(site, "url", None)
        if not isinstance(raw_url, str):
            continue
        url = raw_url.strip()
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            continue
        if url in seen:
            continue
        seen.add(url)
        selected_sites.append(site)

    return selected_sites


def select_site_urls(
    sites: Iterable[Any],
    site_mode: str = "all",
    selected_site_ids: Optional[Iterable[Any]] = None,
) -> List[str]:
    """Return active, unique HTTP(S) site URLs in MoviePilot order."""
    return [
        getattr(site, "url").strip()
        for site in select_sites(sites, site_mode, selected_site_ids)
    ]


def _sanitize_error(error: Exception, secret_values: Iterable[str]) -> str:
    """Remove cookie values from a CDP error before it reaches logs."""
    message = str(error)
    for secret in secret_values:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message


def resolve_websocket_url(version_info: Dict[str, Any], endpoint_url: str) -> str:
    """Resolve a CDP websocket URL returned by a remote /json/version endpoint."""
    raw_websocket_url = version_info.get("webSocketDebuggerUrl")
    if not isinstance(raw_websocket_url, str) or not raw_websocket_url:
        raise ValueError("CDP version response has no webSocketDebuggerUrl")

    endpoint = urlsplit(endpoint_url)
    websocket = urlsplit(raw_websocket_url)
    if websocket.scheme not in {"ws", "wss"}:
        raise ValueError(f"Unsupported CDP WebSocket protocol: {websocket.scheme}")

    if websocket.hostname in LOOPBACK_HOSTS:
        websocket = websocket._replace(
            scheme="wss" if endpoint.scheme == "https" else "ws",
            netloc=endpoint.netloc,
        )
    return urlunsplit(websocket)


def _validate_cdp_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("cdp_url must be an HTTP or HTTPS URL")


def _fetch_json(url: str, timeout: float = 15) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CDP version response is not an object")
    return payload


class _CdpConnection:
    """Small synchronous CDP command client for one browser websocket."""

    def __init__(self, socket: Any, logger: Any = None):
        self._socket = socket
        self._logger = logger
        self._lock = threading.RLock()
        self._next_id = 0
        self._closed = False

    def send(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("CDP connection is closed")
            self._next_id += 1
            message_id = self._next_id
            message = {
                "id": message_id,
                "method": method,
                "params": params or {},
            }
            if session_id:
                message["sessionId"] = session_id
            self._socket.send(
                json.dumps(message)
            )

            while True:
                raw_message = self._socket.recv()
                if not raw_message:
                    raise RuntimeError("CDP websocket closed before command response")
                message = json.loads(raw_message)
                if message.get("id") != message_id:
                    continue
                if message.get("error"):
                    raise RuntimeError(json.dumps(message["error"], ensure_ascii=False))
                result = message.get("result", {})
                return result if isinstance(result, dict) else {}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._socket.close()
            except Exception as error:
                if self._logger is not None:
                    self._logger.debug(f"关闭 CDP 连接失败：{error}")


def _connect_cdp(endpoint_url: str, logger: Any = None) -> _CdpConnection:
    version_info = _fetch_json(endpoint_url)
    websocket_url = resolve_websocket_url(version_info, endpoint_url)

    import websocket

    socket = websocket.create_connection(
        websocket_url,
        timeout=15,
        enable_multithread=True,
        suppress_origin=True,
    )
    return _CdpConnection(socket, logger=logger)


@dataclass
class _OpenRun:
    cdp: _CdpConnection
    target_ids: List[str] = field(default_factory=list)
    timer: Any = None
    cleaned: bool = False
    lock: Any = field(default_factory=threading.RLock)


class CdpRunner:
    """进程内执行一次打开流程；不绑定宿主插件对象，便于独立测试与影子恢复。

    :param config: 已完成基本校验的插件配置字典。
    :param logger: MoviePilot 日志器（可为 ``None``）。
    :param notify: ``notify(message, level="info", site_names=None)`` 结果回调。
    :param notify_cookie_failure: ``notify_cookie_failure(text)`` Cookie 告警回调。
    :param schedule_cron: 已校验的五段 Cron 表达式（仅用于服务注册回调复用）。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        logger: Any = None,
        notify: Any = None,
        notify_cookie_failure: Any = None,
        schedule_cron: str = DEFAULT_SCHEDULE,
    ):
        self.config = config or {}
        self.logger = logger
        self.notify = notify or (lambda message, level="info", site_names=None: None)
        self.notify_cookie_failure = notify_cookie_failure
        self.schedule_cron = schedule_cron
        self._runs: List[_OpenRun] = []
        self._runs_lock = threading.RLock()

    @property
    def url(self) -> str:
        return str(self.config.get("cdp_url") or DEFAULT_CDP_URL).strip()

    @property
    def ttl_minutes(self) -> int:
        value = self.config.get("ttl_minutes", DEFAULT_TTL_MINUTES)
        try:
            ttl = int(value)
        except (TypeError, ValueError):
            return DEFAULT_TTL_MINUTES
        return max(ttl, 0)

    def registered_service_cron(self) -> Optional[str]:
        """返回可注册为任务调度的 Cron 表达式；无效时返回 ``None``。"""
        try:
            CronTrigger.from_crontab(self.schedule_cron)
        except Exception:
            return None
        return self.schedule_cron

    def _connect_cdp(self) -> _CdpConnection:
        if not self.url:
            raise ValueError("未配置远程 CDP 地址")
        return _connect_cdp(self.url, logger=self.logger)

    def _open_site(
        self,
        cdp: _CdpConnection,
        url: str,
        cookie_pairs: List[Tuple[str, str]],
        run: _OpenRun,
    ) -> Tuple[Optional[str], Optional[str]]:
        if not cookie_pairs:
            target = cdp.send(
                "Target.createTarget",
                {"url": url, "background": True},
            )
            target_id = target.get("targetId") if isinstance(target, dict) else None
            if not target_id:
                raise RuntimeError("CDP did not return targetId")
            return target_id, None

        blank_target_id: Optional[str] = None
        try:
            target = cdp.send(
                "Target.createTarget",
                {"url": "about:blank", "background": True},
            )
            blank_target_id = target.get("targetId") if isinstance(target, dict) else None
            if not blank_target_id:
                raise RuntimeError("CDP did not return targetId")

            attached = cdp.send(
                "Target.attachToTarget",
                {"targetId": blank_target_id, "flatten": True},
            )
            session_id = attached.get("sessionId") if isinstance(attached, dict) else None
            if not session_id:
                raise RuntimeError("CDP did not return target sessionId")
        except Exception as error:
            if blank_target_id:
                try:
                    cdp.send("Target.closeTarget", {"targetId": blank_target_id})
                except Exception:
                    pass
            fallback = cdp.send(
                "Target.createTarget",
                {"url": url, "background": True},
            )
            target_id = fallback.get("targetId") if isinstance(fallback, dict) else None
            if not target_id:
                raise RuntimeError("CDP did not return fallback targetId") from error
            reason = _sanitize_error(error, [value for _, value in cookie_pairs])
            return target_id, reason

        failures = []
        for name, value in cookie_pairs:
            try:
                result = cdp.send(
                    "Network.setCookie",
                    {"name": name, "value": value, "url": url},
                    session_id=session_id,
                )
                if isinstance(result, dict) and result.get("success") is False:
                    raise RuntimeError("CDP rejected cookie")
            except Exception as error:
                failures.append(
                    _sanitize_error(error, [cookie_value for _, cookie_value in cookie_pairs])
                )

        cdp.send(
            "Page.navigate",
            {"url": url},
            session_id=session_id,
        )
        return blank_target_id, "; ".join(dict.fromkeys(failures)) or None

    def _report_cookie_failures(
        self,
        failures: List[Tuple[str, str, str]],
    ) -> None:
        if not failures:
            return

        lines = []
        for site_name, url, reason in failures:
            lines.append(f"{site_name} ({url})：{reason}")
            if self.logger is not None:
                self.logger.warning(f"站点 Cookie 注入失败 {site_name} ({url})：{reason}")

        if not self.config.get("notify_enabled"):
            return
        if self.notify_cookie_failure is None:
            return
        try:
            self.notify_cookie_failure("Cookie 注入失败：\n" + "\n".join(lines))
        except Exception as error:
            if self.logger is not None:
                self.logger.warning(f"发送 PT 站点 Cookie 告警失败：{error}")

    def run(self, targets: List[Dict[str, Any]]) -> List[str]:
        """执行一次打开流程，返回成功打开的站点 URL。

        :param targets: 每项含 ``url``、``site``（展示名）与 ``cookie_pairs``。
        """
        if not targets:
            self.notify("没有可打开的启用站点", level="info", site_names=[])
            return []

        try:
            cdp = self._connect_cdp()
        except Exception as error:
            self.notify(f"连接远程 CDP 失败：{error}", level="error", site_names=[])
            return []

        run = _OpenRun(cdp=cdp)
        with self._runs_lock:
            self._runs.append(run)

        opened_urls: List[str] = []
        opened_site_names: List[str] = []
        cookie_failures: List[Tuple[str, str, str]] = []
        for target in targets:
            url = str(target.get("url", "")).strip()
            site_name = str(target.get("site") or url).strip()
            cookie_pairs = [
                (str(name), str(value))
                for name, value in target.get("cookie_pairs") or []
            ]
            try:
                with run.lock:
                    if run.cleaned:
                        break
                    target_id, cookie_failure = self._open_site(
                        cdp,
                        url,
                        cookie_pairs,
                        run,
                    )
                    if not target_id:
                        raise RuntimeError("CDP did not return targetId")
                    run.target_ids.append(target_id)
                    opened_urls.append(url)
                    opened_site_names.append(site_name)
                    if cookie_failure:
                        cookie_failures.append((site_name, url, cookie_failure))
            except Exception as error:
                if self.logger is not None:
                    self.logger.warning(f"打开站点失败 {url}：{error}")

        self._report_cookie_failures(cookie_failures)

        with run.lock:
            if run.cleaned:
                return opened_urls
            has_targets = bool(run.target_ids)
            if has_targets:
                try:
                    cdp.send("Target.activateTarget", {"targetId": run.target_ids[0]})
                except Exception as error:
                    if self.logger is not None:
                        self.logger.warning(f"激活首个站点标签页失败：{error}")

                try:
                    run.timer = threading.Timer(
                        self.ttl_minutes * 60,
                        self._cleanup_run,
                        args=(run,),
                    )
                    run.timer.daemon = True
                    run.timer.start()
                except Exception as error:
                    if self.logger is not None:
                        self.logger.warning(f"启动标签页关闭计时器失败：{error}")
                    self._cleanup_run(run)
                    self.notify(
                        f"打开站点后启动关闭计时器失败：{error}",
                        level="error",
                        site_names=opened_site_names,
                    )
                    return opened_urls

        if has_targets:
            self.notify(
                f"已打开 {len(opened_urls)} 个站点，{self.ttl_minutes} 分钟后关闭",
                level="info",
                site_names=opened_site_names,
            )
        else:
            self._cleanup_run(run)
            self.notify("没有成功打开站点", level="info", site_names=opened_site_names)

        return opened_urls

    def _cleanup_run(self, run: _OpenRun) -> None:
        with run.lock:
            if run.cleaned:
                return
            run.cleaned = True
            target_ids = list(run.target_ids)
            with self._runs_lock:
                if run in self._runs:
                    self._runs.remove(run)

        if run.timer is not None:
            try:
                run.timer.cancel()
            except Exception:
                pass

        for target_id in target_ids:
            try:
                run.cdp.send("Target.closeTarget", {"targetId": target_id})
            except Exception as error:
                if self.logger is not None:
                    self.logger.warning(f"关闭站点标签页失败 {target_id}：{error}")
        run.cdp.close()

    def cleanup_all(self) -> None:
        """立即关闭本内核创建且尚未到期的全部标签页。"""
        with self._runs_lock:
            runs = list(self._runs)
        for run in runs:
            self._cleanup_run(run)