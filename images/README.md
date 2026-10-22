# images

Card art, loaded by URL from this folder (`.jpg` first, then `.png`). Missing
files are skipped. See [../IMAGE_PROMPTS.md](../IMAGE_PROMPTS.md) to make new art.

**Big picture:** `<tool>_<mood>` for every tool (`claude` `chatgpt` `gemini`
`grok` `muse` `copilot` `opencode` `aider` `cursor` `windsurf` `kiro` `vscode`)
and mood (`cooking` `waiting` `success` `failed` `idle` `crew` `push` `sim`).
Claude's set and ChatGPT's cooking / waiting / idle are hand-made; the rest come
from `tools/make_tool_art.py`. All tools share `claude_sim`. `hero.png` is the
README banner.

**Small badge** (not made yet; the card leaves the badge off until they exist):
- agent count: `agents_1` … `agents_9` (9 = 9+)
- actions: `act_edit` `act_read` `act_search` `act_web` `act_subagent` `act_browser`
  `act_test` `act_build` `act_commit` `act_push` `act_sim` `act_shell`
  `act_working` `act_thinking` `act_waiting`
- `git` `streak` `streak_big` `ci_green` `ci_fail` `ci_running`
- `model_claude` `model_gpt` `model_gemini` `model_muse` `model_grok`
