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

import html as htmllib
import json
import os
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ingest, llm, storyboard, web
from .style import TOKENS, FONT_DISPLAY, FONT_MONO

JOBS: dict[str, dict] = {}
CFG: dict = {}


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
<title>paper2vid</title><style>
*{box-sizing:border-box}
body{margin:0;background:%(paper)s;color:%(ink)s;font-family:%(display)s;
     -webkit-font-smoothing:antialiased}
.wrap{max-width:900px;margin:0 auto;padding:120px 28px 120px}
.eyebrow{font-family:%(mono)s;font-size:11px;letter-spacing:.18em;
         text-transform:uppercase;color:%(graphite)s;margin:0 0 18px}
h1{font-size:clamp(40px,6.5vw,72px);font-weight:650;letter-spacing:-.035em;
   margin:0 0 34px;line-height:1}
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
</style></head><body><div class="wrap">

<p class="eyebrow">arXiv, marked up</p>
<h1>Paste a paper.</h1>

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
    a.querySelector('.m').textContent =
      'arXiv:' + it.arxiv_id + '  \u00b7  ' + it.scenes + ' scenes';
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # keep the console for pipeline output
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, INDEX)
        if path == "/api/health":
            # A built page probes this. If it answers, the page is being
            # served and can route questions through here, using the key
            # already in the environment. If it does not, the page is being
            # opened standalone and falls back to asking for one.
            return self._send(200, json.dumps(
                {"ok": True, "provider": CFG.get("llm", "anthropic")}),
                "application/json")
        if path == "/api/library":
            return self._send(200, json.dumps(_library()), "application/json")
        if path.startswith("/api/status/"):
            job = JOBS.get(path.rsplit("/", 1)[1])
            if not job:
                return self._send(404, "{}", "application/json")
            return self._send(200, json.dumps(job), "application/json")
        if path.startswith("/p/"):
            aid = os.path.basename(path[3:])
            f = os.path.join(CFG["library"], f"{aid}.html")
            if os.path.exists(f):
                with open(f, "rb") as fh:
                    return self._send(200, fh.read())
            return self._send(404, f"<p>No page built for {htmllib.escape(aid)}.</p>")
        return self._send(404, "<p>Not found.</p>")

    def do_POST(self):
        if self.path == "/api/ask":
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n))
                answer = llm.complete(
                    body["content"], body.get("system", ""),
                    provider=CFG.get("llm", "anthropic"),
                    model=CFG.get("model"), max_tokens=700)
                return self._send(200, json.dumps({"text": answer}),
                                  "application/json")
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}),
                                  "application/json")
        if self.path != "/api/build":
            return self._send(404, "{}", "application/json")
        n = int(self.headers.get("Content-Length", 0))
        try:
            paper = json.loads(self.rfile.read(n))["paper"]
        except (json.JSONDecodeError, KeyError):
            return self._send(400, '{"error":"bad request"}', "application/json")
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"state": "running", "log": [], "url": None}
        threading.Thread(target=_run_job, args=(job_id, paper),
                         daemon=True).start()
        return self._send(200, json.dumps({"job": job_id}), "application/json")


def serve(host: str, port: int, cfg: dict) -> None:
    CFG.update(cfg)
    os.makedirs(CFG["library"], exist_ok=True)
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
