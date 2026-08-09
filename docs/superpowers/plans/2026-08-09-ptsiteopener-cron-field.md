# PTSiteOpener Cron Field Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Update PTSiteOpener to use the V2 popup Cron calculator, leave the CDP address empty by default with runtime feedback when missing, and identify the plugin author as Lin-max1032.

**Architecture:** Keep the existing schedule string and APScheduler CronTrigger flow. Replace only the form field component with the host-registered VCronField; allow an empty CDP setting through configuration and fail at connection time with the existing result/log/notification path. Update plugin and market metadata to patch version 1.3.1.

**Tech Stack:** Python 3, MoviePilot V2 plugin page JSON, Vuetify VCronField, APScheduler, unittest.

---

## File Map

- Modify tests/v2/ptsiteopener/test_plugin.py: add coverage for empty CDP behavior, the VCronField form node, author, and version 1.3.1.
- Modify plugins.v2/ptsiteopener/__init__.py: make the CDP default empty, defer empty-address failure to connection time, use VCronField, and set the Python author.
- Modify package.v2.json: set the PTSiteOpener market version to 1.3.1 and add its history entry.
- Modify plugins.v2/ptsiteopener/README.md: document the empty default, runtime warning, and popup Cron calculator.

### Task 1: Add Failing Regression Tests

**Files:**
- Modify: tests/v2/ptsiteopener/test_plugin.py

- [ ] Step 1: Extend the existing default/form test.

In test_plugin_registers_user_cron_and_has_expected_defaults, assert:

~~~python
self.assertEqual(model["cdp_url"], "")
self.assertEqual(plugin.plugin_author, "Lin-max1032")
self.assertEqual(plugin.plugin_version, "1.3.1")

schedule_field = next(
    item for item in nodes if item.get("props", {}).get("model") == "schedule"
)
self.assertEqual(schedule_field["component"], "VCronField")
self.assertEqual(schedule_field["props"]["label"], "执行周期")
self.assertEqual(schedule_field["props"]["placeholder"], "五段 Cron 表达式")
~~~

Update the package metadata assertion in the localized text test from 1.3.0 to 1.3.1.

- [ ] Step 2: Add a test for an empty CDP address.

Add this test to PluginTestCase:

~~~python
def test_empty_cdp_address_is_saved_but_reported_when_run(self):
    sites = [
        types.SimpleNamespace(
            id=1,
            url="https://one.example/",
            is_active=True,
            cookie=None,
        )
    ]
    self.module.SiteOper = lambda: types.SimpleNamespace(list_active=lambda: sites)

    plugin = self.module.PTSiteOpener()
    plugin.init_plugin({"enabled": True, "cdp_url": ""})

    self.assertEqual(plugin._cdp_url, "")
    self.assertIsNone(plugin._config_error)
    self.assertTrue(plugin.get_state())

    response = plugin.run_now()

    self.assertFalse(response["success"])
    self.assertIn("未配置远程 CDP 地址", response["message"])
    self.assertTrue(
        any("未配置远程 CDP 地址" in message for _, message in self.logger.messages)
    )
~~~

- [ ] Step 3: Run only the new tests and verify RED.

Run:

~~~powershell
python -m unittest tests.v2.ptsiteopener.test_plugin.PluginTestCase.test_plugin_registers_user_cron_and_has_expected_defaults -q
python -m unittest tests.v2.ptsiteopener.test_plugin.PluginTestCase.test_empty_cdp_address_is_saved_but_reported_when_run -q
~~~

Expected: failures identify the old CDP default, old author/version, VTextField, or empty-address configuration behavior. Fix test setup errors only; do not modify production code in this step.

- [ ] Step 4: Commit the failing tests.

~~~powershell
git add tests/v2/ptsiteopener/test_plugin.py
git commit -m "test: define Cron field and empty CDP behavior"
~~~

### Task 2: Implement Empty CDP Configuration Behavior

**Files:**
- Modify: plugins.v2/ptsiteopener/__init__.py
- Test: tests/v2/ptsiteopener/test_plugin.py

- [ ] Step 1: Make the default empty and separate the placeholder.

Use:

~~~python
DEFAULT_CDP_URL = ""
CDP_URL_PLACEHOLDER = "例如：http://127.0.0.1:9222/json/version"
~~~

Set plugin_author to Lin-max1032. Keep the placeholder separate so the model remains empty and no endpoint is silently used.

- [ ] Step 2: Allow empty values during configuration validation.

In init_plugin, validate the CDP address only when non-empty:

~~~python
try:
    if self._cdp_url:
        _validate_cdp_url(self._cdp_url)
    CronTrigger.from_crontab(self._schedule)
except Exception as error:
    self._config_error = str(error)
    logger.error(f"PT站点自动打开配置无效：{error}")
~~~

Invalid non-empty CDP URLs and invalid Cron expressions remain configuration errors.

- [ ] Step 3: Fail before network access when execution has no CDP address.

Use:

~~~python
def _connect_cdp(self) -> _CdpConnection:
    if not self._cdp_url:
        raise ValueError("未配置远程 CDP 地址")
    return _connect_cdp(self._cdp_url)
~~~

The existing run_once connection exception path must set _last_result, log the failure, and notify when enabled. It must return before _fetch_json.

- [ ] Step 4: Run the focused and full tests.

~~~powershell
python -m unittest tests.v2.ptsiteopener.test_plugin.PluginTestCase.test_empty_cdp_address_is_saved_but_reported_when_run -q
python -m unittest tests.v2.ptsiteopener.test_plugin -q
~~~

Expected: the new test passes and all existing tests pass.

- [ ] Step 5: Commit the CDP behavior.

~~~powershell
git add plugins.v2/ptsiteopener/__init__.py tests/v2/ptsiteopener/test_plugin.py
git commit -m "feat: allow empty CDP configuration"
~~~

### Task 3: Use the V2 Popup Cron Calculator

**Files:**
- Modify: plugins.v2/ptsiteopener/__init__.py
- Test: tests/v2/ptsiteopener/test_plugin.py

- [ ] Step 1: Replace only the schedule form node.

In get_form, preserve the existing VCol width and schedule model, but use:

~~~python
{
    "component": "VCronField",
    "props": {
        "model": "schedule",
        "label": "执行周期",
        "placeholder": "五段 Cron 表达式",
    },
}
~~~

VCronField is globally registered by MoviePilot-Frontend and opens its calculator through a VMenu when the activator input is clicked. The saved value remains a five-field Cron string.

- [ ] Step 2: Run the form and regression tests.

~~~powershell
python -m unittest tests.v2.ptsiteopener.test_plugin.PluginTestCase.test_plugin_registers_user_cron_and_has_expected_defaults -q
python -m unittest tests.v2.ptsiteopener.test_plugin -q
~~~

Expected: the form assertion sees VCronField, the schedule remains 0 */6 * * *, and the complete test file passes.

- [ ] Step 3: Commit the form change.

~~~powershell
git add plugins.v2/ptsiteopener/__init__.py tests/v2/ptsiteopener/test_plugin.py
git commit -m "feat: use V2 Cron calculator field"
~~~

### Task 4: Update Market Metadata and Documentation

**Files:**
- Modify: plugins.v2/ptsiteopener/__init__.py
- Modify: package.v2.json
- Modify: plugins.v2/ptsiteopener/README.md

- [ ] Step 1: Update only the PTSiteOpener package entry.

Set plugin_version in plugins.v2/ptsiteopener/__init__.py to 1.3.1 and update the matching PTSiteOpener package entry:

Set version and history to:

~~~json
"version": "1.3.1",
"author": "Lin-max1032",
"history": {
  "1.3.1": "远程 CDP 地址默认留空；配置页使用 V2 Cron 周期计算器。",
  "1.3.0": "新增按站点名称填写手动 Cookie 的回退配置；仅在站点管理没有有效 Cookie 时使用。"
}
~~~

Keep all other plugin entries unchanged.

- [ ] Step 2: Update README behavior.

Document that the CDP address is empty by default, can be saved empty, and reports 未配置远程 CDP 地址 when execution is attempted. Document that 计划任务 uses the V2 VCronField popup calculator and stores a five-field Cron string.

- [ ] Step 3: Validate metadata and encoding.

~~~powershell
python -c "import json; data=json.load(open('package.v2.json', encoding='utf-8')); assert data['PTSiteOpener']['version'] == '1.3.1'; assert data['PTSiteOpener']['author'] == 'Lin-max1032'; print('package.v2.json: OK')"
rg -n "VCronField|1\.3\.1|Lin-max1032|未配置远程 CDP 地址" plugins.v2/ptsiteopener package.v2.json
git diff --check
~~~

Expected: JSON validation succeeds, all terms are present in intended files, and git diff --check has no output.

- [ ] Step 4: Commit metadata and documentation.

~~~powershell
git add package.v2.json plugins.v2/ptsiteopener/README.md
git commit -m "docs: document Cron calculator and CDP defaults"
~~~

### Task 5: Final Verification

**Files:**
- Verify: plugins.v2/ptsiteopener/__init__.py
- Verify: tests/v2/ptsiteopener/test_plugin.py
- Verify: package.v2.json

- [ ] Step 1: Run the full focused test and static checks.

~~~powershell
python -m unittest tests.v2.ptsiteopener.test_plugin -q
python -m py_compile plugins.v2/ptsiteopener/__init__.py
python -c "import json; data=json.load(open('package.v2.json', encoding='utf-8')); assert data['PTSiteOpener']['version'] == '1.3.1'; print('package.v2.json: OK')"
git diff --check HEAD~4 HEAD
~~~

Expected: all tests pass, compilation succeeds, metadata validation prints package.v2.json: OK, and the diff check is clean.

- [ ] Step 2: Confirm no M-Team 1.4.0 behavior was reintroduced.

~~~powershell
rg -n -i "MTEAM|m-team|馒头认证" plugins.v2/ptsiteopener tests/v2/ptsiteopener
if ($LASTEXITCODE -eq 1) { "No M-Team references in PTSiteOpener scope" }
python -c "import json; data=json.load(open('package.v2.json', encoding='utf-8')); assert '1.4.0' not in json.dumps(data['PTSiteOpener'], ensure_ascii=False); print('No PTSiteOpener 1.4.0 metadata')"
~~~

Expected: no M-Team-specific references in the PTSiteOpener scope.

- [ ] Step 3: Review the final diff and workspace.

~~~powershell
git status --short --branch
git log --oneline --decorate -8
git diff --stat HEAD~4 HEAD
~~~

Expected: only the planned files changed, the worktree is clean, and the implementation commits plus design/plan commits are visible.
