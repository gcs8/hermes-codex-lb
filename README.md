# hermes-codex-lb

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin for people who route
Hermes through a [codex-lb](https://github.com/Soju06/codex-lb) gateway.

**Status: v0.1.0, pre-release.** Tested against Hermes 0.22.0 with Hermes's fake Responses
server; not yet run against a live codex-lb.

## What it does

- **`/lbfast on|off|auto|status`**: asks codex-lb for the priority (fast) tier for the
  current session. Hermes core only sends that tier to first-party OpenAI endpoints
  (NousResearch/hermes-agent#78097), so this is an explicit opt-in for your own gateway.
  `auto` keeps fast on for the first 60 seconds of each user turn.
- **Requested vs served tier**: every response from codex-lb is checked for the tier it was
  actually served at. If the gateway quietly answers priority requests at the default tier,
  `/lbfast status` and the desktop page say so.
- **Codex-LB page in Hermes Desktop** (sidebar entry + status-bar chip): pool remaining for
  the 5-hour and weekly windows, request/token/cost totals for the plugin's key, served-tier
  counts, and banked reset credits.
- **Read-only or read/write**: in `read_write` mode the page can redeem a banked reset credit,
  after a confirmation dialog. It's never available to the model as a tool.

## Install

```bash
hermes plugins install https://github.com/gcs8/hermes-codex-lb
hermes plugins enable codex-lb
```

Then in Hermes Desktop, **Settings → Plugins → codex-lb**:

| Setting | What to put |
|---|---|
| codex-lb base URLs | The `/v1` URL(s) your Hermes provider uses for codex-lb, e.g. `http://codex-lb.local:2455/v1`. Only requests to these URLs are touched. |
| Default fast mode | `off` (default), `on` or `auto` for sessions that haven't used `/lbfast`. |
| Access mode | `read_only` (default) or `read_write`. |
| codex-lb API key | A codex-lb proxy API key, stored as `CODEX_LB_PLUGIN_API_KEY`. |

Give the plugin its own codex-lb key. It only needs `GET /v1/usage` and `/v1/reset-credit`,
so you can lock its allowed models to a name that doesn't exist and it can't run inference.

The same settings live in `config.yaml` under `plugins.entries.codex-lb.settings`.

## What it does on your machine (disclosure)

- **Network:** HTTP requests to the codex-lb base URL(s) you configure (`/v1/usage`,
  `/v1/reset-credit`), with the API key you set. Nothing else.
- **Model requests:** adds `service_tier: "priority"` to requests bound for those URLs, only
  while fast mode is on for that session. The priority tier uses more of your subscription quota.
- **Writes upstream:** redeeming a reset credit (`POST /v1/reset-credit`), only in
  `read_write` mode and only after you confirm in the dialog. Redeems are logged locally.
- **Local storage:** `plugin-data/codex-lb/state.db` in your Hermes profile: per-session fast
  mode, served-tier counts and the redeem log. No prompts or responses are stored.
- **Read-only is client-side today.** codex-lb lets any key that can read usage also redeem
  reset credits. Until codex-lb adds a per-key permission (gcs8/codex-lb#2), use a key whose
  account pool you're comfortable with.
- No telemetry, no shell commands, no background processes. The desktop page polls the
  plugin's own backend once a minute while it's open.

## Known limits

- `/lbfast` reads the session id from Hermes's session context
  (`gateway.session_context`), which isn't a documented plugin API yet;
  `hermes plugins validate` warns about it.
- The built-in `/fast` and the model picker's Fast toggle don't drive this; use `/lbfast`.
  If you had `agent.service_tier` set, it does nothing on a codex-lb provider anyway.
- Per-account detail and error breakdowns need a summary endpoint in codex-lb
  (gcs8/codex-lb#1).

## Development

```bash
HERMES_SRC=/path/to/hermes-agent tests/run.sh        # unit + backend route tests
HERMES_SRC=/path/to/hermes-agent tests/e2e/run.sh    # real Hermes processes, incl. turn isolation
hermes plugins validate .                             # catalog admission checks
```

`HERMES_SRC` is a Hermes checkout with its test environment built
(`python -m pm.build_env --source . --out .venv --group dev --group test`).
Design notes: [docs/DESIGN.md](docs/DESIGN.md). Hook verification: [docs/probes/](docs/probes/).

## License

MIT
