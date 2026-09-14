"""PTSiteOpener V3 宿主合同测试（依赖真实 MoviePilot 后端）。

覆盖：
* V3 源码不触碰已停用路径（app.log / app.core / 手工路由注册）；
* ``get_api()`` 无条件声明与宿主一致的 ``schemas.Response`` 响应模型；
* 执行失败信封与宿主响应结构逐字段对齐。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "plugins.v3" / "ptsiteopener" / "__init__.py"


def _imports() -> set[str]:
    return {
        node.module or ""
        for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }


def _load_plugin():
    return importlib.import_module("app.plugins.ptsiteopener")


class FakeSiteOper:
    """替换 SiteOper，避免真实数据库依赖并固定站点集合。"""

    def __init__(self, sites):
        self._sites = list(sites)

    def list_active(self):
        return list(self._sites)


@pytest.fixture
def plugin_factory(monkeypatch):
    """返回最小插件实例构造器，宿主层依赖（SiteOper）用本用例替身接管。"""
    module = _load_plugin()

    def build(sites, **config):
        plugin = object.__new__(module.PTSiteOpener)
        plugin._config = dict(module.core.DEFAULTS)
        plugin._runner = None
        plugin._last_result = "尚未执行"
        plugin._known_sites = []
        plugin._known_sites_failed = False
        monkeypatch.setattr(module, "SiteOper", lambda: FakeSiteOper(sites))
        plugin.init_plugin(config or {})
        return plugin

    return build


def test_source_imports_avoid_discontinued_paths():
    modules = _imports()

    assert "app.log" not in modules
    assert not any(
        module.startswith(("app.core.", "app.helper.", "app.utils.")) for module in modules
    )
    assert "app.db.oper.site" in modules
    assert "app.sdk.logging" in modules


def test_source_has_no_manual_route_registrations():
    source = SOURCE.read_text(encoding="utf-8")

    assert "register_plugin_api(" not in source
    assert "app.api.endpoints.plugin" not in source


def test_get_api_declares_host_response_model_unconditionally(plugin_factory):
    import app.schemas as schemas

    plugin = plugin_factory([], enabled=False)

    apis = plugin.get_api()
    assert len(apis) == 1
    api = apis[0]
    assert api["path"] == "/run"
    assert api["methods"] == ["POST"]
    assert api["auth"] == "bear"
    assert api["response_model"] is schemas.Response


def test_failure_envelope_validates_against_host_response(plugin_factory):
    from app.schemas import Response as HostResponse

    plugin = plugin_factory([], enabled=True, cdp_url="")

    response = plugin.run_now()
    assert response.success is False
    assert "?" not in response.message

    # 惰性解析路径（app.schemas.Response）也必须能完整校验适配器输出
    envelope = HostResponse(**response.model_dump())
    assert envelope.success is False
    assert isinstance(envelope.data, dict)
    assert "opened" not in envelope.data or envelope.data.get("opened") is not None