"""A local UI: paste an arXiv link, get a page.

This has to be a server rather than a static page for two reasons the browser
cannot get around -- arxiv.org sends no CORS headers, so a page cannot fetch a
paper, and the ingest/figure-download work is Python. So the server runs on
your machine, reads your own .env, and nothing leaves except the model call
you were going to make anyway.

Built on http.server so it adds no dependency. It is a single-user tool on
localhost; it is deliberately not hardened for anything else.
"""

from __future__ import annotations

import hashlib
import html as htmllib
import json
import os
import re
import threading
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ingest, limits as limits_mod, llm, storyboard, web
from .style import TOKENS, FONT_DISPLAY, FONT_MONO

JOBS: dict[str, dict] = {}
CFG: dict = {}
LIMITS = None


# --- the pipeline, as a background job ---------------------------------------

def _run_job(job_id: str, paper: str) -> None:
    job = JOBS[job_id]

    def log(msg: str) -> None:
        job["log"].append(msg)

    def step(i: int) -> None:
        job["step"] = i

    try:
        aid = ingest.parse_id(paper)
        job["arxiv_id"] = aid
        work = os.path.join(CFG["workdir"], aid)
        os.makedirs(work, exist_ok=True)

        out = os.path.join(CFG["library"], f"{aid}.html")
        if os.path.exists(out) and not job.get("force"):
            log("already built - opening the cached page")
            job.update(state="done", url=f"/p/{aid}")
            return

        step(0)
        log(f"fetching arxiv:{aid}")
        doc = ingest.load(paper, work, log=log)
        log(f"{len(doc.sections)} sections, {len(doc.figures)} figures")
        step(1)

        sb_path = os.path.join(work, "storyboard.json")
        if os.path.exists(sb_path) and not job.get("force"):
            log("reusing cached storyboard (no tokens spent)")
            sb = storyboard.Storyboard.from_dict(json.load(open(sb_path)))
        else:
            log("writing the storyboard - one model call")
            sb, warns = storyboard.generate(
                ingest.condense(doc), {f.ref for f in doc.figures},
                minutes=CFG["minutes"], scenes=CFG["scenes"], log=log,
                provider=CFG["llm"], model=CFG["model"])
            for w in warns:
                log(f"note: {w}")
            for w in storyboard._flag_similar_figures(sb, doc):
                log(f"note: {w}")
            if not CFG["no_place"]:
                n = sum(1 for s in sb.scenes if s.visual.get("kind") == "figure")
                step(2)
                log(f"checking markers against {n} figures")
                dropped = storyboard.place_annotations(
                    sb, doc, log=log, provider=CFG["llm"],
                    model=(CFG.get("place_model")
                           if CFG["llm"] == "anthropic" else CFG["model"]))
                if dropped:
                    log(f"dropped {dropped} markers that did not match")
            n = storyboard.prebake_answers(
                sb, ingest.condense(doc), log=log, provider=CFG["llm"],
                model=(CFG.get("place_model")
                       if CFG["llm"] == "anthropic" else CFG["model"]))
            if n:
                log(f"pre-answered the buttons on {n} scenes")
            with open(sb_path, "w") as f:
                f.write(sb.to_json())

        step(3)
        log("building the page")
        os.makedirs(CFG["library"], exist_ok=True)
        web.build(sb, doc, ingest.condense(doc), out)
        with open(os.path.join(CFG["library"], f"{aid}.json"), "w") as f:
            json.dump({"arxiv_id": aid, "title": sb.title,
                       "paper_title": doc.title,
                       "scenes": len(sb.scenes)}, f)
        step(4)
        job.update(state="done", url=f"/p/{aid}")
        log("done")
    except Exception as e:
        job["log"].append(f"{type(e).__name__}: {e}")
        job.update(state="error", error=str(e))
        traceback.print_exc()


def _library() -> list[dict]:
    lib = CFG["library"]
    if not os.path.isdir(lib):
        return []
    out = []
    for name in sorted(os.listdir(lib)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(lib, name)) as f:
                out.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    return out


# --- the UI ------------------------------------------------------------------

INDEX = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paperly</title><style>
*{box-sizing:border-box}
body{margin:0;background:%(paper)s;color:%(ink)s;font-family:%(display)s;
     -webkit-font-smoothing:antialiased}
.wrap{max-width:900px;margin:0 auto;padding:0 28px 120px}
.nav{border-bottom:1px solid %(rule)s;padding:17px 28px;margin-bottom:76px}
.nav .inner{max-width:900px;margin:0 auto}
.mark{font-family:%(display)s;font-size:20px;font-weight:680;
      letter-spacing:-.03em;color:%(ink)s;display:inline-block}
.mark b{color:%(signal)s;font-weight:680}
.eyebrow{font-family:%(mono)s;font-size:11px;letter-spacing:.18em;
         text-transform:uppercase;color:%(graphite)s;margin:0 0 18px}
.sub{font-size:17px;color:%(graphite)s;line-height:1.55;max-width:44ch;
     margin:0 0 30px}
h1{font-size:clamp(40px,6.5vw,72px);font-weight:650;letter-spacing:-.035em;
   margin:0 0 14px;line-height:1}
h1 em{font-style:normal;color:%(signal)s}
.sub{font-size:19px;color:%(graphite)s;margin:0 0 40px;max-width:46ch;
     line-height:1.55}

/* the hero is a specimen: the product doing its one trick, at rest */
.spec{border:1px solid %(rule)s;background:#fff;padding:22px 24px;margin:0 0 40px}
.spec svg{width:100%%;height:auto;display:block}
.spec .cap{font-family:%(mono)s;font-size:10.5px;letter-spacing:.13em;
           text-transform:uppercase;color:%(graphite)s;margin-top:14px}
.dot{animation:drift 5s ease-in-out infinite}
@keyframes drift{0%%,100%%{opacity:.55}50%%{opacity:1}}

form{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
input{font-family:%(display)s;font-size:17px;padding:16px 18px;
      border:2px solid %(ink)s;background:#fff;color:%(ink)s;flex:1;min-width:280px}
input:focus{outline:none;border-color:%(signal)s}
button{font-family:%(mono)s;font-size:12px;letter-spacing:.14em;
       text-transform:uppercase;background:%(ink)s;color:%(paper)s;
       border:2px solid %(ink)s;padding:16px 28px;cursor:pointer}
button:hover{background:%(signal)s;border-color:%(signal)s}
button:disabled{opacity:.4;cursor:wait}
.note{font-family:%(mono)s;font-size:11px;color:%(graphite)s;letter-spacing:.06em}

/* progress: named stages, so waiting has shape */
.run{display:none;margin-top:34px;border:1px solid %(rule)s;background:#fff}
.run.on{display:block}
.runhead{display:flex;justify-content:space-between;align-items:baseline;
         padding:16px 20px;border-bottom:1px solid %(rule)s}
.runhead .t{font-family:%(mono)s;font-size:11px;letter-spacing:.14em;
            text-transform:uppercase;color:%(graphite)s}
.runhead .clock{font-family:%(mono)s;font-size:13px;color:%(ink)s;
                font-variant-numeric:tabular-nums}
.steps{display:grid;gap:0}
.step{display:flex;gap:14px;align-items:center;padding:13px 20px;
      border-bottom:1px solid %(wash)s;font-size:15px;color:%(graphite)s}
.step .pip{width:9px;height:9px;flex:0 0 9px;border-radius:50%%;
           border:1.5px solid %(rule)s;background:transparent}
.step.now{color:%(ink)s;font-weight:560}
.step.now .pip{border-color:%(signal)s;background:%(signal)s;
               animation:beat 1.05s ease-in-out infinite}
.step.was{color:%(ink)s}
.steps.dead .step{opacity:.35}
.steps.dead .step .pip{animation:none;border-color:%(rule)s;background:transparent}
.runhead .t.bad{color:%(signal)s}
.step.was .pip{border-color:%(ink)s;background:%(ink)s}
@keyframes beat{0%%,100%%{transform:scale(.75);opacity:.45}
                50%%{transform:scale(1.25);opacity:1}}

h2{font-size:11px;font-family:%(mono)s;letter-spacing:.18em;text-transform:uppercase;
   color:%(graphite)s;font-weight:500;margin:72px 0 4px;
   padding-bottom:13px;border-bottom:1px solid %(rule)s}
.lib a{display:block;padding:18px 0;border-bottom:1px solid %(rule)s;
       text-decoration:none;color:%(ink)s}
.lib a:hover{padding-left:11px;border-color:%(signal)s;transition:padding .13s}
.lib .t{font-size:18px;font-weight:600;letter-spacing:-.012em}
.lib .m{font-family:%(mono)s;font-size:11px;color:%(graphite)s;
        letter-spacing:.08em;margin-top:6px}
.empty{color:%(graphite)s;font-size:15px;padding:20px 0}
.wait{display:none;margin-top:34px;border:1px solid %(signal)s;background:#fff;
      padding:26px 28px}
.wait.on{display:block}
.wait h3{font-size:22px;font-weight:640;letter-spacing:-.02em;margin:0 0 10px}
.wait p{font-size:16px;color:%(graphite)s;line-height:1.55;margin:0 0 20px;
        max-width:52ch}
.wait form{display:flex;gap:9px;flex-wrap:wrap;margin:0}
.wait .ok{font-family:%(mono)s;font-size:12px;letter-spacing:.1em;
          text-transform:uppercase;color:%(signal)s}
</style></head><body><div class="nav"><div class="inner"><span class="mark">Paperly<b>.</b></span></div></div>

<div class="wrap">
<h1>Paste a paper.</h1>
<p class="sub">Paste any arXiv paper link and get it explained</p>

<form id="f">
  <input id="u" placeholder="https://arxiv.org/abs/2503.01234" autofocus
         autocomplete="off" spellcheck="false">
  <button id="go" type="submit">Build</button>
</form>

<div class="run" id="run">
  <div class="runhead">
    <span class="t" id="rt">Working</span>
    <span class="clock" id="clock">0:00</span>
  </div>
  <div class="steps" id="steps"></div>
</div>

<div class="wait" id="wait">
  <h3 id="waith">That was your free paper.</h3>
  <p id="waitp">Join the waitlist for unlimited access &mdash; or run it
  yourself, it is open source and works on your own key.</p>
  <form id="wf">
    <input id="we" type="email" placeholder="you@university.edu" required>
    <button type="submit">Join waitlist</button>
  </form>
</div>

<h2 id="libh" hidden>Built already</h2>
<div class="lib" id="lib"></div>
</div><script>
const $ = s => document.querySelector(s);
const STEPS = ['Fetching the paper', 'Reading figures and sections',
               'Writing the storyboard', 'Checking markers against the figures',
               'Building the page'];
let t0 = 0, tick = null;

function drawSteps(at) {
  $('#steps').innerHTML = '';
  STEPS.forEach((label, i) => {
    const d = document.createElement('div');
    d.className = 'step' + (i < at ? ' was' : i === at ? ' now' : '');
    d.innerHTML = '<span class="pip"></span><span></span>';
    d.lastChild.textContent = label;
    $('#steps').appendChild(d);
  });
}

function clock() {
  const s = Math.floor((Date.now() - t0) / 1000);
  $('#clock').textContent = Math.floor(s / 60) + ':' +
    String(s %% 60).padStart(2, '0');
}

// Errors still need somewhere to land now that the log tail is gone.
function line(t, err) {
  if (!err) return;
  $('#rt').textContent = t;
}

async function library() {
  const items = await (await fetch('/api/library')).json();
  const lib = $('#lib');
  lib.innerHTML = '';
  if (!items.length) { $('#libh').hidden = true; return; }
  $('#libh').hidden = false;
  items.forEach(it => {
    const a = document.createElement('a');
    a.href = '/p/' + it.arxiv_id;
    a.innerHTML = '<div class="t"></div><div class="m"></div>';
    a.querySelector('.t').textContent = it.title;
    a.querySelector('.m').textContent = 'arXiv:' + it.arxiv_id;
    lib.appendChild(a);
  });
}

// arXiv ids look like 2503.01234, optionally with a version suffix. Anything
// without one is not a paper, and a round trip to find that out is worse than
// saying so straight away.
const ARXIV = /(\d{4}\.\d{4,5})(v\d+)?/;

function stop(msg) {
  $('#run').className = 'run on';
  $('#steps').className = 'steps dead';
  $('#rt').className = 't bad';
  $('#rt').textContent = msg;
  $('#go').disabled = false;
  clearInterval(tick);
}

function showWaitlist(msg) {
  if (msg) $('#waitp').textContent = msg;
  $('#wait').className = 'wait on';
  $('#wait').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

$('#wf').onsubmit = async (e) => {
  e.preventDefault();
  const email = $('#we').value.trim();
  if (!email) return;
  try {
    const r = await fetch('/api/waitlist', { method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email }) });
    const d = await r.json();
    $('#wf').innerHTML = d.ok
      ? '<span class="ok">You are on the list. Thank you.</span>'
      : '<span class="ok">That address did not look right.</span>';
  } catch (err) {
    $('#wf').innerHTML = '<span class="ok">Could not reach the server.</span>';
  }
};

$('#f').onsubmit = async (e) => {
  e.preventDefault();
  const url = $('#u').value.trim();
  if (!url) return;
  if (!ARXIV.test(url)) {
    drawSteps(-1);
    stop("That is not an arXiv link \u2014 try arxiv.org/abs/2503.01234");
    return;
  }
  $('#go').disabled = true;
  $('#run').className = 'run on';
  $('#steps').className = 'steps';
  $('#rt').className = 't';
  $('#rt').textContent = 'Working';
  t0 = Date.now(); drawSteps(0); clock();
  clearInterval(tick); tick = setInterval(clock, 1000);
  try {
    const r = await fetch('/api/build', { method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ paper: url }) });
    if (r.status === 402) {
      const d = await r.json();
      $('#run').className = 'run';
      $('#go').disabled = false;
      clearInterval(tick);
      showWaitlist(d.message);
      return;
    }
    const { job } = await r.json();
    const poll = setInterval(async () => {
      const s = await (await fetch('/api/status/' + job)).json();
      if (s.state === 'running') drawSteps(s.step || 0);
      if (s.state === 'done') {
        clearInterval(poll); clearInterval(tick);
        $('#rt').textContent = 'Opening';
        location.href = s.url;
      }
      if (s.state === 'error') {
        clearInterval(poll);
        stop(s.error);
        library();
      }
    }, 700);
  } catch (err) {
    stop('Could not reach the server: ' + err.message);
  }
};
library();
</script></body></html>""" % {**TOKENS, "display": FONT_DISPLAY, "mono": FONT_MONO}


LOGIN = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paperly</title><style>
body{margin:0;background:%(paper)s;color:%(ink)s;font-family:%(display)s;
     display:flex;align-items:center;justify-content:center;height:100vh}
form{display:flex;gap:10px;flex-wrap:wrap;max-width:420px;padding:0 24px}
input{font-size:17px;padding:15px 17px;border:2px solid %(ink)s;background:#fff;
      color:%(ink)s;flex:1;min-width:200px;font-family:%(display)s}
input:focus{outline:none;border-color:%(signal)s}
button{font-family:%(mono)s;font-size:12px;letter-spacing:.14em;
       text-transform:uppercase;background:%(ink)s;color:%(paper)s;
       border:2px solid %(ink)s;padding:15px 26px;cursor:pointer}
</style></head><body>
<form method="post" action="/login">
  <input type="password" name="pw" placeholder="Passphrase" autofocus>
  <button type="submit">Enter</button>
</form></body></html>""" % {**TOKENS, "display": FONT_DISPLAY, "mono": FONT_MONO}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # keep the console for pipeline output
        pass

    def _visitor(self) -> tuple[str, bool]:
        """Return (visitor id, is_new). Identity is a cookie -- good enough to
        stop casual repeat use, not an identity system."""
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "p2vid" and v:
                return v, False
        return LIMITS.new_visitor() if LIMITS else "anon", True

    def _authed(self) -> bool:
        """A deployed instance spends the owner's key on every build, so it
        cannot be left open. Locally there is no passphrase and no gate."""
        pw = CFG.get("password")
        if not pw:
            return True
        # Cookies do not travel cross-origin by default, so an API client
        # authenticates with a bearer token instead. Same secret either way.
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == pw:
            return True
        cookie = self.headers.get("Cookie", "")
        return f"p2v={hashlib.sha256(pw.encode()).hexdigest()[:32]}" in cookie

    def _deny(self):
        self._send(401, LOGIN)

    def _gate(self, vid: str, kind: str):
        """None if allowed, else the JSON body to return."""
        if not LIMITS:
            return None
        ok, why = LIMITS.check(vid, kind)
        if ok:
            return None
        return json.dumps({
            "error": "limit",
            "reason": why,
            "waitlist": True,
            "message": ("The free preview is out of capacity for now."
                        if why == "budget" else
                        "That is your free " + kind + ". Join the waitlist for "
                        "unlimited access, or run it yourself -- it is open "
                        "source and works on your own key."),
        })

    def _cors(self):
        """A frontend served from another domain -- Lovable, Vercel, localhost
        during development -- is a different origin, so the browser will not
        let it read these responses without permission. Set
        PAPER2VID_CORS_ORIGIN to that domain to grant it."""
        origin = CFG.get("cors")
        if not origin:
            return
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "content-type, authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._authed():
            return self._deny()
        if path == "/":
            vid, is_new = self._visitor()
            if is_new and LIMITS:
                data = INDEX.encode("utf-8")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie",
                                 f"p2vid={vid}; Path=/; Max-Age=31536000; "
                                 f"SameSite=Lax")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            return self._send(200, INDEX)
        if path == "/api/health":
            # A built page probes this. If it answers, the page is being
            # served and can route questions through here, using the key
            # already in the environment. If it does not, the page is being
            # opened standalone and falls back to asking for one.
            return self._send(200, json.dumps(
                {"ok": True, "provider": CFG.get("llm", "anthropic")}),
                "application/json")
        if path == "/api/quota":
            vid, _ = self._visitor()
            return self._send(200, json.dumps(
                LIMITS.remaining(vid) if LIMITS
                else {"builds": None, "asks": None}), "application/json")
        if path == "/api/stats":
            # Owner-only: needs the passphrase even when the site is open.
            if not CFG.get("password") or not self._authed():
                return self._send(404, "{}", "application/json")
            return self._send(200, json.dumps(
                LIMITS.stats() if LIMITS else {}), "application/json")
        if path == "/api/library":
            return self._send(200, json.dumps(_library()), "application/json")
        if path.startswith("/api/status/"):
            job = JOBS.get(path.rsplit("/", 1)[1])
            if not job:
                return self._send(404, "{}", "application/json")
            return self._send(200, json.dumps(job), "application/json")
        if path.startswith("/api/paper/"):
            aid = os.path.basename(path[11:])
            meta = os.path.join(CFG["library"], f"{aid}.json")
            page = os.path.join(CFG["library"], f"{aid}.html")
            if not os.path.exists(page):
                return self._send(404, '{"error":"not built"}', "application/json")
            # The built page carries its scenes inline; hand them back as data
            # so a client can lay them out itself instead of embedding ours.
            with open(page, encoding="utf-8") as fh:
                html = fh.read()
            m = re.search(r"window\.__SCENES__ = (\[.*?\]);\n", html, re.S)
            c = re.search(r"window\.__CONTEXT__ = (\{.*?\});\n", html, re.S)
            out = {"arxiv_id": aid}
            if os.path.exists(meta):
                with open(meta) as fh:
                    out.update(json.load(fh))
            # The library metadata carries a scene COUNT under the same name,
            # so the array has to be written after the merge or it is clobbered
            # by an integer.
            out["scene_count"] = out.get("scenes")
            out["scenes"] = json.loads(m.group(1)) if m else []
            out["context"] = json.loads(c.group(1)) if c else {}
            return self._send(200, json.dumps(out), "application/json")
        if path.startswith("/p/"):
            aid = os.path.basename(path[3:])
            f = os.path.join(CFG["library"], f"{aid}.html")
            if os.path.exists(f):
                with open(f, "rb") as fh:
                    return self._send(200, fh.read())
            return self._send(404, f"<p>No page built for {htmllib.escape(aid)}.</p>")
        return self._send(404, "<p>Not found.</p>")

    def do_POST(self):
        if self.path == "/login":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode()
            pw = urllib.parse.parse_qs(body).get("pw", [""])[0]
            if pw and pw == CFG.get("password"):
                tok = hashlib.sha256(pw.encode()).hexdigest()[:32]
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie",
                                 f"p2v={tok}; Path=/; Max-Age=7776000; "
                                 f"HttpOnly; SameSite=Lax")
                self.end_headers()
                return
            return self._deny()
        if not self._authed():
            return self._deny()
        if self.path == "/api/ask":
            vid, _ = self._visitor()
            blocked = self._gate(vid, "ask")
            if blocked:
                return self._send(402, blocked, "application/json")
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n))
                answer = llm.complete(
                    body["content"], body.get("system", ""),
                    provider=CFG.get("llm", "anthropic"),
                    model=CFG.get("model"), max_tokens=700)
                if LIMITS:
                    LIMITS.charge(vid, "ask")
                return self._send(200, json.dumps({"text": answer}),
                                  "application/json")
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}),
                                  "application/json")
        if self.path == "/api/waitlist":
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return self._send(400, '{"ok":false}', "application/json")
            ok = LIMITS.join(body.get("email", ""),
                             body.get("note", "")) if LIMITS else False
            return self._send(200, json.dumps({"ok": ok}), "application/json")
        if self.path != "/api/build":
            return self._send(404, "{}", "application/json")
        n = int(self.headers.get("Content-Length", 0))
        try:
            paper = json.loads(self.rfile.read(n))["paper"]
        except (json.JSONDecodeError, KeyError):
            return self._send(400, '{"error":"bad request"}', "application/json")
        vid, _ = self._visitor()
        blocked = self._gate(vid, "build")
        if blocked:
            return self._send(402, blocked, "application/json")
        if LIMITS:
            LIMITS.charge(vid, "build")
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"state": "running", "log": [], "url": None}
        threading.Thread(target=_run_job, args=(job_id, paper),
                         daemon=True).start()
        return self._send(200, json.dumps({"job": job_id}), "application/json")


def _load_seed() -> int:
    """Copy committed pages into the library on startup.

    A free-tier host has no persistent disk, so anything built at runtime is
    gone on the next deploy. Pages you want present on arrival have to travel
    with the image -- commit them to seed/ and they land here every boot.
    Runtime builds still win: an existing file is never overwritten.
    """
    seed = CFG.get("seed") or "seed"
    if not os.path.isdir(seed):
        return 0
    import shutil
    n = 0
    for name in os.listdir(seed):
        if not name.endswith((".html", ".json")):
            continue
        dst = os.path.join(CFG["library"], name)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(seed, name), dst)
            n += 1 if name.endswith(".html") else 0
    return n


def serve(host: str, port: int, cfg: dict) -> None:
    global LIMITS
    CFG.update(cfg)
    budget = float(os.environ.get("PAPER2VID_BUDGET_USD", 0) or 0)
    if budget:
        LIMITS = limits_mod.Limits(
            os.path.join(CFG.get("library", "library"), "_limits.json"), budget)
        st = LIMITS.stats()
        print(f"  free tier: {limits_mod.FREE_BUILDS} build + "
              f"{limits_mod.FREE_ASKS} question per visitor, "
              f"${budget:.2f} cap (${st['spent']:.2f} spent, "
              f"{st['waitlist']} on waitlist)", flush=True)
    os.makedirs(CFG["library"], exist_ok=True)
    seeded = _load_seed()
    if seeded:
        print(f"  loaded {seeded} pages from seed/", flush=True)
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except PermissionError:
        raise SystemExit(
            f"  port {port} needs root on this system (anything below 1024 "
            f"does).\n  Try: paper2vid --serve --port 8842")
    except OSError as e:
        raise SystemExit(
            f"  could not bind port {port}: {e}\n"
            f"  Something else may be using it -- try --port {port + 1}.")
    print(f"  paper2vid ready at http://{host}:{port}", flush=True)
    print(f"  library: {os.path.abspath(CFG['library'])}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped", flush=True)
