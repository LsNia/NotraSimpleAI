import hmac
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/session")
ACCESS_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,255}")
COOKIE_NAME = "notra_session"
SESSION_LIFETIME = 7 * 24 * 60 * 60


class Login(BaseModel):
    key: str = Field(repr=False)


def token_hash(request: Request, token: str) -> bytes:
    return hmac.digest(request.app.state.access_key, token.encode(), "sha256")


def authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    return bool(token) and request.app.state.db.has_session(
        token_hash(request, token), int(time.time())
    )


def require_session(request: Request) -> None:
    if not authenticated(request):
        raise HTTPException(401, "Для доступа к переписке введите ключ.")


@router.get("")
def session(request: Request) -> dict[str, bool]:
    return {"authenticated": authenticated(request)}


@router.post("", status_code=204)
def login(body: Login, request: Request, response: Response) -> None:
    if not ACCESS_KEY_PATTERN.fullmatch(body.key):
        raise HTTPException(
            422, "Ключ должен содержать от 8 до 255 разрешённых символов."
        )
    if not secrets.compare_digest(body.key.encode(), request.app.state.access_key):
        raise HTTPException(401, "Неверный ключ доступа.")
    token = secrets.token_urlsafe(32)
    request.app.state.db.add_session(
        token_hash(request, token), int(time.time()), SESSION_LIFETIME
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        path="/api",
        max_age=SESSION_LIFETIME,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )


@router.delete("", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        request.app.state.db.remove_session(token_hash(request, token))
    response.delete_cookie(
        COOKIE_NAME,
        path="/api",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
