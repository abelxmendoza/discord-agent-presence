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

## Multi-device web publishing
The link/relay aggregation above serves the Discord card, but it needs the machines linked and one leader awake to publish. The website must also work when machines are on different networks, are rarely on, and were never linked — so each machine additionally publishes its own file, `status.d/<machine-id>.json`, on the `agent-presence-status` branch, next to the combined `status.json`/`status.svg`. Publishers use a union write that preserves other machines' `status.d/` files; no publisher may erase a machine it isn't.

The web consumer lists `status.d/`, fetches every file, and merges them under these rules:
- Each payload is validated independently (freshness TTL 150 s, coding only with verified activity). A stale machine drops out of the merged view on its own — it can neither hide nor fake the others. If all are stale, the merged view is offline.
- The merged state is the strongest judged state across fresh machines; agents, machines, projects and tools are unioned with per-machine provenance. Busy agents are listed from any fresh machine; waiting agents only from machines judged coding/waiting — see the next section.
- The legacy single `status.json` remains the fallback when no `status.d/` files exist (and, during transition, is still written by the publishing machine's combined view, last-writer-wins among unlinked publishers).

## Only the AI in use, only genuine activity
Presence lists agents with genuine session activity — an executing job, or a session verifiably waiting on the human in a coding context. A tool is never listed merely because it is installed or detected on a machine: no idle Codex/Cursor/Claude entries padding the card, and no entries at all for AIs that are not in use (asking another AI questions is not presence). Chat surfaces follow the stricter rule below: conversation alone is not programming work.

A headless/chat backend node (e.g. assistant sessions executing on a server) publishes only on execution evidence of programming work — tool calls that execute, write or edit inside real project work (e.g. a git worktree) in the user's own sessions. Message recency, an open chat, or a waiting card from such a node is never published: no evidence, no node.

## Coding streak — union of sources
A coding day is a calendar day in America/Los_Angeles with at least one commit authored by the user. The streak (current run, longest run, total days) is the UNION of named sources, deduplicated by day; every day in the computed data names at least one source that evidences it:
- `github` — commits authored by the user pushed to GitHub (commit search over full history plus the recent events feed; a day is the commit's author date in America/Los_Angeles). Authoritative for what it covers.
- `local-git:<machine>` — commits authored by the user in that machine's local git repos, including unpushed ones (`git log --all --author`). You can code without pushing: GitHub silence never erases a locally evidenced day.
- `daemon:<machine>` — days that machine's daemon itself observed live coding activity (secondary, observational source).

A machine's local days travel in its packet and join the union when it next publishes; peers cache them, so an offline machine's days are not lost. If GitHub is unreachable, its last fetched days are used and the published streak is marked `streak_cached`; if no source has data, the streak is omitted — never replaced by a local day counter's invention. Published payloads carry the streak's per-source day counts (`streak_provenance`) and total days (`streak_days_total`); the full per-day provenance is kept in the publisher's computed union file.

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
10. Three machines fresh at once (Mac coding, Linux waiting, headless backend coding with a mobile-origin job) => all three render simultaneously, each with its own agents and provenance.
11. One machine stale + one fresh => the fresh machine renders; the stale one drops individually, never hiding or faking the other. All stale => overall offline.
12. A legacy single-file `status.json` (no `status.d/`) still renders exactly as before.
13. A streak day evidenced only by an unpushed local commit counts (union semantics); a day claimed by no source does not; per-day provenance names the source(s).

## Rollout
Audit publisher, renderer, detector, linked-peer relay and tests. Add schema backward-compatible with v2 consumers; add TTL validation in every web consumer; test locally with frozen clocks and fixture packets; verify Discord-supported visibility integration separately; only then deploy. Do not change production publisher based solely on this contract.
