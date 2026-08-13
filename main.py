import os
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram import F
import aiohttp
import base64
import hashlib
import secrets
import string
import json
from aiohttp import web

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")

# ЮKassa - ВРЕМЕННО ОТКЛЮЧАЕМ ДЛЯ ТЕСТА
# YKASSA_SHOP_ID = os.environ.get("YKASSA_SHOP_ID", "1434221")
# YKASSA_SECRET_KEY = os.environ.get("YKASSA_SECRET_KEY", "live_fH2K3m3SygBdP8P6bjaOwkRj4UKl5FwsatLZC-PJKt8")
# YKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
# YKASSA_WEBHOOK_URL = os.environ.get("YKASSA_WEBHOOK_URL", "https://kildear-vpn-bot.onrender.com/webhook/yookassa")

# Настройки VPN ключей
VPN_KEY_LENGTH = 32
VPN_KEY_PREFIX = "KILDEAR-"

# ======================= ТАРИФЫ =======================
PLANS = {
    "2d": {"days": 2, "price": 0, "devices": 1, "label": "🎁 2 дня бесплатно", "emoji": "🎁"},
    "1m": {"days": 30, "price": 139, "devices": 2, "label": "🔥 1 месяц", "emoji": "🔥"},
    "3m": {"days": 90, "price": 469, "devices": 3, "label": "⭐ 3 месяца", "emoji": "⭐"},
    "1y": {"days": 365, "price": 899, "devices": 5, "label": "💎 1 год", "emoji": "💎"},
}

# ======================= НАСТРОЙКА ЛОГГИРОВАНИЯ =======================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================= ИНИЦИАЛИЗАЦИЯ БОТА =======================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ======================= БАЗА ДАННЫХ =======================
def init_db():
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  registration_date TIMESTAMP,
                  current_subscription TEXT,
                  subscription_end TIMESTAMP,
                  devices INTEGER DEFAULT 1)''')
    
    # Таблица подписок
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  plan_id TEXT,
                  start_date TIMESTAMP,
                  end_date TIMESTAMP,
                  status TEXT,
                  price INTEGER,
                  payment_id TEXT,
                  vpn_key TEXT,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    # Таблица VPN ключей
    c.execute('''CREATE TABLE IF NOT EXISTS vpn_keys
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  key TEXT UNIQUE,
                  user_id INTEGER,
                  subscription_id INTEGER,
                  created_at TIMESTAMP,
                  expires_at TIMESTAMP,
                  status TEXT DEFAULT 'active',
                  devices INTEGER DEFAULT 1,
                  FOREIGN KEY (user_id) REFERENCES users (user_id),
                  FOREIGN KEY (subscription_id) REFERENCES subscriptions (id))''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

init_db()

# ======================= ГЕНЕРАЦИЯ VPN КЛЮЧЕЙ =======================
def generate_vpn_key() -> str:
    """Генерация уникального VPN ключа"""
    alphabet = string.ascii_uppercase + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(VPN_KEY_LENGTH))
    return f"{VPN_KEY_PREFIX}{random_part}"

def is_key_unique(key: str) -> bool:
    """Проверка уникальности ключа"""
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM vpn_keys WHERE key = ?", (key,))
    count = c.fetchone()[0]
    conn.close()
    return count == 0

def get_unique_vpn_key() -> str:
    """Генерация уникального VPN ключа с проверкой"""
    max_attempts = 10
    for _ in range(max_attempts):
        key = generate_vpn_key()
        if is_key_unique(key):
            return key
    raise Exception("Не удалось сгенерировать уникальный ключ")

# ======================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =======================
def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'first_name': user[2],
            'last_name': user[3],
            'registration_date': user[4],
            'current_subscription': user[5],
            'subscription_end': user[6],
            'devices': user[7]
        }
    return None

def create_user(user_id: int, username: str, first_name: str, last_name: str = None):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""INSERT OR IGNORE INTO users 
                 (user_id, username, first_name, last_name, registration_date, devices) 
                 VALUES (?, ?, ?, ?, ?, ?)""",
              (user_id, username, first_name, last_name, datetime.now(), 1))
    conn.commit()
    conn.close()

def get_active_subscription(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""SELECT * FROM subscriptions 
                 WHERE user_id = ? AND status = 'active' AND end_date > datetime('now')
                 ORDER BY end_date DESC LIMIT 1""", (user_id,))
    sub = c.fetchone()
    conn.close()
    
    if sub:
        return {
            'id': sub[0],
            'user_id': sub[1],
            'plan_id': sub[2],
            'start_date': sub[3],
            'end_date': sub[4],
            'status': sub[5],
            'price': sub[6],
            'payment_id': sub[7],
            'vpn_key': sub[8] if len(sub) > 8 else None
        }
    return None

def create_subscription(user_id: int, plan_id: str, price: int, payment_id: str = None) -> int:
    plan = PLANS[plan_id]
    start_date = datetime.now()
    end_date = start_date + timedelta(days=plan['days'])
    
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    # Деактивируем старые подписки
    c.execute("UPDATE subscriptions SET status = 'inactive' WHERE user_id = ? AND status = 'active'", (user_id,))
    
    # Генерируем VPN ключ
    vpn_key = get_unique_vpn_key()
    
    # Создаем новую подписку
    c.execute("""INSERT INTO subscriptions 
                 (user_id, plan_id, start_date, end_date, status, price, payment_id, vpn_key)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (user_id, plan_id, start_date, end_date, 'active', price, payment_id, vpn_key))
    
    subscription_id = c.lastrowid
    
    # Сохраняем VPN ключ в отдельной таблице
    c.execute("""INSERT INTO vpn_keys 
                 (key, user_id, subscription_id, created_at, expires_at, status, devices)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (vpn_key, user_id, subscription_id, start_date, end_date, 'active', plan['devices']))
    
    # Обновляем пользователя
    c.execute("""UPDATE users 
                 SET current_subscription = ?, subscription_end = ?, devices = ?
                 WHERE user_id = ?""",
              (plan_id, end_date, plan['devices'], user_id))
    
    conn.commit()
    conn.close()
    
    return subscription_id

def get_user_vpn_keys(user_id: int) -> list:
    """Получение всех активных VPN ключей пользователя"""
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""SELECT key, expires_at, status, devices FROM vpn_keys 
                 WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
                 ORDER BY created_at DESC""", (user_id,))
    keys = c.fetchall()
    conn.close()
    return keys

# ======================= КЛАВИАТУРЫ =======================
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="my_profile")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help")]
    ])
    return keyboard

def get_plans_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        button_text = f"{plan['emoji']} {plan['label']} — {price_text}"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"plan_{plan_id}")
        ])
    
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    ])
    
    return keyboard

def get_keys_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить ключи", callback_data="refresh_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    return keyboard

# ======================= ОБРАБОТЧИКИ КОМАНД =======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    # Регистрируем пользователя
    create_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    # Приветственное сообщение
    welcome_text = f"""
🌟 <b>Добро пожаловать в VPN сервис Kildear!</b>

Защитите свои данные и получите доступ к любому контенту.

📋 <b>Доступные тарифы:</b>
• 🎁 2 дня бесплатно
• 🔥 1 месяц — 139 ₽
• ⭐ 3 месяца — 469 ₽
• 💎 1 год — 899 ₽

Используйте кнопки ниже для управления подпиской.
"""
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\nВыберите действие:",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_subscription")
async def show_plans(callback: CallbackQuery):
    active_sub = get_active_subscription(callback.from_user.id)
    
    plans_text = "📱 <b>Выберите тарифный план:</b>\n\n"
    
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            if days_left > 0:
                plans_text += f"✅ <i>У вас активна подписка до {end_date.strftime('%d.%m.%Y')} (осталось {days_left} дней)</i>\n\n"
            else:
                plans_text += f"⏰ <i>Подписка истекла {end_date.strftime('%d.%m.%Y')}</i>\n\n"
        except:
            pass
    
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        plans_text += f"{plan['emoji']} <b>{plan['label']}</b>\n"
        plans_text += f"   • 📅 {plan['days']} дней\n"
        plans_text += f"   • 📱 {plan['devices']} устройств\n"
        plans_text += f"   • 💰 {price_text}\n\n"
    
    await callback.message.edit_text(
        plans_text,
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "my_keys")
async def show_keys(callback: CallbackQuery):
    user_id = callback.from_user.id
    keys = get_user_vpn_keys(user_id)
    
    if not keys:
        keys_text = """
🔑 <b>У вас нет активных VPN ключей</b>

Для получения ключа необходимо приобрести подписку.
Нажмите "📱 Купить подписку" чтобы выбрать тариф.
"""
        await callback.message.edit_text(
            keys_text,
            reply_markup=get_keys_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    keys_text = "🔑 <b>Ваши активные VPN ключи:</b>\n\n"
    
    for idx, (key, expires_at, status, devices) in enumerate(keys, 1):
        try:
            expires = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S.%f')
            days_left = (expires - datetime.now()).days
            
            keys_text += f"<b>Ключ #{idx}</b>\n"
            keys_text += f"<code>{key}</code>\n"
            keys_text += f"📱 Устройств: {devices}\n"
            keys_text += f"⏳ Действует до: {expires.strftime('%d.%m.%Y')}\n"
            keys_text += f"📊 Осталось: {days_left} дней\n"
            keys_text += "\n📌 <b>Инструкция:</b>\n"
            keys_text += "1. Скачайте приложение VPN\n"
            keys_text += f"2. Введите ключ: <code>{key}</code>\n"
            keys_text += "3. Подключитесь к серверу\n\n"
            keys_text += "➖➖➖➖➖➖➖➖➖➖➖➖\n\n"
        except Exception as e:
            logger.error(f"Ошибка форматирования ключа: {e}")
    
    await callback.message.edit_text(
        keys_text,
        reply_markup=get_keys_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "refresh_keys")
async def refresh_keys(callback: CallbackQuery):
    await show_keys(callback)

@dp.callback_query(F.data.startswith("plan_"))
async def process_plan_selection(callback: CallbackQuery):
    plan_id = callback.data.replace("plan_", "")
    plan = PLANS.get(plan_id)
    
    if not plan:
        await callback.answer("❌ Тариф не найден")
        return
    
    user_id = callback.from_user.id
    
    # Для бесплатного тарифа
    if plan['price'] == 0:
        # Проверяем, не использовал ли уже пользователь бесплатный период
        conn = sqlite3.connect('subscriptions.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND plan_id = '2d'", (user_id,))
        count = c.fetchone()[0]
        conn.close()
        
        if count > 0:
            await callback.answer("❌ Вы уже использовали бесплатный период!", show_alert=True)
            return
        
        # Активируем подписку
        create_subscription(user_id, plan_id, 0)
        
        # Получаем ключ
        active_sub = get_active_subscription(user_id)
        vpn_key = active_sub.get('vpn_key') if active_sub else None
        
        success_text = f"""
🎉 <b>Поздравляем! Бесплатная подписка активирована!</b>

📅 Период: {plan['days']} дней
📱 Устройств: {plan['devices']}

🔑 <b>Ваш VPN ключ:</b>
<code>{vpn_key}</code>

📌 <b>Инструкция по использованию:</b>
1. Скачайте VPN клиент
2. Введите ключ: <code>{vpn_key}</code>
3. Подключитесь к серверу

Ключ также доступен в разделе "Мои ключи".
"""
        
        await callback.message.edit_text(
            success_text,
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Для платных тарифов
    payment_text = f"""
💳 <b>Оплата подписки</b>

Тариф: {plan['label']}
Сумма: {plan['price']} ₽
Период: {plan['days']} дней

⚠️ <b>Для тестирования используйте кнопку ниже</b>
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тестовая активация", callback_data=f"test_activate_{plan_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_plans")]
    ])
    
    await callback.message.edit_text(
        payment_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("test_activate_"))
async def test_activate(callback: CallbackQuery):
    plan_id = callback.data.replace("test_activate_", "")
    plan = PLANS.get(plan_id)
    user_id = callback.from_user.id
    
    if not plan:
        await callback.answer("❌ Тариф не найден")
        return
    
    # Активируем подписку
    create_subscription(user_id, plan_id, plan['price'], "test_payment")
    
    # Получаем ключ
    active_sub = get_active_subscription(user_id)
    vpn_key = active_sub.get('vpn_key') if active_sub else None
    
    success_text = f"""
✅ <b>Подписка активирована в тестовом режиме!</b>

🎉 Подписка на тариф «{plan['label']}» активирована!
📅 Период: {plan['days']} дней
📱 Устройств: {plan['devices']}

🔑 <b>Ваш VPN ключ:</b>
<code>{vpn_key}</code>

📌 <b>Инструкция по использованию:</b>
1. Скачайте VPN клиент
2. Введите ключ: <code>{vpn_key}</code>
3. Подключитесь к серверу

Ключ также доступен в разделе "Мои ключи".
Спасибо за покупку! 🔒
"""
    
    await callback.message.edit_text(
        success_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "my_profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    active_sub = get_active_subscription(user_id)
    keys = get_user_vpn_keys(user_id)
    
    if not user:
        await callback.answer("❌ Пользователь не найден")
        return
    
    profile_text = f"""
👤 <b>Ваш профиль</b>

🆔 ID: {user['user_id']}
📝 Имя: {user['first_name']}
"""
    
    if user['username']:
        profile_text += f"📌 @{user['username']}\n"
    
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            plan = PLANS.get(active_sub['plan_id'], {})
            
            profile_text += f"""
📱 <b>Текущая подписка</b>
• Тариф: {plan.get('label', active_sub['plan_id'])}
• Действует до: {end_date.strftime('%d.%m.%Y')}
• Осталось дней: {days_left if days_left > 0 else 0}
• Устройств: {plan.get('devices', 1)}
"""
        except:
            profile_text += "\n❌ <b>Ошибка чтения данных подписки</b>"
    else:
        profile_text += "\n❌ <b>Нет активной подписки</b>"
    
    profile_text += f"\n🔑 <b>Всего активных ключей:</b> {len(keys)}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        profile_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    help_text = """
🆘 <b>Помощь</b>

<b>Как купить подписку?</b>
1. Нажмите "📱 Купить подписку"
2. Выберите тариф
3. Для теста нажмите "Тестовая активация"

<b>Как получить VPN ключ?</b>
После активации подписки ключ придет в сообщении.
Вы также можете посмотреть его в разделе "Мои ключи".

<b>Бесплатный период</b>
Вы можете получить 2 дня бесплатно, чтобы протестировать сервис.

<b>Вопросы и поддержка</b>
Если у вас возникли проблемы, напишите нам:
📧 support@kildear.com

<b>Важно:</b>
• После активации подписка работает автоматически
• VPN ключи генерируются индивидуально
• Ключ можно использовать на всех устройствах
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        help_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

# ======================= WEBHOOK ОБРАБОТЧИКИ =======================
async def webhook_handler(request):
    """Обработка вебхуков от Telegram"""
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в вебхуке: {e}")
        return web.Response(status=500)

# ======================= ЗАПУСК БОТА =======================
async def on_startup():
    """Настройка вебхука при запуске"""
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'kildear-vpn-bot.onrender.com')}/webhook"
    
    try:
        # Удаляем старые вебхуки и отключаем polling
        await bot.delete_webhook(drop_pending_updates=True)
        # Устанавливаем новый вебхук
        await bot.set_webhook(webhook_url)
        logger.info(f"Вебхук установлен: {webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка установки вебхука: {e}")

async def main():
    # Получаем порт из переменной окружения
    port = int(os.environ.get('PORT', 8080))
    
    # Создаем веб-приложение
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/', lambda request: web.Response(text='Bot is running!'))
    
    # Настройка вебхука
    await on_startup()
    
    # Запускаем веб-сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Бот запущен на порту {port}")
    logger.info(f"Вебхук: https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook")
    
    # Держим сервер запущенным
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
