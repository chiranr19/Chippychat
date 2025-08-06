"""
Flask backend for ChippyInn booking POC.
Auto-starts Meilisearch, exposes /booking-chat.
"""

import os, re, json, time, subprocess, pathlib, requests, sys
from flask import Flask, request, jsonify
from typing import Optional
from threading import Lock

# ---------------- Config ----------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "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")
MODEL  = "meta-llama/llama-3-8b-instruct"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"

MEILI_PORT = 7700
MEILI  = f"http://127.0.0.1:{MEILI_PORT}"
INDEX  = "rooms"

HDR_OR = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "X-Title": "Chippy Booking Bot",
    "X-Budget": "0"
}
HDR_MEI = {"Content-Type": "application/json"}
BIN_PATH = pathlib.Path("meilisearch")
_lock = Lock()
# -----------------------------------------

def ensure_meili_running():
    url = f"{MEILI}/health"
    try:
        if requests.get(url, timeout=1).json().get("status") == "available":
            return
    except Exception:
        pass
    with _lock:
        try:
            if requests.get(url, timeout=1).json().get("status") == "available":
                return
        except Exception:
            pass
        print("🔄 Starting Meilisearch ...")
        if not BIN_PATH.exists():
            subprocess.run(["curl","-L","https://install.meilisearch.com","-o","install.sh"], check=True)
            subprocess.run(["bash","install.sh"], check=True)
            shutil.move("meilisearch", BIN_PATH)
        subprocess.Popen([str(BIN_PATH), "--http-addr", f"127.0.0.1:{MEILI_PORT}", "--no-analytics"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            if requests.get(url).json().get("status") == "available":
                print("✅ Meilisearch running")
                return
        except Exception:
            time.sleep(1)
    sys.exit("Meilisearch failed to start")

# ---------- Helpers ----------
def extract_json_block(text: str) -> str:
    text = re.sub(r"^```json|```$", "", text.strip(), flags=re.I|re.M)
    m = re.search(r"\{.*\}", text, flags=re.S)
    return m.group(0) if m else "{}"

def ask_llama(query: str, timeout: int = 8) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a booking assistant that returns strict JSON."},
            {"role": "user", "content": f'Return JSON with location, guests, budget_per_night. User: "{query}"'}
        ],
        "temperature": 0.1
    }
    r = requests.post(OR_URL, headers=HDR_OR, json=payload, timeout=timeout)
    r.raise_for_status()
    return json.loads(extract_json_block(r.json()["choices"][0]["message"]["content"]))

def build_filter(d: dict) -> str:
    parts = ["available = true"]
    if d.get("location"):
        parts.append(f'location = "{d["location"]}"')
    if d.get("guests") is not None:
        parts.append(f"guests >= {d['guests']}")
    if d.get("budget_per_night") is not None:
        parts.append(f"price <= {d['budget_per_night']}")
    return " AND ".join(parts)

def search_meili(filter_str: str):
    body = {"q": "*", "filter": filter_str, "limit": 5, "sort": ["price:asc"]}
    r = requests.post(f"{MEILI}/indexes/{INDEX}/search", headers=HDR_MEI, json=body)
    r.raise_for_status()
    return r.json().get("hits", [])

# ---------- Flask ----------
app = Flask(__name__)
sessions = {}

@app.before_first_request
def _init():
    ensure_meili_running()

@app.route("/booking-chat", methods=["POST"])
def booking_chat():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    sid = data.get("sessionId", "anon")
    if not text:
        return jsonify({"reply":"Please say something."})
    sess = sessions.setdefault(sid, {})
    lo = text.lower()
    if "thank" in lo:
        return jsonify({"reply":"You're welcome! 😊"})
    if lo in {"hi","hello","hey"}:
        return jsonify({"reply":"Hi! How can I help with bookings?"})
    try:
        info = ask_llama(text)
    except Exception as e:
        return jsonify({"reply":f"AI error: {e}"})
    for k,v in info.items():
        if v: sess[k]=v
    if "location" not in sess:
        return jsonify({"reply":"Which city?"})
    if "guests" not in sess:
        return jsonify({"reply":"Number of guests?"})
    if "budget_per_night" not in sess:
        return jsonify({"reply":"Any budget per night?"})
    try:
        hits = search_meili(build_filter(sess))
    except Exception as e:
        return jsonify({"reply":f"Search error: {e}"})
    sess.clear()
    if not hits:
        return jsonify({"reply":"No rooms match. Try other criteria."})
    reply = "\n".join([f"• {h['name']} — ₹{h['price']} | {h['location']} | sleeps {h['guests']}" for h in hits])
    return jsonify({"reply":reply})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
