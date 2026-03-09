from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import json
import uuid
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'mtg-draft-secret-change-in-prod')
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)

# ---------------------------------------------------------------------------
# In-memory store  (fine for Railway single-dyno; swap for Redis if needed)
# ---------------------------------------------------------------------------
drafts = {}   # draft_id -> DraftState dict


def make_draft(card_list: list[str], num_players: int = 4) -> dict:
    """
    card_list : list of card names (180 cards)
    Returns a fully-initialised draft state dict.
    """
    random.shuffle(card_list)

    # Build 3 packs of 14 per player -> 12 packs total for 4 players = 168 cards used
    # 180 cards accepted but 12 are randomly excluded (already shuffled above)
    packs_per_player = 3
    pack_size = 14
    total_packs = num_players * packs_per_player  # 12
    cards_needed = total_packs * pack_size        # 168
    card_list = card_list[:cards_needed]          # drop the extra 12 silently

    all_packs = []
    for i in range(total_packs):
        start = i * pack_size
        all_packs.append(card_list[start : start + pack_size])

    # Assign opening packs: player 0 gets pack 0, player 1 gets pack 1, etc.
    # packs[player_index][round_index] = remaining cards in that pack
    player_packs = []
    for p in range(num_players):
        player_packs.append([
            all_packs[p + r * num_players] for r in range(packs_per_player)
        ])

    players = [None] * num_players   # socket-session ids, filled on join

    return {
        "id": str(uuid.uuid4())[:8],
        "num_players": num_players,
        "players": players,           # list of player dicts {sid, name}
        "player_names": [None] * num_players,
        "current_round": 0,           # 0-indexed (0,1,2)
        "picks_this_round": [0] * num_players,   # how many picks each player made
        "picks_per_pack": 2,
        "pack_size": pack_size,
        "player_packs": player_packs, # [player][round] = [card, ...]
        # current_hand[player] = the pack currently in front of that player
        "current_hand": [player_packs[p][0][:] for p in range(num_players)],
        "drafted": [[] for _ in range(num_players)],  # final picks
        "waiting_for": list(range(num_players)),  # players yet to pick this pass
        "round_complete": False,
        "draft_complete": False,
        "created_at": datetime.utcnow().isoformat(),
        "started": False,
        "pass_direction": 1,  # 1 = left (index+1), -1 = right (index-1)
    }


def get_player_index(draft: dict, sid: str) -> int | None:
    for i, p in enumerate(draft["players"]):
        if p and p["sid"] == sid:
            return i
    return None


def pass_packs(draft: dict):
    """Rotate current_hand among players based on pass_direction."""
    n = draft["num_players"]
    d = draft["pass_direction"]
    old_hands = [h[:] for h in draft["current_hand"]]
    for i in range(n):
        receiver = (i + d) % n
        draft["current_hand"][receiver] = old_hands[i]
    draft["waiting_for"] = list(range(n))


def advance_round(draft: dict):
    """Move to next round, reload packs, flip pass direction."""
    draft["current_round"] += 1
    if draft["current_round"] >= 3:
        draft["draft_complete"] = True
        return
    r = draft["current_round"]
    n = draft["num_players"]
    draft["pass_direction"] *= -1
    draft["picks_this_round"] = [0] * n
    draft["current_hand"] = [draft["player_packs"][p][r][:] for p in range(n)]
    draft["waiting_for"] = list(range(n))


def build_pool_text(drafted_cards: list[str]) -> str:
    counts: dict[str, int] = {}
    for c in drafted_cards:
        counts[c] = counts.get(c, 0) + 1
    lines = [f"{qty} {name}" for name, qty in sorted(counts.items())]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create", methods=["POST"])
def create_draft():
    raw = request.form.get("card_list", "")
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]

    # Parse formats: "1x Card Name", "1 x Card Name", "1 Card Name", "Card Name"
    import re
    card_list = []
    for line in lines:
        # Match "NxName", "N x Name", or "N Name" at the start
        m = re.match(r'^(\d+)\s*x\s+(.+)$', line, re.IGNORECASE)
        if not m:
            m = re.match(r'^(\d+)\s+(.+)$', line)
        if m:
            try:
                qty = int(m.group(1))
                name = m.group(2).strip()
                card_list.extend([name] * qty)
                continue
            except ValueError:
                pass
        card_list.append(line)

    if len(card_list) < 168:
        return render_template("index.html", error=f"Need at least 168 cards, got {len(card_list)}.")
    draft = make_draft(card_list, num_players=4)
    draft_id = draft["id"]
    drafts[draft_id] = draft
    return redirect(url_for("lobby", draft_id=draft_id))


@app.route("/draft/<draft_id>/lobby")
def lobby(draft_id):
    draft = drafts.get(draft_id)
    if not draft:
        return "Draft not found", 404
    return render_template("lobby.html", draft_id=draft_id)


@app.route("/draft/<draft_id>/play")
def play(draft_id):
    draft = drafts.get(draft_id)
    if not draft:
        return "Draft not found", 404
    return render_template("draft.html", draft_id=draft_id)


@app.route("/draft/<draft_id>/results")
def results(draft_id):
    draft = drafts.get(draft_id)
    if not draft:
        return "Draft not found", 404
    pools = []
    for i, name in enumerate(draft["player_names"]):
        pools.append({
            "name": name or f"Player {i+1}",
            "pool": build_pool_text(draft["drafted"][i]),
            "cards": sorted(draft["drafted"][i]),
        })
    return render_template("results.html", draft_id=draft_id, pools=pools)


# ---------------------------------------------------------------------------
# SocketIO Events
# ---------------------------------------------------------------------------

@socketio.on("join_lobby")
def on_join_lobby(data):
    draft_id = data["draft_id"]
    player_name = data["name"].strip() or "Player"
    draft = drafts.get(draft_id)
    if not draft:
        emit("error", {"msg": "Draft not found"})
        return

    sid = request.sid
    join_room(draft_id)

    # Assign to first empty slot
    assigned = False
    for i, p in enumerate(draft["players"]):
        if p is None:
            draft["players"][i] = {"sid": sid, "name": player_name}
            draft["player_names"][i] = player_name
            assigned = True
            player_index = i
            break

    if not assigned:
        emit("error", {"msg": "Draft is full"})
        return

    # Broadcast updated lobby
    lobby_state = {
        "players": [p["name"] if p else None for p in draft["players"]],
        "player_index": player_index,
        "all_joined": all(p is not None for p in draft["players"]),
    }
    emit("lobby_update", lobby_state, to=draft_id)
    emit("your_index", {"index": player_index})


@socketio.on("start_draft")
def on_start_draft(data):
    draft_id = data["draft_id"]
    draft = drafts.get(draft_id)
    if not draft:
        return
    if not all(p is not None for p in draft["players"]):
        emit("error", {"msg": "Not all players have joined yet"})
        return
    draft["started"] = True
    socketio.emit("draft_started", {"draft_id": draft_id}, to=draft_id)
    # Push initial hands
    _push_hands(draft)


@socketio.on("join_draft")
def on_join_draft(data):
    draft_id = data["draft_id"]
    draft = drafts.get(draft_id)
    if not draft:
        emit("error", {"msg": "Draft not found"})
        return
    sid = request.sid
    join_room(draft_id)

    # Accept seat index passed from the play page (survives page reload)
    idx = data.get("player_index")
    if idx is None:
        emit("error", {"msg": "Missing player_index"})
        return
    idx = int(idx)
    if idx < 0 or idx >= draft["num_players"] or draft["players"][idx] is None:
        emit("error", {"msg": "Invalid seat"})
        return

    # Update sid so the new connection maps to the right seat
    draft["players"][idx]["sid"] = sid
    _push_hand_to(draft, idx)


@socketio.on("submit_picks")
def on_submit_picks(data):
    draft_id = data["draft_id"]
    picks = data["picks"]   # list of 2 card names
    draft = drafts.get(draft_id)
    if not draft or draft["draft_complete"]:
        return

    sid = request.sid
    idx = get_player_index(draft, sid)
    if idx is None:
        return

    if idx not in draft["waiting_for"]:
        emit("error", {"msg": "Not your turn to pick"})
        return

    if len(picks) != draft["picks_per_pack"]:
        emit("error", {"msg": f"Pick exactly {draft['picks_per_pack']} cards"})
        return

    # Validate picks are actually in the hand
    hand = draft["current_hand"][idx][:]
    for card in picks:
        if card not in hand:
            emit("error", {"msg": f"Card '{card}' not in your hand"})
            return
        hand.remove(card)

    # Apply picks
    draft["drafted"][idx].extend(picks)
    draft["current_hand"][idx] = hand
    draft["picks_this_round"][idx] += len(picks)
    draft["waiting_for"].remove(idx)

    emit("picks_accepted", {"picked": picks})

    # Check if everyone has picked
    if len(draft["waiting_for"]) == 0:
        if len(draft["current_hand"][0]) == 0:
            # Pack exhausted → next round
            advance_round(draft)
            if draft["draft_complete"]:
                socketio.emit("draft_complete", {"draft_id": draft_id}, to=draft_id)
                return
            socketio.emit("round_change", {"round": draft["current_round"] + 1}, to=draft_id)
        else:
            pass_packs(draft)
        _push_hands(draft)
    else:
        # Tell this player to wait; tell others how many still waiting
        emit("waiting", {"waiting_count": len(draft["waiting_for"])})
        socketio.emit(
            "picks_progress",
            {"remaining": len(draft["waiting_for"]), "total": draft["num_players"]},
            to=draft_id,
        )


def _push_hands(draft: dict):
    """Send each player their current hand."""
    for i, player in enumerate(draft["players"]):
        if player:
            _push_hand_to(draft, i)


def _push_hand_to(draft: dict, idx: int):
    player = draft["players"][idx]
    if not player:
        return
    hand_data = {
        "hand": draft["current_hand"][idx],
        "drafted": draft["drafted"][idx],
        "round": draft["current_round"] + 1,
        "pack_size": len(draft["current_hand"][idx]),
        "picks_needed": draft["picks_per_pack"],
        "player_index": idx,
        "player_name": draft["player_names"][idx],
    }
    socketio.emit("hand_update", hand_data, to=player["sid"])


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
