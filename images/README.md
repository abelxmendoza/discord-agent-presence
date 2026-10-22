# images

Card art loaded straight from this folder by URL. Any missing file is skipped,
so it's fine to add these a few at a time. All PNG, 1024×1024.
See [../IMAGE_PROMPTS.md](../IMAGE_PROMPTS.md) for the generation prompt.

Done so far: `claude` (all eight moods, incl. `push` and `sim`), `chatgpt` (cooking / waiting / idle), `hero`.

**Big picture** (app × mood): `<app>_cooking`, `_waiting`, `_idle`, plus optional
`_success` (tests/build just passed), `_failed` (failing tests/build or red CI) and
`_crew` (3+ busy agents), `_push` (pushing / just pushed) and `_sim` (simulator running). A missing optional mood falls back to cooking/waiting,
then `<app>.png`, then `logo.png`. Apps: `claude` `chatgpt` `grok` `gemini` `muse` `copilot` `opencode` `aider`
`cursor` `windsurf` `kiro` `vscode`.

**Small badge** (follows the rotating line):
- agent count: `agents_1` … `agents_9` (9 = 9+)
- actions: `act_edit` `act_read` `act_search` `act_web` `act_subagent` `act_browser`
  `act_test` `act_build` `act_commit` `act_push` `act_sim` `act_shell`
  `act_working` `act_thinking` `act_waiting`
- `git` · `streak` · `streak_big` (7+ days) · `model_claude` · `model_gpt` · `model_gemini` · `model_muse` · `model_grok`
- CI: `ci_green` `ci_fail` `ci_running`
