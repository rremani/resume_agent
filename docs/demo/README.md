# Recording the demo

Two ways to make the README GIF. Both record a **real** run (honest, not staged).

## Option A — asciinema + agg (robust, recommended for a first take)

Records at your own pace; idle time is compressed so the LLM waits don't drag.

```bash
brew install asciinema agg          # macOS (or your package manager)

# record — do the run below, then Ctrl-D to stop
asciinema rec docs/demo/demo.cast --cols 92 --rows 30 --idle-time-limit 2

# convert to GIF
agg --theme monokai --font-size 16 docs/demo/demo.cast docs/demo/demo.gif
```

While recording, do this ~40s run:

1. `resume`  → wait for the greeting.
2. Paste the JD:  `pbcopy < docs/demo/sample-jd.txt` beforehand, then paste it in the
   chat (multi-line paste works — Enter to submit).
3. Answer one gap briefly (e.g. *"yes, shipped a RAG pipeline with a feature store"*),
   then `skip` the metrics ask.
4. `2` to open the first role → `tighten 1` → `back` → `done`.
5. Let it render, then Ctrl-D to stop.

`--idle-time-limit 2` caps any pause at 2s, so the model latency won't bloat the GIF.

## Option B — VHS (scripted & reproducible)

```bash
brew install vhs
vhs docs/demo/demo.tape        # → docs/demo/demo.gif
```

Edit the `Sleep` values in `demo.tape` to match your model's latency (do one dry run
first). Uses a single-line target on purpose — a multi-line paste would submit early.

## Embed in the README

Replace the `<!-- TODO -->` line near the top of the main `README.md` with:

```html
<p align="center"><img src="docs/demo/demo.gif" alt="Resume Agent demo" width="800"></p>
```

Keep it under ~2 MB if you can (trim to ~40s, monokai/mocha theme, 15–16px font) so it
loads fast on the repo page.
