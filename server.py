"""
server.py – ChippyInn Booking Bot (LLM-driven, mandatory slots)
"""

# ───────────────── imports ───────────────────────────────────────────
import os, re, json, time, subprocess, pathlib, shutil, sys
from threading import Lock
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ───────────────── configuration ─────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    sys.exit("Set OPENROUTER_API_KEY env-var first.")

MODEL  = "meta-llama/llama-3-8b-instruct"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"

MEILI_PORT = 7700
MEILI      = f"http://127.0.0.1:{MEILI_PORT}"
INDEX      = "rooms"

BIN_PATH        = pathlib.Path("meilisearch")
ROOMS_JSON_PATH = r"C:\Users\chira\OneDrive\Desktop\Chippychatty\rooms.json"

HDR_OR  = {"Authorization": f"Bearer {OPENROUTER_API_KEY}",
           "Content-Type":  "application/json"}
HDR_MEI = {"Content-Type": "application/json"}

FILTERABLE = ["price","guests","location","amenities","smoking","pets"]
SORTABLE   = ["price"]
_lock = Lock()

# ───────────────── load rooms.json for prompt context ────────────────
with open(ROOMS_JSON_PATH, encoding="utf-8") as f:
    rooms = json.load(f)
CITIES    = sorted({r["location"] for r in rooms})
AMENITIES = sorted({a for r in rooms for a in r.get("amenities", [])})
PRICE_MIN = min(r["price"] for r in rooms)
PRICE_MAX = max(r["price"] for r in rooms)
GUEST_MAX = max(r["guests"] for r in rooms)

PROMPT_CONTEXT = (
    f"Valid cities: {', '.join(CITIES)}.\n"
    f"Price range: ₹{PRICE_MIN} – ₹{PRICE_MAX}.\n"
    f"Max guests per room: {GUEST_MAX}.\n"
    f"Amenities: {', '.join(AMENITIES)}.\n"
)

SYSTEM_PROMPT = f"""
You are **ChippyInn Booking Assistant**.

Respond ONLY with minified JSON and NO other text.

Shapes:
1. Ask user → {{"action":"ask","question":"…?"}}
2. Search   → {{"action":"search",
                 "location":<city>,            # REQUIRED
                 "check_in":"YYYY-MM-DD",       # REQUIRED
                 "check_out":"YYYY-MM-DD",      # REQUIRED
                 "guests":<int>,                # REQUIRED
                 "budget_per_night":<int|null>, # optional
                 "preferences":<list|null>}}    # optional

If any REQUIRED field is null or missing, return 'ask'.

{PROMPT_CONTEXT}
"""

# ───────────────── Meilisearch bootstrap ─────────────────────────────
def _wait(task_uid, timeout=20):
    for _ in range(timeout*2):
        if requests.get(f"{MEILI}/tasks/{task_uid}").json()["status"] in {"succeeded","failed"}:
            return
        time.sleep(0.5)
    raise TimeoutError("Meili task timeout")

def _start_meili():
    if requests.get(f"{MEILI}/health").status_code == 200:
        return
    with _lock:
        if requests.get(f"{MEILI}/health").status_code == 200:
            return
        print("🔄  Starting Meilisearch …")
        if not BIN_PATH.exists():
            subprocess.run(["curl","-L","https://install.meilisearch.com","-o","install.sh"],
                           check=True, stdout=subprocess.PIPE)
            subprocess.run(["bash","install.sh"], check=True)
            shutil.move("meilisearch", BIN_PATH)
        subprocess.Popen([str(BIN_PATH),
                          "--http-addr", f"127.0.0.1:{MEILI_PORT}",
                          "--no-analytics"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(25):
        try:
            if requests.get(f"{MEILI}/health").json()["status"] == "available":
                print("✅  Meilisearch running")
                break
        except: time.sleep(1)
    else: sys.exit("Meilisearch did not start.")

def _create_index():
    if requests.get(f"{MEILI}/indexes/{INDEX}").status_code == 200:
        return
    t=requests.post(f"{MEILI}/indexes",headers=HDR_MEI,json={"uid":INDEX}).json()["taskUid"]; _wait(t)
    t=requests.post(f"{MEILI}/indexes/{INDEX}/documents",headers=HDR_MEI,json=rooms).json()["taskUid"]; _wait(t)

def _apply_settings():
    for path,data in (("filterable-attributes",FILTERABLE),
                      ("sortable-attributes",  SORTABLE)):
        r=requests.put(f"{MEILI}/indexes/{INDEX}/settings/{path}",
                       headers=HDR_MEI, data=json.dumps(data))
        if r.status_code in (200,202): _wait(r.json()["taskUid"])

def ensure_meili():
    _start_meili(); _create_index(); _apply_settings()

def _patch_settings(err):
    miss=re.findall(r'`([^`]+)`', err.get("message",""))
    if not miss: return False
    if err["code"]=="invalid_search_filter":
        FILTERABLE.extend(miss)
    elif err["code"]=="invalid_search_sort":
        SORTABLE.extend(miss)
    else:
        return False
    _apply_settings(); return True

def search_meili(filt):
    body={"q":"*","limit":5,"sort":["price:asc"],"filter":filt or ""}
    for _ in range(2):
        r=requests.post(f"{MEILI}/indexes/{INDEX}/search",headers=HDR_MEI,json=body)
        if r.status_code==200 and "hits" in r.json(): return r.json()["hits"]
        if r.status_code==400 and _patch_settings(r.json()): continue
        return None
    return None

# ───────────────── LLM wrapper ───────────────────────────────────────
def call_llm(history):
    payload={"model":MODEL,
             "messages":[{"role":"system","content":SYSTEM_PROMPT}]+history,
             "temperature":0.3}
    r=requests.post(OR_URL,headers=HDR_OR,json=payload); r.raise_for_status()
    try:
        resp=json.loads(r.json()["choices"][0]["message"]["content"].strip("`"))
    except json.JSONDecodeError:
        return {"action":"ask","question":"Sorry, could you rephrase that?"}

    # guardrail: if mandatory slots missing, convert to ask
    if resp.get("action")=="search":
        required=("location","check_in","check_out","guests")
        if not all(resp.get(k) for k in required):
            resp={"action":"ask","question":"I need city, dates, and guest count first."}
    return resp

# ───────────────── Flask app ─────────────────────────────────────────
app=Flask(__name__)
CORS(app)
sessions={}

@app.route("/")
def home():
    return send_from_directory("static","chat-widget.html")

@app.route("/booking-chat", methods=["POST"])
def booking_chat():
    data=request.get_json(force=True)
    user_msg=data.get("text","").strip()
    sid=data.get("sessionId","anon")
    if not user_msg:
        return jsonify({"reply":"Please type something."})

    hist=sessions.setdefault(sid,[])
    hist.append({"role":"user","content":user_msg})
    llm=call_llm(hist)

    # 1) Ask follow-up
    if llm.get("action")=="ask":
        q=llm.get("question","Could you clarify?")
        hist.append({"role":"assistant","content":q})
        return jsonify({"reply":q})

    # 2) Search
    if llm.get("action")=="search":
        flt=[f'location = "{llm["location"]}"',
             f'guests >= {llm["guests"]}']
        if llm.get("budget_per_night"):
            flt.append(f'price <= {llm["budget_per_night"]}')
        hits=search_meili(" AND ".join(flt))
        if not hits:
            msg="No rooms match – try a different budget or city."
        else:
            msg="\n".join(f"• {h['name']} — ₹{h['price']} | {h['location']} | sleeps {h['guests']}" for h in hits)
        hist.append({"role":"assistant","content":msg})
        return jsonify({"reply":msg})

    # fallback
    hist.append({"role":"assistant","content":"Sorry, something went wrong."})
    return jsonify({"reply":"Sorry, something went wrong."})

# ───────────────── main ─────────────────────────────────────────────
if __name__=="__main__":
    ensure_meili()
    app.run(host="0.0.0.0", port=8000)
