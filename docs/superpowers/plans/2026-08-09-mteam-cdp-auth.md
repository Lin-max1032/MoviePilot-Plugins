# M-Team CDP Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit M-Team support to PTSiteOpener by applying MoviePilot's site Token and ApiKey as CDP request headers before opening the site.

**Architecture:** Keep the existing per-site opening and TTL lifecycle. Detect M-Team from the configured URL, require `site.token` and `site.apikey`, create and attach an `about:blank` target, send `Network.setExtraHTTPHeaders` with `Authorization` and `x-api-key`, then navigate. Non-M-Team sites keep their existing Cookie and direct-open branches.

**Tech Stack:** Python 3, MoviePilot V2 plugin API, synchronous Chrome DevTools Protocol over `websocket-client`, `unittest`, pytest.

---

### Task 1: Add failing M-Team authentication tests

**Files:**
- Modify: `tests/v2/ptsiteopener/test_plugin.py`

- [ ] **Step 1: Add tests for URL detection and header mapping**

Add tests that require these public helpers:

```python
def test_is_mteam_site_matches_mteam_domains_only(self):
    self.assertTrue(self.module.is_mteam_site("https://kp.m-team.cc/"))
    self.assertTrue(self.module.is_mteam_site("https://m-team.io/"))
    self.assertFalse(self.module.is_mteam_site("https://m-team.cc.example/"))

def test_resolve_mteam_headers_uses_site_token_and_apikey(self):
    site = types.SimpleNamespace(token=" Bearer token ", apikey=" key ")
    self.assertEqual(
        self.module.resolve_mteam_headers(site),
        {"Authorization": "Bearer token", "x-api-key": "key"},
    )

def test_resolve_mteam_headers_rejects_missing_credentials(self):
    with self.assertRaises(self.module.MTeamAuthError):
        self.module.resolve_mteam_headers(types.SimpleNamespace(token="", apikey="key"))
```

- [ ] **Step 2: Add the CDP behavior test before implementation**

Add a test site with both credentials and an existing Cookie. Assert `run_once()` succeeds, does not call `Network.setCookie`, and calls CDP in this order:

```python
[
    "Target.createTarget",
    "Target.attachToTarget",
    "Network.setExtraHTTPHeaders",
    "Page.navigate",
    "Target.activateTarget",
]
```

Assert the header command contains only the two expected header names and their configured values.

- [ ] **Step 3: Make the fake CDP accept extra headers**

In `FakeCdp.send`, return `{}` for `Network.setExtraHTTPHeaders`. Keep the existing `Network.setCookie` behavior unchanged so ordinary-site regression tests still exercise Cookie injection.

- [ ] **Step 4: Run the focused tests and verify the expected RED failure**

Run from `C:\Users\Administrator\Documents\Codex\2026-08-08\http-music-lulin-fun-5656-json\work\MoviePilot-Plugins`:

```powershell
python -m pytest tests/v2/ptsiteopener/test_plugin.py -k "mteam" -q
```

Expected result: failures reporting that the M-Team helper or CDP header behavior is not implemented. Do not change production code before observing this failure.

### Task 2: Implement M-Team header authentication

**Files:**
- Modify: `plugins.v2/ptsiteopener/__init__.py`

- [ ] **Step 1: Add domain detection and credential resolution**

Add an `MTEAM_DOMAINS` constant and these helpers near the existing Cookie helpers:

```python
MTEAM_DOMAINS = {"m-team.cc", "m-team.io"}

class MTeamAuthError(ValueError):
    pass

def is_mteam_site(value: Any) -> bool:
    raw_url = getattr(value, "url", value)
    parsed = urlsplit(str(raw_url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in MTEAM_DOMAINS)

def resolve_mteam_headers(site: Any) -> Dict[str, str]:
    token = str(getattr(site, "token", "") or "").strip()
    apikey = str(getattr(site, "apikey", "") or "").strip()
    if not token:
        raise MTeamAuthError("馒头认证缺少 Token")
    if not apikey:
        raise MTeamAuthError("馒头认证缺少 ApiKey")
    return {"Authorization": token, "x-api-key": apikey}
```

- [ ] **Step 2: Add a dedicated CDP opening branch**

At the start of `_open_site`, branch on `is_mteam_site(site)`. For M-Team, resolve headers first, create `about:blank`, attach with `flatten=True`, send:

```python
cdp.send(
    "Network.setExtraHTTPHeaders",
    {"headers": resolve_mteam_headers(site)},
    session_id=session_id,
)
cdp.send("Page.navigate", {"url": url}, session_id=session_id)
```

If target creation, attach, header setup, or navigation fails, close the newly created target and raise `MTeamAuthError` only for credential/header setup failures. Never fall back to direct unauthenticated target creation for M-Team.

- [ ] **Step 3: Preserve existing branches for other sites**

Leave the current Cookie-first and no-Cookie direct-open behavior unchanged after the M-Team branch. A M-Team Cookie or named manual Cookie must not cause `Network.setCookie` to run.

- [ ] **Step 4: Run the focused tests and verify GREEN**

```powershell
python -m pytest tests/v2/ptsiteopener/test_plugin.py -k "mteam" -q
```

Expected result: all M-Team tests pass.

### Task 3: Handle M-Team failures without leaking credentials

**Files:**
- Modify: `plugins.v2/ptsiteopener/__init__.py`
- Modify: `tests/v2/ptsiteopener/test_plugin.py`

- [ ] **Step 1: Add missing-credential and failure-notification tests**

Test that a M-Team site with a missing Token or ApiKey:

- opens no target;
- does not call `Network.setCookie`;
- logs a reason containing the site name and missing field;
- never logs the Token or ApiKey value;
- sends a notification when `notify_enabled` is true;
- does not prevent a following ordinary site from opening.

- [ ] **Step 2: Record authentication failures separately from Cookie failures**

Add `_record_mteam_auth_failures` and an `auth_failures` list in `run_once`. Use only site name, URL, and the redacted generic reason in logs and notifications. Call it after the site loop alongside `_record_cookie_failures`.

- [ ] **Step 3: Catch M-Team authentication failures per site**

Catch `MTeamAuthError` separately from generic open errors, append `(site_name, url, str(error))`, and continue processing later sites. Sanitize any CDP exception with both credential values before raising or logging it.

- [ ] **Step 4: Run regression tests**

```powershell
python -m pytest tests/v2/ptsiteopener/test_plugin.py -q
```

Expected result: all existing Cookie injection, manual Cookie fallback, API execution, cleanup, and new M-Team tests pass.

### Task 4: Update plugin metadata and documentation

**Files:**
- Modify: `plugins.v2/ptsiteopener/__init__.py`
- Modify: `plugins.v2/ptsiteopener/README.md`
- Modify: `package.v2.json`
- Modify: `tests/v2/ptsiteopener/test_plugin.py`

- [ ] **Step 1: Bump the plugin version and history**

Set `plugin_version` and the `PTSiteOpener` package entry to `1.4.0`. Add a history entry explaining M-Team `Authorization` plus `x-api-key` CDP authentication.

- [ ] **Step 2: Document required MoviePilot fields**

Document that M-Team does not use the Cookie fields for this flow. The MoviePilot site record must contain:

```text
Token  -> Authorization
ApiKey -> x-api-key
```

State that the plugin does not print either credential and that missing credentials generate an optional notification.

- [ ] **Step 3: Extend metadata encoding tests**

Update the existing version assertion from `1.3.0` to `1.4.0` and assert the package metadata version matches.

- [ ] **Step 4: Run the complete plugin test suite**

```powershell
python -m pytest tests/v2 -q
```

Expected result: exit code 0 with no failures.

### Task 5: Final verification

**Files:**
- Inspect: `plugins.v2/ptsiteopener/__init__.py`
- Inspect: `tests/v2/ptsiteopener/test_plugin.py`
- Inspect: `plugins.v2/ptsiteopener/README.md`
- Inspect: `package.v2.json`

- [ ] **Step 1: Check syntax and metadata JSON**

```powershell
python -m py_compile plugins.v2/ptsiteopener/__init__.py
python -c "import json; json.load(open('package.v2.json', encoding='utf-8'))"
```

- [ ] **Step 2: Search for credential leaks in changed code**

```powershell
rg -n "logger\.(debug|info|warning|error)|post_message|MTeamAuthError|Authorization|x-api-key" plugins.v2/ptsiteopener/__init__.py
```

Confirm that log and notification text contains site metadata and redacted reasons only, never `site.token` or `site.apikey` values.

- [ ] **Step 3: Inspect the final diff and test status**

```powershell
git diff --check
git status --short
git diff -- plugins.v2/ptsiteopener/__init__.py tests/v2/ptsiteopener/test_plugin.py plugins.v2/ptsiteopener/README.md package.v2.json
```

Only the planned plugin, tests, README, metadata, and plan files should be changed.
