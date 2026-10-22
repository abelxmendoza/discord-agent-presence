<p align="center">
  <img src="images/hero.png" alt="Neon robot mascots coding with Claude and ChatGPT" width="720">
</p>

<h1 align="center">discord-agent-presence</h1>

<p align="center">
  <a href="https://github.com/abelxmendoza/discord-agent-presence/releases/latest"><img src="https://img.shields.io/badge/⬇_Download-latest_release-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Download latest release"></a>
  <a href="#install"><img src="https://img.shields.io/badge/One--line_install-curl_%7C_bash-22D3EE?style=for-the-badge&logo=gnubash&logoColor=white" alt="One-line install"></a>
  <a href="https://abelxmendoza.github.io/discord-agent-presence/"><img src="https://img.shields.io/badge/Website-live_demo-F97316?style=for-the-badge&logo=githubpages&logoColor=white" alt="Website"></a>
</p>

Discord Rich Presence for AI coding tools: **Claude, ChatGPT / Codex, Gemini
CLI, GitHub Copilot CLI, Grok Build, Muse Code, OpenCode, Aider, Cursor,
Windsurf, Kiro and VS Code**. It shows which projects you're working on, how
many AI agents are running, and what they're doing right now.

It behaves like Discord's built-in app detection (the "Playing Visual Studio
Code" card): one card per open app, a timer from when the app launched, and the
card goes away when you close the app. Each card also rotates through live lines:

```
Watching Claude
▸ px4-autonomous-mission ✦ Omega-Code
  ↻ ⚡ 2 cooking · ⏳ 1 waiting for me  (2 of 3)
  ↻ 🧪 running tests · px4-autonomous-mission
  ↻ 🌿 feat/sim-asset-pipeline · +159 −18
  ↻ 🔥 3-day streak · 📦 2 commits today
  ↻ 🧠 Opus 5.5 · ✅ CI green
[ ⭐ View on GitHub ]
```

- **Agents.** Each Claude Code session (from the desktop app, the CLI or the VS
  Code extension) counts, and so does each Codex thread, Gemini CLI session,
  and running Copilot, OpenCode, Aider, Grok or Muse CLI.
  An agent is "cooking" while its transcript is being written, and "waiting for
  me" once it finishes, asks you something, or sits on a permission prompt.
- **Live actions.** The agent's latest tool call becomes a line like ✍ editing
  foo.py, 🧪 running tests, 🔨 building, 🛸 running the sim, 🌐 researching,
  📦 committing or 🚀 pushing.
- **Projects.** Agent working directories and open Cursor / VS Code windows each
  add their project, shown as the git repo name.
- **Git.** The branch and uncommitted +/− lines for the top project, plus your
  commits today.
- **Streak.** Consecutive days with commits or an active card.
- **Pass / fail.** The result of the newest test or build run each agent did,
  read from its command exit codes: ✅ tests passed / ❌ build failed.
- **Model and CI.** The model in use (Opus 5.5, GPT-…). CI status comes from the
  GitHub check runs, for public repos only.
- **Mood verb.** **Watching** while agents work and you're in another window,
  **Competing in** at 5+ agents, otherwise **Playing**.
- **GitHub button** on public repos. Other people can click it; Discord doesn't
  show buttons on your own card.

Linux / X11 only. Pure Python 3, no dependencies (uses `xprop`).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/abelxmendoza/discord-agent-presence/main/install.sh | bash
```

That's it. Cards use shared Discord applications that ship with the tool, so
there's nothing to set up. The installer clones to
`~/.local/share/discord-agent-presence`, adds the `agent-presence` command and
starts a login service. Other ways to install:

```bash
pipx install git+https://github.com/abelxmendoza/discord-agent-presence && agent-presence setup
# or from a clone:
git clone https://github.com/abelxmendoza/discord-agent-presence && ./discord-agent-presence/install.sh
```

| Command | |
|---|---|
| `agent-presence doctor` | Checks Discord, X11 and which apps are installed and have cards |
| `agent-presence detect` | Previews every card, rotating line and image |
| `agent-presence setup` / `uninstall` | Adds / removes the login service |
| `agent-presence run` | Runs in the foreground |

Live log: `journalctl --user -u ai-presence -f`

### Use your own Discord applications (optional)

To change the name after "Playing" or the app icon, create an application at
<https://discord.com/developers/applications> and put its **Application ID**
under `client_ids` in `~/.config/ai-presence/config.json`. Set an app to
`"off"` to hide its card.

## Images that change

Each card has two image slots, and both change as you work:

- **Big picture:** the app in its current mood. *cooking* while agents
  work, *crew* with 3+ agents, *success* right after tests or a build pass,
  *failed* when they fail or CI is red, *waiting* when agents need you, and
  *idle* when the app is just open.
- **Small badge:** follows the rotating line. It shows the agent count, the
  current action (test tube, hammer, pencil, drone…), the git branch, a streak
  flame, the model, or CI status.

Images load from [`images/`](images/) in this repo, and missing ones are
skipped. [IMAGE_PROMPTS.md](IMAGE_PROMPTS.md) has ready-to-paste prompts that
generate the whole set. Forks can point `image_base_url` at their own art.

## Make this card replace Discord's own VS Code card

Discord detects VS Code by itself and shows a plain "Playing Visual Studio Code"
card with no project. Apps can't turn that off over Discord's local connection,
so do it once yourself:

1. Make sure the VS Code card is on (`agent-presence doctor`).
2. In Discord, go to **User Settings → Registered Games** (called **Activity
   Settings → Registered Games** in some versions), find **Visual Studio Code**
   and remove it, or turn off its detection.
3. If it keeps coming back, turn off **Activity Privacy → "Share my activity"
   for detected games** and keep "Share activity from Rich Presence apps" on.
   This hides only auto-detected games. Rich Presence cards like these still
   show.

After that, the card from this tool is the only VS Code card and keeps the top
spot in the member list.

## Config

`~/.config/ai-presence/config.json` (created on first run):

| Key | Default | |
|---|---|---|
| `rotate_seconds` | 20 | How often the second line changes |
| `agent_active_seconds` | 300 | A working agent counts if written to this recently |
| `agent_waiting_seconds` | 1800 | How long a finished agent shows as "waiting for me" |
| `hide_projects` | [] | Shown as "private project"; no git/CI/button for them |
| `show_project`, `show_files`, `show_git`, `show_streak`, `show_model`, `show_ci` | true | Toggle each line |
| `github_button`, `mood_verbs` | true | |
| `client_ids` | shared apps | Your own Application ID per app, or `"off"` |
| `images` | `"url"` | `"url"` (PNGs at `image_base_url`), `"assets"` (uploaded per Discord app) or `"none"` |

**Privacy:** everything on the card is visible to anyone who can see your
profile. That includes project, branch and file names and commit counts.
Commit messages and full paths are never shown.

## Supported tools

| Tool | How it's detected | Live actions | Pass / fail |
|---|---|---|---|
| Claude (Desktop, Code CLI, VS Code ext.) | session logs in `~/.claude*/projects` | ✓ | ✓ |
| ChatGPT / Codex (desktop, CLI) | session logs in `~/.codex/sessions` | ✓ | ✓ |
| Gemini CLI (Google) | session files in `~/.gemini/tmp/*/chats` | ✓ | ✓ |
| GitHub Copilot CLI | `copilot` process + its session folder's activity | — | — |
| OpenCode | `opencode` process + its storage's activity | — | — |
| Aider | `aider` process + the project's `.aider.chat.history.md` | — | — |
| Muse Code (Meta) | `muse` process | — | — |
| Grok Build (xAI) | `grok` process | — | — |
| Cursor, Windsurf, Kiro, VS Code | window titles | — | — |

The process-detected CLIs don't have a documented session log format, so their
contents aren't read. Each one counts as "working" while it's writing its
session or history file or using CPU, and as "waiting for me" otherwise. Gemini
support follows its documented session format. If you use any of these and
something looks off, please open an issue.

## How it works

- `/proc` gives app launch times and CLI agents' working directories.
- `xprop` gives the focused window and editor window titles.
- Agent state is read from transcripts: `~/.claude*/projects/*/*.jsonl` and
  `~/.codex/sessions/**/rollout-*.jsonl`.
- Each app gets its own connection to Discord's local IPC socket, so each one
  appears as its own card.
