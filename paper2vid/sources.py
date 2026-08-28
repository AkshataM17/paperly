"""Where a paper comes from.

Everything downstream takes a Doc -- sections, figures, equations -- and does
not care which server produced it. This module is the only place that knows
the difference.

Each source is worth having for the same reason arXiv was: it publishes
structured full text with figures as separate files and captions attached to
them. That structure is what makes annotation possible. A source without it
degrades the product to "summarise this page", which is not worth adding.

The selectors below are written against each site's published markup. Sites
change; `python3 -m paper2vid.sources <url>` prints what a parser actually
found, which is the fastest way to see which one has drifted.
"""

from __future__ import annotations

import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from .ingest import (Doc, Figure, Section, UA, _clean, _get, _text,
                     FIG_PREFIX, NoHTMLSource, parse_id as arxiv_id,
                     fetch_html as arxiv_html, parse as arxiv_parse,
                     fetch_meta as arxiv_meta)

# bioRxiv used 10.1101 for years and has since started issuing others, so the
# prefix cannot be hardcoded -- match any DOI and let the hostname decide
# which server it belongs to.
class RateLimited(NoHTMLSource):
    """The server asked us to slow down. Different from a missing paper."""


BIORXIV_DOI = re.compile(r"(10\.\d{4,9}/[0-9.]+v?\d*)")
PMCID = re.compile(r"PMC(\d+)", re.I)


def detect(url: str) -> str:
    """Which server is this? Falls back to arXiv, which is the common case."""
    u = url.lower()
    if "medrxiv" in u:
        return "medrxiv"
    if "biorxiv" in u:
        return "biorxiv"
    if "pmc" in u or "ncbi.nlm.nih.gov" in u:
        return "pmc"
    if re.search(r"10\.\d{4,9}/\d{4}\.\d{2}\.\d{2}", u):
        return "biorxiv"
    return "arxiv"


def _abstract_text(node) -> str:
    """Abstract body without its own heading.

    Both servers wrap the heading inside the abstract container, so a plain
    get_text() returns "Abstract Preprints provide..." and the model is handed
    a stray word it then has to reason about.
    """
    import copy
    n = copy.copy(node)
    for h in n.find_all(["h1", "h2", "h3", "h4"]):
        h.decompose()
    return _text(n)


# --- bioRxiv / medRxiv -------------------------------------------------------

def _biorxiv_doi(url: str) -> str:
    m = BIORXIV_DOI.search(url)
    if not m:
        raise NoHTMLSource(f"no bioRxiv/medRxiv DOI found in {url!r}")
    return m.group(1)


def fetch_biorxiv(url: str, server: str, session=None) -> tuple[str, str]:
    """The .full view carries the figures in line; the landing page does not."""
    s = session or requests.Session()
    doi = _biorxiv_doi(url)
    host = f"https://www.{server}.org"
    tried = []
    # the DOI often already carries its version, so appending another gives
    # ...747375v1v1.full
    suffixes = (".full", ".full-text") if re.search(r"v\d+$", doi) \
        else (".full", "v1.full", ".full-text")
    for suffix in suffixes:
        u = f"{host}/content/{doi}{suffix}"
        try:
            r = _get(s, u, allow_redirects=True)
        except requests.RequestException as e:
            tried.append(f"{u}: {type(e).__name__}")
            continue
        # "section" appears in almost any HTML, so a loose check accepts the
        # abstract landing page and silently produces an empty document.
        # Require the markers that only the full-text view carries.
        if r.status_code == 429:
            raise RateLimited(
                f"{server} is rate-limiting this machine (HTTP 429). "
                f"Wait a few minutes and try again -- nothing is wrong with "
                f"the paper or the code.")
        # A 200 is not enough: bioRxiv serves a full page frame whose body
        # loads by AJAX, so the shell has site chrome and almost no prose.
        # Count paragraphs -- real full text has dozens.
        if r.status_code == 200 and r.text.count("<p") > 25 and (
                'class="section' in r.text or 'class="fig' in r.text
                or "fig-caption" in r.text):
            return r.text, r.url
        tried.append(f"{u}: HTTP {r.status_code}"
                     + ("" if r.status_code != 200 else ", abstract only"))
    raise NoHTMLSource(
        f"{doi}: {server} has not posted full text for this one yet "
        f"(recent preprints show the PDF before the HTML is converted). "
        f"Tried: " + "; ".join(tried))


def parse_biorxiv(html: str, base_url: str, doi: str) -> Doc:
    """HighWire markup, which is what both servers run on."""
    soup = BeautifulSoup(html, "lxml")

    def first(*selectors):
        for sel in selectors:
            n = soup.select_one(sel)
            if n:
                return n
        return None

    t = first("h1#page-title", "h1.highwire-cite-title", "h1")
    title = _clean(t.get_text()) if t else doi

    authors = [_clean(a.get_text())
               for a in soup.select(".highwire-citation-author, "
                                    ".author-name")][:12]

    abs_node = first("div.section.abstract", "div.abstract", "#abstract-1")
    abstract = _abstract_text(abs_node) if abs_node else ""

    figures: list[Figure] = []
    for fig in soup.select("div.fig, div.figure, figure"):
        img = fig.find("img")
        if not img:
            continue
        src = img.get("data-src") or img.get("src")
        if not src:
            continue
        # thumbnails are served alongside a full-resolution sibling
        # thumbnails sit beside a full-resolution sibling; the model reads
        # legends and axis labels off these, so the small ones are not enough
        for lo, hi in ((".small.", ".large."), (".medium.", ".large."),
                       ("/small/", "/large/"), ("/medium/", "/large/")):
            src = src.replace(lo, hi)
        src = re.sub(r"\.large\.gif$", ".large.jpg", src)
        cap = fig.select_one(".fig-caption, .caption, figcaption")
        figures.append(Figure(
            ref=f"F{len(figures) + 1}",
            dom_id=fig.get("id", ""),
            caption=FIG_PREFIX.sub("", _text(cap)) if cap else "",
            src=urllib.parse.urljoin(base_url, src),
        ))

    sections: list[Section] = []
    for sec in soup.select("div.section"):
        if "abstract" in (sec.get("class") or []):
            continue
        h = sec.find(["h2", "h3"])
        body = " ".join(_text(p) for p in sec.select("p"))
        if not body:
            continue
        sections.append(Section(title=_clean(h.get_text()) if h else "",
                                text=_clean(body)))

    return Doc(arxiv_id=doi, title=title, authors=authors, abstract=abstract,
               sections=sections, figures=figures, equations=[])


# --- PubMed Central ----------------------------------------------------------

# pmc.ncbi.nlm.nih.gov renders in the browser, so scraping it returns an
# empty shell. Europe PMC serves the same articles as JATS XML -- structured
# sections, captions attached to figures, no JavaScript.
EPMC_XML = ("https://www.ebi.ac.uk/europepmc/webservices/rest/"
            "{pmcid}/fullTextXML")
# The old /bin/ path now 404s. Images live on a CDN behind an unguessable
# hash, so the URL cannot be constructed -- but the article page lists every
# one, and each ends with the filename JATS already gave us. Fetch the page
# once, index it by filename, and look each figure up.
PMC_PAGE = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
CDN_RE = re.compile(
    r'https://cdn\.ncbi\.nlm\.nih\.gov/pmc/blobs/\S+?\.(?:jpg|jpeg|png|gif)')


def _pmc_image_index(pmcid: str, session=None) -> dict:
    """{bare filename: cdn url} for every image in the article."""
    try:
        r = _get(session or requests, PMC_PAGE.format(pmcid=pmcid))
    except requests.RequestException:
        return {}
    if r.status_code != 200:
        return {}
    idx = {}
    for u in CDN_RE.findall(r.text):
        name = u.rsplit("/", 1)[-1]
        idx[name] = u
        idx[name.rsplit(".", 1)[0]] = u          # also without extension
    return idx


def fetch_pmc(url: str, session=None) -> tuple[str, str]:
    m = PMCID.search(url)
    if not m:
        raise NoHTMLSource(f"no PMC id found in {url!r}")
    pmcid = "PMC" + m.group(1)
    s = session or requests.Session()
    try:
        r = _get(s, EPMC_XML.format(pmcid=pmcid))
    except requests.RequestException as e:
        raise NoHTMLSource(f"{pmcid}: {type(e).__name__}") from e
    if r.status_code != 200 or "<article" not in r.text:
        raise NoHTMLSource(
            f"{pmcid}: no open-access full text (HTTP {r.status_code}). "
            f"Only the PMC open-access subset can be read this way.")
    return r.text, pmcid


def parse_pmc(xml: str, base_url: str, pmcid: str) -> Doc:
    """JATS XML. Tag names, not CSS classes -- far more stable than scraping."""
    soup = BeautifulSoup(xml, "xml")

    t = soup.find("article-title")
    title = _clean(t.get_text()) if t else pmcid

    # JATS allows several name shapes, and publishers use all of them.
    authors = []
    for c in soup.find_all("contrib"):
        sn, gn = c.find("surname"), c.find("given-names")
        if sn:
            authors.append(_clean((gn.get_text() + " " if gn else "")
                                  + sn.get_text()))
            continue
        alt = c.find("string-name") or c.find("name")
        if alt:
            nm = _clean(alt.get_text())
            if nm:
                authors.append(nm)
    if not authors:
        authors = [_clean(n.get_text())
                   for n in soup.find_all("string-name")][:12]
    authors = [a for a in authors if a][:12]

    ab = soup.find("abstract")
    abstract = _clean(" ".join(p.get_text() for p in ab.find_all("p"))) \
        if ab else ""

    images = _pmc_image_index(pmcid)

    figures: list[Figure] = []
    for fig in soup.find_all("fig"):
        g = fig.find("graphic")
        if g is None:
            continue
        href = (g.get("xlink:href") or g.get("href") or "").strip()
        if not href:
            continue
        stem = href.rsplit("/", 1)[-1]
        src = (images.get(stem) or images.get(stem + ".jpg")
               or images.get(stem.rsplit(".", 1)[0]))
        if not src:
            continue
        cap = fig.find("caption")
        label = fig.find("label")
        text = _clean(cap.get_text()) if cap else ""
        if label:
            text = FIG_PREFIX.sub("", _clean(label.get_text()) + " " + text)
        figures.append(Figure(
            ref=f"F{len(figures) + 1}",
            dom_id=fig.get("id", ""),
            caption=FIG_PREFIX.sub("", text),
            src=src,
        ))

    SKIP = ("ref", "supplementary", "acknowledg", "competing",
            "author contributions", "funding", "abbreviation")
    sections: list[Section] = []
    for sec in soup.find_all("sec"):
        if sec.find_parent("sec") is not None:      # top-level only
            continue
        h = sec.find("title")
        name = _clean(h.get_text()) if h else ""
        if any(k in name.lower() for k in SKIP):
            continue
        body = _clean(" ".join(p.get_text() for p in sec.find_all("p")))
        if len(body) < 120:                         # headings without content
            continue
        sections.append(Section(title=name, text=body))
    sections = sections[:15]

    eqs = []
    for f in soup.find_all("tex-math")[:20]:
        e = _clean(f.get_text()).strip("$")
        if 8 < len(e) < 220:
            eqs.append(e)

    return Doc(arxiv_id=pmcid, title=title, authors=authors,
               abstract=abstract, sections=sections, figures=figures,
               equations=eqs)


# --- one door for all of them ------------------------------------------------

# arXiv is the only source on by default. The others work -- bioRxiv and PMC
# both parse cleanly for papers old enough to have been converted -- but very
# recent preprints and closed-access articles are unreadable everywhere, and a
# reader who hits that sees a failure rather than a limitation. Set
# PAPER2VID_SOURCES=arxiv,biorxiv,medrxiv,pmc to turn them back on.
def enabled() -> set:
    import os
    raw = os.environ.get("PAPER2VID_SOURCES", "arxiv")
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def fetch(url: str, log=None) -> Doc:
    """Resolve any supported link to a parsed Doc, figures not yet downloaded."""
    src = detect(url)
    if src not in enabled():
        raise NoHTMLSource(
            f"{src} links are not supported yet -- paste an arXiv link "
            f"(arxiv.org/abs/...).")
    if log:
        log(f"source: {src}")

    if src in ("biorxiv", "medrxiv"):
        try:
            html, base = fetch_biorxiv(url, src)
            doc = parse_biorxiv(html, base, _biorxiv_doi(url))
            if doc.sections:
                return doc
            if log:
                log("no body text in the HTML; trying Europe PMC")
        except RateLimited:
            raise
        except NoHTMLSource as e:
            if log:
                log(f"{e}")
                log("trying Europe PMC")
        return fetch_preprint_jats(url)

    if src == "pmc":
        html, base = fetch_pmc(url)
        return parse_pmc(html, base, "PMC" + PMCID.search(url).group(1))

    aid = arxiv_id(url)
    html, base = arxiv_html(aid)
    return arxiv_parse(html, base.rsplit("/", 1)[0], aid)


EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def fetch_preprint_jats(url: str, session=None) -> Doc:
    """Preprint full text via Europe PMC.

    Europe PMC assigns preprints a PPR id and, where the licence allows,
    converts them to the same JATS XML used for journal articles. That is
    structured full text with captioned figures -- exactly what the HTML
    scrape is trying to reconstruct, without the AJAX.
    """
    s = session or requests.Session()
    doi = _biorxiv_doi(url)
    bare = re.sub(r"v\d+$", "", doi)
    r = _get(s, EPMC_SEARCH, params={"query": f"DOI:{bare}",
                                     "format": "json", "pageSize": 5})
    hits = (r.json().get("resultList") or {}).get("result") or []
    ppr = next((h["id"] for h in hits
                if str(h.get("id", "")).startswith("PPR")), None)
    if not ppr:
        raise NoHTMLSource(f"{doi}: not indexed by Europe PMC")
    x = _get(s, EPMC_XML.format(pmcid=ppr))
    if x.status_code != 200 or "<article" not in x.text:
        raise NoHTMLSource(f"{doi}: Europe PMC has no full text ({ppr})")
    return parse_pmc(x.text, ppr, ppr)


def fetch_abstract_only(url: str, session=None) -> Doc:
    """Metadata when full text is not available.

    Tries Europe PMC first: we are already talking to it, its search returns
    title and abstract even for preprints it has not converted, and it does
    not go down as often as the preprint servers' own APIs. bioRxiv's API is
    the second try, not the first.
    """
    src = detect(url)
    s_ = session or requests.Session()

    # A PMC article outside the open-access subset has no readable full text,
    # but Europe PMC still returns its metadata -- so a closed paper becomes a
    # short page rather than a dead end.
    if src == "pmc":
        m = PMCID.search(url)
        if not m:
            raise NoHTMLSource(f"no PMC id found in {url!r}")
        pmcid = "PMC" + m.group(1)
        try:
            r = _get(s_, EPMC_SEARCH, params={"query": f"PMCID:{pmcid}",
                                              "format": "json",
                                              "resultType": "core",
                                              "pageSize": 5})
            for h in (r.json().get("resultList") or {}).get("result") or []:
                if h.get("title"):
                    return Doc(
                        arxiv_id=pmcid,
                        title=_clean(h["title"]),
                        authors=[a.strip() for a in
                                 (h.get("authorString") or "").split(",")
                                 if a.strip()][:12],
                        abstract=_clean(h.get("abstractText") or ""))
        except (requests.RequestException, ValueError):
            pass
        raise NoHTMLSource(
            f"{pmcid}: this article is not open access, and Europe PMC has "
            f"no abstract for it either.")

    if src not in ("biorxiv", "medrxiv"):
        raise NoHTMLSource(f"no abstract fallback for {src}")

    doi = _biorxiv_doi(url)
    bare = re.sub(r"v\d+$", "", doi)

    try:
        r = _get(s_, EPMC_SEARCH, params={"query": f"DOI:{bare}",
                                          "format": "json",
                                          "resultType": "core",
                                          "pageSize": 5})
        for h in (r.json().get("resultList") or {}).get("result") or []:
            if h.get("title"):
                return Doc(
                    arxiv_id=doi,
                    title=_clean(h["title"]),
                    authors=[a.strip() for a in
                             (h.get("authorString") or "").split(",")
                             if a.strip()][:12],
                    abstract=_clean(h.get("abstractText") or ""))
    except (requests.RequestException, ValueError):
        pass

    try:
        r = _get(s_, f"https://api.biorxiv.org/details/{src}/{bare}")
        data = r.json().get("collection") or []
        if data:
            d = data[-1]
            return Doc(arxiv_id=doi,
                       title=_clean(d.get("title", "")),
                       authors=[a.strip() for a in
                                d.get("authors", "").split(";")
                                if a.strip()][:12],
                       abstract=_clean(d.get("abstract", "")))
    except (requests.RequestException, ValueError):
        pass

    raise NoHTMLSource(
        f"{doi}: could not reach {src} or Europe PMC for even the abstract. "
        f"The paper may be too new to be indexed, or the network is down.")


def slug(url: str) -> str:
    """A filename-safe id for the library, whatever the source."""
    src = detect(url)
    if src in ("biorxiv", "medrxiv"):
        return _biorxiv_doi(url).replace("/", "_")
    if src == "pmc":
        return "PMC" + PMCID.search(url).group(1)
    return arxiv_id(url)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python3 -m paper2vid.sources <paper url>")
        raise SystemExit(2)
    u = sys.argv[1]
    print(f"detected: {detect(u)}")
    print(f"slug:     {slug(u)}")
    d = fetch(u, log=lambda m: print("  " + m))
    print(f"title:    {d.title[:80]}")
    print(f"authors:  {len(d.authors)}")
    print(f"abstract: {len(d.abstract)} chars")
    print(f"sections: {len(d.sections)}")
    print(f"figures:  {len(d.figures)}")
    for f in d.figures[:4]:
        print(f"  {f.ref} {f.caption[:60]}")
        print(f"     {f.src}")