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
- **Requested vs reported tier**: every response from codex-lb is checked for the tier it
  reports, and `/lbfast status` and the desktop page show the counts. The ChatGPT
  subscription backend reports `default` even when a priority request is applied, so a
  reported `default` is not proof that fast didn't run (gcs8/codex-lb#3).
- **Codex-LB page in Hermes Desktop** (sidebar entry + status-bar chip): pool remaining for
  the 5-hour and weekly windows, request/token/cost totals for the plugin's key, reported-tier
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

On [gcs8/codex-lb](https://github.com/gcs8/codex-lb) (fork builds from 2026-10-16 on) a key
also carries **Allow reset-credit redemption**. Leave it off for `read_only` (the default for
new keys) and codex-lb refuses redeems from the key with 403, whatever the plugin is set to.
Turn it on for `read_write`, otherwise the Redeem button always fails. Keys created before
that build were backfilled with the permission on; check them. Upstream Soju06/codex-lb has
no such permission yet.

The same settings live in `config.yaml` under `plugins.entries.codex-lb.settings`.

## What it does on your machine (disclosure)

- **Network:** HTTP requests to the codex-lb base URL(s) you configure (`/v1/usage`,
  `/v1/reset-credit`), with the API key you set. Nothing else.
- **Model requests:** adds `service_tier: "priority"` to requests bound for those URLs, only
  while fast mode is on for that session. The priority tier uses more of your subscription quota.
- **Writes upstream:** redeeming a reset credit (`POST /v1/reset-credit`), only in
  `read_write` mode and only after you confirm in the dialog. Redeems are logged locally.
- **Local storage:** `plugin-data/codex-lb/state.db` in your Hermes profile: per-session fast
  mode, reported-tier counts and the redeem log. No prompts or responses are stored.
- **Read-only is enforced by codex-lb only on gcs8/codex-lb.** There, a key without
  "Allow reset-credit redemption" cannot redeem (gcs8/codex-lb#2 shipped 2026-10-16).
  Upstream codex-lb lets any key that can read usage also redeem, so against upstream use a
  key whose account pool you're comfortable with.
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

`tests/run.sh` also requires Node.js 22 or newer and runs the desktop component tests.
They load the complete uncompiled module with synthetic SDK and React hook doubles,
covering weekly-only pools, zero remaining capacity, backend errors, cached-token labels
and reported-tier wording. These tests do not exercise real React rendering, CSS,
React Query scheduling or a live Hermes Desktop. Catalog screenshots and live acceptance
remain separate checks. To run just that synthetic component suite:

```bash
node --experimental-vm-modules --test tests/desktop/test_plugin.mjs
```

The unit runner keeps its HOME, TMPDIR and Hermes runtime directory inside its disposable
scratch copy. Set TMPDIR before invoking it to choose the scratch parent.

## License

MIT
