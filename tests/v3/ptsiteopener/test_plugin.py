"""PTSiteOpener V3 行为等价测试与导入覆盖。

侧重三类覆盖:
* 纯 Python 装置（pytest + stub app.*）可运行的 V3 适配器行为等价用例
  （默认值、Cron、表单、立即执行、失败信封、Timer 清理）；
* 通过 ``app.plugins.ptsiteopener`` 导入 V3 实现的 V2 对拍核心用例
  （要求有真实 MoviePilot 后端 venv 时运行）；
* ``/run`` 与宿主响应信封的 V3 专属合同由 ``test_host_contract.py`` 承接。
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest
from pydantic import BaseModel, Field


REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_PATH = REPO_ROOT / "plugins.v3" / "ptsiteopener" / "core.py"
INIT_PATH = REPO_ROOT / "plugins.v3" / "ptsiteopener" / "__init__.py"
METADATA_PATH = REPO_ROOT / "package.v3.json"


class QuietLogger:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(("debug", message))

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))


class FakeTrigger:
    def __init__(self, expression):
        self.expression = expression

    @classmethod
    def from_crontab(cls, expression):
        if expression == "invalid":
            raise ValueError("invalid cron expression")
        if len(str(expression).split()) != 5:
            raise ValueError("cron must contain five fields")
        return cls(expression)


class FakeBase(metaclass=type):
    def post_message(self, **kwargs):
        return None

    def update_config(self, config, plugin_id=None):
        return None

    def get_data(self, key):
        return None

    def save_data(self, key, value):
        return None


class FakeSiteOper:
    def __init__(self, sites):
        self.sites = list(sites)

    def list_active(self):
        return list(self.sites)


def load_core():
    """以最小装置加载 core.py（不触碰 app.* 宿主导入）。"""
    name = "ptsiteopener_v3_under_test.core"
    with mock.patch.dict("sys.modules", {"apscheduler.triggers.cron": types.SimpleNamespace(CronTrigger=FakeTrigger)}):
        spec = importlib.util.spec_from_file_location(name, CORE_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module


def load_plugin(stubs):
    """以补丁装载 V3 适配器 ``__init__.py``；``stubs`` 为模块->对象映射。"""
    core_submodule_name = "ptsiteopener_v3_under_test.core"
    sys.modules.setdefault("apscheduler.triggers.cron", types.SimpleNamespace(CronTrigger=FakeTrigger))
    core_module = load_core()
    name = "ptsiteopener_v3_under_test"
    patched = dict(stubs)
    patched[core_submodule_name] = core_module
    with mock.patch.dict(sys.modules, patched):
        spec = importlib.util.spec_from_file_location(name, INIT_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        assert module.core is core_module
        return module, core_module


class FakeCdp:
    def __init__(self, cookie_failure=False):
        self.calls = []
        self.closed = []
        self.connected = True
        self.next_target = 0
        self.cookie_failure = cookie_failure

    def send(self, method, params=None, session_id=None):
        params = params or {}
        self.calls.append((method, params, session_id))
        if method == "Target.createTarget":
            if params.get("url") == "https://failed.example/":
                raise RuntimeError("target rejected")
            self.next_target += 1
            return {"targetId": f"opened-{self.next_target}"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        if method == "Network.setCookie":
            if self.cookie_failure:
                raise RuntimeError("cookie rejected")
            return {"success": True}
        if method == "Page.navigate":
            return {"frameId": "frame-1"}
        if method == "Target.activateTarget":
            return {}
        if method == "Target.closeTarget":
            self.closed.append(params.get("targetId"))
            return {"success": True}
        raise AssertionError(f"unexpected CDP method: {method}")

    def close(self):
        self.connected = False


class FakeTimer:
    instances = []

    def __init__(self, delay, function, args=None, kwargs=None):
        self.delay = delay
        self.function = function
        self.args = args or []
        self.kwargs = kwargs or {}
        self.cancelled = False
        self.started = False
        self.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def make_site(**kwargs):
    values = {"id": 1, "url": "https://one.example/", "is_active": True, "cookie": None}
    values.update(kwargs)
    return types.SimpleNamespace(**values)


def make_metadata_stubs(sites=()):
    """构造适配器所需的 ``app.*`` 最小模块桩。"""
    module_names = {
        "app",
        "app.plugins",
        "app.db",
        "app.db.oper",
        "app.db.oper.site",
        "app.schemas",
        "app.schemas.response",
        "app.schemas.types",
        "app.sdk",
        "app.sdk.logging",
        "app.sdk.events",
    }
    fake_site_oper = FakeSiteOper(sites)
    stubs = {}
    for name in module_names:
        stubs[name] = types.ModuleType(name)
    stubs["app.plugins"]._PluginBase = FakeBase
    stubs["app.db.oper.site"].SiteOper = lambda: fake_site_oper
    stubs["app.sdk.logging"].logger = QuietLogger()
    stubs["app.schemas.types"].NotificationType = types.SimpleNamespace(Plugin="plugin")
    stubs["app.sdk.events"].eventmanager = types.SimpleNamespace(
        register=lambda _event_type: (lambda func: func)
    )

    class HostResponse(BaseModel):
        success: bool
        message: str = ""

    stubs["app.schemas.response"].Response = HostResponse
    return stubs


def test_pure_page_replicate_v2_selection_filters_and_deduplicates():
    core_module = load_core()
    sites = [
        make_site(id=1, url="https://one.example/"),
        make_site(id=2, url="https://one.example/"),
        make_site(id=3, url="ftp://ignored.example/"),
        make_site(id=4, url="https://inactive.example/", is_active=False),
        make_site(id=5, url="http://two.example/"),
    ]
    assert core_module.select_site_urls(sites) == [
        "https://one.example/",
        "http://two.example/",
    ]
    assert core_module.select_site_urls(
        sites, site_mode="selected", selected_site_ids=["5"]
    ) == ["http://two.example/"]


def test_pure_page_resolve_websocket_url_uses_remote_host():
    core_module = load_core()
    assert core_module.resolve_websocket_url(
        {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/id"},
        "http://music.lulin.fun:5656/json/version",
    ) == "ws://music.lulin.fun:5656/devtools/browser/id"


def test_pure_page_cookie_parsing_matches_v2():
    core_module = load_core()
    assert core_module.parse_site_cookie("sid=abc==; invalid; theme=dark; =x;") == [
        ("sid", "abc=="),
        ("theme", "dark"),
    ]
    assert core_module.parse_site_cookie(None) == []
    assert core_module.parse_manual_site_cookies(
        "朱雀:socute=s%3Aabc:def\ninvalid line\n重复:first=x\n重复:second=y\n",
        logger=QuietLogger(),
    ) == {"朱雀": "socute=s%3Aabc:def", "重复": "second=y"}


def test_plugin_defaults_and_version_contract():
    stubs = make_metadata_stubs()
    module, _ = load_plugin(stubs)
    plugin = object.__new__(module.PTSiteOpener)

    assert plugin.plugin_version == "2.0.0"
    assert plugin.plugin_author == "Lin-max1032"
    assert "?" not in plugin.plugin_name
    assert "?" not in plugin.plugin_desc
    assert module.core.DEFAULTS == {
        "enabled": False,
        "cdp_url": "",
        "schedule": "0 */6 * * *",
        "ttl_minutes": 5,
        "notify_enabled": False,
        "reuse_site_cookie": True,
        "manual_site_cookies": "",
        "site_mode": "all",
        "site_ids": [],
    }

    metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))["PTSiteOpener"]
    v2_meta = json.loads(
        (REPO_ROOT / "package.v2.json").read_text(encoding="utf-8")
    )["PTSiteOpener"]
    assert metadata["version"] == "2.0.0"
    assert int(metadata["version"].split(".")[0]) == int(v2_meta["version"].split(".")[0]) + 1
    for key in ("name", "description", "labels"):
        assert "?" not in metadata[key]


def test_init_plugin_state_and_cron_service_registration():
    stubs = make_metadata_stubs()
    module, _ = load_plugin(stubs)
    plugin = object.__new__(module.PTSiteOpener)
    plugin._config = dict(module.core.DEFAULTS)
    plugin._runner = None
    plugin._known_sites = []
    plugin._known_sites_failed = False
    plugin._last_result = "尚未执行"

    plugin.init_plugin({"enabled": True})
    assert plugin._config["cdp_url"] == ""
    assert plugin._config["schedule"] == "0 */6 * * *"
    assert plugin._config["ttl_minutes"] == 5
    assert plugin._config["notify_enabled"] is False
    assert plugin.get_state() is True

    services = plugin.get_service()
    assert len(services) == 1
    trigger = services[0]["trigger"]
    assert getattr(trigger, "expression", None) in (None, "0 */6 * * *")
    assert str(trigger) != ""
    assert services[0]["func"] == plugin.run_once


def test_form_and_page_shape_match_v2():
    stubs = make_metadata_stubs()
    module, _ = load_plugin(stubs)
    plugin = object.__new__(module.PTSiteOpener)
    plugin._config = dict(module.core.DEFAULTS)
    plugin._runner = None
    plugin._known_sites = []
    plugin._known_sites_failed = False
    plugin._last_result = "尚未执行"

    form, model = plugin.get_form()
    assert model == {
        "enabled": False,
        "cdp_url": "",
        "schedule": "0 */6 * * *",
        "ttl_minutes": 5,
        "notify_enabled": False,
        "reuse_site_cookie": True,
        "manual_site_cookies": "",
        "site_mode": "all",
        "site_ids": [],
    }

    nodes = []

    def collect(items):
        for item in items:
            nodes.append(item)
            collect(item.get("content", []))

    collect(form)
    components = {item.get("component") for item in nodes}
    assert {"VRow", "VCol", "VSwitch", "VTextField", "VCronField", "VSelect", "VTextarea"} <= components

    schedule_field = next(
        item for item in nodes if item.get("props", {}).get("model") == "schedule"
    )
    assert schedule_field["props"]["label"] == "执行周期"
    assert schedule_field["props"]["placeholder"] == "五段 Cron 表达式"

    form_buttons = [item for item in nodes if item.get("component") == "VBtn"]
    assert form_buttons == []

    page_nodes = []

    def collect_page(items):
        for item in items:
            page_nodes.append(item)
            collect_page(item.get("content", []))

    collect_page(plugin.get_page())
    page_buttons = [item for item in page_nodes if item.get("component") == "VBtn"]
    assert len(page_buttons) == 1
    assert page_buttons[0]["text"] == "立即执行"
    assert page_buttons[0]["events"]["click"]["api"] == "plugin/PTSiteOpener/run"
    assert page_buttons[0]["events"]["click"]["method"] == "post"


def test_run_now_failure_returns_matching_envelope():
    """未配置 CDP 地址时返回与宿主响应信封一致的失败结构。"""
    stubs = make_metadata_stubs([make_site(name="One")])
    module, _ = load_plugin(stubs)
    plugin = object.__new__(module.PTSiteOpener)
    plugin._config = dict(module.core.DEFAULTS)
    plugin._runner = None
    plugin._known_sites = []
    plugin._known_sites_failed = False
    plugin._last_result = "尚未执行"
    plugin.init_plugin({"enabled": True, "cdp_url": ""})
    assert plugin._runner is not None
    plugin._runner._connect_cdp = lambda: (_ for _ in ()).throw(
        ValueError("未配置远程 CDP 地址")
    )

    response = plugin.run_now()

    assert response.success is False
    assert "未配置远程 CDP 地址" in response.message
    assert response.data is None or "未配置远程 CDP 地址" in response.data.message
    assert isinstance(module.RunResponse.model_validate(response), module.RunResponse)
    assert "?" not in response.message


def test_run_now_opens_sites_via_core_layer(monkeypatch):
    import threading as real_threading

    stubs = make_metadata_stubs([make_site(name="One")])
    module, _ = load_plugin(stubs)
    cdp = FakeCdp()
    monkeypatch.setattr(real_threading, "Timer", FakeTimer)
    FakeTimer.instances.clear()

    plugin = object.__new__(module.PTSiteOpener)
    plugin._config = dict(module.core.DEFAULTS)
    plugin._runner = None
    plugin._known_sites = []
    plugin._known_sites_failed = False
    plugin._last_result = "尚未执行"
    plugin.init_plugin(
        {"enabled": True, "cdp_url": "http://127.0.0.1:1/json/version", "notify_enabled": True}
    )
    assert plugin._runner is not None
    plugin._runner._connect_cdp = lambda: cdp

    response = plugin.run_now()

    assert response.success is True
    assert response.data is not None
    assert response.data.opened == ["https://one.example/"]
    assert len(FakeTimer.instances) == 1
    FakeTimer.instances[0].function(*FakeTimer.instances[0].args)
    assert cdp.closed == ["opened-1"]
    assert cdp.connected is False


def test_core_cookie_injection_sequence(monkeypatch):
    import threading as real_threading

    core_module = load_core()
    cdp = FakeCdp()
    monkeypatch.setattr(real_threading, "Timer", FakeTimer)
    FakeTimer.instances.clear()
    runner = core_module.CdpRunner(
        config={"cdp_url": "http://x/", "ttl_minutes": 5},
        logger=QuietLogger(),
        notify=lambda *a, **k: None,
        notify_cookie_failure=None,
    )
    runner._connect_cdp = lambda: cdp

    result = runner.run(
        [
            {
                "url": "https://one.example/",
                "site": "One",
                "cookie_pairs": [("sid", "abc=="), ("theme", "dark")],
            }
        ]
    )
    assert result == ["https://one.example/"]
    assert [method for method, _, _ in cdp.calls] == [
        "Target.createTarget",
        "Target.attachToTarget",
        "Network.setCookie",
        "Network.setCookie",
        "Page.navigate",
        "Target.activateTarget",
    ]
    assert cdp.calls[0][1]["url"] == "about:blank"
    assert cdp.calls[2][2] == "session-1"
    assert cdp.calls[2][1]["name"] == "sid"
    assert cdp.calls[2][1]["value"] == "abc=="
    assert cdp.calls[4][1]["url"] == "https://one.example/"
    assert len(FakeTimer.instances) == 1
    assert FakeTimer.instances[0].delay == 300


def test_core_cleanup_only_closes_own_targets(monkeypatch):
    import threading as real_threading

    core_module = load_core()
    cdp = FakeCdp()
    monkeypatch.setattr(real_threading, "Timer", FakeTimer)
    FakeTimer.instances.clear()
    runner = core_module.CdpRunner(
        config={"cdp_url": "http://x/"},
        logger=QuietLogger(),
        notify=lambda *a, **k: None,
        notify_cookie_failure=None,
    )
    runner._connect_cdp = lambda: cdp

    result = runner.run(
        [
            {"url": "https://one.example/", "site": "One", "cookie_pairs": []},
            {"url": "https://failed.example/", "site": "Two", "cookie_pairs": []},
            {"url": "https://two.example/", "site": "Three", "cookie_pairs": []},
        ]
    )
    assert result == ["https://one.example/", "https://two.example/"]
    assert cdp.closed == []
    assert len(FakeTimer.instances) == 1
    FakeTimer.instances[0].function(*FakeTimer.instances[0].args)
    assert cdp.closed == ["opened-1", "opened-2"]
    assert cdp.connected is False


def test_core_cleanup_all_stop_service_semantics(monkeypatch):
    import threading as real_threading

    core_module = load_core()
    cdp = FakeCdp()
    monkeypatch.setattr(real_threading, "Timer", FakeTimer)
    FakeTimer.instances.clear()
    runner = core_module.CdpRunner(
        config={"cdp_url": "http://x/"},
        logger=QuietLogger(),
        notify=lambda *a, **k: None,
        notify_cookie_failure=None,
    )
    runner._connect_cdp = lambda: cdp

    runner.run([{"url": "https://one.example/", "site": "One", "cookie_pairs": []}])
    runner.cleanup_all()

    assert cdp.closed == ["opened-1"]
    assert cdp.connected is False
    assert FakeTimer.instances[0].cancelled is True