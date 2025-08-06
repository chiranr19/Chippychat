
# ChippyInn Chatbot Proof‑of‑Concept

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
export OPENROUTER_API_KEY=<your key>  # already hard‑coded for demo
python server.py
```

* open `chat-widget.html` in your browser
* type booking questions

## What’s inside

| File | Purpose |
|------|---------|
| `server.py` | Flask backend that auto‑launches Meilisearch, calls Llama‑3 via OpenRouter, queries Meili |
| `chat-widget.html` | Minimal front-end widget |
| `rooms.json` | Sample inventory |
