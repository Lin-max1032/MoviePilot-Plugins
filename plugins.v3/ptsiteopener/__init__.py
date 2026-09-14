"""MoviePilot V3 plugin for opening active PT sites through remote CDP.

宿主契约适配层，业务逻辑委托同目录 ``core.py``（与 ``plugins.v2/ptsiteopener``
并行一致）：

* 站点读取使用 V3 稳定端口 ``app.db.oper.site.SiteOper``（生产 3.0.1 实测）；
* 日志使用 ``app.sdk.logging.logger``；
* 动态 API 路由注册交给宿主生命周期（安装/卸载/配置保存时宿主内部
  ``register_plugin_api``），插件内不再手工调用注册函数；
* ``/run`` 显式声明与宿主一致的 ``response_model``，每次 ``get_api()``
  实时解析真实 ``schemas.Response``，解析失败直接报错，禁止静默降级。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field

from app.db.oper.site import SiteOper
from app.plugins import _PluginBase
from app.schemas.types import NotificationType
from app.sdk.logging import logger

from . import core
from .core import (
    CDP_URL_PLACEHOLDER,
    DEFAULT_CDP_URL,
    DEFAULT_SCHEDULE,
    DEFAULT_TTL_MINUTES,
    CdpRunner,
    parse_manual_site_cookies,
    resolve_site_cookie_pairs,
    select_sites,
    _validate_cdp_url,
)

PLUGIN_ID = "PTSiteOpener"

# ---------------------------------------------------------------------------
# /run 响应模型
# ---------------------------------------------------------------------------
# 信封结构与宿主 ``schemas.Response[T]`` 对齐：顶层 ``success``/``message``，
# V2 兼容尾部字段 ``opened`` 位于 ``data`` 段，同时保留顶层 ``message``
# 供宿主统一错误 Toast 消费。
# ---------------------------------------------------------------------------

_HOST_RESPONSE_MODEL_PATHS = (
    "app.schemas.response.Response",
    "app.schemas.Response",
)


class _RunData(BaseModel):
    """``/run`` 业务数据段。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    message: str = ""
    opened: List[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    """``/run`` 输出信封，供测试与文档对齐结构，实际注册使用宿主模型。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    message: str = ""
    data: Optional[_RunData] = None


def _resolve_plugin_response_model() -> Any:
    """实时解析宿主统一响应模型；无法解析时直接报错，禁止静默降级。"""
    for path in _HOST_RESPONSE_MODEL_PATHS:
        module_part, _, attr = path.rpartition(".")
        try:
            module = importlib.import_module(module_part)
        except Exception:
            continue
        value = getattr(module, attr, None)
        if isinstance(value, type) and issubclass(value, BaseModel):
            return value
    raise RuntimeError(
        "PT站点定时访问：无法解析 MoviePilot 宿主统一响应模型 "
        "（app.schemas.response.Response），/run 接口不可用"
    )


class PTSiteOpener(_PluginBase):
    """按计划打开 MoviePilot 中已启用的 PT 站点。"""

    plugin_name = "PT站点定时访问"
    plugin_desc = "按用户配置的 Cron 计划，通过远程 CDP 定时打开 MoviePilot 中已启用的 PT 站点，复用站点 Cookie，并在保留时间到期后关闭插件创建的标签页。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "2.0.0"
    plugin_author = "Lin-max1032"
    author_url = "https://github.com/Lin-max1032/MoviePilot-Plugins"
    plugin_config_prefix = "ptsiteopener_"
    plugin_order = 50
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = dict(core.DEFAULTS)
        self._runner: Optional[CdpRunner] = None
        self._last_result = "尚未执行"
        self._known_sites: List[Any] = []
        self._known_sites_failed = False

    def init_plugin(self, config: dict = None):
        """读取配置并校验计划任务。"""
        self.stop_service()
        incoming = dict(config or {})
        merged: Dict[str, Any] = {}
        for key, default in core.DEFAULTS.items():
            merged[key] = incoming.get(key, default)
        merged["enabled"] = bool(merged.get("enabled"))
        merged["cdp_url"] = str(merged.get("cdp_url") or DEFAULT_CDP_URL).strip()
        merged["schedule"] = str(merged.get("schedule") or DEFAULT_SCHEDULE).strip()
        merged["ttl_minutes"] = self._coerce_ttl(
            merged.get("ttl_minutes", DEFAULT_TTL_MINUTES)
        )
        merged["notify_enabled"] = bool(merged.get("notify_enabled", False))
        merged["reuse_site_cookie"] = bool(merged.get("reuse_site_cookie", True))
        merged["manual_site_cookies"] = str(merged.get("manual_site_cookies") or "")
        if merged.get("site_mode") not in {"all", "selected"}:
            merged["site_mode"] = "all"
        raw_site_ids = merged.get("site_ids") or []
        if isinstance(raw_site_ids, (str, int)):
            raw_site_ids = [raw_site_ids]
        merged["site_ids"] = [str(site_id) for site_id in raw_site_ids]
        merged["config_error"] = None
        self._config = merged

        try:
            if self._config["cdp_url"]:
                _validate_cdp_url(self._config["cdp_url"])
            CronTrigger.from_crontab(self._config["schedule"])
        except Exception as error:
            self._config["config_error"] = str(error)
            logger.error(f"PT站点自动打开配置无效：{error}")

        self._build_runner()

    @staticmethod
    def _coerce_ttl(value: Any) -> int:
        try:
            ttl_minutes = int(value)
        except (TypeError, ValueError):
            return DEFAULT_TTL_MINUTES
        return max(ttl_minutes, 0)

    def get_state(self) -> bool:
        return bool(self._config.get("enabled") and not self._config.get("config_error"))

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/run",
                "endpoint": self.run_now,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即打开 PT 站点",
                "description": "立即执行一次站点打开任务",
                "response_model": _resolve_plugin_response_model(),
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state():
            return []
        try:
            trigger = CronTrigger.from_crontab(self._config["schedule"])
        except Exception as error:
            logger.error(f"PT站点自动打开 Cron 无效：{error}")
            return []
        return [
            {
                "id": PLUGIN_ID,
                "name": "PT站点自动打开服务",
                "trigger": trigger,
                "func": self.run_once,
                "kwargs": {},
            }
        ]

    def _list_known_sites(self) -> List[Any]:
        """读取启用的 PT 站点；失败时回退到最后一次成功读取的缓存。"""
        if not self._known_sites_failed:
            try:
                sites = SiteOper().list_active() or []
            except Exception as error:
                logger.warning(f"读取 MoviePilot 站点列表失败：{error}")
                self._known_sites_failed = True
            else:
                self._known_sites = list(sites)
                self._known_sites_failed = False
                return list(sites)
        return list(getattr(self, "_known_sites", []) or [])

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        site_items = []
        for site in self._list_known_sites():
            site_id = getattr(site, "id", None)
            if site_id is None:
                continue
            site_items.append(
                {
                    "title": getattr(site, "name", None)
                    or getattr(site, "domain", None)
                    or str(site_id),
                    "value": str(site_id),
                }
            )

        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "props": {"class": "mb-2"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify_enabled",
                                            "label": "开启通知推送",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "reuse_site_cookie",
                                            "label": "复用站点 Cookie",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"class": "mb-2"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cdp_url",
                                            "label": "远程 CDP 地址",
                                            "placeholder": CDP_URL_PLACEHOLDER,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"class": "mb-2"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VCronField",
                                        "props": {
                                            "model": "schedule",
                                            "label": "执行周期",
                                            "placeholder": "五段 Cron 表达式",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "ttl_minutes",
                                            "label": "标签页保留时间（分钟）",
                                            "type": "number",
                                            "min": 0,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"class": "mb-2"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "site_mode",
                                            "label": "站点范围",
                                            "items": [
                                                {"title": "全部启用站点", "value": "all"},
                                                {"title": "指定启用站点", "value": "selected"},
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "site_ids",
                                            "label": "指定站点",
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                            "items": site_items,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "props": {"class": "mb-2"},
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "manual_site_cookies",
                                            "label": "手动站点 Cookie",
                                            "placeholder": "站点名称:Cookie，例如：朱雀:socute=...",
                                            "rows": 4,
                                            "autoGrow": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": self._config.get("enabled", core.DEFAULTS["enabled"]),
            "cdp_url": self._config.get("cdp_url", DEFAULT_CDP_URL),
            "schedule": self._config.get("schedule", DEFAULT_SCHEDULE),
            "ttl_minutes": self._config.get("ttl_minutes", DEFAULT_TTL_MINUTES),
            "notify_enabled": self._config.get("notify_enabled", False),
            "reuse_site_cookie": self._config.get("reuse_site_cookie", True),
            "manual_site_cookies": self._config.get("manual_site_cookies", ""),
            "site_mode": self._config.get("site_mode", "all"),
            "site_ids": self._config.get("site_ids", []),
        }

    def get_page(self) -> List[dict]:
        error_text = self._config.get("config_error")
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info" if self.get_state() else "warning",
                    "variant": "tonal",
                    "text": error_text or self._last_result,
                },
            },
            {
                "component": "VRow",
                "props": {"class": "mt-2"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VBtn",
                                "props": {
                                    "color": "primary",
                                    "variant": "tonal",
                                    "block": True,
                                    "prepend-icon": "mdi-play-circle",
                                },
                                "text": "立即执行",
                                "events": {
                                    "click": {
                                        "api": f"plugin/{self.__class__.__name__}/run",
                                        "method": "post",
                                    }
                                },
                            }
                        ],
                    }
                ],
            },
        ]

    def _notify_result(
        self,
        message: str,
        level: str = "info",
        site_names: Optional[Iterable[str]] = None,
    ) -> None:
        """记录最近结果并按通知开关推送。"""
        self._last_result = message
        getattr(logger, level)(message)
        if not self._config.get("notify_enabled"):
            return

        names = [str(site).strip() for site in (site_names or []) if str(site).strip()]
        text = message
        if names:
            text = f"{message}\n打开站点：\n" + "\n".join(names)
        try:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=self.plugin_name,
                text=text,
            )
        except Exception as error:
            logger.warning(f"发送 PT 站点执行通知失败：{error}")

    def _notify_cookie_failure(self, text: str) -> None:
        """Cookie 注入告警推送。"""
        try:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=f"{self.plugin_name} Cookie 告警",
                text=text,
            )
        except Exception as error:
            logger.warning(f"发送 PT 站点 Cookie 告警失败：{error}")

    def _build_runner(self) -> None:
        self._runner = CdpRunner(
            config=self._config,
            logger=logger,
            notify=self._notify_result,
            notify_cookie_failure=self._notify_cookie_failure,
            schedule_cron=str(self._config.get("schedule") or DEFAULT_SCHEDULE),
        )

    def run_now(self) -> RunResponse:
        """立即执行一次站点打开任务（配置页按钮调用）。"""
        logger.info("收到立即执行请求")
        error_message = self._config.get("config_error")
        if error_message:
            message = f"配置无效，无法执行：{error_message}"
            self._notify_result(message, level="error")
            return RunResponse(
                success=False,
                message=message,
                data=_RunData(success=False, message=message),
            )

        opened_urls = self.run_once(manual=True)
        message = self._last_result
        success = bool(opened_urls)
        return RunResponse(
            success=success,
            message=message,
            data=_RunData(success=success, message=message, opened=opened_urls),
        )

    def run_once(self, manual: bool = False) -> List[str]:
        """执行一次计划或手动打开任务，返回成功打开的站点 URL。"""
        error_message = self._config.get("config_error")
        if error_message:
            self._notify_result(f"配置无效，无法执行：{error_message}", level="error")
            return []
        if not manual and not self._config.get("enabled"):
            logger.info("插件未启用，跳过计划执行")
            return []

        try:
            sites = SiteOper().list_active() or []
        except Exception as error:
            failure = f"读取站点失败：{error}"
            self._notify_result(failure, level="error")
            if not manual:
                return []
            sites = list(self._known_sites or [])
            if not sites:
                return []

        selected_sites = select_sites(
            sites,
            site_mode=self._config.get("site_mode") or "all",
            selected_site_ids=self._config.get("site_ids") or [],
        )
        manual_cookies = parse_manual_site_cookies(
            self._config.get("manual_site_cookies", ""), logger
        )
        reuse_cookie = bool(self._config.get("reuse_site_cookie", True))
        targets: List[Dict[str, Any]] = []
        for site in selected_sites:
            url = str(getattr(site, "url", "")).strip()
            site_name = str(
                getattr(site, "name", None)
                or getattr(site, "domain", None)
                or url
            ).strip()
            targets.append(
                {
                    "url": url,
                    "site": site_name,
                    "cookie_pairs": resolve_site_cookie_pairs(
                        site,
                        manual_cookies,
                        reuse_cookie,
                    ),
                }
            )

        if not targets:
            self._notify_result("没有可打开的启用站点")
            return []

        if self._runner is None:
            self._build_runner()
        return self._runner.run(targets)

    def stop_service(self):
        """停止插件时立即关闭本插件创建且尚未到期的标签页。"""
        runner = getattr(self, "_runner", None)
        if runner is not None:
            runner.cleanup_all()