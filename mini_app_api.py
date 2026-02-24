"""
mini_app_api.py — FastAPI сервер для мини-аппа Nano Banano
Запускается ВМЕСТЕ с ботом через run_all.py

Ничего в .env добавлять не нужно — использует те же переменные что и бот.
"""

import asyncio
import aiohttp
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN           = os.getenv("BOT_TOKEN")
NANO_BANANA_API_URL = os.getenv("NANO_BANANA_API_URL", "https://api.kie.ai")
NANO_BANANA_API_KEY = os.getenv("NANO_BANANA_API_KEY")
DB_PATH             = "bot_database.db"


# ════════════════════════════════════════
# БД — работаем с bot_database.db напрямую
# ════════════════════════════════════════

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_tasks_table():
    """Создаём таблицу для задач мини-аппа (один раз при старте)"""
    c = db()
    c.execute("""
        CREATE TABLE IF NOT EXISTS miniapp_tasks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT UNIQUE NOT NULL,
            user_id    TEXT NOT NULL,
            tpl_name   TEXT,
            prompt     TEXT,
            image_size TEXT DEFAULT '1:1',
            state      TEXT DEFAULT 'waiting',
            result_url TEXT,
            cost       INTEGER DEFAULT 10,
            created_at INTEGER NOT NULL
        )
    """)
    c.commit()
    c.close()

def get_balance(user_id: str) -> int:
    """Остаток генераций из subscriptions (та же логика что в боте)"""
    c = db()
    row = c.execute("""
        SELECT (generations_limit - generations_used) AS bal
        FROM subscriptions
        WHERE user_id = ? AND is_active = 1
        ORDER BY end_date DESC LIMIT 1
    """, (user_id,)).fetchone()
    c.close()
    return int(row["bal"]) if row else 0

def spend_generation(user_id: str, cost: int, tpl_name: str):
    """Списываем генерации и пишем в generations"""
    c = db()
    c.execute("""
        UPDATE subscriptions
        SET generations_used = generations_used + ?
        WHERE user_id = ? AND is_active = 1
        AND id = (
            SELECT id FROM subscriptions
            WHERE user_id = ? AND is_active = 1
            ORDER BY end_date DESC LIMIT 1
        )
    """, (cost, user_id, user_id))
    c.execute("""
        INSERT INTO generations (user_id, prompt, generation_type, created_at)
        VALUES (?, ?, 'miniapp_template', datetime('now'))
    """, (user_id, tpl_name or 'miniapp'))
    c.commit()
    c.close()

def save_task(task_id, user_id, tpl_name, prompt, image_size, cost):
    c = db()
    c.execute("""
        INSERT INTO miniapp_tasks
        (task_id, user_id, tpl_name, prompt, image_size, cost, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (task_id, user_id, tpl_name, prompt, image_size, cost, int(time.time())))
    c.commit()
    c.close()

def update_task(task_id: str, state: str, result_url: str = None):
    c = db()
    c.execute("UPDATE miniapp_tasks SET state=?, result_url=? WHERE task_id=?",
              (state, result_url, task_id))
    c.commit()
    c.close()

def fetch_task(task_id: str) -> Optional[dict]:
    c = db()
    row = c.execute("SELECT * FROM miniapp_tasks WHERE task_id=?", (task_id,)).fetchone()
    c.close()
    return dict(row) if row else None

def fetch_history(user_id: str, limit: int = 50) -> list:
    c = db()
    rows = c.execute("""
        SELECT task_id, tpl_name, image_size, state, result_url, cost, created_at
        FROM miniapp_tasks
        WHERE user_id=? AND state='success'
        ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ════════════════════════════════════════
# KIE.AI
# ════════════════════════════════════════

async def kie_create(prompt: str, image_size: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{NANO_BANANA_API_URL}/api/v1/jobs/createTask",
            headers={"Authorization": f"Bearer {NANO_BANANA_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "google/nano-banana",
                  "input": {"prompt": prompt, "output_format": "jpeg",
                             "image_size": image_size}}
        ) as r:
            data = await r.json()
            if data.get("code") != 200:
                raise HTTPException(400, data.get("msg", "kie.ai error"))
            return data["data"]["taskId"]

async def kie_status(task_id: str) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{NANO_BANANA_API_URL}/api/v1/jobs/recordInfo",
            headers={"Authorization": f"Bearer {NANO_BANANA_API_KEY}"},
            params={"taskId": task_id}
        ) as r:
            data = await r.json()
            if data.get("code") != 200:
                return {"state": "fail"}
            return data["data"]


# ════════════════════════════════════════
# TELEGRAM — уведомление с кнопкой OK
# ════════════════════════════════════════

async def notify_done(user_id: str, tpl_name: str):
    """Шлём сообщение в бот. Кнопка OK удаляет его — обработчик в telegram_bot.py"""
    if not BOT_TOKEN:
        return
    async with aiohttp.ClientSession() as s:
        await s.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": user_id,
                "text": (
                    f"✅ <b>Готово!</b>\n\n"
                    f"🎨 Шаблон: <b>{tpl_name}</b>\n"
                    f"Открой приложение → вкладка <b>«Мои»</b>"
                ),
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [[
                    {"text": "OK  ✓", "callback_data": "dismiss_notify"}
                ]]}
            }
        )


# ════════════════════════════════════════
# BACKGROUND TASK — polling kie.ai
# ════════════════════════════════════════

async def poll_task(task_id: str, user_id: str, tpl_name: str):
    """Ждём результат от kie.ai и уведомляем пользователя"""
    for _ in range(60):  # макс 3 минуты (60 × 3с)
        await asyncio.sleep(3)
        try:
            data = await kie_status(task_id)
            state = data.get("state", "waiting")

            if state == "success":
                urls = json.loads(data.get("resultJson", "{}")).get("resultUrls", [])
                result_url = urls[0] if urls else None
                update_task(task_id, "success", result_url)
                await notify_done(user_id, tpl_name)
                return

            if state == "fail":
                update_task(task_id, "fail")
                return

        except Exception:
            continue

    update_task(task_id, "fail")


# ════════════════════════════════════════
# FASTAPI APP
# ════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_tasks_table()
    yield

app = FastAPI(title="Nano Banano Mini App API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class GenerateRequest(BaseModel):
    telegram_user_id: str
    prompt: str
    image_size: str = "1:1"
    template_name: Optional[str] = None
    cost: int = 10


@app.post("/generate")
async def generate(req: GenerateRequest):
    bal = get_balance(req.telegram_user_id)
    if bal < req.cost:
        raise HTTPException(402, f"Недостаточно генераций (есть {bal}, нужно {req.cost})")

    task_id = await kie_create(req.prompt, req.image_size)

    spend_generation(req.telegram_user_id, req.cost, req.template_name)
    save_task(task_id, req.telegram_user_id,
              req.template_name, req.prompt, req.image_size, req.cost)

    asyncio.create_task(
        poll_task(task_id, req.telegram_user_id, req.template_name or "Генерация")
    )
    return {"task_id": task_id, "state": "waiting"}


@app.get("/task/{task_id}")
async def get_task(task_id: str):
    row = fetch_task(task_id)
    if not row:
        raise HTTPException(404, "Task not found")
    return {"task_id": task_id, "state": row["state"], "result_url": row["result_url"]}


@app.get("/history/{user_id}")
async def get_history(user_id: str):
    return {"items": fetch_history(user_id)}


@app.get("/balance/{user_id}")
async def get_bal(user_id: str):
    return {"balance": get_balance(user_id)}


@app.get("/health")
async def health():
    return {"status": "ok"}
