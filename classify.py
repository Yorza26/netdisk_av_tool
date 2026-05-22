#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JAV Collection Classifier

Genre taxonomy:
  jav        — Censored JAV (auto from is_jav flag, no LLM needed)
  uncensored — Uncensored Japanese pro content (Tokyo Hot, H0930, Caribbean, etc.)
  hentai     — Animated adult (hentai OVA, eroge, animated doujin)
  amateur    — Amateur/leaked/voyeur (Chinese/Korean, hotel cams, fan leaks)
  western    — Western professional adult content
  anime      — Non-adult anime/animation
  gravure    — Japanese idol photosets/gravure Blu-rays
  game       — Games, visual novels, software, ebooks
  other      — Miscellaneous

Classification flow:
  1. is_jav=True  → "jav" instantly (no LLM)
  2. Rule-based   → covers ~90% of non-JAV items by pattern
  3. Ollama LLM   → fallback for genuinely ambiguous items only

Usage:
    python classify.py               # classify all unclassified
    python classify.py --all         # re-classify everything
    python classify.py --check       # check Ollama + show stats
    python classify.py --rules-only  # skip LLM, only apply rules
    python classify.py --model NAME  # override Ollama model
"""

import urllib.request, urllib.error
import json, re, os, sys, io, time
from datetime import datetime

if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ─── Configuration ─────────────────────────────────────────────────────────
OLLAMA_HOST  = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:e4b"      # e4b for better accuracy on ambiguous cases
DATA_JS      = "data.js"
CACHE_FILE   = "classify_cache.json"
OUTPUT_FILE  = "classify_data.js"
DELAY        = 0.1
TIMEOUT      = 60
# ───────────────────────────────────────────────────────────────────────────

CATEGORIES = ["jav", "uncensored", "hentai", "amateur", "western",
               "anime", "gravure", "game", "other"]

# ── Rule-based classifier ───────────────────────────────────────────────────
# Each rule: (compiled_regex, category)
# Rules are tried in order; first match wins.
# Patterns are matched against the lowercased folder name.

def _rx(*pats):
    return re.compile('|'.join(pats), re.I)

RULES = [

    # ── SCANNER-MISSED JAV (date-prefixed codes: 1229abp415FHD, 0831aka038FHD) ──
    # Pattern: MMDD + known label letters + 3-digit number + optional HD/FHD/MDavi
    (_rx(r'^\d{4}[a-z]{2,6}\d{3,4}(?:fhd|hd|mavi|mdavi|4k|\.mp4|$)',
         r'\d{4}(?:abp|snis|hnd|mxgs|ipx|wanz|mkbd|sod|fset|hndb|mide|'
         r'ssis|ssni|pred|docp|aka\d|soa|ufd|star|dv|iene|pppd|genm|'
         r'fsdss|pkpd|mvsd|mucd|mukd|ymdd|mond|cjod|sntl|dsvr|'
         r'ibw|emrd|sprd|juy|homa|rbd|jufd)\d{3}'),   'jav'),
    # JAV uploaded/distributed with @oldman, @18p2p, @javbd suffixes in filename
    (_rx(r'@(?:18p2p|oldman|javbd|javhd|javhit)\b',
         r'D9@oldman', r'@JOB_0'),                     'jav'),
    # JAV codes with underscore separator instead of dash: ABP_031, BF_118, MIRD_124
    (_rx(r'\b(?:ABP|AVOP|MIRD|BF|JOY|SOD|IPX|WANZ|SNIS|MXGS|'
         r'PPPD|IENE|GENM|FSET|HOMA|MUCD|MUKD|YMDD)_\d{3}'),   'jav'),

    # ── UNCENSORED Japanese pro studios ──────────────────────────────────
    # Note: avoid trailing \b before digits/underscores — they are \w too,
    # so "asiatengoku0800" has no boundary between 'u' and '0'.
    (_rx(r'\btokyo.?hot\b', r'\btokyohot\b'),                   'uncensored'),
    (_rx(r'\bh0930\b', r'h0930',   r'ki\d{6}'),                 'uncensored'),
    (_rx(r'\bc0930\b', r'c0930'),                                'uncensored'),
    (_rx(r'\bunkotare\b'),                                       'uncensored'),
    (_rx(r'\bheydouga\b'),                                       'uncensored'),
    (_rx(r'\bheyzo\b'),                                          'uncensored'),
    (_rx(r'asiatengoku'),                                        'uncensored'),  # no trailing \b (asiatengoku0800)
    (_rx(r'totsugeki'),                                          'uncensored'),  # TOTSUGEKI031
    (_rx(r'\bhamesamurai\b'),                                     'uncensored'),
    (_rx(r'\bshirotsuma\b', r'shiroutozanmai'),                   'uncensored'),  # SHIROUTOZANMAI217
    (_rx(r'\blivesamurai\b'),                                     'uncensored'),
    (_rx(r'\bcoterieav\b'),                                       'uncensored'),
    (_rx(r'syukou.?club'),                                       'uncensored'),
    (_rx(r'g.area'),                                             'uncensored'),  # g-area_pgm_ (no trailing \b)
    (_rx(r'pgm_\d'),                                             'uncensored'),  # pgm_ preceded by _ (no \b)
    (_rx(r'\bjavtorrent\b', r'\bjapornxxx\b'),                    'uncensored'),
    (_rx(r'\bacup\b', r'\bacupload\b'),                           'uncensored'),
    (_rx(r'\bfellatioJapan\b', r'\bfellatio.?japan\b'),           'uncensored'),
    (_rx(r'\bcovertjapan\b'),                                     'uncensored'),
    (_rx(r'\bnyoshin\b'),                                         'uncensored'),
    (_rx(r'\b1000.?(nin|斬|zangiri)\b', r'1000-\d'),              'uncensored'),
    (_rx(r'caribbeancom', r'caribbean', r'carib'),               'uncensored'),  # CARIB_HD (no \b after)
    (_rx(r'1pondo'),                                             'uncensored'),  # 3xplanet_1Pondo_ (no \b)
    (_rx(r'3xplanet'),                                           'uncensored'),  # aggregator for uncensored
    (_rx(r'\baka0\d{2}\b'),                                       'uncensored'),
    (_rx(r'\bka0\d{2}\b', r'\bn\d{4}\b'),                        'uncensored'),
    (_rx(r'素人卑猥性交'),                                         'uncensored'),
    (_rx(r'\bmywife\b'),                                          'uncensored'),
    (_rx(r'\[jav\].*uncensored', r'uncensored.*\[jav\]'),         'uncensored'),
    (_rx(r'\[uncensored\]'),                                     'uncensored'),  # explicit tag in filename
    # thz.la — Chinese JAV mirror hosting uncensored Caribbean/1pondo style content
    (_rx(r'thz\.la'),                                            'uncensored'),
    # VR JAV Blu-rays (MKBD-S, S2MBD, SM3D2DBD, CW3D2BD, MK3D2DBD, MCB3DBD)
    (_rx(r'\bmkbd', r'\bs2mbd', r'\bsm3d2dbd', r'\bcw3d2bd',
          r'\bmk3d2dbd', r'\bmcb3dbd', r'\bbd.m28\b', r'\byrh_\d'),  'uncensored'),
    # avs-museum, 1000-xxxxxx-HD patterns
    (_rx(r'avs.?museum'),                                        'uncensored'),
    (_rx(r'1000-\d{6}'),                                          'uncensored'),
    (_rx(r'第一会所|第一會所'),                                       'uncensored'),
    # skyhouse — Chinese group that tags/distributes uncensored JAV
    (_rx(r'\bskyhouse\d*\b'),                                    'uncensored'),
    # akibahonpo — known uncensored Japanese site
    (_rx(r'akibahonpo'),                                         'uncensored'),
    # TipTop — uncensored content distributor
    (_rx(r'\[tiptop\]', r'tiptop.*tokyo.angel'),                 'uncensored'),
    # Japanese actress name + Chinese description → uncensored (actress name before dash or CJK)
    (_rx(r'[ぁ-ん]+[　-〿\s\-ー]+.*内射', r'内射.*[ぁ-ん]'),  'uncensored'),
    # Date-coded uncensored content: YYMMDD_NNN, MMDDYY-NNN, YYMMDD_actress patterns
    # typical for caribbean, 1pondo, mywife, etc. distributed without studio branding
    (_rx(r'^\d{6}[_\-]\d{2,4}(?:[_\-]|\.|hd|fhd|$)',
         r'^\d{6}[a-z]{2}[a-z_]+'),                             'uncensored'),
    # 1000_YYMMDD_actress (1pondo style)
    (_rx(r'1000_\d{6}'),                                         'uncensored'),
    # kb/k prefix + date code + actress (known uncensored series)
    (_rx(r'^kb\d{4}_', r'^k\d{4}[_\-]'),                        'uncensored'),
    # Japanese amateur phrasing unique to uncensored content
    (_rx(r'北関東在住', r'ちっぱい娘.*無職', r'無職.*ちっぱい'),          'uncensored'),
    # samurai2M — samurai-branded uncensored series
    (_rx(r'samurai2m', r'samurai.*sp\d{4}'),                     'uncensored'),
    # javhit.com — site distributing uncensored
    (_rx(r'javhit\.com'),                                        'uncensored'),
    # kt\d{5} — KT series uncensored codes; CW3DBD — 3D BD Blu-ray uncensored
    (_rx(r'kt\d{5}', r'cw3dbd', r'cw3d2dbd'),                  'uncensored'),
    # sexinsex.net — JAV/uncensored sharing site
    (_rx(r'sexinsex\.net'),                                      'uncensored'),
    # Western English title pattern for Japanese stepmilf uncensored
    (_rx(r'japanese.stepmilf', r'stepmilf.*tachikawa'),         'uncensored'),
    # Prefixed JAV codes missed by scanner: [HD-720P]ABP-031, [720p]SNIS-xxx
    (_rx(r'\[(?:HD.)?(?:720p|1080p|480p|4K)\][A-Z]{2,5}-\d{3}'), 'jav'),
    # @LABEL\d{3} at end of folder name: 匿名@SW130
    (_rx(r'@[A-Z]{2,5}\d{3}(?:\.|$)'),                         'jav'),
    # Japanese JAV titles without bango (scanner missed; title-only folders)
    (_rx(r'中出し.*独身熟女', r'中出し.*人妻', r'中出し.*オールドミス'), 'jav'),
    # 光月夜也 (Tsukimi Yaya) — known JAV actress — with kt code
    (_rx(r'光月夜也'),                                           'jav'),

    # ── HENTAI (animated adult) ───────────────────────────────────────────
    (_rx(r'ピンクパイナップル', r'pink.?pineapple'),                   'hentai'),
    (_rx(r'にじいろばんび'),                                          'hentai'),
    (_rx(r'ばにぃうぉ', r'bunny walker'),                             'hentai'),
    (_rx(r'ivory.?tower'),                                        'hentai'),
    (_rx(r'magic.?bus|魔人'),                                       'hentai'),
    (_rx(r'fuenoNe.?works', r'fueno.ne'),                          'hentai'),
    (_rx(r'studio.?loires'),                                       'hentai'),
    (_rx(r'nlsoft', r'テルミンスタジオ'),                              'hentai'),
    (_rx(r'sex.?friend.*fantia', r'\[sex friend\]'),               'hentai'),
    (_rx(r'あんてきぬすっ', r'桃色望遠鏡', r'euphoria.*ゲーム',
          r'ランジェリーズ.*office', r'otome.*domain'),                'hentai'),
    (_rx(r'custom.?udon', r'柚木姉妹', r'夜桜字幕.*ivory',
          r'夜桜字幕.*magic', r'夜桜字幕.*nijiiro', r'夜桜字幕.*banni'),  'hentai'),
    (_rx(r'最新99bb.*動畫', r'dream工房.*凌辱', r'獨占動畫'),           'hentai'),
    (_rx(r'maplestar'),                                            'hentai'),
    # Animated doujin/OVA markers
    (_rx(r'OVA.*[ぁ-ん]', r'\[ova\].*\['),                         'hentai'),
    # 桜都字幕組 — Chinese fansub group specializing in hentai subtitles
    (_rx(r'桜都字幕组', r'桜都字幕組'),                                'hentai'),
    # 夜桜字幕組 — another hentai fansub group (already partially covered above)
    (_rx(r'夜桜字幕组', r'夜桜字幕組'),                                'hentai'),
    # Archive files with Japanese bishoujo game / VN titles
    (_rx(r'[ぁ-ん]{4,}.*\.7z$', r'[ぁ-ん]{4,}.*\.zip$'),            'hentai'),

    # ── WESTERN adult ─────────────────────────────────────────────────────
    (_rx(r'\bbang.?bus\b', r'\bbangbus\b'),                        'western'),
    (_rx(r'\bnubile.?films?\b'),                                    'western'),
    (_rx(r'\bbabyGotBoobs\b', r'\bbaby.got.boobs\b'),              'western'),
    (_rx(r'\bstreet.?meat.?asia\b'),                               'western'),
    (_rx(r'\bbride4k\b'),                                          'western'),
    (_rx(r'\bdigitalDesire\b'),                                    'western'),
    (_rx(r'\bjapanesemilf\b', r'japan.lust', r'japanlust'),        'western'),
    (_rx(r'\bWe.?Are.?Hairy\b', r'\bwearhairy\b'),                 'western'),
    (_rx(r'\bManyVids\b'),                                         'western'),
    (_rx(r'\bkiittenymph\b', r'\bfistinchen\b'),                   'western'),
    (_rx(r'ellie.?leen', r'alexa.?grace'),                         'western'),
    (_rx(r'bang\.realteens', r'realteens'),                        'western'),
    (_rx(r'thewhiteboxxx', r'white.?boxxx'),                       'western'),
    (_rx(r'SOFTon', r'beautifl.*asian.*fucked'),                   'western'),
    (_rx(r'hairy.?pussy.?compilation'),                            'western'),

    # ── AMATEUR/leaked ────────────────────────────────────────────────────
    # Chinese social media / amateur producers
    (_rx(r'\b91大神\b', r'\b91[^p]', r'91王老吉', r'91最美'),        'amateur'),
    (_rx(r'chenyuyuhou'),                                          'amateur'),
    (_rx(r'探花[郎]?', r'探花郎'),                                   'amateur'),
    (_rx(r'三飞夜生活'),                                             'amateur'),
    (_rx(r'酒店偷拍', r'宾馆开房', r'酒店.*高清'),                    'amateur'),
    (_rx(r'主题酒店偷拍', r'栖檬.*酒店', r'石家庄.*栖檬'),             'amateur'),
    (_rx(r'门事件', r'泄密', r'私拍流出', r'流出', r'泄露'),           'amateur'),
    (_rx(r'百度云', r'云泄密'),                                      'amateur'),
    (_rx(r'网曝门事件'),                                             'amateur'),
    (_rx(r'偷拍', r'偷窥'),                                         'amateur'),
    (_rx(r'推特.*合集', r'电报.*福利', r'P站.*流出'),                  'amateur'),
    (_rx(r'MyFans', r'Myfans', r'myfans'),                         'amateur'),
    (_rx(r'OF露脸', r'OnlyFans', r'onlyfans'),                     'amateur'),
    (_rx(r'ThZu\.Cc', r'thzu\.cc'),                                'amateur'),
    (_rx(r'7sht\.me'),                                             'amateur'),
    (_rx(r'168x\.me'),                                             'amateur'),
    (_rx(r'ds\d+\.xyz', r'xyz\s*\d{6}\.xyz', r'\d{6}\.xyz'),      'amateur'),
    (_rx(r'aaxv\.xyz'),                                            'amateur'),
    (_rx(r'kpkp3\.com'),                                           'amateur'),
    (_rx(r'BAO.*先生', r'BAO.*韩国', r'黑超大屌.*BAO'),               'amateur'),
    (_rx(r'91[极品].*[美少女网红]', r'[长岛冰茶]'),                   'amateur'),
    (_rx(r'YS\d{10}.*偷拍', r'YS\d{10}'),                          'amateur'),
    (_rx(r'ATMYP'),                                                'amateur'),
    (_rx(r'\bGaoxiao\b', r'gaoxiaonvshen'),                       'amateur'),
    (_rx(r'ZAY\d{8}'),                                             'amateur'),
    (_rx(r'LMYLJ'),                                                'amateur'),
    (_rx(r'U6A6\.COM'),                                            'amateur'),
    # FC2 — Japanese amateur video platform (fc\d{7,8})
    (_rx(r'fc\d{7}'),                                              'amateur'),
    # URL domains used in Chinese amateur content
    (_rx(r'ac74\.xyz', r'www\.ac74'),                              'amateur'),
    # Famous Chinese amateur series / performers
    (_rx(r'紫色面具'),                                              'amateur'),
    (_rx(r'赵小贝'),                                               'amateur'),
    (_rx(r'\[wink是可爱的wink\]', r'wink是可爱的'),                   'amateur'),
    (_rx(r'绝对领域传媒'),                                           'amateur'),  # Chinese AV studio (amateur-style)
    # 楼凤/spa-type services or Chinese leaked content
    (_rx(r'老黄.*会所', r'会所.*足浴', r'足浴.*老黄'),                  'amateur'),
    (_rx(r'土豪.*vip', r'猫先生.*妹子', r'猫先生.*尤物'),               'amateur'),
    # Numbers as names with Chinese adult keywords (e.g. "6 来自洛阳的19岁萌妹子")
    (_rx(r'^\d{1,3}[\s　]+(?:来自|趁着|清纯|老黄|雷爷|让人)'),         'amateur'),
    # Chinese amateur scene descriptions (public sex, university, hotel)
    (_rx(r'(?:学生妹|师妹|妹子|留学生).*(?:外教|老师|洋屌|调教|啪啪)',
         r'大一新生.*宿舍', r'会所.*足浴.*啪啪', r'宾馆.*啪啪', r'酒店.*情人'),  'amateur'),
    # Chinese "推特" (Twitter) / social media leak patterns
    (_rx(r'推特.*(?:尤物|女神|模特|开房|性爱)', r'P站.*(?:博主|亚裔)'), 'amateur'),
    # Chinese descriptive amateur titles (generic loud moaning/explicit descriptions)
    (_rx(r'大奶.*人妻.*啪啪', r'熟女.*人妻.*内射', r'大奶.*少妇',
         r'猛男.*爆操', r'大屌.*爆肏', r'啪啪.*不停.*内射'),              'amateur'),
    # 人前露出 (public nudity) series
    (_rx(r'人前露出', r'露出系'),                                  'amateur'),
    # 风筝断了线 — Chinese amateur series tag
    (_rx(r'风筝断了线'),                                          'amateur'),
    # 门事件 / school scandal leaks
    (_rx(r'(?:学校|中学|高中|大学).*门(?:事件)?.*(?:下载|完整)', r'校门'),   'amateur'),
    # Chinese "福利姬" influencer content
    (_rx(r'福利姬.*(?:VIP|定制|剧情)', r'网红.*福利姬'),              'amateur'),
    # 雷爷 — famous Chinese amateur content creator
    (_rx(r'雷爷.*(?:酒店|少妇|少女|炮)'),                          'amateur'),
    # 2018年最新各系列 — large Chinese amateur collection
    (_rx(r'系列试看.*媲美欣', r'指挥.*系列.*借贷宝', r'各系列试看.*借贷'), 'amateur'),
    # Korean
    (_rx(r'한국인모음', r'한국'),                                     'amateur'),

    # ── GRAVURE (non-nude/semi-nude Japanese idol) ────────────────────────
    (_rx(r'\bgraphis\b'),                                          'gravure'),
    (_rx(r'santa.?fe.*miyazawa', r'miyazawa.*santa.?fe',
          r'宮沢りえ.*santa', r'santa.fe.*rie'),                     'gravure'),
    (_rx(r'kishin.?shinoyama'),                                    'gravure'),
    (_rx(r'syukou.?club.*photo', r'ph.photo'),                     'gravure'),
    (_rx(r'g-area.*写真', r'写真.*race.?queen'),                     'gravure'),
    (_rx(r'\bYuna.?Katase\b'),                                     'gravure'),
    (_rx(r'canan_202'),                                            'gravure'),

    # ── ANIME (non-adult Japanese animation) ─────────────────────────────
    # Known fansub group prefixes
    (_rx(r'\[sakurato\]', r'\[lilith.raws\]', r'\[nc.raws\]',
          r'\[kamigami\]', r'\[hysub\]', r'\[xksub\]', r'\[beanSub\]',
          r'\[sweetsub\]', r'\[uha.wings\]', r'\[caso', r'\[nekomoe',
          r'\[orion.origin\]'),                                    'anime'),
    # Specific known non-adult titles
    (_rx(r'shingeki.no.kyojin', r'attack.on.titan', r'進撃'),      'anime'),
    (_rx(r'odd.?taxi'),                                            'anime'),
    (_rx(r'fumetsu.no.anata', r'to.your.eternity', r'致不灭的你',
          r'給不滅的你'),                                            'anime'),
    (_rx(r'summer.?time.?rendering'),                              'anime'),
    (_rx(r'paripi.?koumei', r'ya.*boy.?kong.?ming'),               'anime'),
    (_rx(r'sono.?bisque.?doll', r'sono.bisuke.doll'),              'anime'),
    (_rx(r'komi.?san'),                                            'anime'),
    (_rx(r'maidragon', r'kobayashi.*maid.dragon'),                 'anime'),
    (_rx(r'super.?cub'),                                           'anime'),
    (_rx(r'sonny.?boy'),                                           'anime'),
    (_rx(r'takt.?op', r'takt op'),                                 'anime'),
    (_rx(r'vivy.*fluorite'),                                       'anime'),
    (_rx(r'akebi.*sailor'),                                        'anime'),
    (_rx(r'getsuyoubi.*tawawa'),                                   'anime'),
    (_rx(r'女孩遇到男孩'),                                           'anime'),
    (_rx(r'重启人生.*WEBRip'),                                      'other'),  # live-action drama

    # ── MOVIE / live-action (put in 'other') ──────────────────────────────
    (_rx(r'\btenet\b.*\d{4}'),                                     'other'),
    (_rx(r'重启人生.*\d{4}'),                                       'other'),

    # ── GAME / software / ebook ───────────────────────────────────────────
    (_rx(r'\.pdf$', r'\.epub$', r'\.mobi$'),                       'game'),
    (_rx(r'effective.?c\+\+', r'game.engine.arch', r'directX',
          r'computer.systems', r'procedural.generation'),           'game'),
    (_rx(r'KoiKoi.*patch', r'HF.?Patch'),                          'game'),
    (_rx(r'神様のような君へPKG', r'セクサロイドな彼女', r'放課後ポニーテール',
          r'NLsoft'),                                               'game'),
    (_rx(r'\blm_res\b'),                                           'game'),
    (_rx(r'game.engine'),                                           'game'),
]


def rule_classify(name: str) -> str | None:
    """Apply rule-based classification. Returns category string or None."""
    for pattern, category in RULES:
        if pattern.search(name):
            return category
    return None


# ── Data helpers ────────────────────────────────────────────────────────────

def read_data_js() -> list:
    try:
        with open(DATA_JS, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[ERROR] {DATA_JS} not found — run scan.py first")
        sys.exit(1)
    m = re.search(r'window\.__javData__\s*=\s*(\{.*\})\s*;?\s*$', content, re.DOTALL)
    if not m:
        print("[ERROR] Could not parse data.js")
        sys.exit(1)
    return json.loads(m.group(1)).get('items', [])


def load_cache() -> dict:
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def write_classify_data(cache: dict, model: str) -> None:
    out = {
        "classified_time": datetime.now().isoformat(),
        "model":           model,
        "classifications": cache,
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('// Auto-generated by classify.py — do not edit manually\n')
        f.write(f'// Model: {model}\n')
        f.write('window.__classifyData__ = ')
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write(';\n')
    print(f"  {OUTPUT_FILE} written.")


# ── Ollama helpers ──────────────────────────────────────────────────────────

def list_ollama_models() -> list | None:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            return [m['name'] for m in json.loads(r.read()).get('models', [])]
    except Exception:
        return None


OLLAMA_PROMPT = (
    "Classify this folder name into exactly one word from: "
    "uncensored, hentai, amateur, western, anime, gravure, game, other.\n"
    "  uncensored = Japanese adult video without censorship (Tokyo Hot, H0930, etc.)\n"
    "  hentai     = animated adult content (hentai OVA, eroge)\n"
    "  amateur    = amateur/leaked/voyeur adult content\n"
    "  western    = western professional adult content\n"
    "  anime      = non-adult Japanese animation\n"
    "  gravure    = Japanese idol photosets, non-nude/semi-nude\n"
    "  game       = games, visual novels, software, ebooks\n"
    "  other      = anything else\n"
    "Folder: {name}\n"
    "Answer:"
)

OLLAMA_CATS = {"uncensored", "hentai", "amateur", "western", "anime", "gravure", "game", "other"}


def classify_with_llm(name: str, title: str, model: str) -> str:
    display = f"{name} / {title}" if (title and title != name) else name
    payload = json.dumps({
        "model":   model,
        "stream":  False,
        "think":   False,
        "options": {"temperature": 0.05, "num_predict": 12},
        "messages": [{"role": "user",
                      "content": OLLAMA_PROMPT.format(name=display)}],
    }).encode('utf-8')
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            msg = json.loads(r.read()).get("message", {})
            raw = msg.get("content", "").strip().lower()
            if not raw:
                found = re.findall(
                    r'\b(uncensored|hentai|amateur|western|anime|gravure|game|other)\b',
                    msg.get("thinking", "").lower())
                raw = found[-1] if found else ""
            word = re.split(r'\W+', raw)[0] if raw else ""
            return word if word in OLLAMA_CATS else "other"
    except Exception as e:
        return f"_err:{str(e)[:60]}"


# ── Main ────────────────────────────────────────────────────────────────────

ICONS = {'jav':'🎌','uncensored':'🔓','hentai':'🌸','amateur':'📱',
         'western':'🌎','anime':'✨','gravure':'📸','game':'🎮','other':'📦'}


def print_stats(cache: dict) -> None:
    total = len(cache)
    if not total:
        print("  (no classifications yet)")
        return
    print(f"\n  Summary ({total} items):")
    for cat in CATEGORIES:
        count = sum(1 for v in cache.values() if v == cat)
        if count == 0:
            continue
        pct = count / total * 100
        bar = '█' * min(int(pct / 2.5), 36)
        print(f"    {ICONS.get(cat,'?')} {cat:<12} {bar:<36} {count:>4}  ({pct:.0f}%)")
    errors = sum(1 for v in cache.values() if v not in CATEGORIES)
    if errors:
        print(f"    ⚠  {'error':<12} {'':36} {errors:>4}")


def main(reclassify: bool = False, rules_only: bool = False,
         model: str = OLLAMA_MODEL) -> None:
    print("=" * 62)
    print("  JAV Collection Classifier")
    print("=" * 62)

    # Ollama check (skip if rules-only)
    if not rules_only:
        models = list_ollama_models()
        if models is None:
            print(f"[WARN] Ollama not reachable — running rules-only mode.")
            rules_only = True
        elif model not in models:
            print(f"[WARN] '{model}' not installed. Running rules-only mode.")
            print(f"       Available: {models}")
            rules_only = True
        else:
            print(f"  Ollama  : connected  (using {model})")
    else:
        print("  Mode    : rules-only (no LLM)")
    print()

    items  = read_data_js()
    cache  = {} if reclassify else load_cache()
    total  = len(items)

    jav_count    = sum(1 for i in items if i.get('is_jav'))
    nonjav_count = total - jav_count
    print(f"  Total: {total}  (JAV: {jav_count}, non-JAV: {nonjav_count})")
    print()

    # ── Pass 1: auto-classify JAV ────────────────────────────────────────
    jav_new = 0
    for item in items:
        if item.get('is_jav') and (item['path'] not in cache or reclassify):
            cache[item['path']] = 'jav'
            jav_new += 1
    if jav_new:
        save_cache(cache)
        print(f"  [auto]  {jav_new} JAV items → 'jav'")

    # ── Pass 2: rule-based for non-JAV ──────────────────────────────────
    need_rules = [i for i in items
                  if not i.get('is_jav') and
                  (i['path'] not in cache or reclassify)]

    rule_ok = rule_skipped = 0
    for item in need_rules:
        cat = rule_classify(item.get('name', ''))
        if cat:
            cache[item['path']] = cat
            rule_ok += 1
        else:
            rule_skipped += 1

    if rule_ok:
        save_cache(cache)
        print(f"  [rules] {rule_ok} items classified by pattern")
    if rule_skipped:
        print(f"  [rules] {rule_skipped} items need LLM (no rule matched)")
    print()

    # ── Pass 3: LLM for remaining ────────────────────────────────────────
    need_llm = [i for i in items
                if not i.get('is_jav') and i['path'] not in cache]

    if not need_llm:
        print("  Nothing left for LLM.")
    elif rules_only:
        n_llm = len(need_llm)
        print(f"  {n_llm} items unclassified (skipped LLM — run without --rules-only to classify with LLM).")
        # Write JS with 'other' for viewer, but do NOT persist 'other' to cache
        # so that a subsequent run without --rules-only will send them to LLM.
        view_cache = dict(cache)
        for item in need_llm:
            view_cache[item['path']] = 'other'
        write_classify_data(view_cache, 'rules-only')
        print_stats(view_cache)
        return  # skip the write_classify_data call below
    else:
        print(f"  [LLM]   {len(need_llm)} items to classify via {model}...\n")
        ok = err = 0
        try:
            for n, item in enumerate(need_llm, 1):
                name     = item.get('name', '')
                title    = item.get('title', '')
                cat      = classify_with_llm(name, title, model)
                if cat.startswith('_err:'):
                    err += 1
                    print(f"  [{n:>4}/{len(need_llm)}] ✗  {name[:58]}")
                else:
                    cache[item['path']] = cat
                    save_cache(cache)
                    ok += 1
                    icon = ICONS.get(cat, '?')
                    print(f"  [{n:>4}/{len(need_llm)}] {icon} {cat:<12} {name[:52]}")
                if n < len(need_llm):
                    time.sleep(DELAY)
        except KeyboardInterrupt:
            print(f"\n  Interrupted — {ok} done, cache saved.")
        print(f"\n  LLM done: {ok} classified, {err} errors.")

    write_classify_data(cache, model)
    print_stats(cache)


def check_only() -> None:
    models = list_ollama_models()
    if models is None:
        print("[ERROR] Ollama not reachable")
        sys.exit(1)
    print(f"Ollama OK. Models: {models}")
    print_stats(load_cache())


if __name__ == '__main__':
    args       = sys.argv[1:]
    model      = OLLAMA_MODEL
    reclassify = False
    rules_only = False

    if '--model' in args:
        idx = args.index('--model')
        if idx + 1 < len(args):
            model = args[idx + 1]
            args  = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]

    if '--all'        in args: reclassify = True
    if '--rules-only' in args: rules_only = True

    if '--check' in args:
        check_only()
    else:
        main(reclassify=reclassify, rules_only=rules_only, model=model)
