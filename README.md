# MTG Pick-2 Draft App

A real-time web app for running a 4-player pick-2 Magic: The Gathering draft.

## How It Works

- 180 cards split into 12 packs of 15 (3 packs per player)
- Each player picks **2 cards** from their pack, then passes it
- **Round 1**: packs pass left → **Round 2**: packs pass right → **Round 3**: packs pass left
- Each player ends up with **30 cards**
- Card images loaded live from the Scryfall API (free, no key needed)

---

## Local Development

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the app
```bash
python app.py
```

### 3. Open in browser
```
http://localhost:5000
```

---

## Card List Format

Paste your 180-card pool in either format (or mixed):

```
Lightning Bolt
2 x Counterspell
1 x Llanowar Elves
Shock
```

The app accepts plain names or `N x Card Name` notation.

---

## Deploy to Railway

### Option A: GitHub (recommended)

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Select your repo — Railway auto-detects the config
4. Add environment variable: `SECRET_KEY` → any random string
5. Click Deploy — you'll get a public URL in ~2 minutes

### Option B: Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask session secret (any random string) |
| `PORT` | Auto-set | Railway sets this automatically |

---

## Project Structure

```
mtg-draft/
├── app.py              # Flask app + SocketIO events + draft logic
├── requirements.txt    # Python dependencies
├── Procfile            # Process definition for Railway/Heroku
├── railway.json        # Railway deployment config
└── templates/
    ├── index.html      # Home page — paste card list, create draft
    ├── lobby.html      # Waiting room — share link, players join
    ├── draft.html      # Main draft interface with card images
    └── results.html    # Final pools for all players
```

---

## Notes

- **Single-dyno friendly**: all state is in-memory. Drafts are lost on restart.
  If you want persistence, replace the `drafts` dict in `app.py` with Redis.
- **Scryfall rate limit**: images are fetched client-side (browser to Scryfall directly),
  so the server has no rate-limit concerns. Unknown card names show a text fallback.
- **Reconnection**: if a player's browser refreshes mid-draft, they rejoin and their
  current hand is re-sent automatically.
