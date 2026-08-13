import os
import logging
import sqlite3
import secrets as pysecrets
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram import F
import json
import uuid
import requests
import urllib3
from aiohttp import web

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo")

XRAY_PANEL_URL = os.environ.get("XRAY_PANEL_URL", "https://2.26.70.65:55347")
XRAY_PANEL_PATH = os.environ.get("XRAY_PANEL_PATH", "/mMH522DscvfBbpFwaA")
XRAY_USERNAME = os.environ.get("XRAY_USERNAME", "fHRTAk9lFz")
XRAY_PASSWORD = os.environ.get("XRAY_PASSWORD", "pM0xjYSy4N")
XRAY_INBOUND_ID = int(os.environ.get("XRAY_INBOUND_ID", "2"))

VPN_SERVER_IP = os.environ.get("VPN_SERVER_IP", "2.26.70.65")
VPN_SERVER_PORT = os.environ.get("VPN_SERVER_PORT", "40224")
VPN_DOMAIN = os.environ.get("VPN_DOMAIN", "2.26.70.65")
VPN_PATH = os.environ.get("VPN_PATH", "/mMH522DscvfBbpFwaA")
SUBSCRIPTION_BASE_URL = os.environ.get("SUBSCRIPTION_BASE_URL", "https://2.26.70.65:2096/sub/")

# ======================= ТАРИФЫ =======================
PLANS = {
    "2d": {"days": 2, "price": 0, "devices": 1, "label": "🎁 2 дня бесплатно", "emoji": "🎁"},
    "1m": {"days": 30, "price": 139, "devices": 2, "label": "🔥 1 месяц", "emoji": "🔥"},
    "3m": {"days": 90, "price": 469, "devices": 3, "label": "⭐ 3 месяца", "emoji": "⭐"},
    "1y": {"days": 365, "price": 899, "devices": 5, "label": "💎 1 год", "emoji": "💎"},
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ======================= БАЗА ДАННЫХ =======================
def init_db():
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  registration_date TIMESTAMP,
                  current_subscription TEXT,
                  subscription_end TIMESTAMP,
                  devices INTEGER DEFAULT 1,
                  subscription_uuid TEXT)''')
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
                  uuid TEXT,
                  client_id TEXT,
                  port INTEGER,
                  subscription_uuid TEXT,
                  FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS vpn_keys
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  key TEXT UNIQUE,
                  user_id INTEGER,
                  subscription_id INTEGER,
                  created_at TIMESTAMP,
                  expires_at TIMESTAMP,
                  status TEXT DEFAULT 'active',
                  devices INTEGER DEFAULT 1,
                  uuid TEXT,
                  client_id TEXT,
                  port INTEGER,
                  subscription_uuid TEXT,
                  FOREIGN KEY (user_id) REFERENCES users (user_id),
                  FOREIGN KEY (subscription_id) REFERENCES subscriptions (id))''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

init_db()

# ======================= РАБОТА С 3X-UI =======================
class XrayAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*"
        })
        path = XRAY_PANEL_PATH if XRAY_PANEL_PATH.endswith("/") else XRAY_PANEL_PATH + "/"
        self.base_url = f"{XRAY_PANEL_URL}{path}".rstrip("/")
        self.panel_root = f"{XRAY_PANEL_URL}{path}"
        self.logged_in = False
        self.csrf_token = None
        logger.info(f"🔧 Инициализация API: {self.base_url}")
        self.login()

    def login(self) -> bool:
        """Авторизация через GET параметры (старый способ)"""
        try:
            # Пробуем авторизацию через GET параметры
            url = f"{self.panel_root}?username={XRAY_USERNAME}&password={XRAY_PASSWORD}"
            logger.info(f"📤 GET авторизация: {url}")
            
            response = self.session.get(url, timeout=30)
            logger.info(f"📥 GET статус: {response.status_code}")
            
            if response.status_code == 200:
                # Проверяем куки
                if self.session.cookies:
                    self.logged_in = True
                    logger.info("✅ Авторизация через GET успешна!")
                    logger.info(f"🍪 Cookies: {self.session.cookies.get_dict()}")
                    return True
            
            # Если GET не сработал, пробуем через POST
            logger.info("🔄 Пробуем POST с Basic Auth...")
            return self.login_basic()
            
        except Exception as e:
            logger.error(f"❌ Ошибка авторизации GET: {e}")
            return False
    
    def login_basic(self) -> bool:
        """Авторизация через Basic Auth"""
        try:
            url = f"{self.base_url}/login"
            
            # Добавляем Basic Auth заголовок
            import base64
            auth_str = f"{XRAY_USERNAME}:{XRAY_PASSWORD}"
            auth_bytes = auth_str.encode('ascii')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            data = {
                "username": XRAY_USERNAME,
                "password": XRAY_PASSWORD
            }
            
            logger.info("📤 POST с Basic Auth")
            response = self.session.post(url, data=data, headers=headers, timeout=30)
            
            logger.info(f"📥 POST статус: {response.status_code}")
            logger.info(f"📥 POST ответ: {response.text[:200] if response.text else 'empty'}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get('success'):
                        self.logged_in = True
                        logger.info("✅ Авторизация через Basic Auth успешна!")
                        return True
                except:
                    pass
            
            # Если ничего не помогло, пробуем через куки из браузера
            logger.info("🔄 Пробуем через cookies...")
            return self.login_cookie()
            
        except Exception as e:
            logger.error(f"❌ Ошибка Basic Auth: {e}")
            return False
    
    def login_cookie(self) -> bool:
        """Авторизация через cookies (сессия из браузера)"""
        try:
            # Здесь мы просто проверяем, есть ли уже куки
            if self.session.cookies:
                self.logged_in = True
                logger.info("✅ Авторизация по существующим кукам!")
                return True
            
            # Пробуем получить куки через GET на корень
            response = self.session.get(self.panel_root, timeout=30)
            if response.status_code == 200 and self.session.cookies:
                self.logged_in = True
                logger.info("✅ Авторизация по кукам после GET!")
                return True
            
            logger.error("❌ Все способы авторизации не сработали")
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка cookie авторизации: {e}")
            return False

    def add_client(self, client_uuid: str, email: str, expiry_time: int, sub_id: str) -> bool:
        """Создание клиента"""
        logger.info(f"📤 Попытка создания клиента: {email}")
        logger.info(f"📤 Текущий статус logged_in: {self.logged_in}")
        
        if not self.logged_in:
            logger.info("🔄 Пытаемся залогиниться...")
            if not self.login():
                logger.error("❌ Не удалось авторизоваться")
                return False

        endpoint = f"{self.base_url}/panel/api/inbounds/addClient"
        logger.info(f"📤 Эндпоинт: {endpoint}")

        client_data = {
            "id": client_uuid,
            "email": email,
            "flow": "xtls-rprx-vision",
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": expiry_time,
            "enable": True,
            "tgId": "",
            "subId": sub_id,
            "reset": 0,
        }

        payload = {
            "id": XRAY_INBOUND_ID,
            "settings": json.dumps({"clients": [client_data]})
        }

        logger.info(f"📤 Payload: {json.dumps(payload, indent=2)}")

        try:
            response = self.session.post(endpoint, json=payload, timeout=30)
            logger.info(f"📥 Статус: {response.status_code}")
            logger.info(f"📥 Ответ: {response.text[:500]}")
            
            if response.status_code == 200:
                try:
                    result = response.json()
                    if result.get('success'):
                        logger.info(f"✅ Клиент {email} создан!")
                        return True
                except:
                    logger.info("✅ Клиент создан (статус 200)")
                    return True
            
            logger.error(f"❌ Ошибка: статус {response.status_code}")
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка запроса: {e}")
            return False

xray_api = XrayAPI()

# ======================= ОСТАЛЬНЫЕ ФУНКЦИИ =======================
def generate_sub_id() -> str:
    return pysecrets.token_hex(8)

def generate_vless_link(client_uuid: str, email: str) -> str:
    return (f"vless://{client_uuid}@{VPN_SERVER_IP}:{VPN_SERVER_PORT}?type=ws&security=none&encryption=none&host={VPN_DOMAIN}&path={VPN_PATH}#{email}")

def create_vpn_user(user_id: int, plan_id: str) -> tuple:
    client_uuid = str(uuid.uuid4())
    email = f"user_{user_id}_{int(datetime.now().timestamp())}"
    sub_id = generate_sub_id()
    plan = PLANS[plan_id]
    expiry_time = int((datetime.now() + timedelta(days=plan['days'])).timestamp() * 1000)

    success = xray_api.add_client(client_uuid, email, expiry_time, sub_id)
    vless_link = generate_vless_link(client_uuid, email)
    return vless_link, client_uuid, email, sub_id, success

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
            'devices': user[7],
            'subscription_uuid': user[8] if len(user) > 8 else None
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
            'vpn_key': sub[8] if len(sub) > 8 else None,
            'uuid': sub[9] if len(sub) > 9 else None,
            'client_id': sub[10] if len(sub) > 10 else None,
            'port': sub[11] if len(sub) > 11 else None,
            'subscription_uuid': sub[12] if len(sub) > 12 else None
        }
    return None

def create_subscription(user_id: int, plan_id: str, price: int, payment_id: str = None):
    plan = PLANS[plan_id]
    start_date = datetime.now()
    end_date = start_date + timedelta(days=plan['days'])

    vpn_key, client_uuid, client_id, sub_id, success = create_vpn_user(user_id, plan_id)

    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()

    status = 'active' if success else 'failed'

    if success:
        c.execute("UPDATE subscriptions SET status = 'inactive' WHERE user_id = ? AND status = 'active'", (user_id,))

    c.execute("""INSERT INTO subscriptions
                 (user_id, plan_id, start_date, end_date, status, price, payment_id, vpn_key, uuid, client_id, port, subscription_uuid)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (user_id, plan_id, start_date, end_date, status, price, payment_id, vpn_key, client_uuid, client_id, VPN_SERVER_PORT, sub_id))

    subscription_id = c.lastrowid

    if success:
        c.execute("""INSERT INTO vpn_keys
                     (key, user_id, subscription_id, created_at, expires_at, status, devices, uuid, client_id, port, subscription_uuid)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (vpn_key, user_id, subscription_id, start_date, end_date, 'active', plan['devices'], client_uuid, client_id, VPN_SERVER_PORT, sub_id))

        c.execute("""UPDATE users
                     SET current_subscription = ?, subscription_end = ?, devices = ?, subscription_uuid = ?
                     WHERE user_id = ?""",
                  (plan_id, end_date, plan['devices'], sub_id, user_id))

    conn.commit()
    conn.close()

    if success:
        logger.info(f"✅ Подписка создана: user={user_id}, plan={plan_id}")
    else:
        logger.error(f"❌ Подписка НЕ активирована: user={user_id}, plan={plan_id}")

    return subscription_id, vpn_key, success

def get_user_vpn_keys(user_id: int) -> list:
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("""SELECT key, expires_at, status, devices, port FROM vpn_keys
                 WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
                 ORDER BY created_at DESC""", (user_id,))
    keys = c.fetchall()
    conn.close()
    return keys

# ======================= КЛАВИАТУРЫ =======================
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Мои ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="📥 Ссылка для подписки", callback_data="my_subscription")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="my_profile")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help")]
    ])

def get_plans_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"{plan['emoji']} {plan['label']} — {price_text}", callback_data=f"plan_{plan_id}")
        ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    ])
    return keyboard

def get_keys_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить ключи", callback_data="refresh_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])

# ======================= ОБРАБОТЧИКИ =======================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    create_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    await message.answer(
        "🌟 <b>Добро пожаловать в VPN сервис Kildear!</b>\n\n📋 Тарифы:\n• 🎁 2 дня бесплатно\n• 🔥 1 месяц — 139 ₽\n• ⭐ 3 месяца — 469 ₽\n• 💎 1 год — 899 ₽",
        reply_markup=get_main_keyboard(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("🏠 Главное меню", reply_markup=get_main_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "buy_subscription")
async def show_plans(callback: CallbackQuery):
    active_sub = get_active_subscription(callback.from_user.id)
    plans_text = "📱 Выберите тариф:\n\n"
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            if days_left > 0:
                plans_text += f"✅ Активна до {end_date.strftime('%d.%m.%Y')} (осталось {days_left} дн.)\n\n"
        except:
            pass
    for plan_id, plan in PLANS.items():
        price_text = f"{plan['price']} ₽" if plan['price'] > 0 else "Бесплатно"
        plans_text += f"{plan['emoji']} {plan['label']} — {price_text}\n"
    await callback.message.edit_text(plans_text, reply_markup=get_plans_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_keys")
async def show_keys(callback: CallbackQuery):
    user_id = callback.from_user.id
    keys = get_user_vpn_keys(user_id)
    if not keys:
        await callback.message.edit_text("🔑 Нет активных ключей", reply_markup=get_keys_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    text = "🔑 Ваши ключи:\n\n"
    for idx, key_data in enumerate(keys, 1):
        key, expires_at, status, devices, port = key_data
        expires = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S.%f')
        days_left = (expires - datetime.now()).days
        text += f"<b>#{idx}</b>\n<code>{key}</code>\n📱 {devices} уст. ⏳ {days_left} дн.\n\n"
    await callback.message.edit_text(text, reply_markup=get_keys_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_subscription")
async def show_subscription(callback: CallbackQuery):
    user_id = callback.from_user.id
    active_sub = get_active_subscription(user_id)
    if not active_sub:
        await callback.message.edit_text("❌ Нет активной подписки", reply_markup=get_main_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    sub_id = active_sub.get('subscription_uuid', '')
    subscription_url = f"{SUBSCRIPTION_BASE_URL}{sub_id}"
    end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
    text = f"📥 Ссылка для подписки:\n<code>{subscription_url}</code>\n\n📅 До: {end_date.strftime('%d.%m.%Y')}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Копировать", callback_data=f"copy_{sub_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("copy_"))
async def copy_sub(callback: CallbackQuery):
    sub_id = callback.data.replace("copy_", "")
    await callback.message.answer(f"🔗 <code>{SUBSCRIPTION_BASE_URL}{sub_id}</code>", parse_mode="HTML")
    await callback.answer("✅ Скопируйте!")

@dp.callback_query(F.data == "refresh_keys")
async def refresh_keys(callback: CallbackQuery):
    await show_keys(callback)

@dp.callback_query(F.data.startswith("plan_"))
async def process_plan(callback: CallbackQuery):
    plan_id = callback.data.replace("plan_", "")
    plan = PLANS.get(plan_id)
    if not plan:
        await callback.answer("❌ Тариф не найден")
        return
    user_id = callback.from_user.id

    if plan['price'] == 0:
        conn = sqlite3.connect('subscriptions.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND plan_id = '2d' AND status != 'failed'", (user_id,))
        count = c.fetchone()[0]
        conn.close()
        if count > 0:
            await callback.answer("❌ Вы уже использовали бесплатный период!", show_alert=True)
            return
        _, vpn_key, success = create_subscription(user_id, plan_id, 0)
        if not success:
            await callback.message.edit_text("⚠️ Не удалось создать ключ на сервере. Попробуйте ещё раз чуть позже.", reply_markup=get_main_keyboard(), parse_mode="HTML")
            await callback.answer()
            return
        text = f"🎉 Бесплатная подписка активирована!\n\n📅 {plan['days']} дней\n🔌 Порт: {VPN_SERVER_PORT}\n\n🔑 Ключ:\n<code>{vpn_key}</code>"
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")
        await callback.answer()
        return

    text = f"💳 <b>Оплата подписки</b>\n\nТариф: {plan['label']}\nСумма: {plan['price']} ₽\n\n⚠️ <b>Тестовый режим</b>"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тестовая активация", callback_data=f"test_{plan_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("test_"))
async def test_activate(callback: CallbackQuery):
    plan_id = callback.data.replace("test_", "")
    plan = PLANS.get(plan_id)
    user_id = callback.from_user.id
    if not plan:
        await callback.answer("❌ Ошибка")
        return
    _, vpn_key, success = create_subscription(user_id, plan_id, plan['price'], "test")
    if not success:
        await callback.message.edit_text("⚠️ Не удалось создать ключ на сервере. Попробуйте ещё раз через минуту.", reply_markup=get_main_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    text = f"✅ Подписка активирована!\n\n🎉 {plan['label']}\n📅 {plan['days']} дней\n🔌 Порт: {VPN_SERVER_PORT}\n\n🔑 Ключ:\n<code>{vpn_key}</code>"
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)
    active_sub = get_active_subscription(user_id)
    keys = get_user_vpn_keys(user_id)
    text = f"👤 Профиль\n\n🆔 ID: {user_id}"
    if user:
        text += f"\n📝 Имя: {user.get('first_name', '?')}"
    if active_sub:
        try:
            end_date = datetime.strptime(active_sub['end_date'], '%Y-%m-%d %H:%M:%S.%f')
            days_left = (end_date - datetime.now()).days
            plan = PLANS.get(active_sub['plan_id'], {})
            text += f"\n\n📱 Подписка: {plan.get('label', '?')}"
            text += f"\n📅 До: {end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
            text += f"\n🔌 Порт: {active_sub.get('port', '?')}"
        except:
            pass
    else:
        text += "\n\n❌ Нет подписки"
    text += f"\n🔑 Ключей: {len(keys)}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Купить", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="my_keys")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    text = "🆘 Помощь\n\n1. Нажмите 'Купить подписку'\n2. Выберите тариф\n3. Нажмите 'Тестовая активация'\n\nКлюч появится в 'Мои ключи'."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

# ======================= WEBHOOK =======================
async def webhook_handler(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return web.Response(status=500)

async def on_startup():
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(webhook_url)
        logger.info(f"✅ Вебхук: {webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка: {e}")

async def main():
    port = int(os.environ.get('PORT', 8080))
    app = web.Application()
    app.router.add_post('/webhook', webhook_handler)
    app.router.add_get('/', lambda request: web.Response(text='Bot is running!'))
    await on_startup()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🚀 Бот запущен на порту {port}")
    logger.info(f"📡 Панель: {XRAY_PANEL_URL}{XRAY_PANEL_PATH}")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
