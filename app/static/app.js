const byId = (id) => document.getElementById(id);
const loginForm = byId("login-form");
const key = byId("access-key");
const loginButton = byId("login-submit");
const toggleKey = byId("toggle-key");
const logout = byId("logout");
const chat = byId("chat");
const feed = byId("feed");
const messages = byId("messages");
const composer = byId("message-form");
const message = byId("message");
const send = byId("send");
const chatError = byId("chat-error");
const chatStatus = byId("chat-status");
const retryHistory = byId("retry-history");
const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"
});

let checkingKey = false;
let keyTimer;
let view = 0;
let ready = false;
let sending = false;
let lastRequest;

async function api(path, method = "GET", body) {
  const options = { method, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch("/api/" + path, options);
  } catch {
    throw new Error("Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(data.detail || "Не удалось выполнить запрос. Попробуйте ещё раз.");
    error.status = response.status;
    throw error;
  }
  if (response.status !== 204) return response.json();
}

function keyIssue() {
  if (!key.value) return "Введите ключ доступа.";
  if (/[^A-Za-z0-9_-]/.test(key.value)) return "Используйте латинские буквы, цифры, дефис или подчёркивание.";
  if (key.value.length < 8 || key.value.length > 255) return "Длина ключа должна быть от 8 до 255 символов.";
  return "";
}

function keyFeedback(text, invalid = Boolean(text)) {
  byId("login-error").textContent = text;
  key.setAttribute("aria-invalid", String(invalid));
  key.setAttribute("aria-describedby", text ? "key-hint login-error" : "key-hint");
}

function updateLoginButton() {
  loginButton.disabled = checkingKey || Boolean(keyIssue()) || key.getAttribute("aria-invalid") === "true";
  loginButton.textContent = checkingKey ? "Проверяем ключ…" : "Войти в чат";
}

key.addEventListener("input", () => {
  clearTimeout(keyTimer);
  keyFeedback("");
  updateLoginButton();
  if (key.value && keyIssue()) keyTimer = setTimeout(() => keyFeedback(keyIssue()), 320);
});

key.addEventListener("blur", () => {
  clearTimeout(keyTimer);
  if (key.value && keyIssue()) keyFeedback(keyIssue());
});

function showKey(visible) {
  key.type = visible ? "text" : "password";
  toggleKey.dataset.visible = String(visible);
  toggleKey.setAttribute("aria-label", visible ? "Скрыть ключ" : "Показать ключ");
}

toggleKey.addEventListener("click", () => showKey(key.type === "password"));

function updateComposer() {
  const length = Array.from(message.value).length;
  const tooLong = length > 500;
  byId("count").textContent = length + " / 500";
  byId("count").classList.toggle("over", tooLong);
  message.setAttribute("aria-invalid", String(tooLong));
  message.readOnly = sending || !ready;
  send.disabled = !ready || sending || tooLong || !message.value.trim();
}

function showLogin(note = "", draft = "") {
  view++;
  ready = false;
  sending = false;
  lastRequest = undefined;
  chat.hidden = true;
  logout.hidden = true;
  byId("login").hidden = false;
  document.body.classList.remove("in-chat");
  messages.replaceChildren();
  message.value = draft;
  chatError.textContent = "";
  chatStatus.textContent = "";
  retryHistory.hidden = true;
  key.value = "";
  showKey(false);
  keyFeedback(note, false);
  updateLoginButton();
  key.focus();
}

function messageNode(author, text, timestamp) {
  const entry = document.createElement("article");
  entry.className = "message " + author;
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const name = document.createElement("strong");
  name.textContent = author === "user" ? "Вы" : "NOTRA";
  const time = document.createElement("time");
  time.dateTime = timestamp;
  time.textContent = dateFormat.format(new Date(timestamp));
  const content = document.createElement("p");
  content.textContent = text;
  meta.append(name, time);
  entry.append(meta, content);
  return entry;
}

function turnNodes(turn) {
  return [
    messageNode("user", turn.question, turn.asked_at),
    messageNode("assistant", turn.answer, turn.answered_at)
  ];
}

function chatFailure(error) {
  if (error.status === 401) {
    showLogin("Сессия завершилась. Введите ключ ещё раз.", message.value);
  } else {
    chatError.textContent = error.message;
  }
}

async function loadHistory() {
  const current = view;
  ready = false;
  byId("empty").hidden = true;
  retryHistory.hidden = true;
  chatError.textContent = "";
  chatStatus.textContent = "Загружаем переписку…";
  updateComposer();
  try {
    const turns = await api("chat");
    if (current !== view) return;
    const entries = document.createDocumentFragment();
    for (const turn of turns) entries.append(...turnNodes(turn));
    messages.replaceChildren(entries);
    byId("empty").hidden = turns.length > 0;
    ready = true;
    feed.scrollTop = feed.scrollHeight;
  } catch (error) {
    if (current !== view) return;
    retryHistory.hidden = false;
    chatFailure(error);
  } finally {
    if (current === view) {
      chatStatus.textContent = "";
      updateComposer();
    }
  }
}

function showChat() {
  view++;
  clearTimeout(keyTimer);
  key.value = "";
  showKey(false);
  byId("login").hidden = true;
  logout.hidden = false;
  chat.hidden = false;
  document.body.classList.add("in-chat");
  byId("chat-title").focus();
  loadHistory();
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loginButton.disabled || keyIssue()) return;
  checkingKey = true;
  key.readOnly = true;
  keyFeedback("");
  updateLoginButton();
  try {
    await api("session", "POST", { key: key.value });
    showChat();
  } catch (error) {
    keyFeedback(error.message, error.status === 401 || error.status === 422);
    key.focus();
  } finally {
    checkingKey = false;
    key.readOnly = false;
    updateLoginButton();
  }
});

logout.addEventListener("click", async () => {
  logout.disabled = true;
  try {
    await api("session", "DELETE");
    showLogin();
  } catch (error) {
    chatFailure(error);
  } finally {
    logout.disabled = false;
  }
});

retryHistory.addEventListener("click", loadHistory);
byId("suggestions").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !ready || sending) return;
  message.value = button.textContent;
  message.dispatchEvent(new Event("input"));
  message.focus();
});

message.addEventListener("input", () => {
  chatError.textContent = Array.from(message.value).length > 500 ? "Сократите сообщение до 500 символов." : "";
  updateComposer();
});

message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!send.disabled) composer.requestSubmit();
  }
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (send.disabled) return;
  const current = view;
  const draft = message.value;
  const text = draft.trim();
  if (lastRequest?.text !== text) lastRequest = { text, request_id: crypto.randomUUID() };
  sending = true;
  message.value = "";
  updateComposer();
  chatError.textContent = "";
  chatStatus.textContent = "Готовим ответ…";
  byId("empty").hidden = true;
  const pending = messageNode("user", text, new Date().toISOString());
  messages.append(pending);
  feed.scrollTop = feed.scrollHeight;
  try {
    const turn = await api("chat", "POST", lastRequest);
    if (view !== current) return;
    pending.replaceWith(...turnNodes(turn));
    lastRequest = undefined;
    feed.scrollTop = feed.scrollHeight;
  } catch (error) {
    if (view !== current) return;
    pending.remove();
    byId("empty").hidden = messages.childElementCount > 0;
    message.value = draft;
    chatFailure(error);
  } finally {
    if (view === current) {
      sending = false;
      chatStatus.textContent = "";
      updateComposer();
      message.focus();
    }
  }
});

api("session").then((session) => {
  if (session.authenticated && chat.hidden && !checkingKey) showChat();
}).catch((error) => {
  if (chat.hidden) keyFeedback(error.message, false);
});
