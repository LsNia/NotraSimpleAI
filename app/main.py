import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth
from .gemini import Gemini, GeminiError
from .storage import Database, Turn

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    key = os.getenv("ACCESS_KEY", "12345678")
    if not auth.ACCESS_KEY_PATTERN.fullmatch(key):
        raise RuntimeError(
            "Задайте ACCESS_KEY из 8–255 латинских букв, цифр, дефисов или подчёркиваний."
        )
    app.state.access_key = key.encode()
    database_path = os.getenv("DATABASE_PATH") or ROOT.parent / "data" / "pomol.sqlite3"
    app.state.db = Database(Path(database_path))
    with httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        timeout=httpx.Timeout(45, connect=5),
    ) as client:
        app.state.gemini = Gemini(
            client,
            model=os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite",
            api_key=os.getenv("GEMINI_API_KEY", ""),
        )
        yield


app = FastAPI(title="NOTRA", lifespan=lifespan, openapi_url=None)
app.include_router(auth.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class Question(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    request_id: UUID = Field(default_factory=uuid4)


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError):
    detail = "Проверьте данные для входа."
    if request.url.path == "/api/chat":
        detail = "Проверьте сообщение. Допустимая длина — от 1 до 500 символов."
    return JSONResponse({"detail": detail}, status_code=422)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/chat", dependencies=[Depends(auth.require_session)])
def history(request: Request) -> list[Turn]:
    return request.app.state.db.history()


@app.post("/api/chat", dependencies=[Depends(auth.require_session)])
def send_message(body: Question, request: Request) -> Turn:
    question = body.text.strip()
    if not question:
        raise HTTPException(422, "Введите сообщение.")
    db = request.app.state.db
    request_id = str(body.request_id)
    turn = db.find_turn(request_id)
    if turn is None:
        asked_at = datetime.now(UTC).isoformat()
        try:
            answer = request.app.state.gemini.reply(question, db.history(limit=6))
        except GeminiError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        answered_at = datetime.now(UTC).isoformat()
        turn = db.save_turn(Turn(request_id, question, asked_at, answer, answered_at))
    if turn.question != question:
        raise HTTPException(409, "С этим идентификатором уже сохранён другой вопрос.")
    return turn
