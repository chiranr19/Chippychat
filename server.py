"""
server.py – ChippyInn Booking Bot
 • Keeps per-session memory (no more repetitive questions)
 • Parses natural-language date ranges (“Aug 20 to 23”, “next weekend”)
"""

# ───────── imports ───────────────────────────────────────────────────
import os, re, json, requests, uuid
from datetime import datetime
from dateutil import tz
import dateparser
from flask import Flask, request, jsonify, send_from_directory

# ───────── config ────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or "YOUR_OPENROUTER_API_KEY"
MODEL  = "meta-llama/llama-3-8b-instruct"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
HDR_OR = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type":  "application/json",
    "X-Title":       "Chippy Booking Bot",
    "X-Budget":      "0"
}

MEILI_PORT = 7700
MEILI_URL  = f"http://127.0.0.1:{MEILI_PORT}"
INDEX      = "rooms"
BIN_PATH   = "meilisearch"
HDR_MEI    = {"Content-Type": "application/json"}

# ────────── Meilisearch helpers (unchanged) ──────────────────────────
def ensure_meili_running():
    """Use Meilisearch only if it is already reachable.

    Raises if Meilisearch isn't up, so the caller can fall back to the built-in
    in-memory search. To use Meilisearch, start it yourself first, e.g.:
        docker run -p 7700:7700 getmeili/meilisearch
    """
    resp = requests.get(f"{MEILI_URL}/health", timeout=1)
    if resp.json().get("status") == "available":
        print("Meilisearch is available."); return
    raise RuntimeError("Meilisearch health check did not report 'available'")

def upload_rooms_if_needed():
    if requests.get(f"{MEILI_URL}/indexes/{INDEX}").status_code == 200:
        stats = requests.get(f"{MEILI_URL}/indexes/{INDEX}/stats").json()
        if stats.get("numberOfDocuments",0) > 0: return

    print("Indexing rooms …")
    requests.put(f"{MEILI_URL}/indexes/{INDEX}")
    filterables = ["location","price","guests","available","type","bedrooms"]
    requests.put(f"{MEILI_URL}/indexes/{INDEX}/settings/filterable-attributes",
                 headers=HDR_MEI, data=json.dumps(filterables))
    requests.put(f"{MEILI_URL}/indexes/{INDEX}/settings/sortable-attributes",
                 headers=HDR_MEI, data=json.dumps(["price"]))

    with open("rooms.json", encoding="utf-8") as f:
        docs = f.read()
    r = requests.post(f"{MEILI_URL}/indexes/{INDEX}/documents", headers=HDR_MEI, data=docs)
    if r.status_code not in (200,202):
        raise RuntimeError("Rooms upload failed:", r.text)
    print("rooms.json indexed.")

# ────────── LLM helper ───────────────────────────────────────────────
def extract_json(text):
    text = re.sub(r"^```json|```$", "", text.strip(), flags=re.I|re.M)
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else "{}"

def ask_llama(query):
    prompt = (
      "Return ONLY JSON keys: location, check_in, check_out, guests, "
      "budget_per_night, preferences.\n"
      "Dates like 'next Monday' or 'tomorrow' → YYYY-MM-DD.\n"
      "Numbers in words → digits.\n"
      "Unknown → null. No extra text.\n\n"
      f"User: \"{query}\""
    )
    payload = {
        "model": MODEL,
        "messages":[
            {"role":"system","content":"You are a booking assistant that outputs strict JSON."},
            {"role":"user","content": prompt}
        ],
        "temperature": 0.2
    }
    res = requests.post(OR_URL, headers=HDR_OR, json=payload, timeout=8)
    res.raise_for_status()
    return json.loads(extract_json(res.json()["choices"][0]["message"]["content"]))

# ────────── heuristics ───────────────────────────────────────────────
CITY_LIST = ["chennai","coimbatore","madurai","salem","tirunelveli"]
WORD_NUM  = {
    "one":1,"two":2,"three":3,"four":4,"five":5,
    "six":6,"seven":7,"eight":8,"nine":9,"ten":10,
    "couple":2,"parents":2,"family":4,"brother":1,"sister":1
}

def parse_date_phrase(txt, base=None):
    s = {"PREFER_DATES_FROM":"future"}
    if base: s["RELATIVE_BASE"]=base
    d = dateparser.parse(txt, settings=s)
    return d.astimezone(tz.UTC).strftime("%Y-%m-%d") if d else None

def split_date_range(raw):
    parts = re.split(r"\s*(?:to|-|–|until|till)\s*", raw, 1, flags=re.I)
    if len(parts) == 2:
        start_txt, end_txt = parts
        start = parse_date_phrase(start_txt)
        end   = parse_date_phrase(end_txt)
        # if end lacked month/year, borrow from start
        if start and not re.search(r"[a-z]", end_txt, re.I):
            try_mixed = dateparser.parse(end_txt + " " + start_txt, settings={"PREFER_DATES_FROM":"future"})
            if try_mixed:
                end = try_mixed.astimezone(tz.UTC).strftime("%Y-%m-%d")
        return start, end
    single = parse_date_phrase(raw)
    return single, None

def heuristic_city(txt):
    t = txt.lower()
    for c in CITY_LIST:
        if c in t: return c.capitalize()
    return None

def heuristic_guests(txt):
    if m := re.search(r"(\d{1,2})\s*(guest|people|pax)", txt, re.I):
        return int(m.group(1))
    if txt.strip().isdigit(): return int(txt.strip())
    for w,n in WORD_NUM.items():
        if re.search(fr"\b{w}\b", txt, re.I): return n
    return None

# ────────── filter + search ─────────────────────────────────────────
def build_filter(info):
    f = ["available = true"]
    if info.get("location"): f.append(f'location = "{info["location"]}"')
    if info.get("guests"):   f.append(f"guests >= {info['guests']}")
    if info.get("budget_per_night"): f.append(f"price <= {info['budget_per_night']}")
    return " AND ".join(f)

def search_meili(filt, limit=5):
    body = {"q":"*","filter":filt,"limit":limit,"sort":["price:asc"]}
    r = requests.post(f"{MEILI_URL}/indexes/{INDEX}/search", headers=HDR_MEI, json=body)
    r.raise_for_status()
    return r.json()["hits"]

# ────────── in-memory fallback (no Meilisearch required) ─────────────
with open("rooms.json", encoding="utf-8") as _f:
    ROOMS = json.load(_f)

USE_MEILI = False   # decided at startup in __main__

def search_local(info, limit=5):
    """Filter rooms.json in memory — same criteria as the Meili filter."""
    results = [r for r in ROOMS if r.get("available", True)]
    if info.get("location"):
        loc = info["location"].lower()
        results = [r for r in results if r.get("location", "").lower() == loc]
    if info.get("guests"):
        results = [r for r in results if r.get("guests", 0) >= info["guests"]]
    if info.get("budget_per_night"):
        results = [r for r in results if r.get("price", 0) <= info["budget_per_night"]]
    results.sort(key=lambda r: r.get("price", 0))
    return results[:limit]

def search_rooms(info):
    """Dispatch to Meilisearch when available, else the in-memory fallback."""
    if USE_MEILI:
        try:
            return search_meili(build_filter(info))
        except Exception as exc:
            print(f"[WARN] Meilisearch query failed ({exc}); using in-memory search.")
    return search_local(info)

# ────────── Flask app & sessions ─────────────────────────────────────
app = Flask(__name__)
sessions = {}        # sid -> dict of filled slots

@app.route("/")
def home():
    return send_from_directory("static", "index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    sid  = data.get("sessionId") or str(uuid.uuid4())
    user = data.get("message","").strip()

    sess = sessions.setdefault(sid, {})

    # ---- small talk ----
    if rep := small_talk_reply(user):
        return jsonify({"reply":rep, "sid":sid})

    # ---- LLM extraction ----
    parsed = ask_llama(user)

    # ---- merge into session ----
    for k,v in parsed.items():
        if v: sess[k] = v

    # ---- heuristics for still-missing ----
    if "location" not in sess:
        c = heuristic_city(user)
        if c: sess["location"] = c
    if "guests" not in sess:
        g = heuristic_guests(user)
        if g: sess["guests"] = g
    if not sess.get("check_in") or not sess.get("check_out"):
        start,end = split_date_range(user)
        if start and end:
            sess["check_in"], sess["check_out"] = start,end

    # ---- ask follow-up ----
    if "location" not in sess:
        return jsonify({"reply":"Which city are you interested in?", "sid":sid})
    if not sess.get("check_in") or not sess.get("check_out"):
        return jsonify({"reply":"What dates will you be staying (check-in and check-out)?", "sid":sid})
    if "guests" not in sess:
        return jsonify({"reply":"How many guests?", "sid":sid})

    # ---- search & reply ----
    hits = search_rooms(sess)
    if not hits:
        sess.clear()
        return jsonify({"reply":"No rooms found. Try another budget or city.", "sid":sid})

    listing = "\n".join(f"• {h['name']} — ₹{h['price']} | {h['location']} | sleeps {h['guests']}"
                        for h in hits)
    sess.clear()               # reset after success
    return jsonify({"reply":listing, "sid":sid})

# ────────── main ────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        ensure_meili_running()
        upload_rooms_if_needed()
        USE_MEILI = True
        print("🔎 Search backend: Meilisearch")
    except Exception as exc:
        USE_MEILI = False
        print(f"🔎 Search backend: in-memory over rooms.json (Meilisearch unavailable: {exc})")
    app.run(host="0.0.0.0", port=5000)
