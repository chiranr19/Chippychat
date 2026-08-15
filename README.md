# ChippyInn Booking Bot

A conversational hotel-room booking assistant. It parses natural-language
requests ("a room in Chennai for 2 guests, Aug 20 to 23, under ₹1500"), keeps
per-session memory so it doesn't re-ask what you've already answered, and returns
matching rooms.

- **LLM slot-filling** via Llama-3 (through [OpenRouter](https://openrouter.ai)) to
  extract location, dates, guests, and budget from free text.
- **Heuristic fallbacks** for city, guest count, and date-range parsing so it stays
  useful even when the model is unsure.
- **Search** over the room inventory — uses [Meilisearch](https://www.meilisearch.com/)
  when it's running, and otherwise falls back to a built-in in-memory filter over
  `rooms.json` (so it runs from a clean clone with zero extra infrastructure).

## Run

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

export OPENROUTER_API_KEY=<your key>              # Windows: set OPENROUTER_API_KEY=<your key>
python server.py
```

Then open <http://localhost:5000> (or `static/index.html`) and start chatting.

Get an OpenRouter key at <https://openrouter.ai/keys>. The key is read from the
`OPENROUTER_API_KEY` environment variable — it is never hard-coded or committed.

### Optional: Meilisearch backend

The in-memory search is enabled by default. To use Meilisearch instead, start it
before launching the server:

```bash
docker run -p 7700:7700 getmeili/meilisearch
```

On startup the server prints which backend it selected.

## What's inside

| File | Purpose |
|------|---------|
| `server.py` | Flask backend: LLM extraction, session memory, room search (Meili or in-memory) |
| `rooms.json` | Sample room inventory |
| `static/` | Minimal chat front-end (`index.html`, `chat-widget.html`, `script.js`) |

## License

MIT
