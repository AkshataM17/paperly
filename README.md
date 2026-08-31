# paper2vid

Paste an arXiv link. Get the paper's own figures, marked up, with somewhere to
ask what the caption assumed you already knew.

```bash
paper2vid --serve
```

Opens a page at `localhost:8842`. Paste a link, wait about a minute, read.
Everything you build stacks up in a local library.

Runs on your machine, on your key. There is no hosted version and nothing is
proxied through anyone else.

---

## Why not just ask a chatbot

Because a chatbot can read the text but can't hand you **Figure 3**.

This pulls the paper's actual figure files out of arXiv's HTML, puts markers on
the parts that matter, and checks those markers against the image before
showing them to you. Then when you ask what the orange dashed line means, the
figure goes with the question.

That's the whole thing. Everything else is plumbing to make it trustworthy.

## Four decisions that took the longest

**Read arXiv's HTML, never the PDF.** arXiv has generated HTML from submitted
LaTeX since late 2023, which hands over the section tree, the figures as
separate image files, captions already attached to their figure, and equations
with the LaTeX intact. Parsing the PDF means fighting two-column layout and
losing figure boundaries — it's where projects like this go to die.

**Check every marker against the image.** The model writing the storyboard has
never seen the figures; it works from captions. Left alone it produces tidy
symmetric coordinates like 0.28/0.28 that land on empty axis space, and labels
asserting numbers it never read. A second pass sends each figure back with its
proposed markers and asks: where does this really go, and can you read that
value here? Anything unverifiable gets dropped.

**Never invent a number.** Any figure drawn from scratch must use values that
appear in the source. No ranking, scoring or counting the paper didn't do
itself. A confident table of fabricated numbers is the worst thing a tool like
this can produce, because it's exactly the format readers trust without
checking.

**Never show the same plot twice.** Papers contain families of near-identical
figures — one posterior corner plot per model, one correlation matrix per
dataset. Two of those in one page reads as a bug. The storyboard picks one per
family, and a caption-similarity check warns when it doesn't.

## Install

```bash
git clone <your-repo-url> && cd paper2vid
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env      # add your key
```

No system libraries. SVG rendering uses resvg, a prebuilt wheel, so it installs
the same on Linux, macOS and Windows. `ffmpeg` is only needed if you want video
output.

## Use it

```bash
paper2vid --serve                            # browser + library
paper2vid https://arxiv.org/abs/2503.01234   # one page, straight to a file
paper2vid 2503.01234 --format video          # mp4 with narration instead
```

Free, no key at all — local model, local narration:

```bash
paper2vid --serve --llm ollama
```

## What it costs

One model call per paper, plus a small vision call per figure to check markers.
The model never sees the paper; it sees a structured digest of roughly 10k
tokens. Call it 10–20¢ a paper on a mid-tier model, a third of that on a small
one.

Nothing regenerates. Papers are immutable and versioned, so a page you've built
is cached forever and reopening it is free.

## The storyboard is yours

That one model call produces `storyboard.json`. Everything downstream is
deterministic:

```bash
paper2vid 2503.01234 --dry-run          # storyboard only
$EDITOR .paper2vid/2503.01234/storyboard.json
paper2vid 2503.01234                    # re-render, spends nothing
```

Rewrite narration, drop a scene, move a marker. Tokens are spent once.

## Degrading, not refusing

- **No figures** (theory papers) → builds as a claim-by-claim read, with the
  paper's equations as the visuals rather than ten invented diagrams.
- **No arXiv HTML** (LaTeX that didn't convert) → falls back to the abstract
  and builds a short page.
- **A scene that won't render** → falls back to a text card rather than killing
  the run.

## Options

| flag | default | |
|---|---|---|
| `--serve` | | browser UI + library |
| `--port` | `8842` | |
| `--format` | `web` | `video`, `both` |
| `--llm` | `anthropic` | `openai`, `openrouter`, `ollama` |
| `--place-model` | Haiku | model for the marker-checking pass |
| `--no-place` | | skip marker checking (faster, less accurate) |
| `--scenes` | `10` | |
| `--dry-run` | | storyboard only |
| `--tts` | `kokoro` | video only: `openai`, `elevenlabs`, `none` |

## Use it from an assistant

Paperly is also an MCP server, so anything speaking that protocol can build
and read papers directly:

```bash
pip install 'paper2vid[mcp]'
```

In Claude Desktop's config:

```json
{"mcpServers": {"paperly": {"command": "paperly-mcp"}}}
```

Three tools: `build_paper(url)` builds a page and returns its outline,
`get_paper(id)` reads a built one back as structured scenes with annotation
coordinates, and `list_papers()` lists the library. Asking an assistant to
explain a paper then produces a real annotated page rather than a summary
written from the abstract.

## Known gaps

- **Tables are thrown away.** `ingest.py` doesn't extract `table.ltx_tabular`,
  so results tables never reach the model. This is the biggest remaining hole:
  right now the model is *forbidden* from using numbers it can't source, when
  it should be *given* them.
- Non-English papers.
- PDF-only papers get abstract-only treatment.

PRs welcome on all three.

## How it fits together

```
arxiv.org/html/<id>  →  structured digest   (deterministic, free)
                     →  ONE model call      →  storyboard.json
                     →  vision pass         →  markers verified per figure
                     →  self-contained html →  figures inlined, questions live
```

`ingest.py` parses, `storyboard.py` writes and verifies, `scene.py` composes,
`web.py` builds the page, `server.py` is the local UI. The video path —
`render.py`, `tts.py`, `video.py` — is secondary and optional.

## License

MIT.