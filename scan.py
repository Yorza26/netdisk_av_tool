#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JAV Collection Scanner  —  stdlib only, no pip install needed
Queries Everything's HTTP API and saves a snapshot to data.js.
data.js is a plain JavaScript file that assigns the data to a global
variable so index.html can load it as a <script> tag — no server needed.

Usage:
    python scan.py               # saves to data.js
    python scan.py --help
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import re
import os
import sys
import io
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
EVERYTHING_PORT = 80        # Change if you changed it in Everything's options

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
# Bango extraction
# ─────────────────────────────────────────────

_BANGO_PATTERNS = [
    # FC2-PPV-XXXXXXX
    (re.compile(r'\bFC2[-_]?PPV[-_]?(\d{4,7})\b', re.I),
     lambda m: (f"FC2-PPV-{m.group(1)}", "FC2-PPV")),

    # HEYZO-XXXX  (also handles heyzo_hd_XXXX, heyzo-hd-XXXX, heyzo_hd_XXXX_full)
    # Use (?!\d) instead of \b so underscore-suffixed names like _full still match
    (re.compile(r'\bHEYZO[-_](?:HD[-_])?(\d{4})(?!\d)', re.I),
     lambda m: (f"HEYZO-{m.group(1)}", "HEYZO")),

    # Caribbean carib format: 102720-001-carib[-1080p]
    (re.compile(r'(?<!\d)(\d{6})[-_](\d{3})[-_]CARIB\b', re.I),
     lambda m: (f"CARIBBEANCOM-{m.group(1)}-{m.group(2)}", "CARIBBEANCOM")),

    # 1PONDO / CARIBBEANCOM — MMDDYY-XXX
    (re.compile(r'\b(1PONDO|CARIBBEANCOM|CARIBPR)[-_](\d{6})[-_](\d{3})\b', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}-{m.group(3)}", m.group(1).upper())),

    # Numeric-prefix series: 300MAAN-456, 200GANA-123, 230ORECO-171
    (re.compile(r'(?<![A-Z\d])(\d{1,3}[A-Z]{2,8})[-.](\d{2,5})(?![A-Z\d])', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),

    # Standard with separator: MIDE-332, STARS.001, ssni-661
    (re.compile(r'(?<![A-Z\d])([A-Z]{2,8})[-.](\d{2,5})(?![A-Z\d])', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),

    # Standard without separator: MIDE332, EKDV460
    (re.compile(r'(?<![A-Z\d])([A-Z]{3,8})(\d{2,5})(?![A-Z\d])', re.I),
     lambda m: (f"{m.group(1).upper()}-{m.group(2)}", m.group(1).upper())),
]


def extract_bango(text: str):
    """Try to find a bango in text. Retries after stripping leading site prefixes."""
    attempts = [text]
    cleaned = _strip_site_prefix(text)
    if cleaned != text and cleaned:
        attempts.append(cleaned)

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
    dirs: dict[str, dict] = {}

    for item in raw:
        name      = item.get('name', '')
        path      = item.get('path', '')
        # size comes back as a numeric string for files, empty string for folders
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

        if top not in dirs:
            bango, series = extract_bango(top)
            dirs[top] = {
                'name':         top,
                'path':         os.path.join(norm_root, top),
                'bango':        bango,
                'series':       series,
                'is_jav':       bango is not None,
                'total_size':   0,
                'file_count':   0,
                'video_count':  0,
                'files':        [],
            }

        if len(parts) == 1:
            continue  # the dir itself

        if not is_folder and size > 0:
            entry = dirs[top]
            entry['total_size']  += size
            entry['file_count']  += 1
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXTS:
                entry['video_count'] += 1
            if len(parts) == 2:
                entry['files'].append({
                    'name':       name,
                    'size':       size,
                    'size_human': bytes_to_human(size),
                    'ext':        ext,
                })

    # Infer bango from filenames when folder name had none
    for entry in dirs.values():
        if not entry['is_jav']:
            for f in entry['files']:
                bango, series = extract_bango(f['name'])
                if bango:
                    entry.update(bango=bango, series=series, is_jav=True)
                    break

    # Finalise each entry
    for entry in dirs.values():
        entry['files'].sort(key=lambda f: f['size'], reverse=True)
        entry['total_size_human'] = bytes_to_human(entry['total_size'])

    # Statistics
    series_count: dict[str, int] = defaultdict(int)
    series_size:  dict[str, int] = defaultdict(int)
    jav_count = non_jav_count = total_size = 0

    items = list(dirs.values())
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
# HTML inline injection
# ─────────────────────────────────────────────

_DATA_START = '<!--jav-data-start-->'
_DATA_END   = '<!--jav-data-end-->'
_APP_START  = '<!--jav-app-start-->'
_APP_END    = '<!--jav-app-end-->'

def _inject_into_html(data: dict) -> None:
    """
    Embed data + app.js as inline <script> blocks inside index.html so it
    works by double-clicking — no server needed.

    Chrome blocks <script src="file.js"> from file:// pages but always runs
    inline <script> blocks.  Sentinel comments allow clean re-injection on
    repeated scan runs.
    """
    here      = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(here, 'index.html')
    app_path  = os.path.join(here, 'app.js')

    if not os.path.exists(html_path):
        print("  [WARN] index.html not found — skipping injection")
        return
    if not os.path.exists(app_path):
        print("  [WARN] app.js not found — skipping injection")
        return

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    with open(app_path,  'r', encoding='utf-8') as f:
        app_js = f.read()

    # ── Data block ────────────────────────────────────────────────────────
    # Compact JSON; escape </ so it can't prematurely close the script tag
    compact = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    compact = compact.replace('</', r'<\/')
    data_block = (
        f'{_DATA_START}'
        f'<script>window.__javData__={compact};</script>'
        f'{_DATA_END}'
    )

    # ── App block ─────────────────────────────────────────────────────────
    # app.js content is plain JS — no </ to worry about in app source
    app_block = f'{_APP_START}<script>\n{app_js}\n</script>{_APP_END}'

    def replace_or_first(src, start, end, new_block, fallback_tag):
        r"""Replace sentinel block if present, else replace fallback_tag.

        Use a lambda replacement so backslashes in new_block are treated
        literally (re.sub interprets \n, \s etc. in plain string replacements).
        """
        updated = re.sub(
            re.escape(start) + r'.*?' + re.escape(end),
            lambda m: new_block, src, flags=re.DOTALL
        )
        if updated == src:
            updated = src.replace(fallback_tag, new_block)
        return updated

    updated = replace_or_first(html,    _DATA_START, _DATA_END, data_block,
                                '<script src="data.js"></script>')
    updated = replace_or_first(updated, _APP_START,  _APP_END,  app_block,
                                '<script src="app.js"></script>')

    if updated == html:
        print("  [WARN] Could not find injection points in index.html")
        return

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(updated)
    print(f"  [OK] Data injected inline into index.html")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  JAV Collection Scanner  (stdlib only)")
    print("=" * 55)
    print(f"  Root   : {ROOT_DIR}")
    print(f"  Port   : {EVERYTHING_PORT}")
    print(f"  Output : {OUTPUT_FILE}")
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

    # ── Save data.js (readable, for reference / HTTP serving) ────────
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('// Auto-generated by scan.py — do not edit manually\n')
        f.write(f'// Scanned: {data["scan_time"]}\n')
        f.write('window.__javData__ = ')
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write(';\n')

    s = data['statistics']
    print(f"\n[OK] Scan complete")
    print(f"   Directories : {s['total_items']}")
    print(f"   JAV         : {s['jav_count']}")
    print(f"   Non-JAV     : {s['non_jav_count']}")
    print(f"   Total size  : {s['total_size_human']}")
    print()
    print("   Top series by count:")
    for series, count in list(s['series_count'].items())[:15]:
        print(f"   {'|' * min(count, 35):<35}  {series}  ({count})")
    print()
    print("   Done — run start.bat to open in browser.")
    print()


if __name__ == '__main__':
    main()
