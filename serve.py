#!/usr/bin/env python3
"""
Tiny HTTP server for the JAV Collection viewer.
Serves index.html + data.js so the viewer works from any device on the LAN
(phone, tablet, etc.) -- not just the local PC.

Also writes config.js so the frontend knows the Everything HTTP server URL.
This lets file/folder links in the detail panel open via Everything on iOS
instead of using file:// URLs (which only work on the local PC).

Usage:
    python serve.py                   # viewer on :8080, Everything on :80
    python serve.py 9000              # viewer on :9000
    python serve.py 8080 8080         # viewer on :8080, Everything on :8080
"""

import http.server
import socket
import json
import sys
import os

# ── Ports ─────────────────────────────────────────────────────────────────
# Viewer port: the port this script listens on
# Everything port: the port Everything's HTTP server listens on (Tools → Options
#   → HTTP Server).  Default is 80; change if you changed it in Everything.
try:
    SERVE_PORT      = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    EVERYTHING_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 80
except ValueError:
    print("Usage: python serve.py [viewer-port] [everything-port]")
    print("       Defaults: viewer=8080  everything=80")
    sys.exit(1)

# ── Helpers ────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    """Return the LAN IP this machine uses to reach the outside world."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no data sent; just picks the right interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ── Setup ──────────────────────────────────────────────────────────────────

# Serve from the directory this script lives in
os.chdir(os.path.dirname(os.path.abspath(__file__)))

ip = get_local_ip()

# Build the Everything base URL.  Port 80 is the default HTTP port so we
# omit it from the URL to match what Everything itself shows in its links.
if EVERYTHING_PORT == 80:
    everything_base = f"http://{ip}"
else:
    everything_base = f"http://{ip}:{EVERYTHING_PORT}"

# Write config.js so app.js can build Everything links at runtime.
# The file is tiny (~80 bytes) and is re-created every time the server starts,
# so the IP is always current even if DHCP gave you a new address.
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.js")
with open(config_path, "w", encoding="utf-8") as f:
    cfg = {"everythingBase": everything_base, "everythingPort": EVERYTHING_PORT}
    f.write(f"window.__serverConfig__ = {json.dumps(cfg)};\n")

handler = http.server.SimpleHTTPRequestHandler
handler.log_message = lambda *a: None   # silence per-request logs

print("=" * 52)
print("  JAV Collection Viewer - local HTTP server")
print("=" * 52)
print(f"  This PC   : http://localhost:{SERVE_PORT}/")
print(f"  iPhone    : http://{ip}:{SERVE_PORT}/")
print(f"  Everything: {everything_base}/  (file links)")
print()
print("  Make sure your phone is on the same Wi-Fi.")
print("  Press Ctrl+C to stop.")
print("=" * 52)

with http.server.HTTPServer(("", SERVE_PORT), handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Remove config.js so stale IPs don't confuse file:// usage later
        try:
            os.remove(config_path)
        except OSError:
            pass
        print("\nServer stopped.")
