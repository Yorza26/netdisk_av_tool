#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JAV Collection Scanner  —  stdlib only, no pip install needed
Queries Everything's HTTP API and saves a snapshot to data.js.
Fetches metadata (title, cover, actresses, maker, label, series, genres, …)
from a self-hosted MetaTube API server (see METATUBE_URL below).
Then open index.html directly in your browser — no server needed.

Usage:
    python scan.py                          # full scan + metadata (50 per run)
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
    r"E:\115\!NSFW\CenPack\総集編",
    r"E:\115\!NSFW\CenPack\Series",
    r"E:\115\!NSFW\CenPack\Actress",
    r"E:\115\!NSFW\4k",
    # r"E:\115\!NSFW\ISO",
    # r"E:\115\!NSFW\Anthology\Gachinco",
]
OUTPUT_FILE     = "data.js"
META_CACHE_FILE     = "meta_cache.json"
ACTRESS_CACHE_FILE  = "actress_cache.json"    # actress avatar URLs (MetaTube/Gfriends)
CLASSIFY_CACHE_FILE = "classify_cache.json"   # written by classify.py
META_PER_RUN    = 50    # max new items to fetch per scan run
ACTRESS_PER_RUN = 100    # max new actress avatar lookups per scan run
META_DELAY      = 0.1    # seconds between items — throttles MetaTube's UPSTREAM scraping
                         # (JavBus/FANZA/… ban aggressive IPs; Railway IPs are shared).
                         # Cached items are served from MetaTube's own DB instantly,
                         # so this only matters for cold fetches.
EVERYTHING_PORT = 80     # Change if you changed it in Everything's options

# ── MetaTube API server (https://github.com/metatube-community/metatube-sdk-go) ──
METATUBE_URL   = "https://metatube-server-production-967d.up.railway.app"
METATUBE_TOKEN = ""      # only needed if the server was started with -token
META_VERSION   = 2       # bump to force a full re-fetch of all cached metadata

# When several providers return the same bango, the first match in this list
# wins (full info is fetched from it). Case-insensitive; unlisted providers
# rank last. Missing key fields are backfilled from the runner-up provider.
PROVIDER_PRIORITY = [
    "FANZA", "MGS", "JavBus", "JAV321", "AVE", "SOD", "FALENO", "DAHLIA",
    "DUGA", "Getchu", "HEYZO", "Caribbeancom", "CaribbeancomPR", "1Pondo",
    "10musume", "PACOPACOMAMA", "MURAMURA", "TOKYO-HOT", "KIN8", "HeyDouga",
    "FC2", "FC2PPVDB", "fc2hub", "C0930", "H0930", "H4610", "MYWIFE",
]

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
                e1['is_file_item'] = True   # path points to the file, not a folder
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
                'is_file_item':     True,   # path points to the file, not a folder
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
# MetaTube metadata fetching
# (self-hosted API server — see METATUBE_URL at the top)
# ─────────────────────────────────────────────

_META_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36')


def _metatube_get(path: str, params: dict = None):
    """GET <METATUBE_URL><path>?<params> and unwrap the {"data": ...} envelope.

    Returns (data, None) on success, (None, errmsg) on network/server error.
    A 404 (movie/actor not found) returns (None, None) so callers can
    distinguish "not found" from real errors."""
    url = METATUBE_URL.rstrip('/') + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    headers = {'User-Agent': _META_UA, 'Accept': 'application/json'}
    if METATUBE_TOKEN:
        headers['Authorization'] = f'Bearer {METATUBE_TOKEN}'
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
        obj = json.loads(body.decode('utf-8'))
        return obj.get('data'), None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None          # not found — not an error
        return None, f"HTTP {e.code}"
    except Exception as exc:
        return None, str(exc)


def _mt_search_term(bango: str) -> str:
    """Convert an internal bango to the term MetaTube searches best with.

    Date-based studio codes carry an internal studio prefix that MetaTube's
    providers don't use in their numbers — strip it:
      1PONDO-101015-001       →  101015_001
      CARIBBEANCOM-102720-001 →  102720-001
      CARIBPR-041426-001      →  041426-001
    Everything else (MIDE-332, HEYZO-2345, FC2-PPV-1234567) searches as-is."""
    m = re.match(r'^1PONDO-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    m = re.match(r'^(?:CARIBBEANCOM|CARIBPR)-(\d{6})-(\d{3,4})$', bango, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return bango


def _norm_num(s: str) -> str:
    """Normalize a bango/number for comparison: uppercase, alphanumerics only."""
    return re.sub(r'[^A-Z0-9]', '', (s or '').upper())


_PROVIDER_RANK = {p.upper(): i for i, p in enumerate(PROVIDER_PRIORITY)}


def _provider_rank(name: str) -> int:
    return _PROVIDER_RANK.get((name or '').upper(), len(PROVIDER_PRIORITY))


def _iso_date(s: str) -> str:
    """'2016-05-29T00:00:00Z' → '2016-05-29'; hide zero dates."""
    d = (s or '')[:10]
    return '' if d.startswith('0001') else d


def _meta_from_info(info: dict) -> dict:
    """Map a MetaTube full-info (or compact search) record onto our cache schema."""
    return {
        'title':          info.get('title', ''),
        'cover':          info.get('big_cover_url') or info.get('cover_url', ''),
        'thumb':          info.get('big_thumb_url') or info.get('thumb_url', ''),
        'actresses':      info.get('actors') or [],
        'maker':          info.get('maker', ''),
        'label':          info.get('label', ''),
        'm_series':       info.get('series', ''),
        'genres':         info.get('genres') or [],
        'director':       info.get('director', ''),
        'summary':        info.get('summary', ''),
        'release_date':   _iso_date(info.get('release_date', '')),
        'runtime':        info.get('runtime', 0),
        'score':          info.get('score', 0),
        'homepage':       info.get('homepage', ''),
        'preview_images': (info.get('preview_images') or [])[:12],
        'provider':       info.get('provider', ''),
        'provider_id':    info.get('id', ''),
    }


# Image hosts that reject hotlinking (Referer check) — the browser can never
# load these directly, so data.js gets a MetaTube image-proxy URL instead.
_HOTLINK_BLOCKED_RE = re.compile(r'https?://[^/]*javbus\.com/', re.I)


def _usable_image(url: str, provider: str, provider_id: str, kind: str) -> str:
    """Return `url` unchanged when it's browser-loadable, otherwise a MetaTube
    /v1/images proxy URL (kind: 'thumb' | 'primary' | 'backdrop') that fetches
    it server-side with proper headers."""
    if not url or not _HOTLINK_BLOCKED_RE.match(url):
        return url
    if not (provider and provider_id):
        return url   # can't build a proxy URL — leave it (frontend hides on error)
    base = METATUBE_URL.rstrip('/')
    return (f"{base}/v1/images/{kind}/"
            f"{urllib.parse.quote(provider, safe='')}/"
            f"{urllib.parse.quote(provider_id, safe='')}"
            f"?url={urllib.parse.quote(url, safe='')}")


def _fetch_one_meta(bango: str) -> dict:
    """Fetch full metadata for one bango via MetaTube.

    1. /v1/movies/search?q=<term>  — cross-provider search
    2. keep results whose normalized number equals the bango
    3. fetch full info from the best-ranked provider (PROVIDER_PRIORITY)
    4. backfill sparse fields from the runner-up provider when needed

    Returns {} when not found, {'_err': msg} on network error."""
    term = _mt_search_term(bango)
    results, err = _metatube_get('/v1/movies/search', {'q': term})
    if err:
        return {'_err': err}
    want = {_norm_num(term), _norm_num(bango)}
    matches = [r for r in (results or []) if _norm_num(r.get('number')) in want]
    if not matches:
        return {}
    matches.sort(key=lambda r: _provider_rank(r.get('provider')))

    def full_info(res):
        provider = urllib.parse.quote(res.get('provider', ''), safe='')
        movie_id = urllib.parse.quote(res.get('id', ''), safe='')
        info, e = _metatube_get(f'/v1/movies/{provider}/{movie_id}')
        return info if (info and not e) else None

    info = full_info(matches[0])
    meta = _meta_from_info(info) if info else _meta_from_info(matches[0])

    # Backfill sparse fields from the runner-up provider (one extra request,
    # only when the winner is missing important data).
    if len(matches) > 1 and not (meta['maker'] and meta['genres'] and meta['actresses']):
        info2 = full_info(matches[1])
        if info2:
            m2 = _meta_from_info(info2)
            for k in ('actresses', 'genres', 'maker', 'label', 'm_series',
                      'director', 'summary', 'preview_images'):
                if not meta.get(k):
                    meta[k] = m2[k]
            if not meta.get('score'):
                meta['score'] = m2['score']

    # Cheap backfills from compact search hits (no extra requests)
    if not meta['actresses']:
        for r in matches:
            if r.get('actors'):
                meta['actresses'] = r['actors']
                break
    if not meta['score']:
        meta['score'] = max((r.get('score') or 0) for r in matches)

    meta['_v'] = META_VERSION
    return meta


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


# ─────────────────────────────────────────────
# Actress avatars (MetaTube actor search → Gfriends images)
# ─────────────────────────────────────────────

def load_actress_cache() -> dict:
    """actress_cache.json — keyed by actress name. {} entries are cached
    misses so unknown names aren't re-queried every run."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ACTRESS_CACHE_FILE)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_actress_cache(cache: dict) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ACTRESS_CACHE_FILE)
    tmp  = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _fetch_one_actress(name: str) -> dict:
    """Look up one actress on MetaTube. Returns {'avatar': url, 'images': [...]}
    or {} when not found, {'_err': msg} on network error."""
    results, err = _metatube_get('/v1/actors/search', {'q': name})
    if err:
        return {'_err': err}
    if not results:
        return {}
    # Prefer exact name match, else first result
    best = next((r for r in results if r.get('name') == name), results[0])
    images = best.get('images') or []
    if not images:
        return {}
    return {'avatar': images[0], 'images': images[:4],
            'provider': best.get('provider', '')}


def enrich_actresses(items: list, all_meta: bool = False) -> dict:
    """Fetch avatar images for actresses appearing in `items`
    (up to ACTRESS_PER_RUN new lookups per run, or all with --all-meta).
    Returns {name: avatar_url} for every actress with a cached avatar."""
    cache = load_actress_cache()
    names, seen = [], set()
    for e in items:
        for a in e.get('actresses') or []:
            if a not in seen:
                seen.add(a)
                names.append(a)

    missing = [n for n in names if n not in cache]
    limit   = len(missing) if all_meta else min(len(missing), ACTRESS_PER_RUN)

    if missing:
        remaining = len(missing) - limit
        print(f"  Fetching actress avatars: {limit}"
              + (f"  [{remaining} more on next run]" if remaining else "") + " ...")
        print("  Press Ctrl+C to stop early (progress is saved).")
    try:
        for n, name in enumerate(missing[:limit], 1):
            info = _fetch_one_actress(name)
            if info.get('_err'):
                print(f"  [{n}/{limit}] ✗ ({info['_err'][:50]})  {name}", flush=True)
                continue   # network error — don't cache, retry next run
            cache[name] = info          # {} miss cached so we don't retry forever
            save_actress_cache(cache)
            status = '✓' if info.get('avatar') else '– (no image)'
            print(f"  [{n}/{limit}] {status}  {name}", flush=True)
            if n < limit:
                time.sleep(META_DELAY)
    except KeyboardInterrupt:
        print("\n  Interrupted — actress cache saved.")

    return {n: cache[n]['avatar'] for n in names
            if cache.get(n) and cache[n].get('avatar')}


def enrich_with_meta(items: list, cache: dict, all_meta: bool = False) -> int:
    """Fetch missing/outdated metadata via MetaTube (up to META_PER_RUN per run,
    or all if all_meta=True). Entries cached before META_VERSION are re-fetched;
    their legacy fields are kept as a fallback when MetaTube has no match.
    Saves cache after every fetch. Handles Ctrl+C gracefully.
    Returns number of newly fetched items."""

    def _needs_fetch(e):
        if not (e.get('is_jav') and e.get('bango')):
            return False
        cached = cache.get(e['bango'])
        return cached is None or cached.get('_v') != META_VERSION

    # De-duplicate bangos (flat packs can repeat one bango across items)
    need, seen = [], set()
    for e in items:
        if _needs_fetch(e) and e['bango'] not in seen:
            seen.add(e['bango'])
            need.append(e['bango'])

    ok = fail = 0
    total_needed = len(need)
    limit        = total_needed if all_meta else min(total_needed, META_PER_RUN)

    if total_needed:
        remaining = total_needed - limit
        print(f"  Fetching metadata from MetaTube ({METATUBE_URL})"
              + (f"  [{remaining} more on next run]" if remaining else "") + " ...")
        print("  Press Ctrl+C to stop early (progress is saved).")
    else:
        cached = sum(1 for e in items if e.get('is_jav') and e.get('bango') and e['bango'] in cache)
        print(f"  All metadata cached ({cached} items)")

    try:
        for bango in need[:limit]:
            meta = _fetch_one_meta(bango)
            n    = ok + fail + 1

            if meta.get('_err'):
                fail += 1
                status = f"✗ ({meta['_err'][:60]})"
            elif meta:
                cache[bango] = meta
                save_meta_cache(cache)   # persist after every success
                ok += 1
                status = f"✓ [{meta.get('provider', '?')}]"
                if meta.get('actresses'):
                    status += f" [{', '.join(meta['actresses'][:2])}]"
            else:
                # Not found on MetaTube — keep legacy fields (if any) as a
                # fallback and stamp the version so we don't retry forever.
                legacy = cache.get(bango) or {}
                legacy['_v'] = META_VERSION
                legacy['_notfound'] = True
                cache[bango] = legacy
                save_meta_cache(cache)
                fail += 1
                had = legacy.get('title') or legacy.get('cover')
                status = '– (not on MetaTube' + (', kept legacy data)' if had else ')')

            print(f"  [{n}/{limit}] {status}  {bango}", flush=True)
            if n < limit:
                time.sleep(META_DELAY)

    except KeyboardInterrupt:
        print(f"\n  Interrupted — {ok} fetched, cache saved.")

    if ok + fail:
        print(f"  Done: {ok} with metadata, {fail} not found/errors.")

    # Apply cache to all items. Image URLs from hotlink-blocked hosts (JavBus)
    # are rewritten to MetaTube proxy URLs here — the cache keeps the originals.
    for entry in items:
        bango = entry.get('bango')
        if bango and bango in cache:
            meta = cache[bango]
            prov = meta.get('provider', '')
            pid  = meta.get('provider_id', '')
            entry['cover']          = _usable_image(meta.get('cover', ''), prov, pid, 'backdrop')
            entry['thumb']          = _usable_image(meta.get('thumb', ''), prov, pid, 'thumb')
            entry['title']          = meta.get('title', '')
            entry['actresses']      = meta.get('actresses', [])
            entry['maker']          = meta.get('maker', '')
            entry['label']          = meta.get('label', '')
            entry['m_series']       = meta.get('m_series', '')
            entry['genres']         = meta.get('genres', [])
            entry['director']       = meta.get('director', '')
            entry['summary']        = meta.get('summary', '')
            entry['release_date']   = meta.get('release_date', '')
            entry['runtime']        = meta.get('runtime', 0)
            entry['score']          = meta.get('score', 0)
            entry['homepage']       = meta.get('homepage', '')
            entry['preview_images'] = [_usable_image(u, prov, pid, 'backdrop')
                                       for u in meta.get('preview_images', [])]
            entry['meta_provider']  = prov
            entry['provider_id']    = pid

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
    # Frontend uses this to build /v1/images/... fallback URLs when a
    # provider blocks hotlinking (e.g. JavBus covers 403 outside their site).
    data['metatube_url'] = METATUBE_URL.rstrip('/')

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

    # ── Enrich with MetaTube metadata ─────────────────────────────────
    print("Enriching with metadata ...")
    meta_cache = load_meta_cache()
    fetched = enrich_with_meta(data['items'], meta_cache, all_meta=all_meta)

    # ── Actress avatars (MetaTube actor search → Gfriends) ───────────
    data['actresses'] = enrich_actresses(data['items'], all_meta=all_meta)

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
    """Fetch and print MetaTube metadata for a single bango."""
    term = _mt_search_term(bango)
    print(f"Fetching metadata for: {bango}")
    print(f"  Server      : {METATUBE_URL}")
    print(f"  Search term : {term}")

    results, err = _metatube_get('/v1/movies/search', {'q': term})
    if err:
        print(f"  Search error: {err}")
        return
    want = {_norm_num(term), _norm_num(bango)}
    matches = [r for r in (results or []) if _norm_num(r.get('number')) in want]
    print(f"  Search hits : {len(results or [])} total, {len(matches)} matching number")
    for r in sorted(matches, key=lambda r: _provider_rank(r.get('provider'))):
        print(f"    - {r.get('provider', '?'):<16} id={r.get('id')}  number={r.get('number')}")

    meta = _fetch_one_meta(bango)
    if meta.get('_err'):
        print(f"  Error    : {meta['_err']}")
        return
    if not meta:
        print("  Not found on MetaTube")
        return
    print(f"  Provider : {meta.get('provider')} (id={meta.get('provider_id')})")
    print(f"  Title    : {meta.get('title') or '(none)'}")
    print(f"  Date     : {meta.get('release_date', '')}   Runtime: {meta.get('runtime')} min   Score: {meta.get('score')}")
    print(f"  Maker    : {meta.get('maker', '')}   Label: {meta.get('label', '')}   Series: {meta.get('m_series', '')}")
    print(f"  Director : {meta.get('director', '')}")
    print(f"  Genres   : {', '.join(meta.get('genres', []))}")
    print(f"  Actresses: {meta.get('actresses', [])}")
    print(f"  Cover    : {meta.get('cover') or '(none)'}")
    print(f"  Previews : {len(meta.get('preview_images', []))} images")


if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) == 2 and args[0] in ('--test-meta', '--test-bango'):
        test_meta_bango(args[1])
    elif args == ['--skip-meta']:
        main(skip_meta=True)
    elif args == ['--all-meta']:
        main(all_meta=True)
    elif args == ['--export-bangos']:
        # Dump the unique bango list for fetch_meta.py (run the fetch job on a
        # cloud server instead of keeping this PC on for hours).
        search = ' | '.join(f'path:"{d}"' for d in ROOT_DIRS)
        raw = fetch_everything(search)
        if not raw:
            print("No results from Everything.")
            sys.exit(1)
        data   = process_results(raw, ROOT_DIRS)
        bangos = sorted({e['bango'] for e in data['items']
                         if e.get('is_jav') and e.get('bango')})
        with open('bangos.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(bangos) + '\n')
        print(f"bangos.txt written — {len(bangos)} unique bangos.")
        print("Upload scan.py + fetch_meta.py + bangos.txt (+ meta_cache.json)")
        print("to any machine with Python and run:  python fetch_meta.py")
    elif not args:
        main()
    else:
        print("Usage:")
        print("  python scan.py                          # full scan + metadata (50 per run)")
        print("  python scan.py --all-meta               # full scan + fetch ALL missing metadata")
        print("  python scan.py --skip-meta              # fast scan, no metadata")
        print("  python scan.py --export-bangos          # write bangos.txt for fetch_meta.py (cloud)")
        print("  python scan.py --test-bango <BANGO>     # test MetaTube fetch for one bango")
        sys.exit(1)
