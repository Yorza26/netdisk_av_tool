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
import gzip as _gzip
import zlib
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
# line_buffering=True ensures every print() flushes immediately — without it
# the new TextIOWrapper defaults to full-block buffering and output only appears
# after the buffer fills or the process exits (looks like nothing until Ctrl+C).
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace', line_buffering=True)
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8',
                                  errors='replace', line_buffering=True)
from collections import defaultdict

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
ROOT_DIRS = [
    r"E:\115\云下载",
    r"E:\115\!NSFW\CenPack\H265",
    r"E:\115\!NSFW\4k",
    # r"E:\115\!NSFW\Anthology\Gachinco",
]
OUTPUT_FILE     = "data.js"
META_CACHE_FILE     = "meta_cache.json"
CLASSIFY_CACHE_FILE = "classify_cache.json"   # written by classify.py
META_PER_RUN    = 50    # max new items to fetch per scan run
META_DELAY      = 0.3    # seconds between metadata requests (be polite)
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

    # Gachinco (g-area): GACHI-0001, GACHI_0001, GACHIG_001, GACHIP_001
    # Files often use underscore separator which the general [-.]  pattern misses.
    (re.compile(r'(?<![A-Z\d])(GACHI[GP]?)[-_](\d{3,5})(?!\d)', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),

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


def process_results(raw: list, root_dirs: list) -> dict:
    norm_roots = [os.path.normpath(r) for r in root_dirs]

    def find_root(full_path: str):
        """Return the norm_root that is a parent of full_path, or None."""
        for nr in norm_roots:
            try:
                rel = os.path.relpath(full_path, nr)
            except ValueError:
                continue
            if not rel.startswith('..'):
                return nr, rel
        return None, None

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
    # Key is (norm_root, top) to avoid collisions across different roots
    d1: dict[tuple, dict] = {}
    d2: dict[tuple, dict[str, dict]] = defaultdict(dict)

    for item in raw:
        name      = item.get('name', '')
        path      = item.get('path', '')
        try:
            size = int(item.get('size') or 0)
        except (ValueError, TypeError):
            size = 0
        is_folder = item.get('type') == 'folder'
        full_path = os.path.normpath(os.path.join(path, name))

        norm_root, rel = find_root(full_path)
        if norm_root is None:
            continue
        parts = rel.split(os.sep)
        if not parts or parts[0] in ('', '.'):
            continue

        top = parts[0]
        key1 = (norm_root, top)
        if key1 not in d1:
            d1[key1] = new_entry(top, os.path.join(norm_root, top))

        if len(parts) == 1:
            # Depth-1 folder → just the container itself, nothing to credit.
            # Depth-1 FILE → the root points directly to a flat file directory
            # (e.g. ROOT_DIRS = ["…/H265"]).  Credit size/count to this entry
            # so it appears as a real item with correct stats.
            if not is_folder and size > 0:
                ext_1 = os.path.splitext(name)[1].lower()
                e1 = d1[key1]
                e1['total_size'] += size
                e1['file_count'] += 1
                if ext_1 in VIDEO_EXTS:
                    e1['video_count'] += 1
                e1['files'].append({'name': name, 'size': size,
                                    'size_human': bytes_to_human(size),
                                    'ext': ext_1})
            continue

        sub = parts[1]

        if is_folder:
            # Register depth-2 folder
            if len(parts) == 2 and sub not in d2[key1]:
                d2[key1][sub] = new_entry(sub, os.path.join(norm_root, top, sub))
            continue   # don't treat folders as files

        if size <= 0:
            continue

        ext      = os.path.splitext(name)[1].lower()
        is_video = ext in VIDEO_EXTS

        if len(parts) == 2:
            # File directly inside a depth-1 folder
            e = d1[key1]
            e['total_size'] += size
            e['file_count'] += 1
            if is_video: e['video_count'] += 1
            e['files'].append({'name': name, 'size': size,
                               'size_human': bytes_to_human(size), 'ext': ext})
        else:
            # File inside a depth-2 subfolder (or deeper) — credit to d2 entry
            if sub not in d2[key1]:
                d2[key1][sub] = new_entry(sub, os.path.join(norm_root, top, sub))
            e = d2[key1][sub]
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

    # ── Flat-pack detection ───────────────────────────────────────────────
    # A "flat pack" is a folder whose OWN name has no bango, but whose
    # direct files each carry distinct bangos (e.g. H265/ holding
    # ABF-090.H265.mp4, PRED-123.H265.mp4, …).  Such a folder should
    # surface each file as its own item rather than being merged into one.
    def expand_flat_pack(entry: dict) -> list:
        """Return a list of per-file items if entry is a flat pack,
        otherwise return [entry] unchanged."""
        if not entry.get('is_jav'):
            return [entry]
        # If the folder name itself yielded a bango, it's a normal item
        if extract_bango(entry['name'])[0]:
            return [entry]
        # Gather (bango, series, file) for every direct file that has a bango
        tagged = []
        for f in entry.get('files', []):
            fb, fs = extract_bango(f['name'])
            if fb:
                tagged.append((fb, fs, f))
        # Need ≥2 distinct bangos to confirm it's a flat pack
        if len({b for b, _, _ in tagged}) < 2:
            return [entry]
        # Split: one virtual item per file
        result = []
        for fb, fs, f in tagged:
            ext = f.get('ext', '')
            result.append({
                'name':             fb,   # use bango as display name
                'path':             os.path.join(entry['path'], f['name']),
                'bango':            fb,
                'series':           fs,
                'is_jav':           True,
                'total_size':       f['size'],
                'total_size_human': f.get('size_human', bytes_to_human(f['size'])),
                'file_count':       1,
                'video_count':      1 if ext in VIDEO_EXTS else 0,
                'files':            [f],
            })
        return result

    # ── Decide which folders become items ────────────────────────────────
    # • d1 has bango                       → JAV item
    # • d1 has no bango, ≥1 JAV child     → collection: surface d2 children
    #   (d2 children that are flat packs are further split into file items)
    # • d1 has no bango, no JAV children  → non-JAV item
    items = []
    for top, d1e in d1.items():
        subs = d2.get(top, {})
        if d1e['is_jav'] or not subs:
            items.append(d1e)
        elif any(s['is_jav'] for s in subs.values()):
            # Collection folder — surface each child (splitting flat packs)
            for sub_entry in subs.values():
                items.extend(expand_flat_pack(sub_entry))
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
        'root_dirs':  root_dirs,
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


def _http_fetch(url: str, headers: dict, *,
                data: bytes = None, timeout: int = 12):
    """GET (or POST when data is given) with automatic gzip/deflate decompression.

    Always sends Accept-Encoding: gzip, deflate so servers return compressed
    bodies — typically 5-10x smaller than plain text, which cuts VPN data usage
    significantly. urllib.request does NOT add this header by default.

    Returns (html_str, final_url).  final_url is needed for POST redirects
    (jav321 search → video page) to check whether we landed on a real result.
    """
    h = {**headers, 'Accept-Encoding': 'gzip, deflate'}
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body      = r.read()
        final_url = r.url
        enc       = r.headers.get('Content-Encoding', '').lower()
        if 'gzip' in enc:
            body = _gzip.decompress(body)
        elif 'deflate' in enc:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                body = zlib.decompress(body, -zlib.MAX_WBITS)  # raw deflate
    return body.decode('utf-8', errors='replace'), final_url


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
        html, _ = _http_fetch(url, headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Direct URL 404 — try POST search as fallback
            try:
                post_data = urllib.parse.urlencode({'sn': bango}).encode()
                post_headers = {**headers,
                                'Content-Type': 'application/x-www-form-urlencoded',
                                'Referer':      'https://www.jav321.com/'}
                html, final_url = _http_fetch(
                    "https://www.jav321.com/search", post_headers,
                    data=post_data, timeout=15)
                if '/video/' not in final_url:
                    return {}   # search didn't land on a video page
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


# ─────────────────────────────────────────────
# Uncensored metadata: javhoo.com (primary) + avsox.click (fallback)
# ─────────────────────────────────────────────

def _javhoo_url(bango: str) -> str:
    """Build a direct javhoo.com page URL for any bango.

    javhoo uses the bango directly in the URL path for most studios:
      HEYZO-3837         →  /ja/HEYZO-3837
      NHDTB-001          →  /ja/NHDTB-001
      MIDE-332           →  /ja/MIDE-332
      FC2-PPV-1234567    →  /ja/FC2-PPV-1234567

    For date-based codes (1PONDO / CARIBBEANCOM / CARIBPR), the studio prefix
    is stripped and the remaining MMDDYY-NNN part is used directly:
      1PONDO-052124-001      →  /ja/052124-001
      CARIBBEANCOM-102720-001 →  /ja/102720-001
      CARIBPR-041426-001     →  /ja/041426-001
    """
    # 1PONDO-MMDDYY-NNN  →  /ja/MMDDYY-NNN
    m = re.match(r'^1PONDO-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"https://www.javhoo.com/ja/{m.group(1)}-{m.group(2)}"
    # CARIBBEANCOM-MMDDYY-NNN  →  /ja/MMDDYY-NNN
    m = re.match(r'^CARIBBEANCOM-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"https://www.javhoo.com/ja/{m.group(1)}-{m.group(2)}"
    # CARIBPR-MMDDYY-NNN  →  /ja/MMDDYY-NNN
    m = re.match(r'^CARIBPR-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"https://www.javhoo.com/ja/{m.group(1)}-{m.group(2)}"
    # All others: use bango as-is
    return f"https://www.javhoo.com/ja/{bango}"


def _fetch_one_meta_javhoo(bango: str) -> dict:
    """Fetch cover / title / actresses from javhoo.com for any bango.
    Returns {} on not-found (HTTP 404), {'_err': msg} on network error."""
    url = _javhoo_url(bango)

    headers = {
        'User-Agent':      _META_UA,
        'Accept-Language': 'ja,zh-TW;q=0.9,zh;q=0.8,en;q=0.7',
        'Referer':         'https://www.javhoo.com/',
    }
    try:
        html, _ = _http_fetch(url, headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        return {'_err': f"javhoo HTTP {e.code}"}
    except Exception as exc:
        return {'_err': f"javhoo {exc}"}

    # Cover image comes in two formats depending on studio:
    #   date codes (1PONDO/CARIB): src="https://pics.javhoo.net/YYYY/MM/{code}_b.jpg"
    #   series codes (NHDTB/MIDE/…): src="https://pics.javhoo.net/YYYY/MM/cover/{bango}.jpg"
    cover = ''
    m = re.search(r'src="(https://pics\.javhoo\.net/[^"]+_b\.jpg)"', html)
    if m:
        cover = m.group(1)
    if not cover:
        m = re.search(r'src="(https://pics\.javhoo\.net/[^"]+/cover/[^"]+\.jpg)"', html)
        if m:
            cover = m.group(1)

    # Title: <h1> contains "BANGO title actress" — strip leading bango/date-code
    # For 1PONDO/CARIB the bango in the H1 is the stripped form (MMDDYY-NNN),
    # so we need to strip both the internal bango and the URL-form prefix.
    title = ''
    m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if m:
        raw = m.group(1).strip()
        # Build the code that javhoo uses in the H1 (may differ from our bango key)
        url_code = _javhoo_url(bango).rsplit('/', 1)[-1]   # last path segment
        for prefix in (bango, url_code):
            raw = re.sub(r'^' + re.escape(prefix) + r'[\s　]+', '', raw, flags=re.IGNORECASE)
        title = raw.strip().strip('「」').strip()

    # Actresses — javhoo uses SINGLE-QUOTED href attributes (href='...') inside
    # pods_widget_field divs.  All double-quote-only regexes fail silently.
    # Strategy: match either quote style; search the specific <h3>演員</h3> field
    # rather than the bare text "演員" (which also appears in search form placeholders).
    actresses = []

    # Pattern A: /ja/star/ links — either quote style
    for _pat in (
        r"""href=['"]https://www\.javhoo\.com/ja/star/[^'"]+?['"][^>]*>([^<]+)</a>""",
        r"""href=['"]/ja/star/[^'"]+?['"][^>]*>([^<]+)</a>""",
    ):
        _found = list(dict.fromkeys(n.strip() for n in re.findall(_pat, html) if n.strip()))
        if _found:
            actresses = _found
            break

    # Pattern B: /tag/ links (WordPress-style slugs) — either quote style
    if not actresses:
        for _pat in (
            r"""href=['"]https://www\.javhoo\.com/tag/[^'"]+?['"][^>]*>([^<]+)</a>""",
            r"""href=['"]/tag/[^'"]+?['"][^>]*>([^<]+)</a>""",
        ):
            _found = list(dict.fromkeys(n.strip() for n in re.findall(_pat, html) if n.strip()))
            if _found:
                actresses = _found
                break

    # Pattern C: find <h3>演員</h3> label (the real movie-info field, not the
    # search-form placeholder) and extract <a> link texts that follow it.
    if not actresses:
        _m = re.search(r'<h3>演[員员][^<]*</h3>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
        if _m:
            _candidates = re.findall(r'<a\b[^>]*>([^<]+)</a>', _m.group(1))
            actresses = list(dict.fromkeys(
                c.strip() for c in _candidates if c.strip() and len(c.strip()) <= 30
            ))

    return {'cover': cover, 'title': title, 'actresses': actresses}


def _avsox_search_term(bango: str) -> str:
    """Convert an internal bango to the raw code avsox.click uses in searches."""
    # 1PONDO-MMDDYY-NNN  →  MMDDYY_NNN
    m = re.match(r'^1PONDO-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    # CARIBBEANCOM-MMDDYY-NNN  →  MMDDYY-NNN
    m = re.match(r'^CARIBBEANCOM-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # CARIBPR-MMDDYY-NNN  →  MMDDYY-NNN
    m = re.match(r'^CARIBPR-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # All others (HEYZO-3851, FC2-PPV-..., etc.) — use as-is
    return bango


def _fetch_one_meta_avsox(bango: str) -> dict:
    """Search avsox.click for a bango and scrape the movie page.
    Two HTTP requests: search page → movie page.
    Returns {} on not-found, {'_err': msg} on network error."""
    term = _avsox_search_term(bango)
    search_url = f"https://avsox.click/ja/search/{urllib.parse.quote(term, safe='')}"
    headers = {
        'User-Agent':      _META_UA,
        'Accept-Language': 'ja,zh-TW;q=0.9,zh;q=0.8,en;q=0.7',
        'Referer':         'https://avsox.click/',
    }

    # ── Step 1: search ────────────────────────────────────────────────
    try:
        html, _ = _http_fetch(search_url, headers)
    except Exception as exc:
        return {'_err': f"avsox search {exc}"}

    # Find movie links; prefer the one whose card text contains the search term
    pattern = re.compile(
        r'href="((?:https?:)?//avsox\.click/ja/movie/[a-f0-9]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    movie_url = None
    for href, card_text in pattern.findall(html):
        if term.lower() in card_text.lower() or bango.lower() in card_text.lower():
            movie_url = href
            break
    if not movie_url:
        # Fallback: any movie link (first one)
        m = re.search(r'href="((?:https?:)?//avsox\.click/ja/movie/[a-f0-9]+)"', html)
        if not m:
            return {}   # no results
        movie_url = m.group(1)

    if movie_url.startswith('//'):
        movie_url = 'https:' + movie_url

    # ── Step 2: movie page ────────────────────────────────────────────
    try:
        html2, _ = _http_fetch(movie_url, headers)
    except Exception as exc:
        return {'_err': f"avsox movie {exc}"}

    # Cover: player_thumbnail.jpg (best available on avsox)
    cover = ''
    m = re.search(r'src="(https?://[^"]+player_thumbnail\.jpg)"', html2)
    if m:
        cover = m.group(1)
    if not cover:  # 1pondo uses str.jpg (sample strip)
        m = re.search(r'src="(https?://[^"]+str\.jpg)"', html2)
        if m:
            cover = m.group(1)

    # Title: <h3>BANGO actress title - 無修正アダルト動画 STUDIO</h3>
    title = ''
    m = re.search(r'<h3>([^<]+)</h3>', html2)
    if m:
        raw = m.group(1).strip()
        # Strip bango / raw code prefix
        for prefix in (bango, term):
            raw = re.sub(r'^' + re.escape(prefix) + r'\s*', '', raw, flags=re.IGNORECASE)
        # Strip site suffix " - 無修正アダルト動画 ..."
        raw = re.sub(r'\s*-\s*無修正アダルト動画.*$', '', raw)
        # Strip actress name in 【...】 reading brackets (they repeat in actresses list)
        raw = re.sub(r'【[^】]*】', '', raw)
        title = raw.strip()

    # Actresses: //avsox.click/ja/star/{hash}
    actresses = list(dict.fromkeys(
        re.findall(
            r'href="(?:https?:)?//avsox\.click/ja/star/[a-f0-9]+"[^>]*>([^<]+)</a>',
            html2,
        )
    ))

    return {'cover': cover, 'title': title, 'actresses': actresses}


def _fetch_one_meta_uncensored(bango: str) -> dict:
    """Uncensored path: javhoo.com first, avsox.click as fallback."""
    meta = _fetch_one_meta_javhoo(bango)
    if meta and not meta.get('_err') and (meta.get('cover') or meta.get('title')):
        meta['_src'] = 'javhoo'
        return meta
    avsox = _fetch_one_meta_avsox(bango)
    if avsox and not avsox.get('_err'):
        avsox['_src'] = 'avsox'
    return avsox


def _fetch_one_meta_censored(bango: str) -> dict:
    """Censored path: javhoo.com first, jav321.com as fallback.

    javhoo tends to have more complete actress data than jav321, so it is tried
    first.  jav321 is used as a fallback when javhoo returns nothing (404 / not
    in database yet)."""
    javhoo = _fetch_one_meta_javhoo(bango)
    if javhoo.get('cover') or javhoo.get('title'):
        javhoo['_src'] = 'javhoo'
        return javhoo
    # javhoo returned empty or errored → try jav321
    meta = _fetch_one_meta(bango)
    if meta.get('cover') or meta.get('title'):
        meta['_src'] = 'jav321'
        return meta
    if meta.get('_err'):
        meta['_src'] = 'jav321'
        return meta
    # Neither found it; return javhoo's response (may be {} or have _err)
    javhoo.setdefault('_src', 'javhoo')
    return javhoo


def load_meta_cache() -> dict:
    """Load meta_cache.json.

    The cache is keyed by bango string (e.g. "MIDE-332"), NOT by file path.
    This means cached metadata is preserved even when you change ROOT_DIRS:
    removing a directory only hides its items from the current scan; adding
    it back restores them from cache with no re-fetch needed.

    Also recovers from an interrupted previous save: if a leftover .tmp file
    exists and is valid JSON, it means os.replace() was interrupted — we
    complete the rename so no data is lost.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, META_CACHE_FILE)
    tmp  = path + '.tmp'

    # Recover from an interrupted atomic save (tmp written, rename not done yet)
    if os.path.exists(tmp):
        try:
            with open(tmp, 'r', encoding='utf-8') as f:
                recovered = json.load(f)
            os.replace(tmp, path)   # complete the interrupted rename
            return recovered
        except (json.JSONDecodeError, OSError):
            try:
                os.remove(tmp)      # corrupt temp — discard it
            except OSError:
                pass

    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_meta_cache(cache: dict) -> None:
    """Write meta_cache.json atomically (write to .tmp, then rename).

    A direct open('w') truncates the file before writing, so a crash or
    disk-full mid-write would destroy the entire cache.  Writing to a temp
    file and renaming means the original is only replaced once the new data
    is fully on disk — a crash at any point leaves the cache intact.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, META_CACHE_FILE)
    tmp  = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # atomic on POSIX; best-effort on Windows


# Studios whose content is always uncensored — route to javhoo→avsox path.
# classify.py marks all is_jav items as 'jav' (not 'uncensored'), so we
# identify these by series prefix rather than by classify_cache.json.
_UNCENSORED_JAV_SERIES = frozenset({
    '1PONDO', 'CARIBBEANCOM', 'CARIBPR', 'HEYZO', '1000GIRI',
    'GACHI', 'GACHIP', 'GACHIG',   # Gachinco (g-area)
})


def _load_uncensored_paths() -> set:
    """Return the set of folder paths classified as 'uncensored' by classify.py.
    This covers non-JAV folders (tokyo-hot, h0930, …) detected by classify rules.
    JAV-coded items from known uncensored studios are handled separately via
    _UNCENSORED_JAV_SERIES so classify.py's 'jav' auto-label doesn't hide them.
    Returns an empty set if classify_cache.json doesn't exist yet."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, CLASSIFY_CACHE_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        return {p for p, cat in cache.items() if cat == 'uncensored'}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def fill_missing_actresses(items: list, cache: dict) -> int:
    """Re-fetch via javhoo for cached JAV items that have a title but no actresses.

    Run with:  python scan.py --fill-actresses

    Some items were fetched from jav321 which had no actress data. This command
    tries javhoo for those specific items and patches the cache in place.
    Returns the number of entries updated."""
    need = [
        e for e in items
        if e.get('is_jav') and e.get('bango')
        and e['bango'] in cache
        and cache[e['bango']].get('title')
        and not cache[e['bango']].get('actresses')
    ]

    if not need:
        print("  Nothing to do — all cached JAV items already have actress data.")
        return 0

    print(f"  Found {len(need)} items with title but no actresses — re-fetching via javhoo ...")
    print("  Press Ctrl+C to stop early (progress is saved).")
    updated = 0
    try:
        for n, entry in enumerate(need, 1):
            bango = entry['bango']
            meta  = _fetch_one_meta_javhoo(bango)
            if meta.get('actresses'):
                cache[bango]['actresses'] = meta['actresses']
                # Also patch cover if we had none before
                if meta.get('cover') and not cache[bango].get('cover'):
                    cache[bango]['cover'] = meta['cover']
                save_meta_cache(cache)
                updated += 1
                names  = meta['actresses']
                shown  = ', '.join(names[:2]) + ('…' if len(names) > 2 else '')
                status = f"✓  [{shown}]"
            elif meta.get('_err'):
                status = f"✗  ({meta['_err'][:60]})"
            elif meta.get('title') or meta.get('cover'):
                # Page found but no actress credits listed on javhoo
                status = '–  (page found, no actress credit on javhoo)'
            elif meta == {}:
                # Genuine 404 — not in javhoo's database
                status = '–  (not on javhoo)'
            else:
                status = '–  (no data)'
            print(f"  [{n}/{len(need)}] {status}  {bango}", flush=True)
            if n < len(need):
                time.sleep(META_DELAY)
    except KeyboardInterrupt:
        print(f"\n  Interrupted — {updated} updated, cache saved.")

    print(f"  Done: {updated}/{len(need)} entries updated.")
    return updated


def enrich_with_meta(items: list, cache: dict, all_meta: bool = False) -> int:
    """Fetch missing metadata (up to META_PER_RUN items per run, or all if all_meta=True).
    • Censored JAV  →  javhoo.com → jav321.com (fallback)
    • Uncensored JAV →  javhoo.com → avsox.click (fallback)
    Saves cache after every successful fetch. Handles Ctrl+C gracefully.
    Returns number of newly fetched items."""
    uncensored = _load_uncensored_paths()

    def _needs_fetch(e):
        return e.get('is_jav') and e.get('bango') and e['bango'] not in cache

    need_fetch = [e for e in items if _needs_fetch(e)]
    ok = fail = 0
    total_needed = len(need_fetch)
    limit        = total_needed if all_meta else min(total_needed, META_PER_RUN)

    def _is_uncensored(e: dict) -> bool:
        """True when this item should use the uncensored fetch path (javhoo→avsox).
        Covers: classify_cache 'uncensored' folder + known uncensored JAV series."""
        if e.get('path') in uncensored:
            return True
        return e.get('series') in _UNCENSORED_JAV_SERIES

    n_uncensored = sum(1 for e in need_fetch if _is_uncensored(e))
    n_censored   = total_needed - n_uncensored

    if total_needed:
        remaining = total_needed - limit
        parts = []
        if n_censored:   parts.append(f"{n_censored} censored (javhoo→jav321)")
        if n_uncensored: parts.append(f"{n_uncensored} uncensored (javhoo→avsox)")
        print(f"  Fetching metadata: {', '.join(parts)}"
              + (f"  [{remaining} more on next run]" if remaining else "") + " ...")
        print("  Press Ctrl+C to stop early (progress is saved).")
    else:
        cached = sum(1 for e in items if e.get('is_jav') and e.get('bango') and e['bango'] in cache)
        print(f"  All metadata cached ({cached} items)")

    try:
        for entry in need_fetch:
            if ok + fail >= limit:
                break
            bango = entry['bango']
            is_unc = _is_uncensored(entry)
            meta   = _fetch_one_meta_uncensored(bango) if is_unc else _fetch_one_meta_censored(bango)
            n      = ok + fail + 1
            src    = meta.pop('_src', 'javhoo' if is_unc else 'jav321')

            if meta.get('cover') or meta.get('title'):
                cache[bango] = meta
                save_meta_cache(cache)   # persist after every success
                ok += 1
                status = f'✓ [{src}]'
                if meta.get('cover'):
                    status += ' cover'
                if meta.get('actresses'):
                    status += f' [{", ".join(meta["actresses"][:2])}]'
            elif meta.get('_err'):
                fail += 1
                status = f"✗ ({meta['_err'][:60]})"
            else:
                # Not found — cache the miss so we don't retry forever
                cache[bango] = {}
                save_meta_cache(cache)
                fail += 1
                status = f'– (not on {src})'

            print(f"  [{n}/{limit}] {status}  {bango}", flush=True)
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
    for d in ROOT_DIRS:
        print(f"  Root   : {d}")
    print(f"  Port   : {EVERYTHING_PORT}")
    print(f"  Output : {OUTPUT_FILE}")
    if skip_meta:
        print("  Meta   : SKIPPED (--skip-meta)")
    elif all_meta:
        print("  Meta   : ALL (no per-run limit)")
    print("=" * 55)
    print()

    search = ' | '.join(f'path:"{d}"' for d in ROOT_DIRS)
    print(f"Query: {search}")
    raw = fetch_everything(search)

    if not raw:
        print("No results. Check the paths and that Everything has indexed them.")
        sys.exit(1)

    print(f"Processing {len(raw)} items ...")
    data = process_results(raw, ROOT_DIRS)

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

    # ── Always re-write data.js so cached metadata is never lost ─────
    # enrich_with_meta applies the full cache to every item; even if no
    # new items were fetched this run, previously-cached covers / titles /
    # actresses must appear in the output file.
    _write_data_js(data)
    covered = sum(1 for e in data['items'] if e.get('cover'))
    if fetched:
        print(f"  {OUTPUT_FILE} updated — {fetched} new + {covered} total covers.")
    else:
        print(f"  {OUTPUT_FILE} updated — {covered} cached covers applied.")
    print()



def test_meta_bango(bango: str) -> None:
    """Fetch and print metadata for a single bango.

    Routing mirrors enrich_with_meta:
      • Known uncensored studios (1PONDO, CARIB, HEYZO, FC2-PPV, …)
        → javhoo.com first, avsox.click fallback
      • All other bangos (MIDE, STARS, NHDTB, …)
        → jav321.com first, javhoo.com fallback
    """
    series = extract_bango(bango)[1] or ''
    is_unc_studio = series in _UNCENSORED_JAV_SERIES

    if is_unc_studio:
        print(f"Fetching metadata for: {bango}  (uncensored path: javhoo → avsox)")
        print(f"  javhoo URL : {_javhoo_url(bango)}")
        print(f"  avsox term : {_avsox_search_term(bango)}")
        meta = _fetch_one_meta_uncensored(bango)
        src  = meta.pop('_src', 'javhoo')
    else:
        print(f"Fetching metadata for: {bango}  (censored path: javhoo → jav321)")
        print(f"  javhoo URL : {_javhoo_url(bango)}")
        print(f"  jav321 ID  : {_jav321_id(bango)}")
        meta = _fetch_one_meta_censored(bango)
        src  = meta.pop('_src', 'javhoo')

    if meta.get('_err'):
        print(f"  Error    : {meta['_err']}")
    elif not (meta.get('cover') or meta.get('title')):
        print(f"  Not found (tried {src})")
    else:
        print(f"  Source   : {src}")
        print(f"  Title    : {meta.get('title', '(none)')}")
        print(f"  Cover    : {meta.get('cover', '(none)')}")
        print(f"  Actresses: {meta.get('actresses', [])}")


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == '--debug-javhoo':
        # Fetch raw HTML from javhoo and show the star/cover snippets so you
        # can verify the regexes match.  Useful when actress data is missing.
        bango = args[1]
        url   = _javhoo_url(bango)
        print(f"URL: {url}")
        headers = {'User-Agent': _META_UA, 'Accept-Language': 'ja,zh-TW;q=0.9'}
        try:
            html, _ = _http_fetch(url, headers)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"Page size: {len(html)} bytes")
        # Show lines that look like actress/star/tag links
        for label, kw in (('star href lines', 'star'), ('tag href lines', '/tag/')):
            lines = [ln.strip() for ln in html.splitlines() if kw in ln and 'href' in ln]
            print(f"\n--- {label} ({len(lines)}) ---")
            for ln in lines[:20]:
                print(' ', ln[:300])
        # Show the <h3>演員</h3> field section specifically
        m_act = re.search(r'<h3>演[員员][^<]*</h3>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
        if m_act:
            print(f"\n--- <h3>演員</h3> field ---")
            print(m_act.group(0)[:500])
        else:
            # Fallback: first occurrence of 演員 in HTML (may be search form)
            m_act2 = re.search(r'演[員员]', html)
            if m_act2:
                print(f"\n--- 演員 context (no <h3> found, raw context) ---")
                print(html[m_act2.start() : m_act2.start() + 300])
        # Show parsed result
        print("\n--- parsed result ---")
        result = _fetch_one_meta_javhoo(bango)
        for k, v in result.items():
            print(f"  {k}: {v}")
    elif len(args) == 2 and args[0] in ('--test-meta', '--test-bango'):
        test_meta_bango(args[1])
    elif args == ['--skip-meta']:
        main(skip_meta=True)
    elif args == ['--all-meta']:
        main(all_meta=True)
    elif args == ['--fill-actresses']:
        # Re-fetch actress data from javhoo for cached items that have a title
        # but empty actresses list (happens when jav321 had no actress info).
        # Rewrites data.js with the patched cache applied.
        print("Scanning collection ...")
        cache  = load_meta_cache()
        search = ' | '.join(f'path:"{d}"' for d in ROOT_DIRS)
        raw    = fetch_everything(search)
        if not raw:
            print("No results from Everything — cannot determine item list.")
            sys.exit(1)
        data = process_results(raw, ROOT_DIRS)
        print()
        updated = fill_missing_actresses(data['items'], cache)
        if updated:
            # Re-apply full cache to items so data.js reflects the patches
            for entry in data['items']:
                bango = entry.get('bango')
                if bango and bango in cache:
                    m = cache[bango]
                    entry['cover']     = m.get('cover', '')
                    entry['title']     = m.get('title', '')
                    entry['actresses'] = m.get('actresses', [])
            _write_data_js(data)
            print(f"  {OUTPUT_FILE} updated.")
    elif not args:
        main()
    else:
        print("Usage:")
        print("  python scan.py                          # full scan + metadata (50 per run)")
        print("  python scan.py --all-meta               # full scan + fetch ALL missing metadata")
        print("  python scan.py --skip-meta              # fast scan, no metadata")
        print("  python scan.py --fill-actresses         # patch missing actress data via javhoo")
        print("  python scan.py --test-bango <BANGO>     # test metadata fetch for one bango")
        print("  python scan.py --debug-javhoo <BANGO>   # dump raw javhoo HTML snippets for regex diagnosis")
