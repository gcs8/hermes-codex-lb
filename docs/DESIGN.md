# hermes-codex-lb design

Status: draft, nothing implemented yet.

## Goal

Make a [codex-lb](https://github.com/Soju06/codex-lb) gateway visible and controllable from Hermes:

1. Choose fast mode (OpenAI priority tier) per session for requests routed to codex-lb.
2. Show what codex-lb actually served: requested vs served tier, cache hits, errors.
3. Show pool health in the desktop app: 5h / weekly window usage, resets, credits.

Hermes core deliberately sends `service_tier` only to first-party endpoints
(NousResearch/hermes-agent#78097, closed not planned). A plugin is the supported
way for a user to opt their own gateway in.

## Public surfaces used (Hermes >= 0.22.0)

All of these are documented plugin APIs. No core patching (catalog rule 9).

| Need | Surface | Notes |
|---|---|---|
| Add `service_tier: priority` to codex-lb requests | `ctx.register_middleware("llm_request", ...)` | Receives `request`, `provider`, `base_url`, `session_id`; returns `{"request": {...}}`. Only touches requests whose `base_url` matches a configured codex-lb URL. |
| Read the tier the response was served at | `ctx.register_middleware("llm_execution", ...)` | Wraps the provider call; reads `service_tier` off the returned response. The `post_api_request` hook payload doesn't carry the served tier (raw usage is stripped), so the execution wrapper is the public way to see it. |
| Per-session switch | `ctx.register_command("lbfast", ...)` | `/lbfast on|off|auto|status`. State kept per session id in plugin storage. |
| Usage numbers | codex-lb `GET /v1/usage` with a proxy API key | Totals, credit limits, primary/secondary pool windows. |
| Per-account / error detail | codex-lb `GET /v1/lb/summary` | Doesn't exist yet: gcs8/codex-lb#1 (read-only, key-scoped, off by default). |
| Desktop UI | `desktop/plugin.js` via `@hermes/plugin-sdk` | Status-bar chip (pool %, fast on/off, last served tier) and a pane. Data comes from the plugin's Python backend (`ctx.rest`), so the API key never reaches the renderer. |

## Package layout (one install, both halves)

Per the Desktop plugin SDK's "one package, both SDKs" pattern:

```
hermes-codex-lb/
├── plugin.yaml            # agent half: middleware, /lbfast, config_schema
├── __init__.py
├── dashboard/
│   ├── manifest.json      # { "name": "codex-lb", "api": "plugin_api.py" }
│   └── plugin_api.py      # backend routes /api/plugins/codex-lb/*; holds the API key
└── desktop/
    └── plugin.js          # page + sidebar nav, status chip, settings page
```

The renderer never sees the key; it calls `ctx.rest(...)` and the Python backend talks to codex-lb.

## Desktop UI

- **Page** (`ROUTES_AREA` + `SIDEBAR_NAV_AREA` row "Codex-LB"): pool windows, resets, credits,
  served-tier counts, and (v2) per-account detail, errors, cache and model mix. The goal is
  that you don't need to open the codex-lb dashboard for day-to-day stats.
- **Status-bar chip**: pool %, fast on/off, last served tier.
- **Settings** go in Settings → Plugins via `ctx.registerSettingsPage` (the SDK says not to
  build a separate preferences pane).
- Built from the SDK UI kit (`Button`, `Badge`, `StatusDot`, `ConfirmDialog`, ...), styled with
  theme variables only, data via the shared React Query client.

## Modes: read-only and read/write

`mode: read_only | read_write`, default `read_only`.

| | read_only | read_write |
|---|---|---|
| Pool usage, credits, served tier, stats page | yes | yes |
| List banked reset credits (`GET /v1/reset-credit`) | yes | yes |
| Redeem a reset credit (`POST /v1/reset-credit`) | no | yes, human-confirmed |
| Fast-tier toggle | yes | yes |

Rules for write actions:

- Only a person triggers them: a button behind a `ConfirmDialog`, or a slash command.
  They are **not** exposed as model tools, so the agent can't spend a banked reset on its
  own (catalog rule 12, approvals).
- The confirm dialog names the account (masked email or alias), the credit's expiry, and
  that it can't be undone.
- Every write is logged to the plugin's own storage (time, account alias, result).

**Enforcement caveat.** codex-lb today lets any API key that can read usage also redeem
reset credits (`validate_usage_api_key` guards both routes). So `read_only` in the plugin is
a client-side promise only. Real read-only needs a key-level permission in codex-lb:
gcs8/codex-lb#2. Until that lands, the README tells users to give the plugin a key
restricted to the accounts they're willing to have redeemed.

## Config

```yaml
plugins:
  enabled: [codex-lb]
  entries:
    codex-lb:
      settings:
        base_urls: ["http://codex-lb.example:2455/v1"]   # which providers count as codex-lb
        default_fast: off        # off | on | auto
        usage_poll_seconds: 60
```

The API key for `/v1/usage` is a `config_schema` secret read with
`agent.secret_scope.get_secret(...)` (catalog rule 11). The plugin never reads
Hermes's provider `key_cmd`, `.env` or `auth.json`.

## Behaviour notes

- **Billing.** The priority tier costs more quota. The plugin only sends it when
  the user turns it on, and the README says so.
- **Silent downgrade.** codex-lb 1.25.0-beta.9 serves `priority` requests as
  `default` (Soju06/codex-lb#1454). The served-tier counter makes that visible
  instead of letting "fast" look on while nothing changes.
- **Prompt caching.** `service_tier` is a per-request parameter; it doesn't change
  the prompt prefix, so toggling it shouldn't break the cache.
- **Unattended runs.** No prompts, no blocking calls on the request path. Usage
  polling runs off the request path and fails quietly.

## Out of scope

- Managing codex-lb accounts, keys or settings (that's the codex-lb dashboard).
- Steering (that's a Hermes core fix, not a plugin concern).
