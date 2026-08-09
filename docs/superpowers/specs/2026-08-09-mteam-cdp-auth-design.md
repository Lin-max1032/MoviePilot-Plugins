# M-Team CDP Authentication Design

## Context

The PT site opener currently handles ordinary sites by injecting a Cookie into a CDP target before navigation. M-Team's current web client authenticates its API requests with an `Authorization` header and also supports the site API key. MoviePilot stores these credentials separately on the site record:

- `site.token` is the Authorization value.
- `site.apikey` is the `x-api-key` value.

M-Team does not provide the browser session as a normal Cookie, so the existing Cookie path cannot authenticate it reliably.

## Goal

When the plugin API or scheduled service opens an M-Team site, use the same remote Chrome/CDP workflow while attaching the following headers before navigation:

```text
Authorization: <site.token>
x-api-key: <site.apikey>
```

The opened tab must continue to use the configured TTL cleanup. Credentials must never appear in logs, notifications, test output, or error messages.

## Non-goals

- Do not call M-Team API endpoints from the MoviePilot server as a replacement for opening the browser page.
- Do not read or copy browser local storage or existing browser tokens.
- Do not change authentication behavior for non-M-Team sites.
- Do not use a Cookie or manual Cookie fallback for M-Team authentication.

## Detection

Treat a site as M-Team when its normalized URL hostname is `m-team.cc`, `m-team.io`, or a subdomain of either domain. Detection is based on the URL rather than the display name so it works with localized or custom site names.

## Flow

1. `run_once` selects active sites using the existing site-mode and selected-site rules.
2. For an M-Team site, require both `site.token` and `site.apikey` after trimming whitespace.
3. Create a background `about:blank` target and attach a flattened CDP session.
4. Send `Network.setExtraHTTPHeaders` on that session with the two M-Team headers.
5. Navigate the target to the configured site URL.
6. Track the target like other opened sites and activate the first successful target.
7. Close all targets and the CDP connection after the configured TTL or plugin shutdown.

If either credential is missing, skip the site before creating a target and report a redacted authentication failure. A failure for one site must not prevent other selected sites from opening.

## Compatibility

Non-M-Team sites keep the existing behavior:

- Managed MoviePilot Cookie is preferred.
- Named manual Cookie is used only when the managed Cookie is absent.
- Cookie injection uses `Network.setCookie` before navigation.
- Sites without Cookie use the existing direct `Target.createTarget` path.

For M-Team, the header path takes precedence even if a Cookie is present, preventing an obsolete Cookie from masking the API authentication configuration.

## Errors and notifications

Authentication failures are logged with the site name, URL, and a generic reason such as `缺少 Token` or `缺少 ApiKey`; credential values are redacted. Existing open-target and CDP errors continue through the normal per-site warning path. When notification push is enabled, the authentication failure is included in the plugin alert without any credential value.

## Testing

Add focused tests for:

- M-Team hostname detection, including subdomains and non-M-Team lookalikes.
- Header resolution from `site.token` and `site.apikey`.
- M-Team CDP call order: create target, attach, set extra headers, navigate.
- M-Team skipping Cookie injection even when a Cookie is present.
- Missing credentials producing a redacted failure while allowing other sites to run.
- Existing Cookie injection and direct-open behavior remaining unchanged.
- API-triggered manual execution continuing to use the same flow.

Update the plugin metadata and README to document the required MoviePilot site fields.
