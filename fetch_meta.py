#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone MetaTube fetcher — runs ANYWHERE with Python 3.8+ (cloud VM,
GitHub Actions, Colab…). No Everything, no filesystem scan, stdlib only.
Reuses the fetch logic from scan.py, so keep both files together.

Workflow:
    1. On the PC:      python scan.py --export-bangos     → writes bangos.txt
    2. On the server:  put scan.py + fetch_meta.py + bangos.txt
                       (+ existing meta_cache.json, optional) in one folder,
                       then:  python fetch_meta.py
    3. Back on the PC: copy meta_cache.json + actress_cache.json into the
                       project folder and run:  python scan.py
                       (instant — everything comes from cache)

Progress is saved after every item, so the job can be killed/resumed freely.

Usage:
    python fetch_meta.py               # fetch all missing metadata + avatars
    python fetch_meta.py --no-actress  # metadata only, skip avatar lookups
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan   # noqa: E402  — reuses _fetch_one_meta, caches, METATUBE_URL, …


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, 'bangos.txt')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            bangos = [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    except FileNotFoundError:
        print("bangos.txt not found.")
        print("Create it on your PC with:  python scan.py --export-bangos")
        sys.exit(1)

    # Minimal fake items — enrich_with_meta only needs is_jav + bango,
    # and fills in 'actresses' for the avatar pass.
    items = [{'is_jav': True, 'bango': b, 'path': b, 'name': b} for b in bangos]

    cache = scan.load_meta_cache()
    done  = sum(1 for b in bangos
                if cache.get(b, {}).get('_v') == scan.META_VERSION)
    print(f"MetaTube : {scan.METATUBE_URL}")
    print(f"Bangos   : {len(bangos)}  ({done} already cached, "
          f"{len(bangos) - done} to fetch)")
    print()

    scan.enrich_with_meta(items, cache, all_meta=True)

    if '--no-actress' not in sys.argv[1:]:
        print()
        scan.enrich_actresses(items, all_meta=True)

    print()
    print("Done. Copy meta_cache.json + actress_cache.json back to the PC,")
    print("then run:  python scan.py")


if __name__ == '__main__':
    main()
