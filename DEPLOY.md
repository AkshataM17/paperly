# Deploying your own instance

`--serve` is a long-running process that holds job state in memory and writes
finished pages to disk. That rules out serverless — Vercel, Netlify Functions,
Lambda — where the poll request can land on a different instance than the one
building, and the filesystem vanishes between calls. Use anything that runs a
container with a volume.

## Railway (simplest)

1. New Project → Deploy from GitHub → pick this repo. It detects the Dockerfile.
2. Variables:
   - `ANTHROPIC_API_KEY` — your key
   - `PAPER2VID_PASSWORD` — a passphrase; **set this**
3. Add a Volume mounted at `/data` so built pages survive redeploys.
4. Settings → Generate Domain.

`PORT` is injected automatically, and the server binds `0.0.0.0` when it sees it.

## Render / Fly.io

Same shape: Docker deploy, a persistent disk mounted at `/data`, and the two
environment variables above.

## Give it something to show

A fresh deploy has an empty library, so the first thing a visitor does is paste
a link and watch a progress bar for a minute. Build a few pages first and
commit them:

```bash
./build-seed.sh                    # or pass your own ids
git add seed && git commit -m "seed library" && git push
```

Anything in `seed/` is copied into the library on every boot, so it survives
redeploys even without a volume. Runtime builds always win — an existing page
is never overwritten.

Each page is 1–2MB because figures are inlined, so keep it to a handful.

## Set a passphrase

Every build spends *your* API credits. Without `PAPER2VID_PASSWORD` anyone who
finds the URL can run up your bill — the server warns on startup if it's
deployed without one. With it set, all routes return a login page until you
enter the passphrase, which is then remembered for 90 days.

This is a lock on your own door, not a multi-user auth system. One passphrase,
no accounts. If you later want to charge people, that's a different build.

## Free tier and the waitlist

Set `PAPER2VID_BUDGET_USD` and the preview turns on:

```
PAPER2VID_BUDGET_USD=50
```

Each visitor gets one build and one question, then the waitlist panel appears.
Two protections, because they fail differently — the per-visitor quota stops
one person building fifty papers, the budget stops ten thousand people building
one each. The budget is a hard stop checked before every paid call, not an
alert afterwards.

Both are enforced server-side. Anything checked in the browser is bypassed with
devtools in ten seconds, and it is your money.

Signups land in `library/_limits.json`. Read them with:

```bash
curl -s https://your-app/api/stats -H "Authorization: Bearer $PAPER2VID_PASSWORD"
```

Change `FREE_BUILDS` and `FREE_ASKS` in `paper2vid/limits.py` to give more away.
Without `PAPER2VID_BUDGET_USD` there are no limits at all — correct for a
private instance, dangerous for a public one.

## Costs

The container is a few dollars a month. The API calls are yours: roughly 10–20¢
per new paper, and nothing at all for one already in the library.
