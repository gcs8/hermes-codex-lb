# hermes-codex-lb

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin for people who route
Hermes through a [codex-lb](https://github.com/Soju06/codex-lb) gateway.

**Status: design stage. Nothing to install yet.** See [docs/DESIGN.md](docs/DESIGN.md) and the issues.

## Planned features

- `/lbfast on|off|auto`: ask codex-lb for the priority (fast) tier per session. Hermes core
  only sends that tier to first-party OpenAI endpoints, so this is an explicit opt-in for
  your own gateway.
- Served-tier tracking: counts requests that asked for priority and what the gateway
  actually served, so a silent downgrade shows up.
- Desktop status chip and pane: pool window usage (5h and weekly), resets, credits, cache
  hit rate.

## What it will do on your machine (disclosure)

- Sends HTTP requests to the codex-lb base URL(s) you configure, using an API key you give
  the plugin through Hermes's plugin settings. No other network calls.
- Adds `service_tier` to model requests bound for those URLs, only when you turn fast mode on.
  The priority tier uses more of your subscription quota.
- Stores per-session fast-mode state and request counters in its own plugin storage.
- No telemetry, no shell commands, no background processes beyond an in-process usage poll.

## License

MIT
