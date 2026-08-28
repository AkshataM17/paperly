"""arXiv -> structured document.

We read arxiv.org/html/<id>, which is LaTeXML output arXiv has generated from
the submitted LaTeX since late 2023. That gives us the section tree, figure
files as separate images, captions already attached to their figures, and
equations with LaTeX in the alttext.

This is the single most important decision in the pipeline. Parsing the PDF
instead would mean fighting two-column layout, losing figure boundaries, and
shipping the whole paper to a model. Here the parsing is deterministic and
free, and only a condensed structure goes to the LLM.

Papers before ~Dec 2023 have no HTML. We do not support them yet. That is a
deliberate v0 cut, not an oversight.
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "paper2vid (+https://github.com/)"}
RETRIES = 1
BACKOFF = 0.0


def _get(session, url, **kw):
    """GET with retries.

    arXiv's endpoints are frequently slow and occasionally just time out.
    One transient failure should not end a build, so this backs off and tries
    again before giving up.
    """
    last = None
    for attempt in range(RETRIES):
        try:
            return session.get(url, headers=UA, timeout=20, **kw)
        except requests.RequestException as e:
            last = e
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF * (attempt + 1))
    raise last
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


class NoHTMLSource(Exception):
    pass


@dataclass
class Figure:
    ref: str            # F1, F2 ... what the storyboard refers to
    dom_id: str         # S3.F2
    caption: str
    src: str            # absolute URL
    local: str = ""     # filled after download


@dataclass
class Section:
    title: str
    text: str


@dataclass
class Doc:
    arxiv_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    sections: list[Section] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def parse_id(url_or_id: str) -> str:
    m = ARXIV_ID.search(url_or_id.strip())
    if not m:
        raise ValueError(f"could not find an arXiv id in {url_or_id!r}")
    return m.group(1)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ", "mu": "μ",
    "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ",
    "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def _latex_to_text(tex: str) -> str:
    """Make an alttext readable, or give up and drop it.

    The alttext is raw LaTeX. Dropped into a caption verbatim it reads as
    "\\boldsymbol{\\Theta}=(h,\\Omega_{0m})", which is worse than showing
    nothing. We transliterate the common cases -- Greek letters, simple
    subscripts -- and bail out if what is left still looks like markup.
    """
    t = tex
    for cmd in ("boldsymbol", "mathrm", "mathbf", "mathit", "text", "rm",
                "left", "right", "displaystyle", "operatorname"):
        t = t.replace("\\" + cmd, "")
    for name, ch in _GREEK.items():
        t = t.replace("\\" + name, ch)
    t = re.sub(r"_\{([0-9a-zA-Z+\-=()]{1,4})\}",
               lambda m: m.group(1).translate(_SUB), t)
    t = re.sub(r"_([0-9a-zA-Z])", lambda m: m.group(1).translate(_SUB), t)
    t = t.replace("\\%", "%").replace("\\,", " ").replace("\\;", " ")
    t = t.replace("{", "").replace("}", "").strip()
    # still carrying commands or a stray backslash -> not readable, drop it
    if "\\" in t or len(t) > 48:
        return ""
    return t


def _text(node) -> str:
    """get_text() but without LaTeXML's doubled maths.

    LaTeXML emits every equation twice: rendered MathML for browsers, and an
    alttext attribute holding the original LaTeX. BeautifulSoup's get_text()
    concatenates both, so a caption comes out as "w(z)w(z)" or worse,
    "68%68\\%". We drop the MathML subtree and keep a single readable form.
    """
    import copy
    n = copy.copy(node)
    for m in n.find_all("math"):
        alt = _latex_to_text(_clean(m.get("alttext", "")))
        m.replace_with(f" {alt} " if alt else " ")
    return _clean(n.get_text())


FIG_PREFIX = re.compile(
    r"^(?:Figure|Fig\.?)\s*\d+\s*[:.\-\u2013\u2014]?\s*", re.I)


def fetch_html(arxiv_id: str, session=None) -> tuple[str, str]:
    """Return (html, base_url). Tries arXiv native HTML, then ar5iv."""
    s = session or requests.Session()
    tried = []
    for url in (f"https://arxiv.org/html/{arxiv_id}",
                f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"):
        try:
            r = _get(s, url, allow_redirects=True)
        except requests.RequestException as e:
            tried.append(f"{url}: {type(e).__name__} after {RETRIES} tries")
            continue
        if r.status_code == 200 and "ltx_document" in r.text:
            return r.text, r.url
        tried.append(f"{url}: HTTP {r.status_code}"
                     + ("" if r.status_code != 200 else ", no rendered body"))

    # Age is only one reason HTML can be missing. arXiv generates it from the
    # submitted LaTeX, and that conversion fails outright on papers using
    # packages LaTeXML cannot handle -- which happens to recent papers too.
    # Blaming the date sends you looking in the wrong place.
    year_month = arxiv_id.split(".")[0]
    likely_old = len(year_month) == 4 and year_month < "2312"
    why = ("this paper predates arXiv's HTML rendering (~Dec 2023)"
           if likely_old else
           "arXiv has no HTML for this one -- its LaTeX did not convert")
    raise NoHTMLSource(f"{arxiv_id}: {why}. Tried: " + "; ".join(tried))


ARXIV_API = "http://export.arxiv.org/api/query"


def fetch_meta(arxiv_id: str, session=None) -> Doc:
    """Title, abstract and authors from the arXiv API.

    The fallback when HTML is unavailable. No figures, no section tree -- but
    an abstract is enough to say what the paper claims, and a short honest
    page beats an error.
    """
    s = session or requests.Session()
    try:
        r = _get(s, ARXIV_API, params={"id_list": arxiv_id})
        r.raise_for_status()
    except requests.RequestException as e:
        raise NoHTMLSource(
            f"{arxiv_id}: no HTML, and arXiv's API did not answer either "
            f"({type(e).__name__}). Check the id, or try again in a moment."
        ) from e
    soup = BeautifulSoup(r.text, "xml")
    entry = soup.find("entry")
    if entry is None or entry.find("title") is None:
        raise NoHTMLSource(f"{arxiv_id}: not found on arXiv")
    return Doc(
        arxiv_id=arxiv_id,
        title=_clean(entry.find("title").get_text()),
        authors=[_clean(a.get_text())
                 for a in entry.find_all("name")][:12],
        abstract=_clean(entry.find("summary").get_text()
                        if entry.find("summary") else ""),
    )


def parse(html: str, base_url: str, arxiv_id: str) -> Doc:
    soup = BeautifulSoup(html, "lxml")

    title = _clean(soup.find(class_="ltx_title_document").get_text()) \
        if soup.find(class_="ltx_title_document") else arxiv_id

    authors = [_clean(a.get_text())
               for a in soup.select(".ltx_personname")][:12]

    abs_node = soup.find(class_="ltx_abstract")
    abstract = _clean(" ".join(_text(p) for p in abs_node.select("p"))) \
        if abs_node else ""

    figures: list[Figure] = []
    for fig in soup.select("figure.ltx_figure"):
        img = fig.find("img")
        if not img or not img.get("src"):
            continue
        cap = fig.find("figcaption")
        figures.append(Figure(
            ref=f"F{len(figures) + 1}",
            dom_id=fig.get("id", ""),
            caption=FIG_PREFIX.sub("", _text(cap)) if cap else "",
            src=urllib.parse.urljoin(base_url + "/", img["src"]),
        ))

    sections: list[Section] = []
    for sec in soup.select("section.ltx_section"):
        h = sec.find(class_="ltx_title_section")
        sections.append(Section(
            title=_clean(h.get_text()) if h else "",
            text=_clean(" ".join(_text(p) for p in sec.select("p.ltx_p"))),
        ))

    equations = []
    for m in soup.select("table.ltx_equation math[alttext]")[:24]:
        alt = _clean(m.get("alttext"))
        if 8 < len(alt) < 220:
            equations.append(alt)

    return Doc(arxiv_id=arxiv_id, title=title, authors=authors,
               abstract=abstract, sections=sections, figures=figures,
               equations=equations)


def download_figures(doc: Doc, outdir: str, session=None) -> None:
    """Fetch every figure at once.

    These are independent requests to the same host and each one is mostly
    latency. Done one at a time a ten-figure paper spends most of a minute
    waiting; done together it costs about as much as the slowest single one.
    """
    os.makedirs(outdir, exist_ok=True)

    def grab(f: Figure) -> None:
        ext = os.path.splitext(urllib.parse.urlparse(f.src).path)[1] or ".png"
        path = os.path.join(outdir, f"{f.ref}{ext}")
        if os.path.exists(path):
            f.local = path
            return
        try:
            r = _get(requests, f.src)
            if r.status_code == 200 and r.content:
                with open(path, "wb") as fh:
                    fh.write(r.content)
                f.local = path
        except requests.RequestException:
            pass

    if not doc.figures:
        return
    with ThreadPoolExecutor(max_workers=min(8, len(doc.figures))) as pool:
        list(pool.map(grab, doc.figures))


def load(url_or_id: str, workdir: str, log=None, allow_abstract_only=True) -> Doc:
    """arXiv, bioRxiv, medRxiv or PMC -- resolved by paper2vid.sources."""
    from . import sources
    if sources.detect(url_or_id) != "arxiv":
        try:
            doc = sources.fetch(url_or_id, log=log)
        except sources.RateLimited:
            raise
        except NoHTMLSource as e:
            if not allow_abstract_only:
                raise
            if log:
                log(str(e))
                log("falling back to the abstract - the page will be short")
            return sources.fetch_abstract_only(url_or_id)
        found = len(doc.figures)
        if log and found:
            log(f"downloading {found} figures")
        download_figures(doc, os.path.join(workdir, "figures"))
        doc.figures = [f for f in doc.figures if f.local]
        for i, f in enumerate(doc.figures, 1):
            f.ref = f"F{i}"
        if log and found != len(doc.figures):
            log(f"{found - len(doc.figures)} of {found} figures failed to "
                f"download and were skipped")
        return doc

    aid = parse_id(url_or_id)
    if log:
        log(f"fetching arxiv:{aid}")
    try:
        html, base = fetch_html(aid)
    except NoHTMLSource as e:
        if not allow_abstract_only:
            raise
        if log:
            log(str(e))
            log("falling back to the abstract - the page will be short")
        return fetch_meta(aid)
    doc = parse(html, base.rsplit("/", 1)[0], aid)
    found = len(doc.figures)
    # Figure downloads are the slow part of ingest and, without a word here,
    # a paper with a dozen large figures is indistinguishable from a hang.
    if log and found:
        log(f"downloading {found} figures")
    download_figures(doc, os.path.join(workdir, "figures"))
    doc.figures = [f for f in doc.figures if f.local]
    # "0 figures" is ambiguous on its own -- a paper with none and a paper
    # whose images all failed to download look identical downstream, and the
    # fixes are completely different.
    if log and found != len(doc.figures):
        log(f"{found - len(doc.figures)} of {found} figures failed to "
            f"download and were skipped")
    if log and found == 0:
        log("this paper's HTML contains no figures at all")
    for i, f in enumerate(doc.figures, 1):   # renumber after drops
        f.ref = f"F{i}"
    return doc


# --- what actually goes to the model -----------------------------------------

def condense(doc: Doc, budget_chars: int = 26_000) -> str:
    """Structured digest, not the paper. This is the token-control step."""
    out = [f"TITLE: {doc.title}",
           f"ARXIV: {doc.arxiv_id}",
           f"ABSTRACT: {doc.abstract}", ""]

    if doc.figures:
        out.append("FIGURES (refer to these by ref):")
        for f in doc.figures:
            out.append(f"  [{f.ref}] {f.caption[:400]}")
        out.append("")

    if doc.equations:
        # With no figures the equations are the only structure the paper
        # exposes, so send more of them -- they become the visuals.
        cap = 10 if doc.figures else 20
        out.append("EQUATIONS (LaTeX):")
        out += [f"  {e}" for e in doc.equations[:cap]]
        out.append("")

    used = sum(len(x) for x in out)
    per = max(600, (budget_chars - used) // max(1, len(doc.sections)))
    out.append("SECTIONS:")
    for s in doc.sections:
        out.append(f"## {s.title}")
        out.append(f"{s.text[:per]}")
    return "\n".join(out)[:budget_chars]
