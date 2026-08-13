import os
import asyncio
import logging
from datetime import datetime
from flask import Flask

os.environ["TELEGRAM_BOT_API_URL"] = "https://telegram.dog/bot"

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")

# ======================= ИНИЦИАЛИЗАЦИЯ =======================
logging.basicConfig(level=logging.INFO)

session = AiohttpSession()
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ======================= КЛАВИАТУРА =======================
def get_plans_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎁 2 дня бесплатно",
                callback_data="plan_2d"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔥 1 месяц — 139 ₽",
                callback_data="plan_1m"
            ),
            InlineKeyboardButton(
                text="⭐ 3 месяца — 469 ₽",
                callback_data="plan_3m"
            )
        ],
        [
            InlineKeyboardButton(
                text="💎 1 год — 899 ₽",
                callback_data="plan_1y"
            )
        ]
    ])
    return keyboard

# ======================= ОБРАБОТЧИКИ =======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🛡️ <b>Добро пожаловать в Kildear VPN!</b>\n\n"
        "👇 <b>Выберите тариф:</b>",
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("plan_"))
async def process_plan(callback: CallbackQuery):
    await callback.answer()
    
    user_id = callback.from_user.id
    username = f"user_{user_id}_{int(datetime.now().timestamp())}"
    
    # Готовая рабочая ссылка
    vless_link = f"vless://1c44eb8c-b33f-4629-8849-6e5a187ef3e1@2.26.70.65:40224?type=ws&encryption=none&path=%2Fvpn&host=&security=none#{username}"
    
    await callback.message.answer(
        f"✅ <b>VPN-ключ готов!</b>\n\n"
        f"🔗 <b>Ссылка:</b>\n<code>{vless_link}</code>\n\n"
        f"📱 <b>Инструкция:</b>\n"
        f"1. Скачайте V2RayNG или Hiddify\n"
        f"2. Вставьте ссылку\n"
        f"3. Подключитесь\n\n"
        f"📅 Подписка активна",
        parse_mode="HTML"
    )

# ======================= ЗАПУСК =======================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
