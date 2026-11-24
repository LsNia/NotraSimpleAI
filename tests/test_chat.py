import asyncio
import json
import os
import secrets
import tempfile
import unittest
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.auth import COOKIE_NAME
from app.main import app


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.key = secrets.token_urlsafe(18)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "ACCESS_KEY": self.key,
                    "GEMINI_API_KEY": "test-provider-key",
                    "DATABASE_PATH": str(Path(directory) / "chat.sqlite3"),
                },
            )
        )
        self.stack = AsyncExitStack()
        self.addAsyncCleanup(self.stack.aclose)
        self.requests = []
        await self.start_app()

    async def start_app(self):
        await self.stack.enter_async_context(app.router.lifespan_context(app))
        self.client = await self.stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://side-b.test"
            )
        )
        app.state.gemini.client = self.stack.enter_context(
            httpx.Client(
                transport=httpx.MockTransport(self.provider),
                base_url="https://generativelanguage.googleapis.com/v1beta/",
            )
        )

    def provider(self, request):
        self.requests.append(request)
        parts = [
            {"thought": True, "text": "служебный текст"},
            {"text": "Начните с альбома In Rainbows."},
        ]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}]})

    async def login(self):
        response = await self.client.post("/api/session", json={"key": self.key})
        self.assertEqual(response.status_code, 204)
        return response

    async def test_session_protects_history_and_send(self):
        self.assertEqual((await self.client.get("/api/chat")).status_code, 401)
        self.assertEqual(
            (await self.client.post("/api/chat", json={"text": "Джаз?"})).status_code,
            401,
        )
        response = await self.login()
        cookie = response.headers["set-cookie"].lower()
        for attribute in ("httponly", "samesite=strict", "secure", "path=/api"):
            self.assertIn(attribute, cookie)
        self.assertNotIn(self.key, response.headers["set-cookie"])
        token = self.client.cookies[COOKIE_NAME]
        self.assertTrue((await self.client.get("/api/session")).json()["authenticated"])
        self.assertEqual((await self.client.delete("/api/session")).status_code, 204)
        self.client.cookies.set(COOKIE_NAME, token, domain="side-b.test", path="/api")
        self.assertEqual((await self.client.get("/api/chat")).status_code, 401)

    async def test_access_key_alphabet_and_length(self):
        for key in ("", "a" * 7, "a" * 256, "абвгдежз", "bad key!", "good-key\n"):
            with self.subTest(key=key):
                response = await self.client.post("/api/session", json={"key": key})
                self.assertEqual(response.status_code, 422)
        for key in ("Aa0_b-CD", "a" * 255):
            self.assertEqual(
                (await self.client.post("/api/session", json={"key": key})).status_code,
                401,
            )
        response = await self.client.post("/api/session", json={"key": [self.key]})
        self.assertNotIn(self.key, response.text)

    async def test_message_boundaries(self):
        await self.login()
        for text in ("", " \t\n ", "я" * 501, " " * 500 + "я"):
            with self.subTest(length=len(text)):
                self.assertEqual(
                    (
                        await self.client.post("/api/chat", json={"text": text})
                    ).status_code,
                    422,
                )
        self.assertEqual(self.requests, [])
        for text in ("я", "🎵" * 500):
            response = await self.client.post("/api/chat", json={"text": text})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["question"], text)

    async def test_context_and_provider_contract(self):
        await self.login()
        for text in ("  С чего начать слушать Radiohead?  ", "А что послушать дальше?"):
            self.assertEqual(
                (await self.client.post("/api/chat", json={"text": text})).status_code,
                200,
            )
        request = self.requests[-1]
        self.assertTrue(request.url.path.endswith(":generateContent"))
        self.assertEqual(request.headers["x-goog-api-key"], "test-provider-key")
        self.assertNotIn("test-provider-key", str(request.url))
        body = json.loads(request.content)
        self.assertIn("NOTRA", body["systemInstruction"]["parts"][0]["text"])
        self.assertEqual(
            [part["role"] for part in body["contents"]], ["user", "model", "user"]
        )
        self.assertEqual(
            body["contents"][0]["parts"][0]["text"], "С чего начать слушать Radiohead?"
        )
        self.assertEqual(
            body["contents"][1]["parts"][0]["text"], "Начните с альбома In Rainbows."
        )

    async def test_retry_and_restart(self):
        await self.login()
        payload = {"text": "С чего начать слушать блюз?", "request_id": str(uuid4())}
        first = await self.client.post("/api/chat", json=payload)
        turn = first.json()
        self.assertLessEqual(
            datetime.fromisoformat(turn["asked_at"]),
            datetime.fromisoformat(turn["answered_at"]),
        )
        token = self.client.cookies[COOKIE_NAME]
        await self.stack.aclose()
        await self.start_app()
        self.client.cookies.set(COOKIE_NAME, token, domain="side-b.test", path="/api")
        second = await self.client.post("/api/chat", json=payload)
        self.assertEqual(second.json(), turn)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual((await self.client.get("/api/chat")).json(), [turn])
        conflict = await self.client.post(
            "/api/chat", json={**payload, "text": "Другой вопрос"}
        )
        self.assertEqual(conflict.status_code, 409)

    async def test_simultaneous_retries_save_one_turn(self):
        await self.login()
        payload = {"text": "Что послушать в дороге?", "request_id": str(uuid4())}
        first, second = await asyncio.gather(
            self.client.post("/api/chat", json=payload),
            self.client.post("/api/chat", json=payload),
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual((await self.client.get("/api/chat")).json(), [first.json()])

    async def test_key_rotation_keeps_chat_and_revokes_old_session(self):
        await self.login()
        turn = (
            await self.client.post("/api/chat", json={"text": "Как звучит бибоп?"})
        ).json()
        token = self.client.cookies[COOKIE_NAME]
        await self.stack.aclose()
        self.key = secrets.token_urlsafe(18)
        os.environ["ACCESS_KEY"] = self.key
        await self.start_app()
        self.client.cookies.set(COOKIE_NAME, token, domain="side-b.test", path="/api")
        self.assertEqual((await self.client.get("/api/chat")).status_code, 401)
        await self.login()
        self.assertEqual((await self.client.get("/api/chat")).json(), [turn])

    async def test_context_limit_retains_full_history(self):
        await self.login()
        questions = [
            f"Что послушать из музыки {year} года?" for year in range(1970, 1978)
        ]
        for question in questions:
            response = await self.client.post("/api/chat", json={"text": question})
            self.assertEqual(response.status_code, 200)
        contents = json.loads(self.requests[-1].content)["contents"]
        sent = [part["parts"][0]["text"] for part in contents if part["role"] == "user"]
        self.assertEqual(sent, questions[1:])
        self.assertEqual(
            len((await self.client.get("/api/chat")).json()), len(questions)
        )

    async def test_unavailable_provider_keeps_history_consistent(self):
        await self.login()
        client = app.state.gemini.client
        for response, expected in (
            (httpx.Response(429), 429),
            (httpx.Response(503), 502),
            (httpx.Response(200, json={"candidates": []}), 502),
            (httpx.Response(200, text="invalid json"), 502),
        ):
            with patch.object(client, "post", return_value=response):
                result = await self.client.post(
                    "/api/chat", json={"text": "Что послушать в дороге?"}
                )
                self.assertEqual(result.status_code, expected)
                self.assertTrue(result.json()["detail"])
        with patch.object(client, "post", side_effect=httpx.ReadTimeout("timeout")):
            self.assertEqual(
                (
                    await self.client.post("/api/chat", json={"text": "Джаз?"})
                ).status_code,
                504,
            )
        app.state.gemini.api_key = ""
        self.assertEqual(
            (await self.client.post("/api/chat", json={"text": "Джаз?"})).status_code,
            503,
        )
        self.assertEqual((await self.client.get("/api/chat")).json(), [])
