# JAV Collection Manager

Browse, manage, and classify your local JAV collection through a local web UI.
Reads file data from **Everything**, fetches full metadata (title · cover · actresses · maker · label · series · genres · director · release date · runtime · score · summary · preview images) from a self-hosted **[MetaTube](https://github.com/metatube-community/metatube-sdk-go)** API server.
Optional AI genre classification via a local **Ollama** model.

**PC use:** open `index.html` directly — no server needed.
**iOS / LAN use:** run `serve.bat` (or `python serve.py`) and browse to the printed URL on your phone.

---

## Requirements

| Requirement | Notes |
|---|---|
| [Everything](https://www.voidtools.com/) | Must be running with HTTP server enabled |
| Python 3.8+ | Standard library only — **no `pip install` needed** |
| [MetaTube API server](https://github.com/metatube-community/metatube-sdk-go) | Self-hosted (e.g. Docker on Railway) — used **only during scans**, never while browsing |
| [Ollama](https://ollama.com/) + `gemma4:e4b` | **Optional** — only needed for `classify.py` |

---

## One-time setup

### 1 — Enable Everything's HTTP server

1. Open Everything
2. **Tools → Options → HTTP Server**
3. ✅ **Enable HTTP Server** (default port: **80**)
4. Click OK

> If port 80 is taken, change it to e.g. `8080` and update
> `EVERYTHING_PORT = 80` at the top of `scan.py` **and** pass the port to `serve.py`:
> `python serve.py 8080 8080`

### 2 — Set your collection roots and MetaTube server

Edit the top of `scan.py`:

```python
ROOT_DIRS = [
    r"E:\115\云下载",
    r"E:\115\!NSFW\4k",   # add as many folders as you like
]

METATUBE_URL   = "https://your-metatube-server"   # self-hosted MetaTube API
METATUBE_TOKEN = ""            # only if the server was started with -token
PROVIDER_PRIORITY = ["FANZA", "MGS", "JavBus", ...]  # who wins on multi-hit
```

You can add or remove directories any time — cached metadata is indexed by bango code, not by path, so it survives the change.

See **`METATUBE_API.md`** for the full API reference of the MetaTube server.

---

## Daily use

**Step 1 — generate data** (run whenever your collection changes):

```
python scan.py
```

Scans your collection, writes `data.js`, then fetches up to **50 missing metadata records** and **100 actress avatars** per run. Each run adds another batch until everything is covered.

**Step 2 — open the UI:**

- **PC:** double-click `index.html` (or drag it into your browser)
- **iOS / other devices:** double-click `serve.bat`, then browse to the URL shown in the terminal

---

## Scanner options

| Command | What it does |
|---|---|
| `python scan.py` | Scan + fetch up to 50 new metadata records |
| `python scan.py --all-meta` | Scan + fetch **all** missing metadata + avatars in one go |
| `python scan.py --skip-meta` | Scan only — no network calls, fastest |
| `python scan.py --export-bangos` | Write `bangos.txt` so the fetch job can run in the cloud (see below) |
| `python scan.py --fix-covers` | Swap hotlink-blocked (JavBus) image URLs in the cache for DMM-hosted ones |
| `python scan.py --test-bango MIDE-332` | Test the MetaTube fetch for a single bango |

> **Tip:** Run `--all-meta` once on a large collection, then use plain `scan.py` for day-to-day updates.
> Ctrl+C at any time — progress is saved to `meta_cache.json` and resumes on the next run.
> Already-cached items are **never** re-fetched (bump `META_VERSION` in `scan.py` to force a full refresh).

### Running the fetch job in the cloud

The metadata fetch doesn't need your files or Everything — only the bango list and the cache. So the hours-long first migration can run on any machine with Python (a free GitHub Actions runner works well; see `fetch_meta_workflow.yml` for a ready-made workflow):

1. **PC:** `python scan.py --export-bangos` → writes `bangos.txt`
2. **Cloud:** put `scan.py` + `fetch_meta.py` + `bangos.txt` (+ existing caches) together, run `python fetch_meta.py`
3. **PC:** copy `meta_cache.json` + `actress_cache.json` back, run `python scan.py` (instant — all from cache)

---

## iOS / LAN access

`serve.py` starts a tiny HTTP server so the viewer works from any device on the same Wi-Fi:

```
python serve.py              # viewer on :8080, Everything on :80
python serve.py 9000         # viewer on :9000
python serve.py 8080 8080    # viewer on :8080, Everything on :8080
```

Or just double-click **`serve.bat`**.

The terminal prints two URLs — one for the PC, one for your phone.
File and folder links in the detail panel open via Everything's HTTP server, so you can stream video directly in Safari.

---

## How metadata works

`scan.py` searches your MetaTube server (`/v1/movies/search`), keeps the hits whose number matches the bango, fetches full info from the best-ranked provider in `PROVIDER_PRIORITY`, and backfills missing fields (actresses, genres, score…) from the runner-up. Actress avatar images come from MetaTube's **Gfriends** actor provider.

- Results are cached in `meta_cache.json` (per bango) and `actress_cache.json` (per name) — each is fetched only once
- The cache is keyed by bango code, not by path — safe to add/remove `ROOT_DIRS` entries
- Items MetaTube can't find keep any legacy metadata as a fallback and are marked so they aren't retried
- **Images load directly from the original hosts (DMM/MGS…), never through the MetaTube server at browse time.** JavBus-hosted images are hotlink-blocked, so `scan.py` prefers the same artwork from a DMM-hosted provider; run `--fix-covers` once to patch older cache entries

---

## Genre classifier (optional)

`classify.py` categorises every folder into one of 9 genres using rule-based matching first, with an Ollama LLM fallback for anything the rules miss.

**Genres:** `jav` · `uncensored` · `hentai` · `amateur` · `western` · `anime` · `gravure` · `game` · `other`

### Setup

Install Ollama and pull the model:

```
ollama pull gemma4:e4b
```

### Usage

```
python classify.py                  # rules first, then LLM for unmatched items
python classify.py --rules-only    # rules only — no Ollama needed, fast
python classify.py --all           # ignore cache, reclassify everything
```

Results are saved to `classify_cache.json` and exported to `classify_data.js` for the browser.
The **Classifier** tab shows all items with their genre, category filter pills, and stats. Items can be selected, marked for deletion, and opened in the detail panel — same as Browse.

---

## Views

| View | What it shows |
|---|---|
| **Dashboard** | Total size · item counts · top-20 series / makers / genres charts |
| **Browse** | All items sorted by size/date/score; cards show cover · title · maker · label · actresses · lazy-loaded |
| **Statistics** | Sortable table switchable between Series code · Maker · Label · Genre · Series (meta) · Director; click a row → browse it |
| **Actresses** | Avatar card grid with item count, size, top makers/genres; click a card → browse her items |
| **Classifier** | Items grouped by genre; category filter pills · stats bar · select/mark/detail · lazy-loaded |
| **Non-JAV** | Directories where no bango could be detected |

**Detail panel** → full metadata table (released · runtime · maker · label · series · director · score · source link), clickable genre chips, preview image strip with lightbox, collapsible summary.
**Mark for deletion** → marks items with a red border; **Export list** → downloads a `.txt`.
**Multi-select** → check boxes or Shift+click to range-select; mark all at once.

### Search syntax (Browse)

Free text matches name, bango, title, actress, maker, label, series, genres, and director. Field-scoped tokens narrow it down and can be combined (AND):

```
maker:S1  label:"S1 NO.1 STYLE"  genre:単体作品  actress:JULIA
series:MIDE  mseries:交わる体液  director:ながしまん  provider:JavBus
```

Every chip in the UI (maker, label, genre, series, actress…) is clickable and jumps to the corresponding filtered search.

---

## Supported bango formats

| Format | Example |
|---|---|
| Standard | `MIDE-332`, `SNIS-001`, `CAWD-100` |
| No dash | `MIDE332`, `SSNI001`, `EKDV460` |
| Dot separator | `STARS.001` |
| FC2 | `FC2-PPV-1234567`, `FC2PPV3456789` |
| HEYZO | `HEYZO-2345`, `heyzo_hd_2345`, `(HEYZO)(2345)` |
| Numeric prefix | `300MAAN-456`, `200GANA-123`, `230ORECO-171` |
| 1Pondo / Caribbean | `1PONDO-101015-001`, `102720-001-carib`, `(1pondo)(062414_832)` |
| 1000Giri | `(1000人斬り)(150610yume)` |
| Gachinco | `GACHI-0001`, `GACHI_0001`, `GACHIG-001`, `GACHIP-001` |
| Distributor-tagged | `第一会所新片@SIS001@(Heyzo)(0435)…` → extracts real bango, ignores `@SIS001@` |
| Site-prefixed | `[Thz.la] MIDE-332`, `hhd800.com@MIDE-332` |

---

## File layout

```
jav_tool/
├── index.html              ← open this in your browser
├── style.css
├── app.js
├── chart.js
├── scan.py                 ← scan + MetaTube metadata fetch → writes data.js
├── fetch_meta.py           ← standalone fetcher for running the job in the cloud
├── fetch_meta_workflow.yml ← ready-made GitHub Actions workflow for fetch_meta.py
├── METATUBE_API.md         ← MetaTube API reference
├── classify.py             ← genre classifier → writes classify_data.js  (optional)
├── serve.py                ← LAN HTTP server for iOS access
├── serve.bat               ← double-click to start serve.py on Windows
├── data.js                 ← generated by scan.py           (gitignored)
├── meta_cache.json         ← full metadata cache, per bango (gitignored)
├── actress_cache.json      ← actress avatar cache, per name (gitignored)
├── classify_cache.json     ← genre classification cache     (gitignored)
└── classify_data.js        ← generated by classify.py       (gitignored)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Page shows "Could not load data.js" | Run `python scan.py` first |
| No covers showing | Run `python scan.py` (or `--all-meta`) — check `METATUBE_URL` is reachable |
| Covers broken for some items | Old cache entries with JavBus URLs — run `python scan.py --fix-covers`, then `python scan.py` |
| Metadata wrong / from wrong provider | Adjust `PROVIDER_PRIORITY`, delete the bango's entry from `meta_cache.json`, re-run |
| Force refresh of ALL metadata | Bump `META_VERSION` in `scan.py`, then `python scan.py --all-meta` |
| Bango not detected | Check the Non-JAV view; rename the folder to include the bango |
| Wrong bango | Rename the folder, delete its entry from `meta_cache.json`, re-run |
| After adding new files | Re-run `python scan.py` and reload the page (F5) |
| Classifier tab shows "No classification data" | Run `python classify.py --rules-only` (no Ollama needed) |
| Ollama errors in classify.py | Make sure `ollama serve` is running and `gemma4:e4b` is pulled |
| File links don't open on iOS | Make sure Everything's HTTP server is on and you opened the page via `serve.bat` |
| iOS shows wrong IP in file links | Restart `serve.bat` (it re-detects your LAN IP on startup) |
