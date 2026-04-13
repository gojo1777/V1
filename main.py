"""
StreamBox API Wrapper — v2.0.0
Video     : StreamBox direct MP4 sources only (no vidsrc embed)
Subtitles : StreamBox captions + AI Sinhala translate
Download  : Direct download links via StreamBox proxy
"""

import re
import httpx
import json
import os
import urllib.parse
import asyncio
from fastapi import FastAPI, Query, Path, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse, RedirectResponse

app = FastAPI(
    title="StreamBox API",
    description="StreamBox direct MP4 stream + download + AI Sinhala subs",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)

# ── Config ────────────────────────────────────────────────────────────────────

STREAMBOX_API = "https://api.streambox.top"
TMDB_KEY = os.getenv("TMDB_API_KEY", "b0ef895dce19301767377c1e10a1f345")
SUBJECT_TYPE_MAP = {1: "movie", 2: "series", 5: "anime"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36",
    "Accept": "application/json",
}

SUB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Referer":    "https://videodownloader.site/",
    "Origin":     "https://videodownloader.site",
    "Accept":     "*/*",
}


# ── StreamBox helpers ─────────────────────────────────────────────────────────

async def sb_get(path: str, params: dict = None) -> dict:
    url = f"{STREAMBOX_API}{path}"
    async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


def extract_items(data: dict) -> list:
    if not data:
        return []
    d = data.get("data", data)
    if isinstance(d, dict):
        if "items" in d:
            return d["items"]
        if "subjectList" in d:
            return d["subjectList"]
    if isinstance(d, list):
        return d
    return data.get("results", [])


def fmt_item(item: dict) -> dict:
    subject = item.get("subject", item)
    cover = item.get("poster") or item.get("cover") or item.get("image") or subject.get("cover")
    if isinstance(cover, dict):
        cover = cover.get("url")
    stype = int(subject.get("subjectType") or item.get("type") or item.get("subjectType") or 1)
    return {
        "id":          subject.get("subjectId") or item.get("id") or item.get("subjectId"),
        "title":       subject.get("title") or item.get("title") or item.get("name"),
        "type":        SUBJECT_TYPE_MAP.get(stype, "movie"),
        "subjectType": stype,
        "cover":       cover,
        "releaseDate": subject.get("releaseDate") or item.get("releaseDate") or item.get("year"),
        "genre":       subject.get("genre") or item.get("genre"),
        "imdbRating":  subject.get("imdbRatingValue") or item.get("imdbRatingValue"),
    }


# ── TMDB → IMDB ID ────────────────────────────────────────────────────────────

async def get_imdb_id(title: str, year: str = "", subject_type: int = 1) -> str | None:
    try:
        clean = re.sub(r'\s*\[English\]|\s*\[english\]|\s*S\d+-S\d+', '', title, flags=re.IGNORECASE).strip()
        year4 = str(year)[:4] if year else ""
        endpoint = "tv" if subject_type in [2, 5] else "movie"

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.themoviedb.org/3/search/{endpoint}",
                params={"query": clean, "year": year4, "api_key": TMDB_KEY},
            )
            results = r.json().get("results", [])

            if not results:
                r = await client.get(
                    f"https://api.themoviedb.org/3/search/{endpoint}",
                    params={"query": clean, "api_key": TMDB_KEY},
                )
                results = r.json().get("results", [])

            if not results:
                return None

            best = next(
                (res for res in results if (res.get("first_air_date") or res.get("release_date") or "")[:4] == year4),
                results[0]
            )

            ext = await client.get(
                f"https://api.themoviedb.org/3/{endpoint}/{best['id']}/external_ids",
                params={"api_key": TMDB_KEY},
            )
            return ext.json().get("imdb_id")
    except Exception as e:
        print(f"[get_imdb_id] {e}")
        return None


# ── Sources helper ────────────────────────────────────────────────────────────

async def get_sources(id: str, season: int = 0, episode: int = 0) -> dict:
    path = f"/api/sources/{id}"
    params = {}
    if season and episode:
        params = {"season": season, "episode": episode}
    data = await sb_get(path, params or None)
    raw = data.get("data", data)
    return {
        "sources":   raw.get("processedSources") or raw.get("sources") or raw.get("qualities") or [],
        "captions":  raw.get("captions") or raw.get("subtitles") or [],
        "downloads": raw.get("downloads") or [],
    }


def proxy_url(direct_url: str) -> str:
    """StreamBox download proxy URL"""
    return f"{STREAMBOX_API}/api/download?url={urllib.parse.quote(direct_url, safe='')}"


def fmt_sources(sources: list, captions: list, downloads: list, base: str = "") -> dict:
    """Sources nicely format කරනවා"""
    fmt_src = []
    for s in sources:
        url = s.get("directUrl") or s.get("url") or s.get("link") or ""
        if url:
            fmt_src.append({
                "quality":     s.get("quality") or s.get("resolution") or "HD",
                "url":         url,
                "proxy_url":   proxy_url(url),
                "size":        s.get("size"),
                "format":      s.get("format") or "mp4",
            })

    fmt_dl = []
    for d in downloads:
        url = d.get("url") or d.get("directUrl") or ""
        if url:
            fmt_dl.append({
                "quality":   d.get("resolution") or d.get("quality") or "HD",
                "url":       url,
                "proxy_url": proxy_url(url),
                "size":      d.get("size"),
            })

    fmt_caps = []
    for c in captions:
        sub_url = c.get("url") or ""
        fmt_caps.append({
            "lan":       c.get("lan"),
            "label":     c.get("lanName") or c.get("lan"),
            "url":       sub_url,
            "proxy_vtt": f"{base}/proxy-sub-vtt/{urllib.parse.quote(sub_url, safe='')}" if sub_url and base else sub_url,
        })

    return {"sources": fmt_src, "downloads": fmt_dl, "captions": fmt_caps}


# ── SRT → VTT ─────────────────────────────────────────────────────────────────

def srt_to_vtt(srt: str) -> str:
    vtt = "WEBVTT\n\n"
    for block in srt.replace("\r\n", "\n").strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue
        start = 1 if lines[0].strip().isdigit() else 0
        if start >= len(lines):
            continue
        timing = lines[start].replace(",", ".")
        text = "\n".join(lines[start + 1:])
        vtt += f"{timing}\n{text}\n\n"
    return vtt


# ── Sinhala colloquial fixes ──────────────────────────────────────────────────

SINHALA_FIXES = {
    "ඔබට": "ඔයාට", "ඔබ": "ඔයා", "ඔබේ": "ඔයාගෙ", "ඔහු": "එයා",
    "ඔහුගේ": "එයාගෙ", "ඇය": "එයා", "ඔවුන්": "ඒගොල්ලො",
    "මෙය": "මේක", "එය": "ඒක", "නොවේ": "නෙවෙයි",
    "සඳහා": "වෙනුවෙන්", "නිසා": "හින්දා", "බොහෝ": "ගොඩක්",
    "හැකිය": "පුළුවන්", "නොහැකිය": "බෑ", "ස්තූතියි": "තෑන්ක්ස්",
    "කරුණාකර": "ප්ලීස්", "සමග": "එක්ක", "නොහැක": "බෑ",
    "ඔවුන්ගේ": "ඒගොල්ලන්ගෙ", "ඔහුව": "එයාව", "ඇගේ": "එයාගෙ",
}

def sinhala_colloquial(text: str) -> str:
    for formal, casual in SINHALA_FIXES.items():
        text = re.sub(rf'\b{formal}\b', casual, text)
    return text


async def translate_cue(client: httpx.AsyncClient, cue: dict):
    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=si&dt=t&q={urllib.parse.quote(cue['text'])}"
        )
        r = await client.get(url)
        data = r.json()
        if data and data[0]:
            cue["text"] = sinhala_colloquial("".join(p[0] for p in data[0] if p[0]))
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name":    "StreamBox API",
        "version": "2.0.0",
        "note":    "Direct MP4 stream only — no vidsrc embeds",
        "endpoints": {
            "GET /search":           "?q=bleach&type=all|movie|tv&page=0",
            "GET /trending":         "?page=0&perPage=24",
            "GET /list":             "?cat=Action&page=0",
            "GET /detail/{id}":      "Content detail + IMDB id",
            "GET /sources/{id}":     "?season=1&episode=1 — direct MP4 sources + download links",
            "GET /stream/{id}":      "?season=1&episode=1 — best quality stream URL",
            "GET /download/{id}":    "?season=1&episode=1&quality=1080 — download redirect",
            "GET /subtitles/{id}":   "?season=1&episode=1 — subtitle list",
            "GET /sinhala-sub/{id}": "?season=1&episode=1 — AI Sinhala VTT",
            "GET /proxy-sub-vtt/{url}": "Subtitle proxy → VTT",
            "GET /player/{id}":      "?season=1&episode=1 — HTML5 video player",
        },
    }


@app.get("/search")
async def search(
    q: str = Query(...),
    type: str = Query("all"),
    page: int = Query(0, ge=0),
):
    type_map = {"movie": 1, "movies": 1, "tv": 2, "series": 2, "anime": 5, "all": 0}
    t = type_map.get(type.lower(), 0)
    try:
        data = await sb_get(f"/api/search/{urllib.parse.quote(q)}", {"type": t, "page": page})
        items = extract_items(data)
        return {"query": q, "type": type, "page": page, "total": len(items), "results": [fmt_item(i) for i in items]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/trending")
async def trending(page: int = Query(0, ge=0), perPage: int = Query(24, ge=1, le=100)):
    try:
        data = await sb_get("/api/trending", {"page": page, "perPage": perPage})
        items = extract_items(data)
        return {"page": page, "total": len(items), "results": [fmt_item(i) for i in items]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/list")
async def list_by_cat(cat: str = Query(...), page: int = Query(0, ge=0), perPage: int = Query(24, ge=1, le=100)):
    try:
        data = await sb_get("/api/list", {"cat": cat, "page": page, "perPage": perPage})
        items = extract_items(data)
        return {"category": cat, "page": page, "total": len(items), "results": [fmt_item(i) for i in items]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/detail/{id}")
async def detail(id: str = Path(...)):
    try:
        data = await sb_get(f"/api/info/{id}")
        raw = data.get("data", data)
        subject = raw.get("subject", raw)
        resource = raw.get("resource") or {}
        stars = raw.get("stars", [])

        stype = int(subject.get("subjectType", 1))
        title = subject.get("title", "")
        year = str(subject.get("releaseDate", ""))[:4]
        imdb_id = await get_imdb_id(title, year, stype)

        cover = subject.get("cover")
        if isinstance(cover, dict):
            cover = cover.get("url")

        seasons = []
        for s in (resource.get("seasons") or []):
            seasons.append({"season": s.get("se") or s.get("season"), "episodes": s.get("maxEp", 0)})

        return {
            "id":          id,
            "title":       title,
            "description": subject.get("description") or subject.get("overview"),
            "type":        SUBJECT_TYPE_MAP.get(stype, "movie"),
            "subjectType": stype,
            "cover":       cover,
            "releaseDate": subject.get("releaseDate"),
            "duration":    subject.get("duration"),
            "genre":       subject.get("genre"),
            "country":     subject.get("countryName"),
            "imdbRating":  subject.get("imdbRatingValue"),
            "imdbId":      imdb_id,
            "seasons":     seasons,
            "cast": [
                {"name": s.get("name"), "character": s.get("character"), "avatar": s.get("avatarUrl")}
                for s in stars
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sources/{id}")
async def sources(
    id: str = Path(...),
    season: int = Query(0, ge=0),
    episode: int = Query(0, ge=0),
    request: Request = None,
):
    """Direct MP4 sources + download links + subtitles"""
    try:
        raw = await get_sources(id, season, episode)
        base = str(request.base_url).rstrip("/") if request else ""
        result = fmt_sources(raw["sources"], raw["captions"], raw["downloads"], base)
        return {
            "id":      id,
            "season":  season,
            "episode": episode,
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stream/{id}")
async def stream(
    id: str = Path(...),
    season: int = Query(0, ge=0),
    episode: int = Query(0, ge=0),
    quality: str = Query(None, description="1080 | 720 | 480 | 360 — හිස් නම් best quality"),
):
    """Best quality direct stream URL return කරනවා"""
    try:
        raw = await get_sources(id, season, episode)
        src_list = raw["sources"]

        if not src_list:
            raise HTTPException(status_code=404, detail="No sources found")

        # Quality sort — highest first
        quality_order = {"1080": 4, "720": 3, "480": 2, "360": 1}

        def q_score(s):
            q = str(s.get("quality") or s.get("resolution") or "")
            for k, v in quality_order.items():
                if k in q:
                    return v
            return 0

        sorted_src = sorted(src_list, key=q_score, reverse=True)

        # Requested quality filter
        selected = sorted_src[0]
        if quality:
            for s in sorted_src:
                q_str = str(s.get("quality") or s.get("resolution") or "")
                if quality in q_str:
                    selected = s
                    break

        direct = selected.get("directUrl") or selected.get("url") or ""
        if not direct:
            raise HTTPException(status_code=404, detail="No stream URL found")

        return {
            "id":          id,
            "season":      season,
            "episode":     episode,
            "quality":     selected.get("quality") or selected.get("resolution"),
            "stream_url":  direct,
            "proxy_url":   proxy_url(direct),
            "size":        selected.get("size"),
            "all_sources": [
                {
                    "quality":   s.get("quality") or s.get("resolution"),
                    "stream_url": s.get("directUrl") or s.get("url"),
                    "proxy_url":  proxy_url(s.get("directUrl") or s.get("url") or ""),
                    "size":       s.get("size"),
                }
                for s in sorted_src
                if (s.get("directUrl") or s.get("url"))
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/{id}")
async def download(
    id: str = Path(...),
    season: int = Query(0, ge=0),
    episode: int = Query(0, ge=0),
    quality: str = Query(None, description="1080 | 720 | 480 | 360"),
):
    """Download redirect — best / selected quality"""
    try:
        raw = await get_sources(id, season, episode)

        # downloads list first, fallback to sources
        dl_list = raw["downloads"] or raw["sources"]
        if not dl_list:
            raise HTTPException(status_code=404, detail="No download links found")

        quality_order = {"1080": 4, "720": 3, "480": 2, "360": 1}

        def q_score(s):
            q = str(s.get("resolution") or s.get("quality") or "")
            for k, v in quality_order.items():
                if k in q:
                    return v
            return 0

        sorted_dl = sorted(dl_list, key=q_score, reverse=True)
        selected = sorted_dl[0]

        if quality:
            for d in sorted_dl:
                q_str = str(d.get("resolution") or d.get("quality") or "")
                if quality in q_str:
                    selected = d
                    break

        direct = selected.get("url") or selected.get("directUrl") or ""
        if not direct:
            raise HTTPException(status_code=404, detail="No download URL found")

        # StreamBox proxy redirect
        return RedirectResponse(url=proxy_url(direct), status_code=302)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/subtitles/{id}")
async def subtitles(
    id: str = Path(...),
    season: int = Query(0, ge=0),
    episode: int = Query(0, ge=0),
    language: str = Query(None),
    request: Request = None,
):
    try:
        raw = await get_sources(id, season, episode)
        base = str(request.base_url).rstrip("/") if request else ""
        caps = raw["captions"]

        result = [
            {
                "language":  c.get("lanName") or c.get("lan"),
                "lan":       c.get("lan"),
                "url":       c.get("url"),
                "proxy_vtt": f"{base}/proxy-sub-vtt/{urllib.parse.quote(c['url'], safe='')}" if c.get("url") else None,
            }
            for c in caps
        ]

        if language:
            ll = language.lower()
            result = [c for c in result if c["lan"] and c["lan"].lower() == ll]

        return {"id": id, "season": season, "episode": episode, "total": len(result), "subtitles": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/proxy-sub-vtt/{sub_url:path}")
async def proxy_sub_vtt(sub_url: str = Path(...)):
    decoded = urllib.parse.unquote(sub_url)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            r = await client.get(decoded, headers=SUB_HEADERS)
            r.raise_for_status()
        text = r.text
        vtt = text if text.strip().startswith("WEBVTT") else srt_to_vtt(text)
        return Response(
            content=vtt.encode("utf-8"),
            media_type="text/vtt; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Subtitle fetch failed: {e}")


@app.get("/sinhala-sub/{id}")
async def sinhala_sub(
    id: str = Path(...),
    season: int = Query(0, ge=0),
    episode: int = Query(0, ge=0),
):
    """English → AI Sinhala subtitle (Google Translate) → VTT"""
    try:
        raw = await get_sources(id, season, episode)
        caps = raw["captions"]

        en_cap = next(
            (c for c in caps if (c.get("lan") or "").lower() == "en"
             or "english" in (c.get("lanName") or "").lower()),
            caps[0] if caps else None,
        )
        if not en_cap:
            raise HTTPException(status_code=404, detail="No subtitles found")

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            r = await client.get(en_cap["url"], headers=SUB_HEADERS)
            r.raise_for_status()

        raw_text = r.text
        if not raw_text.strip().startswith("WEBVTT"):
            raw_text = srt_to_vtt(raw_text)

        # Cues parse
        blocks = raw_text.replace("\r\n", "\n").split("\n\n")
        header = blocks[0]
        cues = []
        for block in blocks[1:]:
            if not block.strip():
                continue
            lines = block.strip().split("\n")
            ti = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if ti < 0:
                continue
            cues.append({"meta": "\n".join(lines[:ti + 1]), "text": "\n".join(lines[ti + 1:])})

        # Translate batches of 10
        async with httpx.AsyncClient(timeout=30) as client:
            for i in range(0, len(cues), 10):
                await asyncio.gather(*[translate_cue(client, c) for c in cues[i:i + 10]])

        vtt = header + "\n\n" + "\n\n".join(f"{c['meta']}\n{c['text']}" for c in cues)
        return Response(
            content=vtt.encode("utf-8"),
            media_type="text/vtt; charset=utf-8",
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/player/{id}")
async def player(
    id: str = Path(...),
    season: int = Query(0, ge=0),
    episode: int = Query(0, ge=0),
    quality: str = Query(None),
    request: Request = None,
):
    """HTML5 native video player — direct MP4, no embed"""
    try:
        base = str(request.base_url).rstrip("/") if request else ""
        raw = await get_sources(id, season, episode)

        src_list = raw["sources"]
        if not src_list:
            raise HTTPException(status_code=404, detail="No sources found")

        quality_order = {"1080": 4, "720": 3, "480": 2, "360": 1}

        def q_score(s):
            q = str(s.get("quality") or s.get("resolution") or "")
            for k, v in quality_order.items():
                if k in q:
                    return v
            return 0

        sorted_src = sorted(src_list, key=q_score, reverse=True)

        # Detail fetch for title
        try:
            det = await detail(id=id)
            title = det.get("title") or id
        except Exception:
            title = id

        ep_label = f" — S{season:02d}E{episode:02d}" if season and episode else ""
        full_title = f"{title}{ep_label}"

        # Build source list for player
        player_sources = []
        for s in sorted_src:
            url = s.get("directUrl") or s.get("url") or ""
            if url:
                q = str(s.get("quality") or s.get("resolution") or "HD")
                player_sources.append({
                    "label": f"{q}p",
                    "url":   proxy_url(url),
                    "size":  s.get("size"),
                })

        # Subtitles
        caps = raw["captions"]
        sinhala_url = f"{base}/sinhala-sub/{id}?season={season}&episode={episode}"
        player_subs = []
        for c in caps:
            sub_url = c.get("url") or ""
            if sub_url:
                player_subs.append({
                    "label":     c.get("lanName") or c.get("lan") or "Unknown",
                    "lan":       c.get("lan") or "",
                    "proxy_vtt": f"{base}/proxy-sub-vtt/{urllib.parse.quote(sub_url, safe='')}",
                })
        # Sinhala AI sub add
        player_subs.insert(0, {"label": "🇱🇰 Sinhala (AI)", "lan": "si", "proxy_vtt": sinhala_url})

        sources_js = json.dumps(player_sources)
        subs_js = json.dumps(player_subs)

        # Download links
        dl_list = raw["downloads"] or raw["sources"]
        dl_html = ""
        for d in sorted(dl_list, key=q_score, reverse=True):
            url = d.get("url") or d.get("directUrl") or ""
            if not url:
                continue
            q = str(d.get("resolution") or d.get("quality") or "HD")
            size_bytes = d.get("size") or 0
            try:
                size_mb = f"{int(size_bytes)/1024/1024:.1f} MB" if size_bytes else ""
            except Exception:
                size_mb = ""
            dl_html += f'<a href="{proxy_url(url)}" download class="dl-btn">{q}p {size_mb}</a>\n'

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>{full_title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0a;color:#fff;font-family:sans-serif;min-height:100dvh;display:flex;flex-direction:column}}
#wrap{{position:relative;width:100%;aspect-ratio:16/9;background:#000;max-height:70vh}}
video{{width:100%;height:100%;display:block;background:#000}}
#subwrap{{position:absolute;bottom:10px;left:0;right:0;text-align:center;pointer-events:none;z-index:10}}
#subtitle-text{{display:inline-block;background:rgba(0,0,0,0.85);color:#fff;font-size:clamp(14px,2.5vw,20px);
  padding:4px 12px;border-radius:4px;max-width:90%;line-height:1.5;text-shadow:0 1px 3px #000;white-space:pre-wrap}}
#controls{{background:#111;padding:10px 14px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
#title-bar{{background:#0f0f0f;padding:8px 14px;font-size:13px;color:#aaa;border-bottom:1px solid #222}}
.ctrl-group{{display:flex;align-items:center;gap:6px}}
label{{color:#666;font-size:12px;white-space:nowrap}}
select{{background:#1e1e1e;color:#fff;border:1px solid #333;padding:5px 10px;border-radius:6px;font-size:12px;cursor:pointer}}
select:focus{{outline:none;border-color:#e50914}}
#dl-section{{background:#0f0f0f;padding:10px 14px;border-top:1px solid #1a1a1a}}
#dl-section h3{{font-size:11px;color:#555;text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px}}
.dl-btn{{display:inline-block;padding:5px 14px;background:#1e1e1e;border:1px solid #333;
  border-radius:20px;font-size:12px;color:#ccc;text-decoration:none;margin:2px;
  transition:all .2s}}
.dl-btn:hover{{background:#e50914;border-color:#e50914;color:#fff}}
.quality-active{{background:#e50914;border-color:#e50914;color:#fff}}
</style>
</head>
<body>
<div id="title-bar">{full_title}</div>
<div id="wrap">
  <video id="vid" controls playsinline preload="metadata"></video>
  <div id="subwrap"><span id="subtitle-text"></span></div>
</div>
<div id="controls">
  <div class="ctrl-group">
    <label>Quality:</label>
    <select id="qualsel" onchange="changeQuality()"></select>
  </div>
  <div class="ctrl-group">
    <label>Subtitles:</label>
    <select id="subsel" onchange="changeSub()">
      <option value="">— ඕනෑ නෑ —</option>
    </select>
  </div>
</div>
<div id="dl-section">
  <h3>⬇ Download</h3>
  {dl_html or '<span style="color:#444;font-size:12px">No downloads available</span>'}
</div>
<script>
const allSources = {sources_js};
const allSubs    = {subs_js};
const vid        = document.getElementById('vid');
const subtitleEl = document.getElementById('subtitle-text');
const qualSel    = document.getElementById('qualsel');
const subSel     = document.getElementById('subsel');

// Populate quality select
allSources.forEach((s, i) => {{
  const opt = document.createElement('option');
  opt.value = i;
  opt.textContent = s.label + (s.size ? ' — ' + s.size : '');
  qualSel.appendChild(opt);
}});

// Populate subtitle select
allSubs.forEach(s => {{
  const opt = document.createElement('option');
  opt.value = s.lan;
  opt.textContent = s.label;
  subSel.appendChild(opt);
}});

// Load first source
if(allSources.length) {{
  const saved = vid.currentTime;
  vid.src = allSources[0].url;
  vid.load();
}}

function changeQuality() {{
  const idx = parseInt(qualSel.value);
  const src = allSources[idx];
  if(!src) return;
  const t = vid.currentTime;
  const paused = vid.paused;
  vid.src = src.url;
  vid.load();
  vid.currentTime = t;
  if(!paused) vid.play();
}}

// Subtitle engine
let cues = [], subTimer = null, startMs = null, subOffset = 0;

function parseVTT(text) {{
  const result = [];
  for(const block of text.split(/\n\n+/)) {{
    const lines = block.trim().split('\\n');
    const ti = lines.findIndex(l => l.includes('-->'));
    if(ti < 0) continue;
    const [start, end] = lines[ti].split('-->').map(vttMs);
    const txt = lines.slice(ti+1).join('\\n').replace(/<[^>]+>/g,'');
    if(txt.trim()) result.push({{start, end, txt}});
  }}
  return result;
}}

function vttMs(t) {{
  const p = t.trim().split(':');
  let s = 0;
  if(p.length === 3) s = (+p[0])*3600 + (+p[1])*60 + parseFloat(p[2]);
  else s = (+p[0])*60 + parseFloat(p[1]);
  return Math.round(s * 1000);
}}

function stopSubs() {{
  if(subTimer) clearInterval(subTimer);
  subtitleEl.textContent = '';
  cues = [];
}}

function startSubs(vttText) {{
  cues = parseVTT(vttText);
  if(subTimer) clearInterval(subTimer);
  subTimer = setInterval(() => {{
    const now = Math.round(vid.currentTime * 1000);
    const c = cues.find(c => now >= c.start && now <= c.end);
    subtitleEl.textContent = c ? c.txt : '';
  }}, 100);
}}

async function changeSub() {{
  const lan = subSel.value;
  if(!lan) {{ stopSubs(); return; }}
  const sub = allSubs.find(s => s.lan === lan);
  if(!sub) {{ stopSubs(); return; }}
  subtitleEl.textContent = 'Loading...';
  try {{
    const r = await fetch(sub.proxy_vtt);
    if(!r.ok) throw new Error();
    startSubs(await r.text());
  }} catch(e) {{
    subtitleEl.textContent = 'Sub load failed';
    setTimeout(() => subtitleEl.textContent = '', 2000);
  }}
}}
</script>
</body>
</html>"""
        return HTMLResponse(content=html)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Vercel / Railway ──────────────────────────────────────────────────────────
try:
    from mangum import Mangum
    handler = Mangum(app, lifespan="off")
except ImportError:
    pass
