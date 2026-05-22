#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JAV Collection Scanner  —  stdlib only, no pip install needed
Queries Everything's HTTP API and saves a snapshot to data.js.
Fetches metadata (cover, title, actresses) from jav321.com — no cookies needed.
Then open index.html directly in your browser — no server needed.

Usage:
    python scan.py                          # full scan + metadata (100 per run)
    python scan.py --all-meta               # full scan + fetch ALL missing metadata
    python scan.py --skip-meta              # fast scan, no metadata
    python scan.py --test-bango MIDE-332    # test metadata fetch for one bango
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import re
import os
import sys
import io
import time
from datetime import datetime

# ── Windows console UTF-8 fix ──────────────────
# Without this, printing Chinese/non-ASCII paths on Windows
# raises UnicodeEncodeError (cp932/cp936 codec issues).
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from collections import defaultdict

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
ROOT_DIR        = r"E:\115\云下载"
OUTPUT_FILE     = "data.js"
META_CACHE_FILE     = "meta_cache.json"
CLASSIFY_CACHE_FILE = "classify_cache.json"   # written by classify.py
META_PER_RUN    = 100    # max new items to fetch per scan run
META_DELAY      = 1.0    # seconds between metadata requests (be polite)
EVERYTHING_PORT = 80     # Change if you changed it in Everything's options

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
FILE_ATTRIBUTE_DIRECTORY = 0x10

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.wmv', '.mov', '.m4v',
              '.ts', '.m2ts', '.iso', '.rmvb', '.flv', '.webm'}

FALSE_POSITIVE_SERIES = {
    'MP4', 'MKV', 'AVI', 'WMV', 'MOV', 'FLV', 'ISO', 'AAC', 'AC3',
    'FPS', 'BD', 'DVD', 'VR', 'USB', 'HDD', 'SSD', 'RAM', 'CPU',
    'GPU', 'HDR', 'SDR', 'UHD', 'FHD', 'HD', 'SD', 'TS', 'GB', 'MB',
    'KB', 'TB', 'EP', 'OVA', 'OAD', 'SP', 'CM', 'NC', 'OP', 'ED',
    # Video codec / tech labels that look like letter+digit series codes
    'H264', 'H265', 'X264', 'X265', 'AV1', 'VP9',
}

# ─────────────────────────────────────────────
# Site-prefix stripping
# ─────────────────────────────────────────────
# Strips leading site prefixes like:
#   [Thz.la] →  matched by [\[\(@0-9]* + Thz + .la + [\]\)@_0 \-]*
#   hhd800.com@ hhd000.com_ 0ses23.com0 0Thz.la0 @fengniao131.vip-
_SITE_PREFIX_RE = re.compile(
    r'^[\[\(@0-9]*'                               # optional leading: [ ( @ digits
    r'[a-zA-Z0-9][a-zA-Z0-9\-]*'                 # domain label (at least 1 alpha-start char)
    r'\.(?:com|net|org|la|cc|me|vip|xyz|to|site|info|io|tv)'  # dot + known TLD
    r'[\]\)@_0\s\-]*',                            # trailing separator
    re.I
)

def _strip_site_prefix(text: str) -> str:
    return _SITE_PREFIX_RE.sub('', text).strip()


# ─────────────────────────────────────────────
# Distributor-tag prefix stripping
# ─────────────────────────────────────────────
# Strips distributor watermarks like: 第一會所新片@SIS001@, olo@SIS001@
# Pattern: anything before @WORD@ at the start of the name.
# "SIS001" (and "SEXINSEX") are group tags, NOT JAV bangos.
_DISTRIB_TAG_RE = re.compile(
    r'^[^@]*'          # anything before the first @  (CJK, ASCII, spaces, etc.)
    r'@[A-Za-z0-9]{3,}@',   # @TAG@ — at least 3 alphanumeric chars
    re.I
)

def _strip_distrib_prefix(text: str) -> str:
    """Strip 第一會所新片@SIS001@ style distributor prefixes."""
    stripped = _DISTRIB_TAG_RE.sub('', text)
    return stripped.strip('@_ \t').strip()


# ─────────────────────────────────────────────
# Bango extraction
# ─────────────────────────────────────────────

_BANGO_PATTERNS = [
    # FC2-PPV-XXXXXXX
    (re.compile(r'\bFC2[-_]?PPV[-_]?(\d{4,7})\b', re.I),
     lambda m: (f"FC2-PPV-{m.group(1)}", "FC2-PPV")),

    # HEYZO-XXXX  (also handles heyzo_hd_XXXX_full — use (?!\d) not \b)
    (re.compile(r'\bHEYZO[-_](?:HD[-_])?(\d{4})(?!\d)', re.I),
     lambda m: (f"HEYZO-{m.group(1)}", "HEYZO")),

    # 1pondo trailing format: 072616_346-1pon  (date_num-1pon at the END)
    (re.compile(r'(?<!\d)(\d{6})[-_](\d{3})[-_]1pon(?:do)?\b', re.I),
     lambda m: (f"1PONDO-{m.group(1)}-{m.group(2)}", "1PONDO")),

    # Caribbean carib format: 102720-001-carib[-1080p]
    (re.compile(r'(?<!\d)(\d{6})[-_](\d{3})[-_]CARIB\b', re.I),
     lambda m: (f"CARIBBEANCOM-{m.group(1)}-{m.group(2)}", "CARIBBEANCOM")),

    # 1PONDO / CARIBBEANCOM name-first format: 1PONDO-MMDDYY-NNN
    (re.compile(r'\b(1PONDO|CARIBBEANCOM|CARIBPR)[-_](\d{6})[-_](\d{3})\b', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}-{m.group(3)}", m.group(1).upper())),

    # Parenthesized studio format used by 第一會所 distributors:
    #   (HEYZO)(0435)  →  HEYZO-0435
    (re.compile(r'\(HEYZO\)\((\d{4,5})\)', re.I),
     lambda m: (f"HEYZO-{m.group(1)}", "HEYZO")),

    #   (Caribbean)(YYMMDD_NNN) or (Caribbean)(YYMMDD-NNN)  →  CARIBBEANCOM-…
    (re.compile(r'\(CARIB(?:BEAN(?:COM)?)?\)\((\d{6})[_-](\d{3})\)', re.I),
     lambda m: (f"CARIBBEANCOM-{m.group(1)}-{m.group(2)}", "CARIBBEANCOM")),

    #   (1pondo)(YYMMDD_NNN)  →  1PONDO-…
    (re.compile(r'\(1\s*(?:PONDO|PON|P)\)\((\d{6})[_-](\d{3,4})\)', re.I),
     lambda m: (f"1PONDO-{m.group(1)}-{m.group(2)}", "1PONDO")),

    #   (1000人斬り)(YYMMDD_name) or (1000giri)(YYMMDD)  →  1000GIRI-…
    (re.compile(r'\(1000[^\)]{0,6}\)\((\d{6}[a-z_]*)\)', re.I),
     lambda m: (f"1000GIRI-{m.group(1).rstrip('_')}", "1000GIRI")),

    # Numeric-prefix series: 300MAAN-456, 200GANA-123, 230ORECO-171
    (re.compile(r'(?<![A-Z\d])(\d{1,3}[A-Z]{2,8})[-.](\d{2,5})(?!\d)', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),

    # Standard with separator: MIDE-332, STARS.001, ssni-661, aoz-274z
    # (?!\d) instead of (?![A-Z\d]) so trailing version letters (z, a, b) are allowed
    (re.compile(r'(?<![A-Z\d])([A-Z]{2,8})[-.](\d{2,5})(?!\d)', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),

    # Letter+digits series code: T28-542, S2M-003, R18-123
    (re.compile(r'(?<![A-Z\d])([A-Z]\d{2,4})[-](\d{2,5})(?!\d)', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),

    # Standard without separator: MIDE332, EKDV460
    (re.compile(r'(?<![A-Z\d])([A-Z]{3,8})(\d{2,5})(?!\d)', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),
]


def extract_bango(text: str):
    """Try to find a bango in text.

    Attempt order (mutually exclusive paths):

    A. Distributor-tag detected (e.g. 第一會所@SIS001@):
       → ONLY try the stripped form. Never fall back to the original so that the
         group tag (SIS001) cannot be mistaken for a bango.

    B. No distributor tag:
       1. Original text.
       2. Site-prefix stripped (e.g. [Thz.la] removed).
    """
    # ── Path A: distributor-tagged name ──────────────────────────────────────
    stripped_distrib = _strip_distrib_prefix(text)
    if stripped_distrib and stripped_distrib != text:
        # Has a distributor prefix → ONLY search the stripped remainder.
        # Do NOT fall back to the original; the tag itself (@SIS001@) must
        # never be treated as a bango.
        for pat, fmt in _BANGO_PATTERNS:
            m = pat.search(stripped_distrib)
            if m:
                bango, series = fmt(m)
                if series not in FALSE_POSITIVE_SERIES:
                    return bango, series
        return None, None

    # ── Path B: no distributor prefix ────────────────────────────────────────
    attempts = [text]
    stripped_site = _strip_site_prefix(text)
    if stripped_site and stripped_site != text:
        attempts.append(stripped_site)

    for attempt in attempts:
        for pat, fmt in _BANGO_PATTERNS:
            m = pat.search(attempt)
            if m:
                bango, series = fmt(m)
                if series not in FALSE_POSITIVE_SERIES:
                    return bango, series
    return None, None


# ─────────────────────────────────────────────
# Everything HTTP API  (stdlib urllib only)
# ─────────────────────────────────────────────

def _everything_get(params: dict) -> dict:
    """Single request to Everything HTTP server. Returns parsed JSON."""
    qs  = urllib.parse.urlencode(params)
    url = f"http://localhost:{EVERYTHING_PORT}/?{qs}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw.decode('utf-8'))
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        print(f"\n[FAIL] Cannot connect to Everything HTTP server: {reason}")
        print("  -> Enable it: Everything -> Tools -> Options -> HTTP Server")
        sys.exit(1)
    except json.JSONDecodeError:
        print("\n[FAIL] Everything returned non-JSON. Is the HTTP server enabled?")
        print("  -> Everything -> Tools -> Options -> HTTP Server -> Enable HTTP Server")
        sys.exit(1)


def fetch_everything(search: str) -> list:
    """Fetch ALL results under `search`, handling pagination automatically."""
    PAGE_SIZE  = 5000
    offset     = 0
    total      = None
    all_results = []

    while True:
        data = _everything_get({
            's': search, 'j': 1,
            'path_column': 1, 'size_column': 1,
            'n': PAGE_SIZE, 'o': offset,
        })

        if total is None:
            total = data.get('totalResults', 0)
            print(f"  Everything reports {total} items")

        results = data.get('results', [])
        all_results.extend(results)
        offset += len(results)

        print(f"  Fetched {offset}/{total} ...", end='\r', flush=True)

        if offset >= total or not results:
            break

    print()
    return all_results


# ─────────────────────────────────────────────
# Processing
# ─────────────────────────────────────────────

def bytes_to_human(b: int) -> str:
    if b <= 0:
        return "0 B"
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def process_results(raw: list, root_dir: str) -> dict:
    norm_root = os.path.normpath(root_dir)

    def new_entry(name: str, full_path: str) -> dict:
        bango, series = extract_bango(name)
        return {
            'name':        name,
            'path':        full_path,
            'bango':       bango,
            'series':      series,
            'is_jav':      bango is not None,
            'total_size':  0,
            'file_count':  0,
            'video_count': 0,
            'files':       [],
        }

    # d1[top]      — entry for each direct child of root (depth 1)
    # d2[top][sub] — entry for each grandchild of root (depth 2)
    d1: dict[str, dict] = {}
    d2: dict[str, dict[str, dict]] = defaultdict(dict)

    for item in raw:
        name      = item.get('name', '')
        path      = item.get('path', '')
        try:
            size = int(item.get('size') or 0)
        except (ValueError, TypeError):
            size = 0
        is_folder = item.get('type') == 'folder'
        full_path = os.path.normpath(os.path.join(path, name))

        try:
            rel = os.path.relpath(full_path, norm_root)
        except ValueError:
            continue
        parts = rel.split(os.sep)
        if not parts or parts[0] in ('', '.'):
            continue

        top = parts[0]
        if top not in d1:
            d1[top] = new_entry(top, os.path.join(norm_root, top))

        if len(parts) == 1:
            continue  # the d1 folder itself

        sub = parts[1]

        if is_folder:
            # Register depth-2 folder
            if len(parts) == 2 and sub not in d2[top]:
                d2[top][sub] = new_entry(sub, os.path.join(norm_root, top, sub))
            continue   # don't treat folders as files

        if size <= 0:
            continue

        ext      = os.path.splitext(name)[1].lower()
        is_video = ext in VIDEO_EXTS

        if len(parts) == 2:
            # File directly inside a depth-1 folder
            e = d1[top]
            e['total_size'] += size
            e['file_count'] += 1
            if is_video: e['video_count'] += 1
            e['files'].append({'name': name, 'size': size,
                               'size_human': bytes_to_human(size), 'ext': ext})
        else:
            # File inside a depth-2 subfolder (or deeper) — credit to d2 entry
            if sub not in d2[top]:
                d2[top][sub] = new_entry(sub, os.path.join(norm_root, top, sub))
            e = d2[top][sub]
            e['total_size'] += size
            e['file_count'] += 1
            if is_video: e['video_count'] += 1
            if len(parts) == 3:   # direct files only for the detail list
                e['files'].append({'name': name, 'size': size,
                                   'size_human': bytes_to_human(size), 'ext': ext})

    # ── Infer bango from direct files when folder name had none ──────────
    def infer_bango(entry: dict) -> None:
        if not entry['is_jav']:
            for f in entry['files']:
                bango, series = extract_bango(f['name'])
                if bango:
                    entry.update(bango=bango, series=series, is_jav=True)
                    break

    for e in d1.values():
        infer_bango(e)
    for subs in d2.values():
        for e in subs.values():
            infer_bango(e)

    # ── Decide which folders become items ────────────────────────────────
    # • d1 has bango                       → JAV item
    # • d1 has no bango, ≥1 JAV child     → collection: surface d2 children
    # • d1 has no bango, no JAV children  → non-JAV item
    items = []
    for top, d1e in d1.items():
        subs = d2.get(top, {})
        if d1e['is_jav'] or not subs:
            items.append(d1e)
        elif any(s['is_jav'] for s in subs.values()):
            # Collection folder — each child becomes its own item
            items.extend(subs.values())
        else:
            # Non-JAV folder whose children are also non-JAV → keep as one item
            items.append(d1e)

    # Finalise
    for e in items:
        e['files'].sort(key=lambda f: f['size'], reverse=True)
        e['total_size_human'] = bytes_to_human(e['total_size'])

    # Statistics
    series_count: dict[str, int] = defaultdict(int)
    series_size:  dict[str, int] = defaultdict(int)
    jav_count = non_jav_count = total_size = 0

    for e in items:
        total_size += e['total_size']
        if e['is_jav']:
            jav_count += 1
            if e['series']:
                series_count[e['series']] += 1
                series_size[e['series']]  += e['total_size']
        else:
            non_jav_count += 1

    items.sort(key=lambda x: x['total_size'], reverse=True)

    sorted_sc = dict(sorted(series_count.items(), key=lambda x: x[1], reverse=True))
    series_size_data = {
        k: {'count': series_count[k], 'size': series_size[k],
            'size_human': bytes_to_human(series_size[k])}
        for k in sorted_sc
    }

    return {
        'scan_time':  datetime.now().isoformat(),
        'root_dir':   root_dir,
        'statistics': {
            'total_items':       len(items),
            'jav_count':         jav_count,
            'non_jav_count':     non_jav_count,
            'total_size':        total_size,
            'total_size_human':  bytes_to_human(total_size),
            'series_count':      sorted_sc,
            'series_size':       series_size_data,
        },
        'items': items,
    }


# ─────────────────────────────────────────────
# jav321.com metadata fetching
# (No cookies needed — cover images from pics.dmm.co.jp are public)
# ─────────────────────────────────────────────

_META_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36')


def _jav321_id(bango: str) -> str:
    """Convert MIDE-332 → mide00332, DOCP-175 → docp00175 (jav321 URL format)."""
    m = re.match(r'^([A-Za-z]+)-?(\d+)', bango.strip())
    if m:
        return m.group(1).lower() + m.group(2).zfill(5)
    return bango.lower().replace('-', '')


def _fetch_one_meta(bango: str) -> dict:
    """Fetch cover, title, actresses from jav321.com for a single bango.
    No login/cookies needed. Cover images served by pics.dmm.co.jp (public).
    Returns {} on not-found, {'_err': msg} on network error."""
    vid = _jav321_id(bango)
    url = f"https://www.jav321.com/video/{vid}"
    headers = {
        'User-Agent':      _META_UA,
        'Accept-Language': 'ja,zh-TW;q=0.9,zh;q=0.8,en;q=0.7',
    }

    html = ''
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            html = r.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Direct URL 404 — try POST search as fallback
            try:
                req2 = urllib.request.Request(
                    "https://www.jav321.com/search",
                    data=urllib.parse.urlencode({'sn': bango}).encode(),
                    headers={**headers,
                             'Content-Type': 'application/x-www-form-urlencoded',
                             'Referer':      'https://www.jav321.com/'},
                )
                with urllib.request.urlopen(req2, timeout=15) as r2:
                    if '/video/' not in r2.url:
                        return {}   # search didn't land on a video page
                    html = r2.read().decode('utf-8', errors='replace')
            except Exception as e2:
                return {'_err': str(e2)}
        else:
            return {'_err': str(e)}
    except Exception as exc:
        return {'_err': str(exc)}

    if not html:
        return {}

    # ── Title: <h3>TITLE<small>bango actress</small></h3> ──
    m = re.search(r'<h3>([^<]+)<small>', html)
    title = m.group(1).strip() if m else ''

    # ── Cover: first DMM ps.jpg → upgrade to pl.jpg (large ~160-180 KB) ──
    cover = ''
    m = re.search(r'src="(https?://pics\.dmm\.co\.jp/[^"]+ps\.jpg)"', html)
    if m:
        cover = m.group(1).replace('ps.jpg', 'pl.jpg')
        cover = re.sub(r'(?<=\.jp)//', '/', cover)   # fix double slash

    # ── Actresses: /star/NNN/N links (deduplicated) ──
    actresses = list(dict.fromkeys(
        re.findall(r'href="/star/\d+/\d+">([^<]+)</a>', html)
    ))

    return {'cover': cover, 'title': title, 'actresses': actresses}


def load_meta_cache() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, META_CACHE_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_uncensored_paths() -> set:
    """Return the set of folder paths classified as 'uncensored' by classify.py.
    Returns an empty set if classify_cache.json doesn't exist yet."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, CLASSIFY_CACHE_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        return {p for p, cat in cache.items() if cat == 'uncensored'}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_meta_cache(cache: dict) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, META_CACHE_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def enrich_with_meta(items: list, cache: dict, all_meta: bool = False) -> int:
    """Fetch missing metadata from jav321.com (up to META_PER_RUN items, or all if all_meta=True).
    Skips items classified as 'uncensored' — jav321.com only covers censored JAV.
    Saves cache after every successful fetch. Handles Ctrl+C gracefully.
    Returns number of newly fetched items."""
    uncensored = _load_uncensored_paths()
    if uncensored:
        print(f"  Skipping {len(uncensored)} uncensored items (jav321 only covers censored JAV).")

    need_fetch = [e for e in items if e.get('is_jav') and e.get('bango')
                  and e['bango'] not in cache
                  and e.get('path') not in uncensored]
    ok = fail = 0
    total_needed = len(need_fetch)
    limit        = total_needed if all_meta else min(total_needed, META_PER_RUN)

    if total_needed:
        remaining = total_needed - limit
        print(f"  Fetching metadata from jav321.com: {limit} new items"
              + (f" ({remaining} more on next run)" if remaining else "") + " ...")
        print("  Press Ctrl+C to stop early (progress is saved).")
    else:
        cached = sum(1 for e in items if e.get('is_jav') and e.get('bango') and e['bango'] in cache)
        print(f"  All metadata cached ({cached} items)")

    try:
        for entry in need_fetch:
            if ok + fail >= limit:
                break
            bango = entry['bango']
            meta  = _fetch_one_meta(bango)
            n     = ok + fail + 1

            if meta.get('cover') or meta.get('title'):
                cache[bango] = meta
                save_meta_cache(cache)   # persist after every success
                ok += 1
                status = '✓'
                if meta.get('cover'):
                    status += ' cover'
                if meta.get('actresses'):
                    status += f' [{", ".join(meta["actresses"][:2])}]'
            elif meta.get('_err'):
                fail += 1
                status = f"✗ ({meta['_err'][:60]})"
            else:
                # Not found on jav321 — cache the miss so we don't retry forever
                cache[bango] = {}
                save_meta_cache(cache)
                fail += 1
                status = '– (not on jav321)'

            print(f"  [{n}/{limit}] {status}  {bango}")
            if n < limit:
                time.sleep(META_DELAY)

    except KeyboardInterrupt:
        print(f"\n  Interrupted — {ok} fetched, cache saved.")

    if ok + fail:
        print(f"  Done: {ok} with metadata, {fail} not found/errors.")

    # Apply cache to all items
    for entry in items:
        bango = entry.get('bango')
        if bango and bango in cache:
            meta = cache[bango]
            entry['cover']     = meta.get('cover', '')
            entry['title']     = meta.get('title', '')
            entry['actresses'] = meta.get('actresses', [])

    return ok


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def _write_data_js(data: dict) -> None:
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('// Auto-generated by scan.py — do not edit manually\n')
        f.write(f'// Scanned: {data["scan_time"]}\n')
        f.write('window.__javData__ = ')
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write(';\n')


def main(skip_meta: bool = False, all_meta: bool = False):
    print("=" * 55)
    print("  JAV Collection Scanner  (stdlib only)")
    print("=" * 55)
    print(f"  Root   : {ROOT_DIR}")
    print(f"  Port   : {EVERYTHING_PORT}")
    print(f"  Output : {OUTPUT_FILE}")
    if skip_meta:
        print("  Meta   : SKIPPED (--skip-meta)")
    elif all_meta:
        print("  Meta   : ALL (no per-run limit)")
    print("=" * 55)
    print()

    search = f'path:"{ROOT_DIR}"'
    print(f"Query: {search}")
    raw = fetch_everything(search)

    if not raw:
        print("No results. Check the path and that Everything has indexed it.")
        sys.exit(1)

    print(f"Processing {len(raw)} items ...")
    data = process_results(raw, ROOT_DIR)

    # ── Write data.js immediately so the browser is usable right away ──
    _write_data_js(data)
    s = data['statistics']
    print(f"\n[OK] Scan complete — {OUTPUT_FILE} written (open index.html now)")
    print(f"   Directories : {s['total_items']}")
    print(f"   JAV         : {s['jav_count']}")
    print(f"   Non-JAV     : {s['non_jav_count']}")
    print(f"   Total size  : {s['total_size_human']}")
    print()
    print("   Top series by count:")
    for series, count in list(s['series_count'].items())[:15]:
        print(f"   {'|' * min(count, 35):<35}  {series}  ({count})")
    print()

    if skip_meta:
        print("   Metadata fetch skipped (--skip-meta). Run without flag to fetch covers.")
        print()
        return

    # ── Enrich with jav321 metadata (cover / title / actresses) ──────
    print("Enriching with metadata ...")
    meta_cache = load_meta_cache()
    fetched = enrich_with_meta(data['items'], meta_cache, all_meta=all_meta)

    # ── Re-write data.js with metadata embedded ───────────────────────
    if fetched:
        _write_data_js(data)
        print(f"  {OUTPUT_FILE} updated with {fetched} new covers — reload index.html.")
    else:
        # Still apply existing cache entries (no new fetches needed)
        covered = sum(1 for e in data['items'] if e.get('cover'))
        if covered:
            _write_data_js(data)
            print(f"  {OUTPUT_FILE} updated with {covered} cached covers — reload index.html.")
    print()



def test_meta_bango(bango: str) -> None:
    """Fetch and print metadata for a single bango from jav321.com."""
    print(f"Fetching metadata for: {bango}  (jav321 ID: {_jav321_id(bango)})")
    meta = _fetch_one_meta(bango)
    if meta.get('_err'):
        print(f"  Error   : {meta['_err']}")
    elif not meta:
        print("  Not found on jav321.com")
    else:
        print(f"  Title    : {meta.get('title', '(none)')}")
        print(f"  Cover    : {meta.get('cover', '(none)')}")
        print(f"  Actresses: {meta.get('actresses', [])}")


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) == 2 and args[0] in ('--test-meta', '--test-bango'):
        test_meta_bango(args[1])
    elif args == ['--skip-meta']:
        main(skip_meta=True)
    elif args == ['--all-meta']:
        main(all_meta=True)
    elif not args:
        main()
    else:
        print("Usage:")
        print("  python scan.py                          # full scan + metadata (100 per run)")
        print("  python scan.py --all-meta               # full scan + fetch ALL missing metadata")
        print("  python scan.py --skip-meta              # fast scan, no metadata")
        print("  python scan.py --test-bango <BANGO>     # test jav321 fetch for one bango")
