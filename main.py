import os
import json
import logging
import uuid
import time
import random
import string
import base64
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from io import BytesIO

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from bs4 import BeautifulSoup
import qrcode

# ======================= НАСТРОЙКИ =======================
BOT_TOKEN = "8656434661:AAHv3yKPvStdiSDcSiBJPxKaYSgmJLtBlpo"

# 3xUI панель
PANEL_URL = "https://2.26.70.65:55347/mMH522DscvfBbpFwaA"
PANEL_SUB_URL = "https://2.26.70.65:2096/sub"
PANEL_USERNAME = "fHRTAk9lFz"
PANEL_PASSWORD = "pM0xjYSy4N"
PANEL_API_TOKEN = "tlZpRhpGQA54Uyta9p9chp42oymKG8NjoauzprvEqHSHqrye"
PANEL_INBOUND_ID = 2

# ЮKassa
YKASSA_SHOP_ID = "1434221"
YKASSA_SECRET_KEY = "live_fH2K3m3SygBdP8P6bjaOwkRj4UKl5FwsatLZC-PJKt8"
YKASSA_API_URL = "https://api.yookassa.ru/v3/payments"

# Поддержка
SUPPORT_USERNAME = "kildear_vpn"

DB_FILE = "users_db.json"

# ======================= ТАРИФЫ =======================
PLANS = {
    "2d": {"days": 2, "price": 0, "traffic": 5, "label": "🎁 2 дня бесплатно", "emoji": "🎁"},
    "1m": {"days": 30, "price": 139, "traffic": 1000, "label": "1 месяц", "emoji": "🔥"},
    "3m": {"days": 90, "price": 469, "traffic": 1000, "label": "3 месяца", "emoji": "⭐"},
    "1y": {"days": 365, "price": 899, "traffic": 1000, "label": "1 год", "emoji": "💎"},
}

# Состояния
SELECTING_ACTION, SELECTING_PLAN, WAITING_PAYMENT = range(3)

# ======================= ЛОГИРОВАНИЕ =======================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ======================= РАБОТА С БАЗОЙ =======================
class UserDB:
    def __init__(self, db_file=DB_FILE):
        self.db_file = db_file
        self.data = self.load()

    def load(self):
        try:
            with open(self.db_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save(self):
        with open(self.db_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_user(self, user_id: int) -> dict:
        user_id_str = str(user_id)
        if user_id_str not in self.data:
            self.data[user_id_str] = {
                "user_id": user_id,
                "username": None,
                "first_name": None,
                "subscriptions": [],
                "created_at": datetime.now().isoformat()
            }
            self.save()
        return self.data[user_id_str]

    def add_subscription(self, user_id: int, sub_data: dict):
        user = self.get_user(user_id)
        user["subscriptions"].append(sub_data)
        self.save()

    def get_active_subscriptions(self, user_id: int) -> list:
        user = self.get_user(user_id)
        active = []
        now = datetime.now()
        for sub in user["subscriptions"]:
            expiry = datetime.fromisoformat(sub["expiry_date"])
            if expiry > now and sub.get("active", True):
                active.append(sub)
        return active

    def has_free_trial(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        for sub in user["subscriptions"]:
            if sub.get("plan_key") == "2d" and sub.get("active", True):
                return True
        return False

    def update_subscription(self, user_id: int, sub_id: str, data: dict):
        user = self.get_user(user_id)
        for sub in user["subscriptions"]:
            if sub.get("sub_id") == sub_id:
                sub.update(data)
                self.save()
                return True
        return False


db = UserDB()


# ======================= РАБОТА С 3XUI =======================
class PanelClient:
    def __init__(self):
        self.base_url = PANEL_URL
        self.username = PANEL_USERNAME
        self.password = PANEL_PASSWORD
        self.api_token = PANEL_API_TOKEN
        self.inbound_id = PANEL_INBOUND_ID
        self.session = requests.Session()
        self.session.verify = False
        self.csrf_token = None

    def login(self):
        try:
            response = self.session.get(self.base_url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            csrf_meta = soup.find('meta', {'name': 'csrf-token'})
            if csrf_meta:
                self.csrf_token = csrf_meta.get('content')

            login_url = f"{self.base_url}/login"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRF-TOKEN": self.csrf_token,
                "X-Requested-With": "XMLHttpRequest"
            }
            payload = {
                "username": self.username,
                "password": self.password,
                "csrf_token": self.csrf_token
            }

            response = self.session.post(login_url, json=payload, headers=headers, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def generate_uuid(self):
        return str(uuid.uuid4())

    def generate_sub_id(self, length=16):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def generate_email(self, length=10):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def generate_password(self, length=16):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def generate_auth(self, length=16):
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def create_client(self, days: int, traffic_gb: int = 1000, email: str = None) -> Optional[dict]:
        """Создает клиента в панели с указанным трафиком"""
        if not self.login():
            logger.error("Failed to login to panel")
            return None

        if not email:
            email = self.generate_email()

        client_uuid = self.generate_uuid()
        sub_id = self.generate_sub_id()
        password = self.generate_password()
        auth = self.generate_auth()
        expiry_time = int((datetime.now() + timedelta(days=days)).timestamp() * 1000)

        client_data = {
            "client": {
                "id": client_uuid,
                "security": "auto",
                "password": password,
                "auth": auth,
                "email": email,
                "limitIp": 0,
                "totalGB": traffic_gb,  # Трафик в GB
                "expiryTime": expiry_time,
                "enable": True,
                "tgId": 0,
                "subId": sub_id,
                "comment": f"Created {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                "reset": 0
            },
            "inboundIds": [self.inbound_id]
        }

        url = f"{self.base_url}/panel/api/clients/add"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-CSRF-TOKEN": self.csrf_token,
            "Authorization": f"Bearer {self.api_token}"
        }

        try:
            response = self.session.post(url, json=client_data, headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    return {
                        "email": email,
                        "uuid": client_uuid,
                        "sub_id": sub_id,
                        "password": password,
                        "auth": auth,
                        "expiry_time": expiry_time,
                        "expiry_date": datetime.fromtimestamp(expiry_time / 1000).isoformat(),
                        "traffic_gb": traffic_gb
                    }
            logger.error(f"Create client error: {response.text}")
            return None
        except Exception as e:
            logger.error(f"Error creating client: {e}")
            return None

    def generate_vless_link(self, uuid: str, email: str) -> str:
        return f"vless://{uuid}@2.26.70.65:40224?encryption=none&security=none&type=ws&path=/vpn&flow=xtls-rprx-vision&sni=2.26.70.65#{email}"


panel_client = PanelClient()


# ======================= ЮKASSA =======================
class YooKassaClient:
    def __init__(self):
        self.shop_id = YKASSA_SHOP_ID
        self.secret_key = YKASSA_SECRET_KEY
        self.api_url = YKASSA_API_URL

    def create_payment(self, amount: int, description: str, user_id: int, plan_key: str) -> Optional[dict]:
        """Создает платеж в ЮKassa"""
        auth = f"{self.shop_id}:{self.secret_key}"
        auth_b64 = base64.b64encode(auth.encode()).decode()

        payment_data = {
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/KildearVPN_bot"
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": str(user_id),
                "plan_key": plan_key
            }
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth_b64}",
            "Idempotence-Key": str(uuid.uuid4())
        }

        try:
            logger.info(f"Sending payment request: {payment_data}")
            response = requests.post(self.api_url, json=payment_data, headers=headers, timeout=30)

            if response.status_code in [200, 201]:
                result = response.json()
                logger.info(f"Payment created: {result}")
                return result
            else:
                logger.error(f"YooKassa error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"YooKassa exception: {e}")
            return None

    def get_payment_status(self, payment_id: str) -> Optional[dict]:
        """Получает статус платежа"""
        auth = f"{self.shop_id}:{self.secret_key}"
        auth_b64 = base64.b64encode(auth.encode()).decode()

        url = f"{self.api_url}/{payment_id}"
        headers = {
            "Authorization": f"Basic {auth_b64}"
        }

        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error getting payment status: {e}")
            return None


yookassa_client = YooKassaClient()


# ======================= QR КОД =======================
def generate_qr_code_base64(link: str) -> Optional[str]:
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=8,
            border=2,
        )
        qr.add_data(link)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return img_str
    except Exception as e:
        logger.error(f"QR code error: {e}")
        return None


# ======================= КЛАВИАТУРЫ =======================
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📡 Купить VPN", callback_data="buy")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="my_subs")],
        [InlineKeyboardButton("🆘 Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_plans_keyboard():
    keyboard = []
    for key, plan in PLANS.items():
        price_text = "🎁 Бесплатно" if plan["price"] == 0 else f"{plan['price']} ₽"
        traffic_text = f"{plan['traffic']} GB" if plan['traffic'] < 1000 else "1 TB"
        keyboard.append([
            InlineKeyboardButton(
                f"{plan['emoji']} {plan['label']} - {price_text} ({traffic_text})",
                callback_data=f"plan_{key}"
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


def get_payment_keyboard(payment_url: str):
    keyboard = [
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton("✅ Проверить оплату", callback_data="check_payment")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_payment")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_subscription_keyboard(sub_id: str):
    keyboard = [
        [InlineKeyboardButton("📱 QR-код", callback_data=f"qr_{sub_id}")],
        [InlineKeyboardButton("🔙 Назад к списку", callback_data="my_subs")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ======================= ОБРАБОТЧИКИ =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_user(user.id)

    welcome_text = f"""
👋 Привет, {user.first_name}!

Добро пожаловать в **Kildear VPN** — твой надежный защитник в интернете! 🛡️

🌐 Мы предлагаем:
• 🚀 Быстрый и стабильный VPN
• 🔒 Защита от слежки
• 🌍 Доступ к заблокированным сайтам
• 📊 До 1 ТБ трафика

🎁 Попробуй **2 дня бесплатно!**
💰 Цены от 139 ₽ в месяц

Выбери действие в меню ниже 👇
    """

    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = f"""
🆘 **Помощь и инструкция**

📌 **Как купить подписку:**
1. Нажми "📡 Купить VPN"
2. Выбери тариф
3. Оплати через ЮKassa
4. Получи свой ключ и подписку

📱 **Как подключиться:**
1. Скачай VPN клиент (v2rayN, Nekoray, Clash)
2. Скопируй VLESS ссылку или QR-код
3. Импортируй подписку в клиент

💬 **Поддержка:** @{SUPPORT_USERNAME}

🔗 **Наш канал:** @KildearVPN
    """

    await update.message.reply_text(
        help_text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data == "back":
        await query.edit_message_text(
            "🏠 Главное меню",
            reply_markup=get_main_keyboard()
        )
        return

    if data == "buy":
        await query.edit_message_text(
            "📡 Выбери тарифный план:",
            reply_markup=get_plans_keyboard()
        )
        return

    if data == "my_subs":
        await show_subscriptions(query)
        return

    if data == "help":
        await query.edit_message_text(
            f"🆘 Помощь\n\nПоддержка: @{SUPPORT_USERNAME}",
            reply_markup=get_main_keyboard()
        )
        return

    if data.startswith("plan_"):
        plan_key = data.replace("plan_", "")
        plan = PLANS.get(plan_key)

        if not plan:
            await query.edit_message_text("❌ Тариф не найден")
            return

        context.user_data["selected_plan"] = plan_key

        if plan["price"] == 0:
            await handle_free_plan(query, plan_key)
        else:
            await handle_paid_plan(query, plan_key, context)
        return

    if data == "check_payment":
        await check_payment_status(query, context)
        return

    if data == "cancel_payment":
        context.user_data.pop("payment_id", None)
        context.user_data.pop("payment_plan", None)
        await query.edit_message_text(
            "❌ Оплата отменена",
            reply_markup=get_main_keyboard()
        )
        return

    if data.startswith("sub_"):
        sub_id = data.replace("sub_", "")
        await show_subscription_details(query, sub_id)
        return

    if data.startswith("qr_"):
        sub_id = data.replace("qr_", "")
        await show_qr_code(query, sub_id)
        return


async def handle_free_plan(query, plan_key: str):
    user_id = query.from_user.id
    plan = PLANS[plan_key]

    if db.has_free_trial(user_id):
        await query.edit_message_text(
            "❌ Вы уже использовали бесплатный тариф!\n\n"
            "Выберите платный тариф для продолжения:",
            reply_markup=get_plans_keyboard()
        )
        return

    client = panel_client.create_client(days=plan["days"], traffic_gb=plan["traffic"])

    if not client:
        await query.edit_message_text(
            "❌ Ошибка при создании ключа. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
        return

    sub_data = {
        "sub_id": client["sub_id"],
        "plan_key": plan_key,
        "email": client["email"],
        "uuid": client["uuid"],
        "vless_link": panel_client.generate_vless_link(client["uuid"], client["email"]),
        "sub_link": f"{PANEL_SUB_URL}/{client['sub_id']}",
        "expiry_date": client["expiry_date"],
        "created_at": datetime.now().isoformat(),
        "active": True,
        "paid": False,
        "traffic_gb": plan["traffic"]
    }
    db.add_subscription(user_id, sub_data)

    await send_subscription_keys(query, sub_data)


async def handle_paid_plan(query, plan_key: str, context: ContextTypes.DEFAULT_TYPE):
    plan = PLANS[plan_key]
    user_id = query.from_user.id

    payment_desc = f"Подписка {plan['label']} Kildear VPN"

    payment = yookassa_client.create_payment(
        amount=plan["price"],
        description=payment_desc,
        user_id=user_id,
        plan_key=plan_key
    )

    if not payment:
        await query.edit_message_text(
            "❌ Ошибка при создании платежа. Попробуйте позже.\n"
            f"Если проблема повторяется, обратитесь к @{SUPPORT_USERNAME}",
            reply_markup=get_main_keyboard()
        )
        return

    context.user_data["payment_id"] = payment["id"]
    context.user_data["payment_plan"] = plan_key

    payment_url = payment["confirmation"]["confirmation_url"]

    await query.edit_message_text(
        f"""
💳 **Оплата подписки {plan['label']}**

💰 Сумма: {plan['price']} ₽
📊 Трафик: {plan['traffic']} GB
⏰ Длительность: {plan['days']} дней

Нажмите кнопку ниже, чтобы перейти к оплате.
После оплаты нажмите "✅ Проверить оплату".
        """,
        reply_markup=get_payment_keyboard(payment_url),
        parse_mode="Markdown"
    )


async def check_payment_status(query, context: ContextTypes.DEFAULT_TYPE):
    payment_id = context.user_data.get("payment_id")
    plan_key = context.user_data.get("payment_plan")

    if not payment_id:
        await query.edit_message_text(
            "❌ Платеж не найден. Попробуйте выбрать тариф заново.",
            reply_markup=get_main_keyboard()
        )
        return

    payment_status = yookassa_client.get_payment_status(payment_id)

    if not payment_status:
        await query.edit_message_text(
            "❌ Не удалось проверить статус оплаты. Попробуйте позже.",
            reply_markup=get_main_keyboard()
        )
        return

    if payment_status.get("status") == "succeeded":
        await query.edit_message_text("✅ Оплата подтверждена! Создаю ваш ключ...")

        plan = PLANS[plan_key]
        user_id = query.from_user.id

        client = panel_client.create_client(days=plan["days"], traffic_gb=plan["traffic"])

        if not client:
            await query.edit_message_text(
                f"❌ Ошибка при создании ключа. Обратитесь к @{SUPPORT_USERNAME}",
                reply_markup=get_main_keyboard()
            )
            return

        sub_data = {
            "sub_id": client["sub_id"],
            "plan_key": plan_key,
            "email": client["email"],
            "uuid": client["uuid"],
            "vless_link": panel_client.generate_vless_link(client["uuid"], client["email"]),
            "sub_link": f"{PANEL_SUB_URL}/{client['sub_id']}",
            "expiry_date": client["expiry_date"],
            "created_at": datetime.now().isoformat(),
            "active": True,
            "paid": True,
            "payment_id": payment_id,
            "traffic_gb": plan["traffic"]
        }
        db.add_subscription(user_id, sub_data)

        await send_subscription_keys(query, sub_data)

        context.user_data.pop("payment_id", None)
        context.user_data.pop("payment_plan", None)

    elif payment_status.get("status") == "pending":
        payment_url = payment_status.get("confirmation", {}).get("confirmation_url", "")
        await query.edit_message_text(
            "⏳ Платеж еще не завершен. Подождите или оплатите по ссылке.",
            reply_markup=get_payment_keyboard(payment_url)
        )
    else:
        await query.edit_message_text(
            f"❌ Статус платежа: {payment_status.get('status')}\n"
            "Попробуйте оплатить заново.",
            reply_markup=get_plans_keyboard()
        )


async def show_subscriptions(query):
    user_id = query.from_user.id
    subscriptions = db.get_active_subscriptions(user_id)

    if not subscriptions:
        await query.edit_message_text(
            "📋 У вас нет активных подписок.\n\n"
            "Выберите тариф, чтобы начать пользоваться VPN:",
            reply_markup=get_plans_keyboard()
        )
        return

    text = "📋 Ваши активные подписки:\n\n"
    keyboard = []

    for sub in subscriptions:
        expiry = datetime.fromisoformat(sub["expiry_date"])
        days_left = (expiry - datetime.now()).days
        plan_key = sub.get("plan_key", "unknown")
        plan = PLANS.get(plan_key, {"emoji": "🔹", "label": "VPN"})
        traffic = sub.get("traffic_gb", 5)
        traffic_text = f"{traffic} GB" if traffic < 1000 else "1 TB"

        text += f"{plan['emoji']} {plan['label']}\n"
        text += f"   🆔 {sub['sub_id'][:12]}...\n"
        text += f"   📊 Трафик: {traffic_text}\n"
        text += f"   ⏰ Осталось: {days_left} дней\n\n"

        keyboard.append([
            InlineKeyboardButton(
                f"📄 Подробнее о {sub['sub_id'][:8]}...",
                callback_data=f"sub_{sub['sub_id']}"
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_subscription_details(query, sub_id: str):
    user_id = query.from_user.id
    user = db.get_user(user_id)

    sub = None
    for s in user["subscriptions"]:
        if s.get("sub_id") == sub_id:
            sub = s
            break

    if not sub:
        await query.edit_message_text(
            "❌ Подписка не найдена",
            reply_markup=get_main_keyboard()
        )
        return

    plan_key = sub.get("plan_key", "unknown")
    plan = PLANS.get(plan_key, {"label": "VPN", "emoji": "🔹"})
    expiry = datetime.fromisoformat(sub["expiry_date"])
    days_left = (expiry - datetime.now()).days
    traffic = sub.get("traffic_gb", 5)
    traffic_text = f"{traffic} GB" if traffic < 1000 else "1 TB"

    text = f"""
{plan['emoji']} **Подписка {plan['label']}**

📊 **Детали:**
• 🆔 SubId: `{sub['sub_id']}`
• 👤 Email: `{sub['email']}`
• 📅 Создана: {datetime.fromisoformat(sub['created_at']).strftime('%d.%m.%Y %H:%M')}
• ⏰ Истекает: {expiry.strftime('%d.%m.%Y %H:%M')}
• 📆 Осталось дней: {days_left}
• 📊 Трафик: {traffic_text}

🔗 **VLESS ссылка:**
`{sub['vless_link']}`

📋 **Ссылка подписки:**
{sub['sub_link']}

Импортируйте ссылку в ваш VPN клиент.
    """

    await query.edit_message_text(
        text,
        reply_markup=get_subscription_keyboard(sub_id),
        parse_mode="Markdown"
    )


async def show_qr_code(query, sub_id: str):
    user_id = query.from_user.id
    user = db.get_user(user_id)

    sub = None
    for s in user["subscriptions"]:
        if s.get("sub_id") == sub_id:
            sub = s
            break

    if not sub:
        await query.edit_message_text("❌ Подписка не найдена")
        return

    qr_base64 = generate_qr_code_base64(sub["vless_link"])

    if qr_base64:
        image_data = base64.b64decode(qr_base64)
        await query.bot.send_photo(
            chat_id=user_id,
            photo=BytesIO(image_data),
            caption=f"📱 QR-код для подписки {sub_id[:8]}..."
        )
        await query.edit_message_text(
            "📱 QR-код отправлен выше!",
            reply_markup=get_subscription_keyboard(sub_id)
        )
    else:
        await query.edit_message_text(
            "❌ Не удалось сгенерировать QR-код",
            reply_markup=get_subscription_keyboard(sub_id)
        )


async def send_subscription_keys(query, sub_data: dict):
    user_id = query.from_user.id
    plan_key = sub_data.get("plan_key", "unknown")
    plan = PLANS.get(plan_key, {"label": "VPN", "emoji": "🔹"})
    expiry = datetime.fromisoformat(sub_data["expiry_date"])
    traffic = sub_data.get("traffic_gb", 5)
    traffic_text = f"{traffic} GB" if traffic < 1000 else "1 TB"

    text = f"""
🎉 **Поздравляем! Ваша подписка активирована!**

{plan['emoji']} **Тариф:** {plan['label']}
⏰ **Действует до:** {expiry.strftime('%d.%m.%Y %H:%M')}
📊 **Трафик:** {traffic_text}

🔗 **VLESS ссылка (скопируйте):**
`{sub_data['vless_link']}`

📋 **Ссылка подписки (для импорта):**
{sub_data['sub_link']}

💡 **Как подключиться:**
1. Скачайте VPN клиент (v2rayN, Nekoray, Clash)
2. Скопируйте VLESS ссылку выше
3. Импортируйте в клиент
4. Наслаждайтесь свободным интернетом! 🌐

⚠️ Сохраните эти данные!
    """

    # Генерируем QR-код
    qr_base64 = generate_qr_code_base64(sub_data["vless_link"])

    if qr_base64:
        image_data = base64.b64decode(qr_base64)
        await query.bot.send_photo(
            chat_id=user_id,
            photo=BytesIO(image_data),
            caption="📱 QR-код для быстрого подключения"
        )

    await query.edit_message_text(
        text,
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )


# ======================= ЗАПУСК =======================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("🚀 Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
