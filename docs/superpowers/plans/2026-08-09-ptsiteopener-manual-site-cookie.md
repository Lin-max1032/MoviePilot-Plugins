# PTSiteOpener 手动站点 Cookie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow PTSiteOpener to inject a manually configured Cookie for a named MoviePilot site only when that site's managed Cookie is empty or invalid.

**Architecture:** Keep the existing CDP and scheduled execution flow. Parse a multiline `site_name:cookie` configuration into a private site-name map, then resolve each site's Cookie with a strict precedence rule: the `reuse_site_cookie` switch disables all injection, otherwise a valid `Site.cookie` wins and the manual map is only a fallback. The UI remains a static MoviePilot V2 form with one `VTextarea`, avoiding a new API or dynamic configuration component.

**Tech Stack:** Python 3, MoviePilot V2 plugin APIs, CDP Network/Page commands, `unittest`-style tests with the repository's existing fakes.

---

### Task 1: Add the manual Cookie parser contract

**Files:**
- Modify: `tests/v2/ptsiteopener/test_plugin.py`
- Modify: `plugins.v2/ptsiteopener/__init__.py`

- [ ] **Step 1: Write the failing parser tests**

Add a test for the public helper `parse_manual_site_cookies`:

```python
def test_parse_manual_site_cookies_splits_only_first_colon(self):
    self.assertEqual(
        self.module.parse_manual_site_cookies(
            "  朱雀:socute=s%3Aabc:def  \n"
            "invalid line\n"
            " :missing-name\n"
            "空值:\n"
            "重复:first=x\n"
            "重复:second=y\n"
        ),
        {
            "朱雀": "socute=s%3Aabc:def",
            "重复": "second=y",
        },
    )

def test_parse_manual_site_cookies_accepts_only_text(self):
    self.assertEqual(self.module.parse_manual_site_cookies(None), {})
    self.assertEqual(self.module.parse_manual_site_cookies("\n  \n"), {})
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run:

```powershell
python -m unittest discover -s tests\v2\ptsiteopener -p "test_*.py" -q
```

Expected: the new tests fail because `parse_manual_site_cookies` does not exist yet.

- [ ] **Step 3: Implement the minimal parser**

Add this helper after `parse_site_cookie`:

```python
def parse_manual_site_cookies(raw_config: Any) -> Dict[str, str]:
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
            logger.warning(f"忽略第 {line_number} 行无效手动站点 Cookie 配置")
            continue
        manual_cookies[site_name] = cookie
    return manual_cookies
```

- [ ] **Step 4: Run the parser tests and commit**

Run the focused test command again; the parser tests must pass with no Cookie value in warnings. Commit only the parser test and implementation:

```powershell
git add tests/v2/ptsiteopener/test_plugin.py plugins.v2/ptsiteopener/__init__.py
git commit -m "feat: parse named manual site cookies"
```

### Task 2: Implement Cookie precedence and configuration state

**Files:**
- Modify: `tests/v2/ptsiteopener/test_plugin.py`
- Modify: `plugins.v2/ptsiteopener/__init__.py`

- [ ] **Step 1: Write failing precedence tests**

Add a test for `resolve_site_cookie_pairs`:

```python
def test_resolve_site_cookie_pairs_prefers_managed_cookie_and_honors_switch(self):
    manual = {"朱雀": "manual=1"}
    site_without_cookie = types.SimpleNamespace(name="朱雀", cookie=None)
    site_with_cookie = types.SimpleNamespace(name="朱雀", cookie="managed=2")

    self.assertEqual(
        self.module.resolve_site_cookie_pairs(site_without_cookie, manual, True),
        [("manual", "1")],
    )
    self.assertEqual(
        self.module.resolve_site_cookie_pairs(site_with_cookie, manual, True),
        [("managed", "2")],
    )
    self.assertEqual(
        self.module.resolve_site_cookie_pairs(site_without_cookie, manual, False),
        [],
    )
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run:

```powershell
python -m unittest discover -s tests\v2\ptsiteopener -p "test_*.py" -q
```

Expected: the new test fails because the resolver and manual state wiring do not exist.

- [ ] **Step 3: Implement resolver and plugin state wiring**

Add the resolver after `parse_manual_site_cookies`:

```python
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
```

Initialize `self._manual_site_cookies = {}` in `__init__`, and parse `config.get("manual_site_cookies", "")` in `init_plugin`. Change `_open_site` to call `resolve_site_cookie_pairs(site, self._manual_site_cookies, self._reuse_site_cookie)`.

- [ ] **Step 4: Run all focused tests and commit**

Run:

```powershell
python -m unittest discover -s tests\v2\ptsiteopener -p "test_*.py" -q
```

Expected: all existing tests and the new precedence tests pass. Commit the resolver and state changes:

```powershell
git add tests/v2/ptsiteopener/test_plugin.py plugins.v2/ptsiteopener/__init__.py
git commit -m "feat: fall back to named manual site cookies"
```

### Task 3: Expose the setting and verify runtime behavior

**Files:**
- Modify: `tests/v2/ptsiteopener/test_plugin.py`
- Modify: `plugins.v2/ptsiteopener/__init__.py`
- Modify: `plugins.v2/ptsiteopener/README.md`
- Modify: `package.v2.json`

- [ ] **Step 1: Write failing UI, runtime, version, and safety tests**

Add assertions that the form contains a `VTextarea` with model `manual_site_cookies`, the returned default is an empty string, and the plugin version is `1.3.0`. Add a runtime test with `Site(name="朱雀", cookie=None)` and config `manual_site_cookies="朱雀:socute=manual"`; assert that `Network.setCookie` receives `socute/manual` before `Page.navigate`. Add a test with `reuse_site_cookie=False` and the same manual config; assert no `Network.setCookie` call is made.

- [ ] **Step 2: Run the focused tests and verify the expected failures**

Run:

```powershell
python -m unittest discover -s tests\v2\ptsiteopener -p "test_*.py" -q
```

Expected: the new UI, runtime, and version assertions fail against the current `1.2.0` implementation.

- [ ] **Step 3: Implement the UI, metadata, and documentation changes**

Add a full-width `VTextarea` row to `get_form`:

```python
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
```

Add `manual_site_cookies: ""` to the form model, update `plugin_version` to `1.3.0`, add a `1.3.0` entry to the `PTSiteOpener` history, and document the exact line format, fallback precedence, and warning behavior in the plugin README.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```powershell
python -m unittest discover -s tests\v2\ptsiteopener -p "test_*.py" -q
python -m compileall -q plugins.v2 tests
python -c "import json, pathlib; d=json.loads(pathlib.Path('package.v2.json').read_text(encoding='utf-8')); assert d['PTSiteOpener']['version']=='1.3.0'"
git diff --check
```

Expected: all focused tests pass, compilation succeeds, metadata parses with version `1.3.0`, and `git diff --check` is clean.

- [ ] **Step 5: Commit the user-facing feature**

```powershell
git add tests/v2/ptsiteopener/test_plugin.py plugins.v2/ptsiteopener/__init__.py plugins.v2/ptsiteopener/README.md package.v2.json
git commit -m "feat: support named manual site cookies"
```

### Task 4: Final verification and handoff

**Files:**
- Verify: `plugins.v2/ptsiteopener/__init__.py`
- Verify: `tests/v2/ptsiteopener/test_plugin.py`
- Verify: `package.v2.json`

- [ ] **Step 1: Review the final diff and repository state**

Run:

```powershell
git status --short --branch
git diff origin/main...HEAD --stat
git diff --check
```

Confirm only the manual Cookie feature, tests, README, metadata, and the committed design/plan documents are present.

- [ ] **Step 2: Run the focused regression suite again**

Run:

```powershell
python -m unittest discover -s tests\v2\ptsiteopener -p "test_*.py" -q
```

Record the exact test count and failure count. The final report must distinguish this passing focused suite from the repository-wide suite if the local MoviePilot backend environment is unavailable.
