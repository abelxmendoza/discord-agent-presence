# Card art — prompts for ChatGPT

The cards load images straight from [`images/`](images/) by URL, so there's
nothing to upload to Discord. Any missing image is skipped, so you can add
them a batch at a time. Each batch below is a separate ChatGPT message.
**Paste the shared style block first in every message**, so the batches match.

Where they show: the **big picture** is a rounded square about 100 px wide. The
**small badge** is a circle about 30 px wide in its bottom-right corner, so
badges must read clearly in a circle at tiny size.

---

## Shared style (paste at the top of every batch)

```
Make square 1024x1024 PNG icons, one per image, each labeled with its exact
file name. Shared style for all of them:
- Dark cyberpunk "mission control" aesthetic: near-black navy background
  (#0B0F1A) with a faint hex-grid texture and a soft vignette.
- Glowing neon line-art like an illuminated HUD, thick strokes, subtle bloom.
  No photorealism. No text except numerals where specified.
- Centered and symmetrical, with generous padding.
- No real company logos or trademarks. Use original abstract symbols that
  evoke each tool.
If you can, give me a zip with all the files named exactly as listed.
```

## Batch 1 — the big pictures (15)

```
Five app symbols, each in three moods = 15 images. Rounded-square framing, the
symbol fills ~70% of the canvas.

Symbols:
- claude  : a radiant starburst glyph with 8–12 tapered rays, coral-orange
            (#F97316 → #FDBA74)
- chatgpt : an interlocking six-loop rosette knot with a tiny "</>" at the
            center, mint-teal (#10B981 → #6EE7B7)
- grok    : an event-horizon ring with a bright diagonal streak through it,
            white with violet (#8B5CF6) rim light
- cursor  : an isometric wireframe arrow pointer, silver with electric-blue
            (#3B82F6) edges
- vscode  : an abstract folded ribbon forming a "<" chevron, azure (#0EA5E9)
- gemini  : a four-pointed sparkle star, blue-to-violet gradient (#4285F4 → #A142F4)
- muse    : an abstract infinity-loop ribbon, Meta blue (#0866FF) with cyan edges
- copilot : a friendly pilot-goggles visor glyph, white with purple glow (#A371F7)
- opencode: an open angle-bracket "{ }" portal, warm peach (#FAB283)
- aider   : two overlapping speech bubbles with a code caret, green (#14B014)
- windsurf: a stylized wave with a sail, aqua (#58E6D9)
- kiro    : a friendly ghost-like rounded glyph, purple (#9046FF)

Moods (same symbol, same position, so switching between them looks like an
animation frame):
- <name>_cooking : full brightness, strong glow, small motion/speed lines and
                   sparks orbiting the symbol, an energized feel
- <name>_waiting : symbol tinted warm amber (#F59E0B), a soft pulsing halo
                   ring around it, calm "your turn" feel
- <name>_idle    : desaturated grey, dim, almost no glow, sleepy

Files: <app>_cooking, <app>_waiting, <app>_idle for each app:
claude, chatgpt, grok, gemini, muse, copilot, opencode, aider, cursor,
windsurf, kiro, vscode.
Optional extra moods per app: <app>_success (celebrating, green), <app>_failed
(frustrated, red) and <app>_crew (three mascots at a command center).
```

## Batch 2 — agent-count badges (9)

```
Nine circular badges. Keep everything inside the central 80% circle; nothing
important near the corners.
- A glowing circular ring, like a reactor core or status light.
- In the center, one huge bold numeral (heavy geometric sans-serif, bright
  white with a colored glow, ~55% of the badge height): 1 through 8, and
  "9+" for the last one.
- Small dots around the ring equal to the number; 9+ gets a full continuous
  ring.
- Color goes from cool to hot: 1 cyan #22D3EE, 2 teal #2DD4BF, 3 green
  #4ADE80, 4 lime #A3E635, 5 yellow #FACC15, 6 orange #FB923C,
  7 red-orange #F87171, 8 hot pink #F472B6, 9+ magenta #E879F9 (extra intense).
Files: agents_1 … agents_9
```

## Batch 3 — action badges (15)

```
Fifteen circular badges, one bold glowing glyph each, kept inside the central
80% circle. Read at 30 px: one simple shape, thick lines, one main color plus
white highlights.
- act_edit      : a pen nib writing a glowing line (cyan)
- act_read      : an open book with light rising from the pages (sky blue)
- act_search    : a magnifying glass over code brackets (teal)
- act_web       : a wireframe globe (blue)
- act_subagent  : a small robot head with antenna (purple)
- act_browser   : a browser window with a play triangle (indigo)
- act_test      : a bubbling test tube (green)
- act_build     : a hammer striking with sparks (orange)
- act_commit    : a sealed package box with a check (amber)
- act_push      : a rocket launching upward (red-orange)
- act_sim       : a top-down quadcopter drone (electric cyan)
- act_shell     : a terminal prompt ">_" (lime)
- act_working   : a lightning bolt (yellow)
- act_thinking  : a thought bubble with three dots (lavender)
- act_waiting   : an hourglass with glowing sand (amber, pulsing)
```

## Batch 4 — status badges (7)

```
Seven circular badges in the same style, inside the central 80% circle.
- git          : a git-branch glyph (two nodes forking), green #22C55E
- streak       : a single flame, orange
- streak_big   : a large roaring flame with a ring of embers, orange-to-magenta
- model_claude : an abstract starburst (like the claude symbol, simplified),
                 coral
- model_gpt    : an abstract six-loop knot (like the chatgpt symbol,
                 simplified), mint
- model_gemini : a four-pointed sparkle, blue-violet
- model_muse   : an infinity-loop ribbon, Meta blue
- model_grok   : an event-horizon ring with a diagonal streak, white/violet
- ci_green     : a bold check mark inside a shield, green
- ci_fail      : a bold X inside a shield, red
- ci_running   : a circular spinner arc, yellow
```

---

Save every file into `images/` with the exact name plus `.png`, then commit and
push. The cards pick them up within a few hours, or right away after
`systemctl --user restart ai-presence`. Optional fallbacks: `<app>.png`
(e.g. `claude.png`) and `logo.png` are used when a mood image is missing.
