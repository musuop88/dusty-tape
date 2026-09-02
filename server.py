#!/usr/bin/env python3
"""
Dusty Tape — Premium API (No Ads, Global)
- Spotify-like experience: unlimited search, high-quality audio, no interruptions
- Deployable globally: uses PORT env, CORS, health check, yt-dlp auto-discovery
"""
import json, subprocess, urllib.request, urllib.error, threading, sys, re, time, os, shutil

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- Config: env overrides for global deploy ---
YT_KEYS = [
    os.environ.get("YT_KEY", ""),
    "AIzaSyC9XL3ZjWddXya6X74dJoCTL-WEYFDNX30",
    "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
]
YT_KEYS = [k for k in YT_KEYS if k]
# auto-find yt-dlp (local, docker, pip) — no hardcoded machine paths
YTDLP_CANDIDATES = [
    os.environ.get("YTDLP", ""),
    shutil.which("yt-dlp") or "",
    "yt-dlp",
]
YTDLP = next((p for p in YTDLP_CANDIDATES if p and (shutil.which(p) or os.path.isfile(p))), "yt-dlp")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

AUDIO_CACHE = {}
CACHE_LOCK = threading.Lock()
DURATION_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")

def clean_title(t):
    t = re.sub(r'\s*[\(\[][^)\]]*(?:official|lyrics?|video|audio|visualizer|live|performance|remix|cover|karaoke|instrumental|music|hd|4k|explicit|clean|version|full song)[^)\]]*[\)\]]', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*[-–]\s*(Official|Lyrics?|Video|Audio|Visualizer|Live|Performance|Music Video|Full Song).*', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^\d+\s*(Hours?|Min|Minutes?)\s*(of)?\s*', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\|.*$', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def is_valid_video_id(vid):
    if not vid:
        return False
    bad_prefixes = ('PL', 'OLAK', 'RDCLAK', 'UC', 'UUSH', 'UU')
    for p in bad_prefixes:
        if vid.startswith(p):
            return False
    if len(vid) < 10 or len(vid) > 15:
        return False
    return re.match(r'^[A-Za-z0-9_-]+$', vid) is not None

def yt_search(query, limit=50):
    payload = json.dumps({
        "context": {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.20240717.01.00", "hl": "en"}},
        "query": query
    }).encode()
    data = None
    for key in YT_KEYS:
        try:
            req = urllib.request.Request(
                f"https://music.youtube.com/youtubei/v1/search?key={key}",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": UA, "Origin": "https://music.youtube.com"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            sys.stderr.write(f"Search '{query}' key {key[:12]}...: HTTP {e.code}\n")
            if e.code != 403:
                return []
        except Exception as e:
            sys.stderr.write(f"Search error '{query}': {e}\n")
            return []
    if data is None:
        return []

    results = []
    tabs = data.get("contents", {}).get("tabbedSearchResultsRenderer", {}).get("tabs", [])
    for tab in tabs:
        sections = tab.get("tabRenderer", {}).get("content", {}).get("sectionListRenderer", {}).get("contents", [])
        for section in sections:
            isr = section.get("itemSectionRenderer", {})
            items = isr.get("contents", [])
            if not items:
                items = section.get("musicShelfRenderer", {}).get("contents", [])
            for item in items:
                mr = item.get("musicResponsiveListItemRenderer", {})
                if not mr:
                    continue

                vid = ""
                watch_ep = mr.get("overlay", {}).get("musicItemThumbnailOverlayRenderer", {})
                watch_ep = watch_ep.get("content", {}).get("musicPlayButtonRenderer", {})
                watch_ep = watch_ep.get("playNavigationEndpoint", {})
                if "watchEndpoint" in watch_ep:
                    vid = watch_ep.get("watchEndpoint", {}).get("videoId", "")
                else:
                    continue

                if not is_valid_video_id(vid):
                    continue

                flex = mr.get("flexColumns", [])
                if len(flex) < 2:
                    continue

                title = ""
                artist = ""
                col_type = ""
                duration = ""

                # Extract duration from fixedColumns (YouTube Music often puts length there)
                fixed = mr.get("fixedColumns", [])
                for fc in fixed:
                    runs = fc.get("musicResponsiveListItemFixedColumnRenderer", {}).get("text", {}).get("runs", [])
                    for r in runs:
                        txt = r.get("text","").strip()
                        if DURATION_RE.match(txt):
                            duration = txt

                for fc in flex:
                    runs = fc.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text", {}).get("runs", [])
                    col_texts = []
                    for r in runs:
                        txt = r.get("text","").strip()
                        # capture duration if present in flex
                        if DURATION_RE.match(txt) and not duration:
                            duration = txt
                            continue
                        nav = r.get("navigationEndpoint", {}).get("browseEndpoint", {})
                        ptype = nav.get("browseEndpointContextSupportedConfigs", {}).get(
                            "browseEndpointContextMusicConfig", {}).get("pageType", "")
                        if ptype == "MUSIC_PAGE_TYPE_ARTIST":
                            artist = txt
                        elif re.match(r'^[\d.]+[KMB]?\s+plays?$', txt, re.IGNORECASE):
                            continue
                        elif DURATION_RE.match(txt):
                            continue
                        else:
                            col_texts.append(txt)

                    joined = " ".join(col_texts).strip()
                    if not title:
                        title = joined
                    elif not col_type:
                        col_type = joined

                if not title:
                    continue

                title = clean_title(title)
                if not title or len(title) < 2:
                    continue

                first_word = col_type.split(" ")[0] if col_type else ""
                if first_word in ("Artist", "Profile", "Podcast", "Playlist"):
                    continue

                if first_word == "Video":
                    bad_words = ["mashup", "compilation", "documentary", "review", "reaction",
                                 "analysis", "interview", "behind the scenes", "making of",
                                 "history of", "tragic truth", "rise of", "story of", "explained",
                                 "top 10", "best of", "every song", "all songs", "concert",
                                 "tribute", "parody", "cover by", "remix by", "lyrics video",
                                 "without thinking", "type beat", "instrumental", "karaoke"]
                    title_lower = title.lower()
                    if any(bw in title_lower for bw in bad_words):
                        continue

                art = ""
                thumbs = mr.get("thumbnail", {}).get("musicThumbnailRenderer", {}).get("thumbnail", {}).get("thumbnails", [])
                if thumbs:
                    art = thumbs[-1].get("url", "")
                    art = re.sub(r'=w\d+-h\d+.*', '=w400-h400-l90-rj', art)

                results.append({
                    "videoId": vid,
                    "title": title,
                    "artist": artist if artist else "",
                    "art": art,
                    "duration": duration
                })
                if len(results) >= limit:
                    return results
    return results

def get_audio_info(video_id):
    with CACHE_LOCK:
        if video_id in AUDIO_CACHE:
            url, duration, ts = AUDIO_CACHE[video_id]
            if time.time() - ts < 3600:
                return url, duration
            del AUDIO_CACHE[video_id]

    for fmt in ["bestaudio[ext=m4a]/bestaudio", "bestaudio"]:
        try:
            r = subprocess.run(
                [YTDLP, "--no-download", "--print", "%(url)s|%(duration)s", "-f", fmt,
                 f"https://www.youtube.com/watch?v={video_id}"],
                capture_output=True, text=True, timeout=60
            )
            line = r.stdout.strip().split("\n")[0]
            url, _, dur = line.partition("|")
            duration = ""
            if dur and dur != "None":
                try:
                    duration = int(float(dur))
                except Exception:
                    duration = ""
            if url.startswith("http"):
                with CACHE_LOCK:
                    AUDIO_CACHE[video_id] = (url, duration, time.time())
                return url, duration
        except Exception as e:
            sys.stderr.write(f"yt-dlp error {video_id}: {e}\n")
    return None, None

@app.route("/")
def home():
    return jsonify({
        "name": "Dusty Tape Premium API",
        "version": "2.3.0",
        "premium": True,
        "no_ads": True,
        "endpoints": ["/search?q=...", "/audio/<id>", "/stream/<id>", "/startup", "/health"]
    })

@app.route("/health")
def health():
    with CACHE_LOCK:
        cached = len(AUDIO_CACHE)
    return jsonify({"status": "ok", "cached": cached, "yt_dlp": YTDLP})

@app.route("/search")
def search():
    q = request.args.get("q", "")
    limit = min(int(request.args.get("limit", "50")), 200)
    if not q:
        return jsonify([])
    return jsonify(yt_search(q, limit))

@app.route("/audio/<video_id>")
def audio(video_id):
    url, duration = get_audio_info(video_id)
    if url:
        return jsonify({"url": url, "duration": duration})
    return jsonify({"error": "failed"}), 500

@app.route("/stream/<video_id>")
def stream(video_id):
    url, duration = get_audio_info(video_id)
    if not url:
        return "Not found", 404
    try:
        range_header = request.headers.get("Range", "bytes=0-")
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9", "Range": range_header,
        })
        resp = urllib.request.urlopen(req, timeout=60)
        resp_headers = {
            "Content-Type": resp.headers.get("Content-Type", "audio/mpeg"),
            "Accept-Ranges": "bytes",
        }
        if duration:
            resp_headers["Content-Duration"] = str(duration)
        cl = resp.headers.get("Content-Length")
        if cl: resp_headers["Content-Length"] = cl
        cr = resp.headers.get("Content-Range")
        if cr: resp_headers["Content-Range"] = cr

        def generate():
            try:
                while True:
                    chunk = resp.read(65536)
                    if not chunk: break
                    yield chunk
            finally:
                resp.close()

        return Response(generate(), status=resp.status, headers=resp_headers)
    except Exception as e:
        sys.stderr.write(f"Stream error {video_id}: {e}\n")
        return "Stream error", 500

@app.route("/startup")
def startup():
    queries = [
        ("Today's Top Hits", [
            "espresso sabrina carpenter official audio",
            "die with a smile lady gaga bruno mars official",
            "taste sabrina carpenter official audio",
            "please please please sabrina carpenter",
            "good luck babe chappell roan official",
            "birds of a feather billie eilish",
            "not like us kendrick lamar official",
            "we cant be friends ariana grande",
            "stargazing myles smith",
            "beautiful things benson boone",
            "lovin on me jack harlow",
            "water teddy swims official audio",
            "vampire olivia rodrigo",
            "mILLION DOLLAR BABY tommy richman",
            "houdini dua lipa",
        ]),
        ("Desi Bangers", [
            "ap dhillon brown munde official",
            "karan aujla tauba tauba official",
            "badshah o saathiya official",
            "harrdy sandhu kya baat official",
            "sidhu moosewala so high",
            "diljit dosanjh golem genda",
            "raataan lambiyan jubin nautiyal",
            "kesariya arijit singh",
            "apna bana le arijit singh",
            "tum hi ho arijit singh",
            "chaiya chaiya",
            "manmarziyaan arijit",
        ]),
        ("Pop Rising", [
            "bruno mars apt official",
            "charlie puth light switch",
            "sza snooze official audio",
            "doja cat paint the town red",
            "troye sivan rush",
            "central cee band4band",
            "teddy swims lose control",
            "shakira tgt official audio",
            "raye escape my mind",
            "chris brown under the influence",
        ]),
        ("Chill", [
            "death bed powfu",
            "coffee beabadoobee",
            "let her go passenger",
            "another love tom odell",
            "skinny love bon iver",
            "electric feel mgmt",
            "somewhere only we know keane",
            "yellow coldplay",
        ]),
        ("RapCaviar", [
            "kendrick lamar not like us",
            "drake push ups",
            "travis scott fein",
            "lil baby wants and needs",
            "tyler the creator noid",
            "21 savage bank account",
            "future mask off",
            "migos bad and boujee",
            "pop smoke dior",
            "eminem lustration",
            "kanye west strong enough",
        ]),
        ("Bollywood Butter", [
            "channa mereya arijit singh",
            "gerua dilwale",
            "ae dil hai mushkil arijit",
            "pasoori shae gill",
            "aaj ki raat stree 2",
            "love mohit chauhan",
            "kal ho na ho",
            "kabira yeh jawaani hai deewani",
        ]),
        ("Rock Classics", [
            "bohemian rhapsody queen",
            "hotel california eagles",
            "in the end linkin park official",
            "believer imagine dragons",
            "hey jude beatles",
            "sweet child o mine guns n roses",
            "don't stop fleetwood mac",
            "under the bridge red hot chili peppers",
            "wonderwall oasis",
            "radioactive imagine dragons",
        ]),
        ("Late Night Feels", [
            "someone like you adele",
            "all of me john legend",
            "perfect ed sheeran",
            "thinking out loud ed sheeran",
            "cant help falling in love elvis",
            "a thousand years christina perri",
            "shallow lady gaga",
            "until i found you stephen sanchez",
            "wildest dreams taylor swift",
            "loving strangers jocelyn pook",
        ]),
    ]

    all_results = []
    lock = threading.Lock()

    def fetch(label, q_list):
        for q in q_list:
            try:
                r = yt_search(q, 10)
                with lock:
                    for s in r:
                        s["category"] = label
                        all_results.append(s)
            except Exception:
                pass

    threads = []
    for label, q_list in queries:
        t = threading.Thread(target=fetch, args=(label, q_list))
        t.daemon = True
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Premium: dedup by videoId AND title+artist to fix repeating same song glitch
    seen = set()
    seen_titles = set()
    unique = []
    for s in all_results:
        title_key = (s.get("title","").strip().lower() + "|" + s.get("artist","").strip().lower())
        if s["videoId"] in seen or title_key in seen_titles:
            continue
        seen.add(s["videoId"])
        seen_titles.add(title_key)
        unique.append(s)

    with CACHE_LOCK:
        cache_count = len(AUDIO_CACHE)

    sys.stderr.write(f"Startup: {len(unique)} songs, {cache_count} cached\n")
    return jsonify(unique[:600])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5555"))
    print(f"DustyTape Premium API on http://0.0.0.0:{port} — no ads, global", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True)
