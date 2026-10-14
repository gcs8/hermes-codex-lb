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
