# paper2vid

Paste an arXiv link. Read the paper's own figures, annotated, with somewhere to
ask what the caption assumed you already knew.

```bash
pip install -e .
paper2vid --serve
```

Runs on your machine, on your key. There is no hosted version and nothing is
proxied through anyone else.

---

## What it actually does

Most papers get read by finding the one plot that matters and working backwards
from it. So this doesn't summarise the text and draw new pictures — it pulls
**the paper's own figures** out of arXiv's HTML, marks them up, and builds a
page around them.

The parts that took longest to get right:

**It reads arXiv's HTML, not the PDF.** arXiv has generated HTML from submitted
LaTeX since late 2023, which hands over the section tree, the figures as
separate image files, captions already attached to their figure, and equations
with the LaTeX intact. Parsing the PDF instead means fighting two-column layout
and losing figure boundaries.

**Markers get checked against the image.** The model that writes the storyboard
has never seen the figures — it works from captions. Left alone it produces
tidy symmetric coordinates like 0.28/0.28 that land on empty axis space, and
labels asserting numbers it never read. So a second pass sends each figure back
with its proposed markers and asks: where does this really go, and can you read
that value in the image? Anything unverifiable is dropped.

**Nothing invents numbers.** Any figure drawn from scratch must use values that
appear in the source. No ranking, scoring or counting the paper didn't do
itself. A confident table of fabricated numbers is the worst thing a tool like
this can produce, because it's exactly the format readers trust without
checking.

**Questions can see the figure.** Ask what the orange dashed line means and the
image goes with the question. Otherwise the honest answer is "the paper doesn't
describe the colour coding" — useless, when the answer is right there in the
plot.

## Install

```bash
git clone <your-repo-url> && cd paper2vid
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # add your key
```

`ffmpeg` is only needed for video output. SVG rendering uses resvg, a prebuilt
wheel, so there are no system libraries to install on any platform.

## Use it

Browser, with a library of everything you've built:

```bash
paper2vid --serve
```

Or one paper at a time:

```bash
paper2vid https://arxiv.org/abs/2503.01234 --format web
paper2vid 2503.01234 --format video -o out.mp4
```

Free, no key at all:

```bash
paper2vid --serve --llm ollama
```

## What it costs

One model call per paper, plus a small vision call per figure to check markers.
The model never sees the paper — it sees a structured digest of roughly 10k
tokens. Call it 10–20¢ a paper on a mid-tier model, a third of that on a small
one.

Nothing regenerates. Papers are immutable and versioned, so a built page is
cached forever and reopening one is free.

## The storyboard is yours

That one model call produces `storyboard.json`. Everything after it is
deterministic, so:

```bash
paper2vid 2503.01234 --dry-run          # storyboard only
$EDITOR .paper2vid/2503.01234/storyboard.json
paper2vid 2503.01234 --format web       # re-render, spends nothing
```

Rewrite narration, drop a scene, move a marker. Tokens are spent once.

## Options

| flag | default | |
|---|---|---|
| `--serve` | | browser UI + library |
| `--format` | `video` | `web`, `both` |
| `--llm` | `anthropic` | `openai`, `openrouter`, `ollama` |
| `--place-model` | Haiku | model for the marker-checking pass |
| `--no-place` | | skip marker checking |
| `--tts` | `kokoro` | `openai`, `elevenlabs`, `none` |
| `--scenes` | `10` | |
| `--dry-run` | | storyboard only |

## Degrading, not refusing

- **No figures** (theory papers) → builds as a claim-by-claim read, with the
  paper's equations as the visuals rather than ten invented diagrams.
- **No arXiv HTML** (LaTeX that didn't convert) → falls back to the abstract
  and builds a short page.
- **A scene that won't render** → falls back to a text card rather than killing
  the whole run.

## Known gaps

- **Tables are thrown away.** `ingest.py` doesn't extract `table.ltx_tabular`,
  so results tables never reach the model. This is the biggest remaining hole:
  right now the model is *forbidden* from using numbers it can't source, when
  it should be *given* them.
- Non-English papers.
- PDF-only papers get abstract-only treatment.

PRs welcome on all three.

## License

MIT.
