# MetaTube API Reference

Verified against the live server `https://metatube-server-production-967d.up.railway.app`
(source: `route/route.go` in metatube-community/metatube-sdk-go).

All responses are wrapped in `{"data": ...}` on success or `{"error": {"code", "message"}}` on failure.
If the server was started with an auth token (`-token`), private endpoints require the header
`Authorization: Bearer <token>`. Your instance currently answers without a token.

## System endpoints (no cache)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | App name + build version |
| GET | `/v1/modules` | Compiled module versions |
| GET | `/v1/providers` | Lists `actor_providers` and `movie_providers` (name → homepage). Your instance: 30 movie providers (FANZA, JavBus, JAV321, MGS, FC2, HEYZO, Caribbeancom, 1Pondo, DUGA, SOD, FALENO…), 1 actor provider (Gfriends) |
| GET | `/v1/db/version` | Database version (private) |

## Movies (private group)

### `GET /v1/movies/search?q=<keyword>`

Cross-provider search. `q` is a bango/number or keyword. Optional params:
`provider=<name>` (restrict to one provider), `fallback=true` (also query slow providers / DB fallback),
`lazy=true` (serve from DB cache only).

Returns an array of compact results:

```json
{"data": [{
  "id": "MIDE-332",            // provider-specific ID — needed for the info call
  "number": "MIDE-332",        // normalized bango
  "title": "乳フェチ感謝祭…JULIA",
  "provider": "JavBus",
  "homepage": "https://www.javbus.com/ja/MIDE-332",
  "thumb_url": "…/thumb/5hzl.jpg",
  "cover_url": "…/cover/5hzl_b.jpg",
  "score": 0,
  "actors": ["JULIA"],         // present on some providers only
  "release_date": "2016-05-29T00:00:00Z"
}]}
```

### `GET /v1/movies/<provider>/<id>`

Full metadata (`lazy=true` by default — cached in the server DB after first fetch):

```json
{"data": {
  "id": "MIDE-332", "number": "MIDE-332",
  "title": "乳フェチ感謝祭パイズリ凄抜きテクニック JULIA",
  "summary": "",
  "provider": "JavBus", "homepage": "https://www.javbus.com/ja/MIDE-332",
  "director": "HiroA",
  "actors": ["JULIA"],
  "thumb_url": "…", "big_thumb_url": "",
  "cover_url": "…", "big_cover_url": "",
  "preview_video_url": "", "preview_video_hls_url": "",
  "preview_images": ["…jp-1.jpg", "…jp-10.jpg"],
  "maker": "ムーディーズ",
  "label": "MOODYZDIVA",
  "series": "",
  "genres": ["乱交", "パイズリ", "巨乳", "単体作品", "…"],
  "score": 0, "runtime": 176,
  "release_date": "2016-05-29T00:00:00Z"
}}
```

### `GET /v1/reviews/<provider>/<id>`

User reviews (only providers that expose them, e.g. FANZA).

## Actors (private group)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/actors/search?q=<name>` | Search actors. Same `provider` / `fallback` / `lazy` params |
| GET | `/v1/actors/<provider>/<id>` | Full actor info |

Search result shape (Gfriends = images only, no bio):

```json
{"data": [{
  "id": "JULIA", "name": "JULIA", "provider": "Gfriends",
  "homepage": "https://github.com/gfriends/gfriends?gfriends-id=JULIA",
  "images": ["https://raw.githubusercontent.com/gfriends/…/JULIA.jpg", "…"]
}]}
```

## Images (public group, CDN-cacheable ~180 days)

| Path | Description |
|------|-------------|
| `GET /v1/images/primary/<provider>/<id>` | Poster (auto-cropped 7:10, face detection) |
| `GET /v1/images/thumb/<provider>/<id>` | Thumbnail |
| `GET /v1/images/backdrop/<provider>/<id>` | Full cover art |

Optional params: `url=` (proxy an arbitrary image), `ratio=`, `pos=`, `auto=true`, `badge=`, `quality=`.

## Translation (public)

`GET /v1/translate?q=<text>&from=&to=&engine=<google|baidu|openai|…>` — requires engine
credentials configured server-side.

## Notes for scan.py integration

- Number normalization: MetaTube's `number` may differ in separators from our internal bango
  (e.g. 1Pondo `101015_001` vs `1PONDO-101015-001`). Compare after stripping non-alphanumerics.
- Provider quality varies: FANZA/MGS have summary+series, JavBus has genres/label,
  JAV321 has score. `PROVIDER_PRIORITY` in scan.py decides which full record wins;
  missing fields are merged from the other search hits.
- The server caches every full-info fetch in its own DB, so re-fetches are cheap.
