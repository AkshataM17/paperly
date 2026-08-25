# HTTP API

Enough to build your own frontend against a running instance. Everything here
is what the built-in UI already uses.

## Auth

If `PAPER2VID_PASSWORD` is set, every route needs it. A browser on the same
origin gets a cookie from the login form; an API client sends a header:

```
Authorization: Bearer <PAPER2VID_PASSWORD>
```

## CORS

A frontend on another domain is a different origin, so set
`PAPER2VID_CORS_ORIGIN` to that domain — e.g. `https://yourapp.lovable.app`.
Without it the browser will block the response even though the server answered.

## Endpoints

### `GET /api/library`
Everything built so far.
```json
[{"arxiv_id": "2503.01234", "title": "...", "paper_title": "...", "scenes": 10}]
```

### `POST /api/build`  `{"paper": "2503.01234"}`
Starts a build, returns immediately.
```json
{"job": "a1b2c3d4e5f6"}
```

### `GET /api/status/<job>`
Poll it. `step` is 0–4 against: fetching, reading, storyboard, checking
markers, building.
```json
{"state": "running", "step": 2, "log": ["fetching arxiv:2503.01234"], "url": null}
```
`state` ends as `done` (with `url`) or `error` (with `error`).

### `GET /api/paper/<id>`
The page as data, so you can lay it out yourself.
```json
{
  "arxiv_id": "2503.01234",
  "title": "...",
  "scene_count": 10,
  "scenes": [
    {"id": "s1", "kind": "title", "headline": "...", "deck": "...", "narration": "..."},
    {"id": "s4", "kind": "figure", "ref": "F2", "src": "data:image/png;base64,...",
     "caption": "...", "narration": "...",
     "annotations": [{"x": 0.62, "y": 0.31, "label": "...", "reveal": 0.4}]}
  ],
  "context": {"digest": "...", "title": "...", "arxiv_id": "..."}
}
```

Figures arrive as data URIs, so a client needs no second request and no image
host. Annotation `x`/`y` are fractions of the figure box — position markers
over the image with them.

### `POST /api/ask`
Grounded Q&A. `content` is an Anthropic message content array, so an image
block can ride along with the question — that is how "what does the orange line
mean" gets answered from the plot rather than guessed.
```json
{"system": "...", "content": [{"type": "text", "text": "..."}]}
```
→ `{"text": "..."}` or `{"error": "..."}`

### `GET /p/<id>`
The self-contained HTML page, if you would rather link to it than rebuild it.

## Notes

`context.digest` is the structured paper summary the model was given. Pass it
back with questions to keep answers grounded in the paper rather than the
model's memory — the built-in UI does this, and it is the main thing stopping
it inventing numbers.
