"""Storyboard -> a single self-contained interactive HTML file.

The video is a lossy flattening of the storyboard: it picks one narration, one
annotation placement, one pace, and bakes them in. This keeps the structure and
hands the reader the parts the video had to decide for them -- they click the
point on the plot that confuses them and ask about it, instead of the model
guessing in advance where confusion will land.

Everything is inlined: figures as data URIs, storyboard as JSON, the paper
digest as the grounding context for questions. One file, no server, no build
step. Open it from disk or drop it on any static host.

Questions run on the reader's own key, entered in the page and kept in their
browser's localStorage. It never reaches you -- there is nothing to reach.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os

from .style import TOKENS, FONT_DISPLAY, FONT_MONO
from . import scene as scene_mod


def _data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def _scene_payload(sb, doc) -> list[dict]:
    out = []
    for s in sb.scenes:
        v = dict(s.visual)
        kind = v.get("kind")
        item = {"id": s.id, "narration": s.narration, "kind": kind,
                "eyebrow": v.get("eyebrow", ""),
                "prebaked": v.get("prebaked") or {}}
        if kind == "figure":
            fig = next((f for f in doc.figures if f.ref == v.get("ref")), None)
            if fig and fig.local and os.path.exists(fig.local):
                item.update(src=_data_uri(fig.local), caption=fig.caption,
                            ref=fig.ref, annotations=v.get("annotations", []))
            else:
                item["kind"] = "title"
        elif kind == "svg":
            item["svg"] = scene_mod.svg_scene(v.get("svg", ""))  # eyebrow is DOM-side
        else:
            item.update(headline=v.get("headline", ""), deck=v.get("deck", ""))
        out.append(item)
    return out


CSS = """
*{box-sizing:border-box}
body{margin:0;background:%(paper)s;color:%(ink)s;font-family:%(display)s;
     -webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:0 28px 160px}
header{padding:64px 0 32px;border-bottom:1px solid %(rule)s;margin-bottom:8px}
h1{font-size:clamp(28px,3.6vw,40px);font-weight:650;letter-spacing:-.022em;
   margin:0 0 16px;line-height:1.1}
.eyebrow{font-family:%(mono)s;font-size:12px;letter-spacing:.16em;
         text-transform:uppercase;color:%(graphite)s;margin:0 0 14px}
.src{font-family:%(mono)s;font-size:13px;color:%(graphite)s}
.src a{color:%(signal)s;text-decoration:none;border-bottom:1px solid %(rule)s}
.scene{padding:46px 0;border-bottom:1px solid %(rule)s}
.narration{font-size:17px;line-height:1.68;margin:0 0 24px;max-width:72ch}
.plate{position:relative;background:#fff;border:1px solid %(rule)s;
       padding:16px;line-height:0}
.plate img{width:100%%;height:auto}
.caption{font-size:14px;color:%(graphite)s;margin-top:12px;line-height:1.5}
.scene>*{opacity:0;transform:translateY(14px);
          transition:opacity .62s cubic-bezier(.2,.7,.3,1),
                     transform .62s cubic-bezier(.2,.7,.3,1)}
.scene.in>*{opacity:1;transform:none}
.plate img{transition:opacity .8s ease}
[data-reveal]{opacity:0;transition:opacity .55s ease}
.in [data-reveal].lit{opacity:1}
.hit{opacity:0;transform:scale(.4);
     transition:opacity .4s ease,transform .4s cubic-bezier(.3,1.5,.5,1)}
.hit.lit{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){
  .scene>*,.scene.in>*{opacity:1;transform:none;transition:none}
  [data-reveal],[data-reveal].lit{opacity:1;transition:none}
  .hit,.hit.lit{opacity:1;transform:none;transition:none}
}
.hit{position:absolute;width:26px;height:26px;margin:-13px 0 0 -13px;
     border-radius:50%%;border:2px solid %(signal)s;background:rgba(214,33,95,.14);
     cursor:pointer;transition:transform .14s}
.hit.lit:hover{transform:scale(1.28)}
.hit span{position:absolute;left:32px;top:2px;white-space:nowrap;
          font-family:%(mono)s;font-size:11px;letter-spacing:.1em;
          background:%(paper)s;padding:3px 7px;border:1px solid %(rule)s;
          opacity:0;transition:opacity .14s;pointer-events:none}
.hit:hover span{opacity:1}
.hint{font-family:%(mono)s;font-size:11px;letter-spacing:.12em;
      text-transform:uppercase;color:%(graphite)s;margin-top:12px}
.headcard{border-left:3px solid %(signal)s;padding-left:26px}
.headcard h2{font-size:26px;font-weight:650;letter-spacing:-.018em;margin:0 0 10px}
.headcard p{font-size:16px;color:%(graphite)s;margin:0;line-height:1.55}
.ask{display:flex;gap:8px;flex-wrap:wrap;margin-top:22px}
button{font-family:%(mono)s;font-size:11px;letter-spacing:.11em;
       text-transform:uppercase;background:none;color:%(ink)s;
       border:1px solid %(ink)s;padding:9px 15px;cursor:pointer}
button:hover{background:%(ink)s;color:%(paper)s}
button:disabled{opacity:.4;cursor:wait}
input{font-family:%(display)s;font-size:15px;padding:10px 13px;
      border:1px solid %(rule)s;background:#fff;color:%(ink)s;flex:1;min-width:230px}
input:focus{outline:none;border-color:%(signal)s}
.answer b{font-weight:640;color:%(ink)s}
.answer .mdh{display:block;font-size:15px;margin:14px 0 6px;color:%(ink)s}
.answer .mdh:first-child{margin-top:0}
.answer .mdli{display:block;padding-left:16px;text-indent:-10px;margin:3px 0}
.answer .mdli::before{content:'\2013  ';color:%(signal)s}
.answer code{font-family:%(mono)s;font-size:13px;background:#fff;
             padding:1px 5px;border:1px solid %(rule)s}
.answer{margin-top:18px;padding:19px 21px;background:%(wash)s;
        border-left:2px solid %(signal)s;font-size:16px;line-height:1.62;
        }
.answer.err{border-color:%(graphite)s;color:%(graphite)s}
.answer.thinking{color:%(graphite)s;font-style:italic}
.veil{position:fixed;inset:0;background:rgba(26,29,35,.55);display:none;
      align-items:center;justify-content:center;z-index:100;padding:24px}
.veil.on{display:flex}
.cardwrap{position:relative;max-width:470px;width:100%%}
.card{background:%(paper)s;padding:36px 38px;border-top:3px solid %(signal)s;
      box-shadow:0 24px 60px rgba(26,29,35,.22)}
.card h3{font-size:26px;font-weight:660;letter-spacing:-.022em;margin:0 0 12px}
.card p{font-size:16px;color:%(graphite)s;line-height:1.58;margin:0 0 22px}
.card form{display:flex;gap:8px;flex-wrap:wrap;margin:0}
.card input{font-family:%(display)s;font-size:15px;padding:11px 14px;
            border:1px solid %(rule)s;background:#fff;flex:1;min-width:200px}
.card input:focus{outline:none;border-color:%(signal)s}
.card form button{font-family:%(mono)s;font-size:11px;letter-spacing:.11em;
                  text-transform:uppercase;background:%(ink)s;color:%(paper)s;
                  border:1px solid %(ink)s;padding:11px 17px;cursor:pointer}
.card form button:hover{background:%(signal)s;border-color:%(signal)s}
.card .alt{font-family:%(mono)s;font-size:11px;letter-spacing:.08em;
           margin:18px 0 0}
.card .ok{font-family:%(mono)s;font-size:12px;letter-spacing:.1em;
          text-transform:uppercase;color:%(signal)s}
.card .x{position:absolute;top:0;right:0;font-family:%(mono)s;font-size:11px;
         letter-spacing:.1em;color:%(graphite)s;cursor:pointer;background:none;
         border:none;padding:8px 12px;text-transform:uppercase}
.answer.thinking::after{content:'';display:inline-block;width:6px;height:6px;
  margin-left:7px;border-radius:50%%;background:%(signal)s;
  animation:p2vpulse 1.1s ease-in-out infinite}
@keyframes p2vpulse{0%%,100%%{opacity:.2;transform:scale(.7)}50%%{opacity:1;transform:scale(1)}}
.keybar[hidden]{display:none}
.keybar{position:sticky;top:0;background:%(paper)s;border-bottom:1px solid %(rule)s;
        padding:11px 0;z-index:10;display:flex;gap:8px;align-items:center;
        flex-wrap:wrap}
.keybar .note{font-family:%(mono)s;font-size:11px;color:%(graphite)s;
              letter-spacing:.06em}
svg{width:100%%;height:auto}
""" % {**TOKENS, "display": FONT_DISPLAY, "mono": FONT_MONO}


JS = r"""
const S = window.__SCENES__, CTX = window.__CONTEXT__;
const $ = (s, r) => (r || document).querySelector(s);
const el = (t, c, x) => { const n = document.createElement(t);
  if (c) n.className = c; if (x != null) n.textContent = x; return n; };

function key() { return localStorage.getItem('p2v_key') || ''; }

// The model answers in markdown. Rendered as plain text it arrives as a wall
// of asterisks and hashes, which reads worse than no formatting at all.
// Escape first, then allow only the few marks that actually appear.
function md(t) {
  const esc = String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                       .replace(/>/g, '&gt;');
  return esc
    .replace(/^#{1,6}\s*(.+)$/gm, '<b class="mdh">$1</b>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^[-\u2022]\s+(.+)$/gm, '<span class="mdli">$1</span>')
    .replace(/\n{2,}/g, '<br><br>')
    .replace(/\n/g, '<br>');
}

// A page built by `paper2vid --serve` is opened from that same server, which
// already has a key in its environment -- asking the reader for one again is
// just friction. The same file opened from disk has no server behind it, so
// it falls back to BYOK. One artefact, both situations, decided at runtime.
let RELAY = false;
(async () => {
  try {
    const r = await fetch('/api/health', { cache: 'no-store' });
    RELAY = r.ok && (await r.json()).ok === true;
  } catch (e) { RELAY = false; }
  if (!RELAY) document.getElementById('kb').hidden = false;
})();

// Anthropic caps images around 1568px on the long edge; bigger just costs
// tokens for no gain. Plots are line art, so we keep PNG unless the payload
// is large enough that JPEG is worth the artefacts.
async function prepImage(dataUri) {
  const [head, b64] = dataUri.split(',');
  const media = head.slice(5, head.indexOf(';'));
  if (b64.length < 3_000_000) {
    const img = await new Promise((res, rej) => {
      const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = dataUri;
    }).catch(() => null);
    if (img && Math.max(img.width, img.height) <= 1568)
      return { type: 'base64', media_type: media, data: b64 };
  }
  const img = await new Promise((res, rej) => {
    const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = dataUri;
  });
  const k = Math.min(1, 1568 / Math.max(img.width, img.height));
  const c = document.createElement('canvas');
  c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
  c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  const out = c.toDataURL('image/jpeg', 0.92);
  return { type: 'base64', media_type: 'image/jpeg', data: out.split(',')[1] };
}

async function ask(question, sc, into, cacheKey) {
  const k = key();
  if (!RELAY && !k) { into.className = 'answer err';
    into.textContent = 'Add your API key at the top of the page first. It stays in this browser.';
    return; }
  into.className = 'answer thinking'; into.textContent = 'Reading the figure...';

  const hasFig = sc.kind === 'figure' && sc.src;
  const sys = 'You are explaining a specific research paper to a curious reader '
    + 'who is not in this subfield.\n'
    + (hasFig
       ? 'The figure the reader is looking at is attached. Read it directly: '
         + 'legends, axis labels, line colours and styles, which curve is which. '
         + 'Questions about what a colour or marker represents are answered from '
         + 'the image, and you should answer them concretely.\n'
       : '')
    + 'Everything else must come from the paper context. If neither the image '
    + 'nor the context answers the question, say so plainly -- never invent '
    + 'numbers, results or comparisons. Two or three sentences. Plain words.';

  const text = 'PAPER CONTEXT:\n' + CTX.digest
    + '\n\nThe reader is looking at this part:\n' + sc.narration
    + (sc.caption ? '\nFigure caption: ' + sc.caption : '')
    + '\n\nTheir question: ' + question;

  try {
    const content = [];
    if (hasFig) {
      try { content.push({ type: 'image', source: await prepImage(sc.src) }); }
      catch (e) { /* fall back to text-only rather than failing the question */ }
    }
    content.push({ type: 'text', text });

    if (RELAY) {
      const r = await fetch('/api/ask', { method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ content, system: sys,
                               cache: cacheKey || null,
                               paper: CTX.arxiv_id,
                               q: cacheKey ? '' : question }) });
      const d = await r.json();
      // Running out of free questions is not an error -- it is the product
      // working. Say so, and offer the way forward, rather than showing a
      // raw failure string.
      if (r.status === 402 || d.reason) {
        into.className = 'answer'; into.textContent = '';
        showWaitlist(d.message || 'That was your free question.');
        return;
      }
      if (d.error) throw new Error(d.error);
      into.className = 'answer'; into.innerHTML = md(d.text);
      return;
    }
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': k,
                 'anthropic-version': '2023-06-01',
                 'anthropic-dangerous-direct-browser-access': 'true' },
      body: JSON.stringify({ model: 'claude-sonnet-4-6', max_tokens: 700,
                             system: sys,
                             messages: [{ role: 'user', content }] })
    });
    if (!r.ok) throw new Error(r.status + ' ' + (await r.text()).slice(0, 200));
    const d = await r.json();
    into.className = 'answer';
    into.innerHTML = md(d.content.filter(b => b.type === 'text')
                                 .map(b => b.text).join(''));
  } catch (e) {
    into.className = 'answer err';
    into.textContent = 'Request failed: ' + e.message;
  }
}

function renderScene(sc) {
  const s = el('section', 'scene');
  if (sc.eyebrow) s.appendChild(el('p', 'eyebrow', sc.eyebrow));

  if (sc.kind === 'title') {
    const h = el('div', 'headcard');
    h.appendChild(el('h2', null, sc.headline));
    if (sc.deck) h.appendChild(el('p', null, sc.deck));
    s.appendChild(h);
  } else if (sc.kind === 'svg') {
    const d = el('div'); d.innerHTML = sc.svg;
    // The video bakes these reveals to a clock. Here they belong to the
    // reader's scroll: the same staged build, paced by them, not at them.
    d.querySelectorAll('[data-reveal]').forEach(n => n.removeAttribute('opacity'));
    s.appendChild(d);
  } else {
    const plate = el('div', 'plate');
    const img = new Image(); img.src = sc.src; img.alt = 'Figure ' + (sc.ref || '') + ' from the paper';
    plate.appendChild(img);
    (sc.annotations || []).forEach(a => {
      const h = el('div', 'hit');
      h.style.left = (a.x * 100) + '%'; h.style.top = (a.y * 100) + '%';
      h.appendChild(el('span', null, a.label));
      h.onclick = () => askAbout(s, sc,
        'What is happening at the point labelled "' + a.label
        + '" on this figure?',
        CTX.arxiv_id + ':' + sc.id + ':mark:' + a.label);
      plate.appendChild(h);
    });
    s.appendChild(plate);
    if (sc.caption) s.appendChild(el('p', 'caption', sc.caption));
    s.appendChild(el('p', 'hint', 'Click a marked point to ask about it'));
  }

  s.appendChild(el('p', 'narration', sc.narration));

  const bar = el('div', 'ask');
  // These three were answered when the page was built. Serving the stored
  // answer costs nothing and appears instantly; only free-text questions
  // still need the model.
  const canned = [
   ['Why does this matter?', 'Why does this part matter?', 'matters'],
   ['Explain simpler', 'Explain this part again, simpler, no jargon.', 'simpler'],
   ['Go deeper', 'Explain the technical detail behind this part.', 'deeper']];
  if (sc.kind === 'figure')
    canned.splice(1, 0, ['Read this figure',
      'Walk me through this figure: what is on each axis, what each colour, '
      + 'line style and marker represents, and what the reader should notice.',
      'figure']);
  canned
    .forEach(([label, q, key]) => {
      const b = el('button', null, label);
      b.onclick = () => {
        const ready = sc.prebaked && sc.prebaked[key];
        if (ready) {
          let a = s.querySelector('.answer');
          if (!a) { a = el('div', 'answer'); s.appendChild(a); }
          a.className = 'answer'; a.textContent = ready;
          a.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          return;
        }
        askAbout(s, sc, q, CTX.arxiv_id + ':' + sc.id + ':' + key);
      };
      bar.appendChild(b);
    });
  const inp = el('input'); inp.placeholder = 'Ask anything about this part...';
  inp.onkeydown = e => { if (e.key === 'Enter' && inp.value.trim()) {
    askAbout(s, sc, inp.value.trim()); inp.value = ''; } };
  bar.appendChild(inp);
  s.appendChild(bar);
  return s;
}

function askAbout(section, sc, question, cacheKey) {
  let a = $('.answer', section);
  if (!a) { a = el('div', 'answer'); section.appendChild(a); }
  a.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  ask(question, sc, a, cacheKey);
}

// The limit is the one moment a reader actually wants more, so it takes over
// the screen rather than sitting in the answer box.
function showWaitlist(msg) {
  let v = document.getElementById('p2v-veil');
  if (!v) {
    v = el('div'); v.id = 'p2v-veil'; v.className = 'veil';
    v.innerHTML =
      '<div class="cardwrap"><button class="x">Close</button>'
      + '<div class="card"><h3>That was your free question.</h3>'
      + '<p class="msg"></p>'
      + '<form><input type="email" placeholder="you@university.edu" required>'
      + '<button type="submit">Join waitlist</button></form>'
      + '</div></div>';
    document.body.appendChild(v);
    v.querySelector('.x').onclick = () => { v.className = 'veil'; };
    v.onclick = (e) => { if (e.target === v) v.className = 'veil'; };
    v.querySelector('form').onsubmit = async (e) => {
      e.preventDefault();
      const inp = v.querySelector('input');
      if (!inp.value.trim()) return;
      const f = v.querySelector('form');
      try {
        const w = await fetch('/api/waitlist', { method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ email: inp.value.trim() }) });
        const wd = await w.json();
        f.innerHTML = wd.ok
          ? '<span class="ok">You are on the list. Thank you.</span>'
          : '<span class="ok">That address did not look right.</span>';
      } catch (err) {
        f.innerHTML = '<span class="ok">Could not reach the server.</span>';
      }
    };
  }
  v.querySelector('.msg').textContent = msg;
  v.className = 'veil on';
  setTimeout(() => { try { v.querySelector('input').focus(); } catch (e) {} }, 60);
}

const wrap = $('.wrap');
S.forEach(sc => wrap.appendChild(renderScene(sc)));

// Reveal groups carry a 0-1 fraction from the storyboard. Map it onto a
// stagger so a scene assembles in the order its narration explains it.
const REVEAL_MS = 950;
const obs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    const s = e.target;
    s.classList.add('in');
    s.querySelectorAll('[data-reveal]').forEach(n => {
      const f = parseFloat(n.getAttribute('data-reveal')) || 0;
      setTimeout(() => n.classList.add('lit'), 260 + f * REVEAL_MS);
    });
    s.querySelectorAll('.hit').forEach((n, i) => {
      setTimeout(() => n.classList.add('lit'), 520 + i * 220);
    });
    obs.unobserve(s);
  });
}, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
document.querySelectorAll('.scene').forEach(s => obs.observe(s));

const ki = $('#k');
ki.value = key();
ki.oninput = () => localStorage.setItem('p2v_key', ki.value.trim());
"""


def build(sb, doc, digest: str, out_path: str) -> str:
    scenes = _scene_payload(sb, doc)
    ctx = {"digest": digest[:40_000], "title": doc.title,
           "arxiv_id": doc.arxiv_id}
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{scene_mod.escape(sb.title)}</title>
<style>{CSS}</style></head><body>
<div class="wrap">
  <div class="keybar" id="kb" hidden>
    <input id="k" type="password" placeholder="Anthropic API key (stays in this browser)">
  </div>
  <header>
    <p class="eyebrow">arXiv:{scene_mod.escape(doc.arxiv_id)}</p>
    <h1>{scene_mod.escape(sb.title)}</h1>
    <p class="src">{scene_mod.escape(doc.title)} &nbsp;·&nbsp;
      <a href="https://arxiv.org/abs/{scene_mod.escape(doc.arxiv_id)}"
         target="_blank" rel="noopener">read the paper</a></p>
  </header>
</div>
<script>
window.__SCENES__ = {json.dumps(scenes)};
window.__CONTEXT__ = {json.dumps(ctx)};
</script>
<script>{JS}</script>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path