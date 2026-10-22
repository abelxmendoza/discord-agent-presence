# discord-agent-presence

Discord Rich Presence for AI coding tools: **Claude Desktop, ChatGPT / Codex,
Grok Build, Cursor and VS Code**. It shows which projects you're working on, how
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
  Code extension) counts, and so does each Codex thread and running `grok` CLI.
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
- **Model and CI.** The model in use (Opus 5.5, GPT-…). CI status comes from the
  GitHub check runs, for public repos only.
- **Mood verb.** **Watching** while agents work and you're in another window,
  **Competing in** at 5+ agents, otherwise **Playing**.
- **GitHub button** on public repos. Other people can click it; Discord doesn't
  show buttons on your own card.

Linux / X11 only. Pure Python 3, no dependencies (uses `xprop`).

## Install

```bash
git clone git@github.com:abelxmendoza/discord-agent-presence.git
cd discord-agent-presence
./install.sh
```

Then, for each app you want a card for:

1. Go to <https://discord.com/developers/applications> and click **New
   Application**. Its name is what shows after "Playing", e.g. `Claude`,
   `ChatGPT`, `Grok Build`, `Cursor`, `Visual Studio Code`.
2. Paste its **Application ID** into `client_ids` in
   `~/.config/ai-presence/config.json`. Apps you leave blank are skipped.
3. `systemctl --user restart ai-presence`

Useful commands:

```bash
python3 ai_presence.py --detect          # preview every card and rotating line
journalctl --user -u ai-presence -f      # live log
```

## Make this card replace Discord's own VS Code card

Discord detects VS Code by itself and shows a plain "Playing Visual Studio Code"
card with no project. Apps can't turn that off over Discord's local connection,
so do it once yourself:

1. Create the `vscode` application, name it **`Visual Studio Code`** (so it
   looks the same), and add its ID to the config.
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
| `client_ids` | blank | Application ID per app: `claude`, `chatgpt`, `grok`, `cursor`, `vscode` |
| `rotate_seconds` | 20 | How often the second line changes |
| `agent_active_seconds` | 300 | A working agent counts if written to this recently |
| `agent_waiting_seconds` | 1800 | How long a finished agent shows as "waiting for me" |
| `hide_projects` | [] | Shown as "private project"; no git/CI/button for them |
| `show_project`, `show_files`, `show_git`, `show_streak`, `show_model`, `show_ci` | true | Toggle each line |
| `github_button`, `mood_verbs` | true | |
| `images` | `"none"` | `"url"` (PNGs at `image_base_url`) or `"assets"` (uploaded per Discord app) |

**Privacy:** everything on the card is visible to anyone who can see your
profile. That includes project, branch and file names and commit counts.
Commit messages and full paths are never shown.

## Images

See [IMAGE_PROMPTS.md](IMAGE_PROMPTS.md) for a ready-made prompt that generates a
matching icon set (one icon per app, plus agent-count badges 1–9+). The easiest
way to use them is to put the PNGs in `images/` in this repo and set:

```json
"images": "url",
"image_base_url": "https://raw.githubusercontent.com/abelxmendoza/discord-agent-presence/main/images"
```

## How it works

- `/proc` gives app launch times and CLI agents' working directories.
- `xprop` gives the focused window and editor window titles.
- Agent state is read from transcripts: `~/.claude*/projects/*/*.jsonl` and
  `~/.codex/sessions/**/rollout-*.jsonl`.
- Each app gets its own connection to Discord's local IPC socket, so each one
  appears as its own card.
