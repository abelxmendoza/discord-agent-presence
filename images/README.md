# images

Card art loaded straight from this folder by URL. Any missing file is skipped,
so it's fine to add these a few at a time. All PNG, 1024×1024.
See [../IMAGE_PROMPTS.md](../IMAGE_PROMPTS.md) for the generation prompt.

Done so far: `claude` and `chatgpt` (all three moods), `hero`.

**Big picture** (app × mood), with `<app>.png` and `logo.png` used as fallbacks:
`claude_cooking` `claude_waiting` `claude_idle` · `chatgpt_*` · `grok_*` · `cursor_*` · `vscode_*`

**Small badge** (follows the rotating line):
- agent count: `agents_1` … `agents_9` (9 = 9+)
- actions: `act_edit` `act_read` `act_search` `act_web` `act_subagent` `act_browser`
  `act_test` `act_build` `act_commit` `act_push` `act_sim` `act_shell`
  `act_working` `act_thinking` `act_waiting`
- `git` · `streak` · `streak_big` (7+ days) · `model_claude` · `model_gpt`
- CI: `ci_green` `ci_fail` `ci_running`
