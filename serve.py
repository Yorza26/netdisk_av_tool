#!/usr/bin/env python3
"""
serve.py — Zero-dependency web server for JAV Manager
Uses only Python standard library. No pip install needed.

Usage:  python serve.py
Then open:  http://localhost:5000
"""

import http.server
import socketserver
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys

# ─────────────────────────────────────────────
# Configuration  (edit these two lines)
# ─────────────────────────────────────────────
PORT             = 5000
ROOT_DIR         = r"E:\115\云下载"
EVERYTHING_PORT  = 80       # Everything HTTP Server port

# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Headers we send when fetching from javbus
_JAVBUS_HEADERS = {
    'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'text/html,application/xhtml+xml,*/*;q=0.9',
    'Accept-Language': 'zh-TW,zh;q=0.9,ja;q=0.8,en;q=0.7',
    'Referer':         'https://www.javbus.com/',
}

# Image CDN domains we're willing to proxy
_ALLOWED_IMAGE_DOMAINS = (
    'javbus.com', 'jwba.com',
    'dmm.co.jp', 'pics.dmm.co.jp',
)


# ─────────────────────────────────────────────
# Request handler
# ─────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    """
    Extends SimpleHTTPRequestHandler (which handles static files)
    with /api/* endpoints for Everything and javbus proxying.
    """

    def __init__(self, *args, **kwargs):
        # Serve static files from the script's own directory
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    # Suppress per-request stdout noise; only print API calls
    def log_message(self, fmt, *args):
        msg = fmt % args
        if '/api/' in msg:
            print(f"  {self.command} {self.path.split('?')[0]}")

    # ── Routing ──────────────────────────────────────────────────────

    def do_GET(self):
        if self.path.startswith('/api/'):
            try:
                self._route_api()
            except Exception as exc:
                self._send_json({'error': str(exc)}, 500)
        else:
            super().do_GET()

    def _route_api(self):
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in
                  urllib.parse.parse_qs(parsed.query, keep_blank_values=False).items()}
        p = parsed.path

        if   p == '/api/config':        self._api_config()
        elif p == '/api/everything':    self._api_everything(params)
        elif p == '/api/javbus':        self._api_javbus(params.get('id', ''))
        elif p == '/api/image-proxy':   self._api_image_proxy(params.get('url', ''))
        else:                           self.send_error(404)

    # ── /api/config ───────────────────────────────────────────────────

    def _api_config(self):
        self._send_json({'root_dir': ROOT_DIR, 'everything_port': EVERYTHING_PORT})

    # ── /api/everything ───────────────────────────────────────────────
    # Proxy to Everything's HTTP server (solves port-CORS).

    _ALLOWED_EVERYTHING_PARAMS = frozenset({
        's', 'search', 'n', 'count', 'o', 'offset',
        'sort', 'ascending', 'regex', 'case',
    })

    def _api_everything(self, params):
        clean = {k: v for k, v in params.items()
                 if k in self._ALLOWED_EVERYTHING_PARAMS}
        clean.update({'j': '1', 'p': '1', 'm': '1', 'a': '1'})

        url = f'http://localhost:{EVERYTHING_PORT}/?{urllib.parse.urlencode(clean)}'
        req = urllib.request.Request(url)

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            self._send_bytes(body, 'application/json; charset=utf-8')
        except (urllib.error.URLError, OSError):
            self._send_json({
                'error': 'Cannot reach Everything HTTP server. '
                         'Enable it: Everything → Tools → Options → HTTP Server.',
                'totalResults': 0,
                'results': [],
            }, 503)

    # ── /api/javbus ───────────────────────────────────────────────────
    # Fetch the raw javbus HTML page and return it with a CORS header.
    # The browser's DOMParser handles extraction — no bs4 needed here.

    def _api_javbus(self, bango):
        bango = bango.strip().upper()
        if not bango:
            self.send_error(400, 'Missing id')
            return

        url = f'https://www.javbus.com/{urllib.parse.quote(bango)}'
        req = urllib.request.Request(url, headers=_JAVBUS_HEADERS)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                final_url = resp.geturl()
                body      = resp.read()

            # Javbus redirects to /search/... when the bango doesn't exist
            if '/search/' in final_url or bango not in final_url.upper():
                self._send_json({
                    'found':      False,
                    'bango':      bango,
                    'message':    'Not found on javbus',
                    'search_url': (f'https://www.javbus.com/search/{urllib.parse.quote(bango)}'
                                   '&type=&parent=ce'),
                })
                return

            # Return raw HTML — the frontend parses it with DOMParser
            self._send_bytes(body, 'text/html; charset=utf-8')

        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self._send_json({
                    'found':      False,
                    'bango':      bango,
                    'message':    'Not found on javbus (404)',
                    'search_url': (f'https://www.javbus.com/search/{urllib.parse.quote(bango)}'
                                   '&type=&parent=ce'),
                })
            else:
                self._send_json({'error': f'javbus returned HTTP {exc.code}'}, 502)
        except urllib.error.URLError as exc:
            self._send_json({'error': f'Cannot reach javbus: {exc.reason}'}, 504)

    # ── /api/image-proxy ──────────────────────────────────────────────
    # Proxy cover images so the browser can display them without
    # hotlink-blocking or mixed-content issues.

    def _api_image_proxy(self, url):
        if not url.startswith('https://'):
            self.send_error(400, 'https:// URL required')
            return
        if not any(d in url for d in _ALLOWED_IMAGE_DOMAINS):
            self.send_error(403, 'Domain not in allowlist')
            return

        req = urllib.request.Request(url, headers=_JAVBUS_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_type = resp.headers.get('Content-Type', 'image/jpeg')
                body = resp.read()
            self._send_bytes(body, content_type)
        except Exception as exc:
            self.send_error(502, str(exc))

    # ── Low-level response helpers ────────────────────────────────────

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self._send_bytes(body, 'application/json; charset=utf-8', status)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)


# ─────────────────────────────────────────────
# Threaded server (handles concurrent requests — important when
# the frontend fetches Everything data AND a cover image at the same time)
# ─────────────────────────────────────────────

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == '__main__':
    data_js   = os.path.join(BASE_DIR, 'data.js')
    data_json = os.path.join(BASE_DIR, 'data.json')

    print("=" * 50)
    print("  JAV Manager — serve.py (stdlib only)")
    print("=" * 50)
    print(f"  Root dir : {ROOT_DIR}")
    print(f"  Everything port: {EVERYTHING_PORT}")

    if os.path.exists(data_js):
        print(f"  data.js  : found (offline mode available)")
    elif os.path.exists(data_json):
        print(f"  data.json: found (fallback available)")
    else:
        print("  ⚠  No data.js / data.json — app will query Everything live")

    print(f"\n  Open:  http://localhost:{PORT}")
    print("  Stop:  Ctrl+C\n")

    try:
        with ThreadedServer(('127.0.0.1', PORT), Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as e:
        if 'Address already in use' in str(e) or '10048' in str(e):
            print(f"\n✗ Port {PORT} is in use. Change PORT in serve.py or stop the other process.")
        else:
            raise
