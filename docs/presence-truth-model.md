# Presence truth model — implementation contract

## Observed regression (2026-10-25)
Published profile `agent-presence-status/status.json` contained `agents[0].status="waiting"`, `state="coding"`, and `last_active="now"`. This must not be interpreted as active human coding. This is a snapshot, not proof the machine is currently online.

## State semantics
Compute independently, never derive one from another:
- `machine_reachable`: fresh signed heartbeat (TTL 150 s), not proof of keyboard activity or power state.
- `human_active`: supported OS idle sensor shows recent input (default <= 120 s) AND relevant editor focus; otherwise `unknown`, not `true`.
- `agent_executing`: fresh execution evidence (tool start/end, running process with verified active job, or provider event); a process merely open, a waiting prompt, and an idle session do not count.
- `agent_waiting`: explicitly waiting for human/tool; not executing.
- `mobile_origin`: authenticated job origin metadata from a mobile client, NOT inferred from iPhone connectivity or a ChatGPT app being open.
- `public_visibility`: `on | off | unknown`, with off winning. Never publish human activity from `unknown` visibility if Discord sync is required.

Published `state` is `coding` only with human_active or agent_executing. Else `waiting` if waiting agents exist, `idle` if reachable, `offline` if stale. `last_active` must be an absolute UTC timestamp of last verified activity; never the string `now`. Include `published_at`, `heartbeat_at`, `schema_version`, `tracking_since`, and per-machine `last_seen_at`.

## Cross-machine aggregation
Aggregate all linked, signed machine snapshots with stable machine IDs. Retain simultaneous activity on Mac, Windows, Linux and headless nodes. Focus machine follows latest verified human input, but all executing agents remain visible. Count agent execution intervals per agent; merge overlapping human-input intervals before calculating human time. Never count waiting/sleep as work. Deduplicate by job ID across machines; keep machine/agent provenance.

Mobile is a job-origin label on an executing agent, not a fake phone daemon. Render e.g. `📱 iPhone → 💻 MacBook · Codex executing` only while a linked job has fresh execution evidence. A mobile browsing session does not count.

## Discord privacy gate
Do not use user tokens, self-bots, scraping, or undocumented APIs to read global Discord online status. Rich Presence RPC alone does not guarantee authoritative global status. Offer explicit local `public_presence_enabled` switch immediately; gate Discord, website JSON/SVG, and README publisher together. For automatic sync, use only supported OAuth/Gateway integration with appropriate consent and documented access to presence, if feasible. If Discord status is unavailable or ambiguous, fail closed when sync is enabled; do not claim a live sync.

When visibility goes off, publish a neutral offline/hidden card or stop publishing and ensure website TTL hides stale data. Never expose private project paths, secrets, raw prompts or personal activity logs.

## Acceptance tests (must be implemented before merging)
1. One waiting Muse agent + no input => `waiting`, not `coding`; `last_active` unchanged.
2. Sleeping machine with stale heartbeat => offline after TTL; website ignores stale cached JSON even if SVG CDN lags.
3. Active Mac + active Linux => two machines, both jobs, no double-counted human time.
4. Switch human focus Mac→Windows => focused label changes, remote job remains listed.
5. iPhone opens ChatGPT without coding => no mobile coding state.
6. Authenticated mobile-origin agent starts actual tool execution => mobile origin displayed until execution ends; waiting returns to waiting.
7. Public visibility off => no live Discord or website/README status; stale consumers fail closed.
8. Clock skew, duplicate packets, malformed status, and absent idle sensor => unknown/fail-safe, never invented activity.
9. Stats only claim coverage since `tracking_since`; historic gaps remain unknown.

## Rollout
Audit publisher, renderer, detector, linked-peer relay and tests. Add schema backward-compatible with v2 consumers; add TTL validation in every web consumer; test locally with frozen clocks and fixture packets; verify Discord-supported visibility integration separately; only then deploy. Do not change production publisher based solely on this contract.
