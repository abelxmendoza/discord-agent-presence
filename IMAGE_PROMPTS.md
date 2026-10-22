# Art for the Discord presence — paste into ChatGPT

Paste everything in the box below into ChatGPT (image generation). It makes
15 images. See "Where they go" at the bottom for how to hook them up.

How Discord shows them: the big image is a rounded square about 100 px wide. The
small image is a **circle** in its bottom-right corner, about 30 px wide. Anything
outside the center circle of a small image gets cut off, and small details
disappear at that size.

---

```
I need a matching set of 15 square PNG icons for my Discord Rich Presence,
which shows what AI coding tools I'm using and how many AI agents are running.
Generate them one at a time, each 1024x1024, and label each with its file name.

SHARED STYLE (apply to every image):
- Dark cyberpunk / synthwave "mission control" aesthetic.
- Background: near-black deep navy (#0B0F1A) with a faint circuit-trace
  or hex-grid texture and a soft vignette.
- Glowing neon line-art, like an illuminated HUD. Thick strokes (must read
  clearly when shrunk to 30 px). Subtle bloom/glow, no photorealism.
- Flat-on, centered, symmetrical composition, generous padding.
- No text other than the numerals specified. No real company logos or
  trademarks — use original abstract symbols evoking each tool.

BIG ICONS (rounded-square framing, subject fills ~70% of canvas):
1. logo.png    — A neon terminal cursor ">_" inside a hexagon, orbited by three
                 small glowing dots (agents). Cyan #22D3EE + magenta #E879F9.
2. claude.png  — A radiant starburst / sunburst glyph with 8–12 tapered rays.
                 Warm coral-orange glow #F97316 → #FDBA74.
3. chatgpt.png — An interlocking knot of six rounded loops forming a flower-
                 like rosette, with a small "</>" at its center.
                 Mint-teal glow #10B981 → #6EE7B7.
4. grok.png    — A bold slashed-diagonal monogram-style glyph forming an
                 abstract black-hole / event-horizon ring with a bright
                 diagonal streak through it. White #F8FAFC with violet
                 #8B5CF6 rim light.
5. cursor.png  — A 3D isometric arrow pointer made of glowing wireframe
                 facets, slightly tilted. Silver-white with electric blue
                 #3B82F6 edges.
6. vscode.png  — An abstract folded ribbon forming a chevron "<" bracket
                 shape. Azure blue glow #0EA5E9.

AGENT-COUNT BADGES (these appear as a tiny CIRCLE overlay, so design for
a circle: keep everything inside the central 80% circle, nothing important
near the corners):
7–15. agents_1.png, agents_2.png, ... agents_9.png
   - A glowing circular ring badge (like a reactor core / status light).
   - In the center, one huge bold numeral: 1, 2, 3, 4, 5, 6, 7, 8 —
     and for agents_9 use "9+". The numeral should take ~55% of the badge
     height, heavy geometric sans-serif, bright white with a colored glow.
   - Around the ring, small dots equal to the number (1 dot for 1, up to
     8 dots; 9+ gets a full continuous glowing ring).
   - Color ramps from cool to hot as the count rises, so it reads at a
     glance: 1 cyan #22D3EE, 2 teal #2DD4BF, 3 green #4ADE80,
     4 lime #A3E635, 5 yellow #FACC15, 6 orange #FB923C,
     7 red-orange #F87171, 8 hot pink #F472B6, 9+ magenta #E879F9
     with an extra-intense pulse glow.
   - Same dark background as the others.

After generating, give me a downloadable zip with all 15 PNGs named exactly
as above if you can; otherwise, list them in order so I can save each one.
```

---

## Where they go

Each app (Claude, ChatGPT, …) has its own Discord application, so pick one:

- **`"images": "url"` (easiest):** put all 15 PNGs in `images/` in this repo and set
  `image_base_url` to its raw URL. The script uses `claude.png`, `chatgpt.png`,
  `grok.png`, `cursor.png`, `vscode.png` and `agents_1.png` … `agents_9.png` directly.
- **`"images": "assets"`:** in each Discord application's Art Assets, upload that
  app's icon with the name **`logo`**, plus `agents_1` … `agents_9`.
  (You don't need `logo.png` from the prompt in this mode.)

`agents_9` means 9 or more. The badge only appears when that app has agents running.
Discord can take a few minutes to start showing new art.
