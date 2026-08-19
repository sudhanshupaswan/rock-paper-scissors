from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import os
import random
import string

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


app = FastAPI(title="LAN Rock Paper Scissors")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

games: dict[str, dict] = {}
VALID_PLAYERS = {"player1", "player2"}
VALID_CHOICES = {"rock", "paper", "scissors"}


def generate_room_code() -> str:
    while True:
        room_code = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=6)
        )
        if room_code not in games:
            return room_code


def winner_for(choice1: str, choice2: str) -> str:
    if choice1 == choice2:
        return "draw"

    winners = {
        "rock": "scissors",
        "paper": "rock",
        "scissors": "paper",
    }
    return "player1" if winners[choice1] == choice2 else "player2"


def current_players(game: dict) -> dict:
    return {
        player_id: {"name": player["name"]}
        for player_id, player in game["players"].items()
    }


async def send_json(player: dict, payload: dict) -> bool:
    websocket = player.get("websocket")
    if websocket is None:
        return False

    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        player["websocket"] = None
        return False


async def broadcast(room_code: str, payload: dict) -> None:
    game = games.get(room_code)
    if not game:
        return

    for player in game["players"].values():
        await send_json(player, payload)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/api/create-room")
async def create_room():
    room_code = generate_room_code()
    games[room_code] = {
        "players": {},
        "scores": {"player1": 0, "player2": 0},
        "round": 1,
    }
    return {"room_code": room_code}


@app.get("/api/room/{room_code}")
async def get_room(room_code: str):
    game = games.get(room_code.upper())
    if not game:
        return {"exists": False}

    return {
        "exists": True,
        "full": len(game["players"]) >= 2,
        "players": current_players(game),
    }


@app.websocket("/ws/{room_code}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, player_id: str):
    await websocket.accept()

    room_code = room_code.upper()
    game = games.get(room_code)
    if game is None:
        await websocket.send_json({"type": "error", "message": "Room does not exist."})
        await websocket.close()
        return

    if player_id not in VALID_PLAYERS:
        await websocket.send_json({"type": "error", "message": "Invalid player."})
        await websocket.close()
        return

    if player_id not in game["players"] and len(game["players"]) >= 2:
        await websocket.send_json({"type": "error", "message": "Room is full."})
        await websocket.close()
        return

    player = game["players"].setdefault(
        player_id,
        {"name": player_id.title(), "choice": None, "websocket": None},
    )
    player["websocket"] = websocket

    try:
        join_message = await websocket.receive_json()
    except Exception:
        player["websocket"] = None
        return

    if join_message.get("type") == "join":
        name = str(join_message.get("name", "Player")).strip() or "Player"
        player["name"] = name[:20]

    await send_json(
        player,
        {
            "type": "connected",
            "player_id": player_id,
            "room_code": room_code,
            "players": current_players(game),
            "scores": game["scores"],
            "round": game["round"],
        },
    )
    await broadcast(
        room_code,
        {"type": "players", "players": current_players(game), "scores": game["scores"], "round": game["round"]},
    )

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")

            if message_type == "choice":
                choice = message.get("choice")
                if choice not in VALID_CHOICES:
                    continue
                if player["choice"] is not None:
                    continue

                player["choice"] = choice
                await broadcast(room_code, {"type": "choice_made", "player": player_id})

                player1 = game["players"].get("player1")
                player2 = game["players"].get("player2")
                if not player1 or not player2:
                    continue
                if not player1["choice"] or not player2["choice"]:
                    continue

                winner = winner_for(player1["choice"], player2["choice"])
                if winner in VALID_PLAYERS:
                    game["scores"][winner] += 1

                await broadcast(
                    room_code,
                    {
                        "type": "result",
                        "player1_choice": player1["choice"],
                        "player2_choice": player2["choice"],
                        "winner": winner,
                        "scores": game["scores"],
                        "round": game["round"],
                    },
                )

                player1["choice"] = None
                player2["choice"] = None
                game["round"] += 1

            elif message_type == "reset":
                game["scores"] = {"player1": 0, "player2": 0}
                game["round"] = 1
                for game_player in game["players"].values():
                    game_player["choice"] = None

                await broadcast(
                    room_code,
                    {"type": "reset", "scores": game["scores"], "round": game["round"]},
                )

    except WebSocketDisconnect:
        player["websocket"] = None
        player["choice"] = None
        await broadcast(room_code, {"type": "player_disconnected", "player": player_id})


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=False)