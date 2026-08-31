"""Paperly as an MCP server.

The pipeline is worth more when it is reachable from wherever someone is
already working. An assistant that can call `build_paper` turns "explain this
paper" into a real annotated page -- the paper's own figures, marked up and
verified -- instead of a summary written from the abstract.

Three tools, deliberately: build one, read one back as structured data, list
what exists. Everything else here already exists in the library; this file is
a thin adapter, not a second implementation.

Run it:
    paperly-mcp

Or point a client at it, e.g. in Claude Desktop's config:
    {"mcpServers": {"paperly": {"command": "paperly-mcp"}}}
"""

from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer

from . import ingest, sources, storyboard, web

app = MCPServer(
    "paperly",
    instructions=(
        "Turns a research paper into an annotated, readable page built from "
        "the paper's own figures. Use build_paper for a link the user gives "
        "you, get_paper to read a built one back as structured scenes, and "
        "list_papers to see what is already available."
    ),
)

LIBRARY = os.environ.get("PAPERLY_LIBRARY", "library")
WORKDIR = os.environ.get("PAPERLY_WORKDIR", ".paper2vid")


def _meta_path(slug: str) -> str:
    return os.path.join(LIBRARY, f"{slug}.json")


def _page_path(slug: str) -> str:
    return os.path.join(LIBRARY, f"{slug}.html")


@app.tool(
    description=(
        "Build a readable page from a paper link. Extracts the paper's own "
        "figures, annotates them, verifies each marker against the real "
        "image, and writes a self-contained HTML file. Returns the path and "
        "a scene-by-scene outline. Costs about 10 cents; a paper already "
        "built is returned from cache for free."
    )
)
def build_paper(url: str, scenes: int = 10) -> str:
    """url: an arXiv link or id. scenes: how many sections to produce."""
    slug = sources.slug(url)
    if os.path.exists(_page_path(slug)):
        return json.dumps({
            "status": "cached", "id": slug, "page": _page_path(slug),
            "note": "already built; nothing was spent",
        })

    work = os.path.join(WORKDIR, slug)
    os.makedirs(work, exist_ok=True)
    doc = ingest.load(url, work)

    digest = ingest.condense(doc)
    sb, warnings = storyboard.generate(
        digest, {f.ref for f in doc.figures}, scenes=scenes)
    warnings += storyboard._flag_similar_figures(sb, doc)
    dropped = storyboard.place_annotations(sb, doc)

    os.makedirs(LIBRARY, exist_ok=True)
    with open(os.path.join(work, "storyboard.json"), "w") as f:
        f.write(sb.to_json())
    page = web.build(sb, doc, digest, _page_path(slug))
    with open(_meta_path(slug), "w") as f:
        json.dump({"arxiv_id": slug, "title": sb.title,
                   "paper_title": doc.title, "scenes": len(sb.scenes)}, f)

    return json.dumps({
        "status": "built",
        "id": slug,
        "title": sb.title,
        "paper_title": doc.title,
        "page": page,
        "figures_used": sum(1 for s in sb.scenes
                            if s.visual.get("kind") == "figure"),
        "markers_dropped": dropped,
        "warnings": warnings,
        "outline": [{"id": s.id, "kind": s.visual.get("kind"),
                     "narration": s.narration} for s in sb.scenes],
    }, indent=2)


@app.tool(
    description=(
        "Read a built paper back as structured scenes: narration, which "
        "figure each scene uses, and where its annotation markers sit as "
        "fractions of the figure. Use this to answer questions about a paper "
        "already built, without spending anything."
    )
)
def get_paper(paper_id: str) -> str:
    """paper_id: the id returned by build_paper, e.g. 2503.01234."""
    slug = os.path.basename(paper_id)
    if not os.path.exists(_page_path(slug)):
        return json.dumps({"error": f"{slug} has not been built yet. "
                                    f"Call build_paper first."})

    sb_path = os.path.join(WORKDIR, slug, "storyboard.json")
    if not os.path.exists(sb_path):
        return json.dumps({"error": f"{slug} has a page but no storyboard."})

    with open(sb_path) as f:
        sb = json.load(f)

    out = {"id": slug, "title": sb.get("title"), "scenes": []}
    if os.path.exists(_meta_path(slug)):
        with open(_meta_path(slug)) as f:
            out["paper_title"] = json.load(f).get("paper_title")
    for s in sb.get("scenes", []):
        v = s.get("visual", {})
        out["scenes"].append({
            "id": s.get("id"), "narration": s.get("narration"),
            "kind": v.get("kind"), "figure": v.get("ref"),
            "annotations": [{"label": a.get("label"), "x": a.get("x"),
                             "y": a.get("y")}
                            for a in v.get("annotations", [])],
        })
    return json.dumps(out, indent=2)


@app.tool(description="List every paper already built and readable.")
def list_papers() -> str:
    if not os.path.isdir(LIBRARY):
        return json.dumps({"papers": []})
    out = []
    for name in sorted(os.listdir(LIBRARY)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        if not os.path.exists(os.path.join(LIBRARY, name[:-5] + ".html")):
            continue
        try:
            with open(os.path.join(LIBRARY, name)) as f:
                d = json.load(f)
            if d.get("arxiv_id"):
                out.append({"id": d["arxiv_id"], "title": d.get("title"),
                            "paper_title": d.get("paper_title")})
        except (OSError, json.JSONDecodeError):
            pass
    return json.dumps({"papers": out}, indent=2)


def main() -> None:
    app.run(transport=os.environ.get("PAPERLY_MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()