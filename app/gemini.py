import httpx

from .storage import Turn

SYSTEM_PROMPT = (
    "Ты NOTRA и ведёшь музыкальный чат. Помогай находить музыку по настроению, "
    "жанру и любимым исполнителям. Объясняй, как звучат разные жанры и с чего начать "
    "знакомство с ними. В подборке давай 3–5 треков или альбомов, если собеседник "
    "не попросил другой объём. Для каждой рекомендации укажи исполнителя, название "
    "и коротко объясни, чем она подходит. Если не уверен в названии или факте, "
    "скажи об этом. Учитывай предыдущие сообщения о музыке. Если вопрос на другую "
    "тему, предложи вернуться к музыке. Отвечай на языке собеседника простым текстом "
    "без Markdown."
)


class GeminiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Gemini:
    def __init__(self, client: httpx.Client, model: str, api_key: str):
        self.client = client
        self.model = model
        self.api_key = api_key

    def reply(self, question: str, history: list[Turn]) -> str:
        if not self.api_key:
            raise GeminiError(
                503, "На сервере не задан ключ Gemini. Обратитесь к владельцу чата."
            )
        contents = []
        for turn in history:
            contents.append({"role": "user", "parts": [{"text": turn.question}]})
            contents.append({"role": "model", "parts": [{"text": turn.answer}]})
        contents.append({"role": "user", "parts": [{"text": question}]})
        try:
            response = self.client.post(
                f"models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": contents,
                    "generationConfig": {"maxOutputTokens": 2048},
                },
            )
        except httpx.TimeoutException as exc:
            raise GeminiError(
                504, "Модель не успела ответить. Попробуйте ещё раз."
            ) from exc
        except httpx.RequestError as exc:
            raise GeminiError(502, "Нет связи с Gemini. Попробуйте ещё раз.") from exc
        if response.status_code == 429:
            raise GeminiError(
                429, "Достигнут лимит запросов к Gemini. Попробуйте позже."
            )
        if not response.is_success:
            raise GeminiError(
                502, "Gemini не смог обработать запрос. Попробуйте позже."
            )
        try:
            parts = response.json()["candidates"][0]["content"]["parts"]
            answer = "".join(
                part["text"]
                for part in parts
                if "text" in part and not part.get("thought")
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            answer = ""
        if not answer.strip():
            raise GeminiError(
                502, "Модель не вернула текст. Попробуйте переформулировать вопрос."
            )
        return answer.strip()
