# Hook probe: Hermes v0.22.0

Question: can a plain user plugin, using only documented APIs, set `service_tier` on
requests to a codex-lb (`codex_responses`) provider and read back the tier it was served,
in every runtime Hermes Desktop uses?

Answer: **yes.** No Hermes core change is needed for the fast toggle or served-tier tracking.

## How it was run

`test_zz_codexlb_plugin_probe.py` was dropped into a Hermes v0.22.0 checkout under
`tests/e2e/core/providers/` and run with the repo's own harness:

```bash
scripts/run_tests.sh --include-integration tests/e2e/core/providers/test_zz_codexlb_plugin_probe.py
```

It writes a real plugin into a disposable `HERMES_HOME/plugins/`, enables it, and drives real
Hermes processes against Hermes's fake Responses server (`tests/fakes/providers/openai_responses.py`),
which validates every request against the OpenAI SDK schema. The fake was patched to answer every
response with `service_tier: "default"`, the way codex-lb 1.25.0-beta.9 downgrades priority.

## Results (`results-v0.22.0.jsonl`)

| Runtime | `llm_request` fired | Wire `service_tier` | `llm_execution` saw served tier | `/command` ran in |
|---|---|---|---|---|
| CLI one-shot (`hermes -z`) | yes | `priority` | `default` | n/a |
| tui_gateway, in-process | yes | `priority` x2 turns | `default` | same process |
| tui_gateway, `dashboard.turn_isolation: true` | yes, **in the compute-host child** (`HERMES_COMPUTE_HOST_CHILD=1`) | `priority` x2 turns | `default` | **parent** process |

All 4 cases passed. Requests stayed valid against the SDK schema.

## What this means for the design

1. `llm_request` middleware works on `codex_responses` and inside the compute-host child.
   `base_url`, `provider`, `api_mode` and `session_id` are in the middleware context.
2. `llm_execution` middleware gets the parsed response; `result.service_tier` is the
   served tier. That's the served-tier tracker.
3. **Process split under turn isolation.** Slash commands run in the parent gateway process,
   but model requests run in the compute-host child. In-memory state set by `/lbfast` would be
   invisible to the middleware. Per-session fast state must live in shared storage:
   `plugins.plugin_storage.plugin_db("codex-lb")` (SQLite, WAL) under the profile's
   `plugin-data/` dir, read by the middleware on each request.
4. **Session id differs by hook.** `llm_request` gets `session_id` in its context;
   `llm_execution` didn't (it got `None`). Slash commands read it from
   `gateway.session_context.get_session_env("HERMES_SESSION_ID")`. The command's id matched
   the middleware's id in every run, so that's the join key. The execution wrapper should
   take the id from the request middleware (e.g. stash it on the request) rather than its
   own kwargs.
5. `provider` differs by path (`custom` in-process, the provider name in the child), so match
   on `base_url`, not provider name.

## Not covered

- The desktop renderer half (`desktop/plugin.js`) and the dashboard backend (`plugin_api.py`).
- A real codex-lb (the fake stands in for it).
