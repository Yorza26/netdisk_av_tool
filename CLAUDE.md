# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A local-first, browser-based media collection manager for JAV (Japanese Adult Video) libraries. Python stdlib backend + vanilla JS frontend with no external Python dependencies. Designed for Windows + LAN access with support for very large collections (6000+ items, 28+ TB).

## Commands

There is no build system. All scripts run directly with Python 3.8+ (standard library only — no `pip install` needed).

```bash
# Scan collection and fetch up to 50 missing metadata records (MetaTube)
python scan.py

# Scan with options
python scan.py --all-meta        # fetch ALL missing metadata + actress avatars in one run
python scan.py --skip-meta       # scan only, no network calls
python scan.py --test-bango MIDE-332  # debug MetaTube fetch for one code

# Classify items by genre (rules-based + optional Ollama LLM fallback)
python classify.py
python classify.py --rules-only  # skip Ollama, rules only
python classify.py --all         # ignore cache, reclassify everything
python classify.py --check       # verify Ollama + show stats

# Start LAN HTTP server (serves UI + proxies Everything file links for iOS)
python serve.py                  # port 8080, Everything on port 80
python serve.py 9000             # viewer on port 9000
python serve.py 8080 8080        # viewer on 8080, Everything on 8080
```

No tests, linters, or CI are configured.

## Architecture

### Data Flow

```
Everything HTTP API → scan.py → data.js (JS global: items + actress avatars)
         MetaTube API ↗        meta_cache.json (bango → full metadata)
                               actress_cache.json (name → avatar images)

data.js → classify.py → classify_data.js
                       classify_cache.json (path → category)

data.js + classify_data.js + config.js → index.html + app.js (browser UI)
```

### Key Files

- **`scan.py`** — Core engine: queries the [Everything](https://www.voidtools.com/) search HTTP API, extracts bango codes, fetches metadata (title, cover, actresses, maker, label, series, genres, director, release date, runtime, score, summary, preview images) from a self-hosted [MetaTube](https://github.com/metatube-community/metatube-sdk-go) API server, plus actress avatars via MetaTube's Gfriends actor provider. Writes `data.js`. See `METATUBE_API.md` for the API reference.
- **`classify.py`** — Genre classifier: 200+ regex rules first, Ollama (`gemma4:e4b`) as fallback for ambiguous items, writes `classify_data.js`.
- **`serve.py`** — HTTP server for LAN/iOS access; detects local IP and writes `config.js` so remote devices can open file links via Everything's HTTP server.
- **`app.js`** — All frontend logic: tabs (Dashboard, Browse, Statistics, Actresses, Classifier, Non-JAV), field-scoped search (`maker:S1`, `label:"…"`, `genre:…`, `actress:…`, `series:`, `mseries:`, `director:`, `provider:`), multi-dimension statistics, actress card grid with avatars, rich detail panel (metadata table, genre chips, preview images), lazy loading, multi-select, mark-for-deletion, lightbox.
- **`index.html`** / **`style.css`** — Dark-theme UI shell.

### Generated Files (gitignored, do not edit by hand unless patching cache)

| File | Contents | Key |
|------|----------|-----|
| `data.js` | `window.__javData__` global — items + `actresses` avatar map | — |
| `meta_cache.json` | full MetaTube metadata per bango (`_v` = schema version) | bango code |
| `actress_cache.json` | avatar image URLs per actress ({} = cached miss) | actress name |
| `classify_data.js` | `window.__classifyData__` global — genre per path | — |
| `classify_cache.json` | genre per path | full filesystem path |
| `config.js` | Everything server URL written at serve.py startup | — |

`meta_cache.json` is keyed by **bango** (survives folder reorganization). Entries with `_v` older than `META_VERSION` in scan.py are re-fetched on the next run; legacy fields are kept as a fallback when MetaTube has no match. `classify_cache.json` is keyed by **full path** (must be refreshed if directories move).

### Configuration Constants (edit at top of each file)

**`scan.py`:**
```python
ROOT_DIRS = [r"E:\115\云下载", ...]   # directories to scan
EVERYTHING_PORT = 80                  # Everything HTTP server port
META_PER_RUN = 50                     # max new metadata fetches per run
ACTRESS_PER_RUN = 100                 # max new actress avatar lookups per run
META_DELAY = 0.3                      # seconds between requests
METATUBE_URL = "https://..."          # self-hosted MetaTube API server
METATUBE_TOKEN = ""                   # Bearer token if the server uses -token
META_VERSION = 2                      # bump to force full metadata re-fetch
PROVIDER_PRIORITY = ["FANZA", ...]    # which provider wins on multi-hit search
```

**`classify.py`:**
```python
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:e4b"
```

**`serve.py`:**
```python
SERVE_PORT = 8080
EVERYTHING_PORT = 80
```

### Bango Code Formats

`extract_bango()` in scan.py handles 15+ formats with ordered regex fallthrough:

- Standard: `MIDE-332`, `SNIS-001`
- No dash: `MIDE332`
- Dot-separated: `STARS.001`
- FC2: `FC2-PPV-1234567`
- HEYZO: `HEYZO-2345`, `heyzo_hd_2345`
- Numeric prefix: `300MAAN-456`
- Date-based: `1PONDO-101015-001`, `102720-001-carib`
- Distributor-tagged: `第一会所新片@SIS001@(Heyzo)(0435)`
- Site-prefixed: `[Thz.la] MIDE-332`

Order of patterns matters — more specific patterns must appear before general ones.

### Frontend Lazy Loading

Browse and Classifier tabs render 15 items at a time and append more on scroll. This is critical for smooth operation with 6000+ items. Any change to rendering logic must preserve this pattern.

### Flat-Pack Detection

`process_results()` in scan.py identifies folders containing multiple different bangos across their files (not subfolders). These are split into separate logical items sharing the same parent path. This is a non-obvious structural concept that affects item counts and path handling throughout the codebase.

## Runtime Requirements

- **Windows 10/11** with [Everything](https://www.voidtools.com/) installed and its HTTP server enabled
- **Python 3.8+** (stdlib only)
- **MetaTube API server** (self-hosted, see `METATUBE_URL` in scan.py) for metadata fetching
- **Ollama** (optional) with `gemma4:e4b` model for LLM classification fallback
