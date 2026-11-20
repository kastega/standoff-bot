import asyncio
import logging
import os
import random
import time
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

# ==== НАСТРОЙКИ ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "bot.db")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel")

_admin_env = os.getenv("ADMIN_IDS", "")
if _admin_env:
    ADMIN_IDS = {int(x) for x in _admin_env.split(",") if x.strip().isdigit()}
else:
    ADMIN_IDS = {123456789}

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN.")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

LAST_CLICK = {}
CLICK_COOLDOWN = 2
HILO_CURRENT = {}

HEADER = "🎮 <b>STANDOFF 2 — БОТ ЗАДАНИЙ</b>\n"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━\n"
BOT_USERNAME = "your_bot"


# ==== БАЗА ДАННЫХ ====
def db_init():
    parent = os.path.dirname(DB_PATH)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            sub_done INTEGER DEFAULT 0,
            video_done INTEGER DEFAULT 0,
            purchases INTEGER DEFAULT 0,
            referrer_id INTEGER DEFAULT NULL,
            ref_count INTEGER DEFAULT 0,
            twitch_done INTEGER DEFAULT 0,
            last_bonus INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            reward INTEGER NOT NULL,
            uses_left INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_used (
            code TEXT,
            user_id INTEGER,
            PRIMARY KEY (code, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            details TEXT,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at INTEGER
        )
    """)
    conn.commit()
    conn.close()


def db_get_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT balance, sub_done, video_done, purchases, referrer_id, ref_count, twitch_done, last_bonus, gold "
        "FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def db_add_user(user_id: int, username: str, referrer_id: int = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username or ""))
    if referrer_id and referrer_id != user_id:
        cur.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        if r and r[0] is None:
            cur.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer_id, user_id))
            cur.execute("UPDATE users SET ref_count = ref_count + 1, balance = balance + 5 WHERE user_id = ?", (referrer_id,))
            cur.execute("UPDATE users SET balance = balance + 3 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def db_add_balance(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def db_add_gold(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET gold = gold + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def db_set_balance(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def db_mark_task(user_id: int, task: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if task == "sub":
        cur.execute("UPDATE users SET sub_done = 1 WHERE user_id = ?", (user_id,))
    elif task == "video":
        cur.execute("UPDATE users SET video_done = 1 WHERE user_id = ?", (user_id,))
    elif task == "twitch":
        cur.execute("UPDATE users SET twitch_done = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def db_set_last_bonus(user_id: int, ts: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (ts, user_id))
    conn.commit()
    conn.close()


def db_add_purchase(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET purchases = purchases + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def db_get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, balance, ref_count FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows


def db_create_promo(code: str, reward: int, uses: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO promos (code, reward, uses_left) VALUES (?, ?, ?)", (code, reward, uses))
    conn.commit()
    conn.close()


def db_get_promo(code: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT reward, uses_left FROM promos WHERE code = ?", (code,))
    row = cur.fetchone()
    conn.close()
    return row


def db_use_promo(code: str, user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO promo_used (code, user_id) VALUES (?, ?)", (code, user_id))
    cur.execute("UPDATE promos SET uses_left = uses_left - 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()


def db_promo_already_used(code: str, user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM promo_used WHERE code = ? AND user_id = ?", (code, user_id))
    row = cur.fetchone()
    conn.close()
    return row is not None


def db_list_promos():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT code, reward, uses_left FROM promos ORDER BY code")
    rows = cur.fetchall()
    conn.close()
    return rows


def db_create_withdrawal(user_id: int, amount: int, details: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO withdrawals (user_id, amount, details, created_at) VALUES (?, ?, ?, ?)",
        (user_id, amount, details, int(time.time())),
    )
    conn.commit()
    conn.close()


def db_get_pending_withdrawals():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, amount, details FROM withdrawals WHERE status = 'pending' ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def db_complete_withdrawal(withdrawal_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount FROM withdrawals WHERE id = ? AND status = 'pending'", (withdrawal_id,))
    row = cur.fetchone()
    if row:
        user_id, amount = row
        cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        cur.execute("UPDATE withdrawals SET status = 'done' WHERE id = ?", (withdrawal_id,))
    conn.commit()
    conn.close()


def db_create_deposit(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO deposits (user_id, amount, created_at) VALUES (?, ?, ?)",
        (user_id, amount, int(time.time())),
    )
    conn.commit()
    conn.close()


def db_get_pending_deposits():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, amount FROM deposits WHERE status = 'pending' ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def db_complete_deposit(deposit_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount FROM deposits WHERE id = ? AND status = 'pending'", (deposit_id,))
    row = cur.fetchone()
    if row:
        user_id, amount = row
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        cur.execute("UPDATE deposits SET status = 'done' WHERE id = ?", (deposit_id,))
    conn.commit()
    conn.close()


# ==== ОФОРМЛЕНИЕ ====
def profile_text(user_id: int) -> str:
    u = db_get_user(user_id)
    balance = u[0] if u else 0
    purchases = u[3] if u else 0
    ref_count = u[5] if u else 0
    gold = u[8] if u else 0
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
    return (
        f"{HEADER}{DIVIDER}"
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💎 Баланс: <code>{balance}</code> баллов\n"
        f"🌟 Голда: <code>{gold}</code>\n"
        f"🛒 Покупок: <code>{purchases}</code>\n"
        f"👥 Рефералов: <code>{ref_count}</code>\n\n"
        f"🔗 Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"{DIVIDER}"
        f"Выбери действие ниже 👇"
    )


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="shop")],
        [InlineKeyboardButton(text="🎁 Ввести промокод", callback_data="enter_promo")],
        [InlineKeyboardButton(text="📋 Задания", callback_data="tasks")],
        [InlineKeyboardButton(text="💸 Вывод средств", callback_data="withdraw")],
    ])


def shop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Кейс Миг Удачи — 25", callback_data="buy_case")],
        [InlineKeyboardButton(text="💎 100 GOLD — 65", callback_data="buy_gold100")],
        [InlineKeyboardButton(text="💎 1000 GOLD — 600", callback_data="buy_gold1000")],
        [InlineKeyboardButton(text="✊ Камень-ножницы-бумага — 3", callback_data="game_rps")],
        [InlineKeyboardButton(text="🔢 Угадай число — 3", callback_data="game_guess")],
        [InlineKeyboardButton(text="🎲 Кости — 3", callback_data="game_dice")],
        [InlineKeyboardButton(text="🔢 Больше-меньше — 3", callback_data="game_hilo")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


def tasks_keyboard(user_id: int) -> InlineKeyboardMarkup:
    u = db_get_user(user_id)
    sub_done = u[1] if u else 0
    video_done = u[2] if u else 0
    twitch_done = u[6] if u else 0

    rows = []
    if not sub_done:
        rows.append([InlineKeyboardButton(text="📺 Подписаться на канал  +5", callback_data="task_sub")])
    if not video_done:
        rows.append([InlineKeyboardButton(text="👀 Посмотреть видео  +3", callback_data="task_video")])
    if not twitch_done:
        rows.append([InlineKeyboardButton(text="🟣 Перейти на Twitch", callback_data="twitch_go")])
    if not rows:
        rows.append([InlineKeyboardButton(text="✅ Все задания выполнены", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_list_promos")],
        [InlineKeyboardButton(text="💰 Изменить баланс", callback_data="admin_set_balance")],
        [InlineKeyboardButton(text="💳 Заявки на депозит", callback_data="admin_deposits")],
        [InlineKeyboardButton(text="💸 Заявки на вывод", callback_data="admin_withdrawals")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])


def reply_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🛒 Магазин")],
            [KeyboardButton(text="🎁 Бонус"), KeyboardButton(text="📋 Задания")],
            [KeyboardButton(text="💳 Депозит"), KeyboardButton(text="💸 Вывод")],
            [KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def rps_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✊ Камень", callback_data="rps_rock"),
            InlineKeyboardButton(text="✋ Бумага", callback_data="rps_paper"),
            InlineKeyboardButton(text="✌️ Ножницы", callback_data="rps_scissors"),
        ],
    ])


def guess_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="guess_1"),
            InlineKeyboardButton(text="2", callback_data="guess_2"),
            InlineKeyboardButton(text="3", callback_data="guess_3"),
            InlineKeyboardButton(text="4", callback_data="guess_4"),
            InlineKeyboardButton(text="5", callback_data="guess_5"),
        ],
        [
            InlineKeyboardButton(text="6", callback_data="guess_6"),
            InlineKeyboardButton(text="7", callback_data="guess_7"),
            InlineKeyboardButton(text="8", callback_data="guess_8"),
            InlineKeyboardButton(text="9", callback_data="guess_9"),
            InlineKeyboardButton(text="10", callback_data="guess_10"),
        ],
    ])


def hilo_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬆️ Больше", callback_data="hilo_more"),
            InlineKeyboardButton(text="⬇️ Меньше", callback_data="hilo_less"),
        ],
    ])


# ==== АНТИФЛУД ====
def is_flooding(user_id: int) -> bool:
    now = time.time()
    last = LAST_CLICK.get(user_id, 0)
    if now - last < CLICK_COOLDOWN:
        return True
    LAST_CLICK[user_id] = now
    return False


# ==== СТАРТ / АДМИН-КОМАНДЫ ====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split(maxsplit=1)
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            referrer_id = int(args[1][3:])
        except ValueError:
            referrer_id = None
    db_add_user(message.from_user.id, message.from_user.username, referrer_id)
    await message.answer(profile_text(message.from_user.id), reply_markup=main_keyboard())
    await message.answer("Меню закреплено внизу 👇", reply_markup=reply_menu())


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа")
        return
    await message.answer(f"{HEADER}{DIVIDER}🛠 <b>Админ-панель</b>\n{DIVIDER}Выбери действие:", reply_markup=admin_keyboard())


@dp.message(F.text.startswith("/balance"))
async def cmd_set_balance(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Формат: <code>/balance user_id сумма</code>")
        return
    try:
        uid, amount = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("Нужны числа")
        return
    db_set_balance(uid, amount)
    await message.answer(f"✅ Баланс <code>{uid}</code> = <code>{amount}</code>")


@dp.message(F.text.startswith("/promo"))
async def cmd_create_promo(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer("Формат: <code>/promo КОД награда_баллов использований</code>")
        return
    code, reward, uses = parts[1].upper(), parts[2], parts[3]
    try:
        reward, uses = int(reward), int(uses)
    except ValueError:
        await message.answer("Награда и использования — числа")
        return
    db_create_promo(code, reward, uses)
    await message.answer(f"✅ Промокод <code>{code}</code> на {reward} баллов, использований: {uses}")


# ==== ТЕКСТОВЫЕ КНОПКИ REPLY-МЕНЮ ====
@dp.message(F.text == "👤 Профиль")
async def btn_profile(message: Message):
    await message.answer(profile_text(message.from_user.id), reply_markup=reply_menu())


@dp.message(F.text == "🛒 Магазин")
async def btn_shop(message: Message):
    u = db_get_user(message.from_user.id)
    balance = u[0] if u else 0
    gold = u[8] if u else 0
    text = (
        f"🛒 <b>МАГАЗИН</b>\n{DIVIDER}"
        f"💎 Твой баланс: <code>{balance}</code> баллов\n"
        f"🌟 Голда: <code>{gold}</code>\n\n"
        f"🎰 <b>Кейс Миг Удачи</b> — 25 баллов\n"
        f"💎 <b>100 GOLD</b> — 65 баллов\n"
        f"💎 <b>1000 GOLD</b> — 600 баллов\n\n"
        f"🎮 <b>Мини-игры</b> — 3 балла за игру"
    )
    await message.answer(text, reply_markup=shop_keyboard())


@dp.message(F.text == "📋 Задания")
async def btn_tasks(message: Message):
    await message.answer(
        f"{HEADER}{DIVIDER}📋 <b>Задания</b>\n{DIVIDER}Выбери задание:",
        reply_markup=tasks_keyboard(message.from_user.id),
    )


@dp.message(F.text == "🎁 Бонус")
async def btn_daily_bonus(message: Message):
    user_id = message.from_user.id
    db_add_user(user_id, message.from_user.username)
    u = db_get_user(user_id)
    last = u[7] if u else 0
    now = int(time.time())
    if now - last < 86400:
        remaining = 86400 - (now - last)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await message.answer(f"⏳ Бонус уже получен. Следующий через <b>{hours} ч {minutes} мин</b>.")
        return
    reward = random.randint(3, 5)
    db_add_balance(user_id, reward)
    db_set_last_bonus(user_id, now)
    await message.answer(f"🎁 Ежедневный бонус: <b>+{reward}</b> баллов!\nПриходи через 24 часа.")


@dp.message(F.text == "💳 Депозит")
async def btn_deposit(message: Message):
    await message.answer(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Отправь сумму пополнения в баллах одним сообщением.\n"
        "Пример: <code>100</code>\n\n"
        "После создания заявки администратор подтвердит платёж, и баллы будут начислены.",
        reply_markup=reply_menu(),
    )


@dp.message(F.text == "💸 Вывод")
async def btn_withdraw(message: Message):
    await message.answer(
        "💸 <b>Вывод средств</b>\n\n"
        "Курс: <b>1 балл = 1 ₽</b>\n"
        "Минимум: <b>100 баллов</b>\n\n"
        "Отправь одним сообщением: <code>сумма реквизиты</code>\n"
        "Пример: <code>100 2200123456789012</code>",
        reply_markup=reply_menu(),
    )


@dp.message(F.text == "❓ Помощь")
async def btn_help(message: Message):
    await message.answer(
        f"{HEADER}{DIVIDER}❓ <b>Помощь</b>\n{DIVIDER}"
        f"• <b>👤 Профиль</b> — баланс, голда, рефералы\n"
        f"• <b>🛒 Магазин</b> — кейс, GOLD, мини-игры\n"
        f"• <b>🎁 Бонус</b> — раз в 24 часа, 3–5 баллов\n"
        f"• <b>📋 Задания</b> — подписки за баллы\n"
        f"• <b>💳 Депозит</b> — заявка на пополнение\n"
        f"• <b>💸 Вывод</b> — обмен баллов на рубли (1:1), минимум 100\n\n"
        f"Админ-команды: /admin",
        reply_markup=reply_menu(),
    )


# ==== ИНЛАЙН-ХЕНДЛЕРЫ ====
@dp.callback_query(F.data == "back")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text(profile_text(callback.from_user.id), reply_markup=main_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    await callback.message.edit_text(profile_text(callback.from_user.id), reply_markup=main_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "tasks")
async def open_tasks(callback: CallbackQuery):
    await callback.message.edit_text(
        f"{HEADER}{DIVIDER}📋 <b>Задания</b>\n{DIVIDER}Выбери задание:",
        reply_markup=tasks_keyboard(callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "shop")
async def open_shop(callback: CallbackQuery):
    u = db_get_user(callback.from_user.id)
    balance = u[0] if u else 0
    gold = u[8] if u else 0
    text = (
        f"🛒 <b>МАГАЗИН</b>\n{DIVIDER}"
        f"💎 Твой баланс: <code>{balance}</code> баллов\n"
        f"🌟 Голда: <code>{gold}</code>\n\n"
        f"🎰 <b>Кейс Миг Удачи</b> — 25 баллов (от 1 до 50)\n"
        f"💎 <b>100 GOLD</b> — 65 баллов\n"
        f"💎 <b>1000 GOLD</b> — 600 баллов\n\n"
        f"🎮 <b>Мини-игры</b> — 3 балла за игру\n\n"
        f"Выбери товар 👇"
    )
    await callback.message.edit_text(text, reply_markup=shop_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "withdraw")
async def open_withdraw(callback: CallbackQuery):
    await callback.message.answer(
        "💸 <b>Вывод средств</b>\n\n"
        "Курс: <b>1 балл = 1 ₽</b>\n"
        "Минимум: <b>100 баллов</b>\n\n"
        "Отправь одним сообщением: <code>сумма реквизиты</code>\n"
        "Пример: <code>100 2200123456789012</code>",
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_case")
async def buy_case(callback: CallbackQuery):
    if is_flooding(callback.from_user.id):
        await callback.answer("⏳ Подожди пару секунд", show_alert=True)
        return
    u = db_get_user(callback.from_user.id)
    balance = u[0] if u else 0
    price = 25
    if balance < price:
        await callback.answer(f"❌ Нужно {price} баллов, у тебя {balance}", show_alert=True)
        return
    db_add_balance(callback.from_user.id, -price)
    db_add_purchase(callback.from_user.id)
    prize = random.randint(1, 50)
    db_add_balance(callback.from_user.id, prize)
    await callback.answer(f"🎰 Выпало {prize} баллов!", show_alert=True)
    new_bal = balance - price + prize
    await callback.message.edit_text(
        f"🎰 <b>Кейс Миг Удачи</b>\n{DIVIDER}"
        f"💸 Стоимость: <code>{price}</code>\n"
        f"🎁 Выпало: <code>{prize}</code> баллов\n"
        f"💎 Новый баланс: <code>{new_bal}</code>",
        reply_markup=shop_keyboard(),
    )


@dp.callback_query(F.data == "buy_gold100")
async def buy_gold100(callback: CallbackQuery):
    await _buy_gold(callback, 100, 65)


@dp.callback_query(F.data == "buy_gold1000")
async def buy_gold1000(callback: CallbackQuery):
    await _buy_gold(callback, 1000, 600)


async def _buy_gold(callback: CallbackQuery, gold: int, price: int):
    if is_flooding(callback.from_user.id):
        await callback.answer("⏳ Подожди пару секунд", show_alert=True)
        return
    u = db_get_user(callback.from_user.id)
    balance = u[0] if u else 0
    if balance < price:
        await callback.answer(f"❌ Нужно {price} баллов, у тебя {balance}", show_alert=True)
        return
    db_add_balance(callback.from_user.id, -price)
    db_add_purchase(callback.from_user.id)
    db_add_gold(callback.from_user.id, gold)
    await callback.answer(f"✅ Куплено {gold} GOLD", show_alert=True)
    await callback.message.edit_text(
        f"🛒 <b>Покупка</b>\n{DIVIDER}"
        f"🌟 {gold} GOLD\n"
        f"💸 Списано: <code>{price}</code> баллов\n"
        f"💰 Остаток: <code>{balance - price}</code>\n\n"
        f"⚠️ Выдача GOLD производится вручную администратором.",
        reply_markup=shop_keyboard(),
    )


# ==== ЗАДАНИЯ ====
@dp.callback_query(F.data == "task_sub")
async def process_task_sub(callback: CallbackQuery):
    if is_flooding(callback.from_user.id):
        await callback.answer("⏳ Не так быстро", show_alert=True)
        return
    user_id = callback.from_user.id
    db_add_user(user_id, callback.from_user.username)
    u = db_get_user(user_id)
    if u and u[1] == 1:
        await callback.answer("✅ Ты уже подписан на канал — задание выполнено", show_alert=True)
        return
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status in ("member", "administrator", "creator"):
            db_mark_task(user_id, "sub")
            db_add_balance(user_id, 5)
            await callback.answer("🎉 +5 баллов за подписку!", show_alert=True)
            await callback.message.edit_text(
                f"{HEADER}{DIVIDER}📋 <b>Задания</b>\n{DIVIDER}Выбери задание:",
                reply_markup=tasks_keyboard(user_id),
            )
        else:
            await callback.answer("❌ Ты ещё не подписан на канал", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        await callback.answer("⚠️ Не удалось проверить подписку. Проверь настройки канала.", show_alert=True)


@dp.callback_query(F.data == "task_video")
async def process_task_video(callback: CallbackQuery):
    if is_flooding(callback.from_user.id):
        await callback.answer("⏳ Не так быстро", show_alert=True)
        return
    user_id = callback.from_user.id
    db_add_user(user_id, callback.from_user.username)
    u = db_get_user(user_id)
    if u and u[2] == 1:
        await callback.answer("✅ Ты уже выполнил это задание", show_alert=True)
        return
    db_mark_task(user_id, "video")
    db_add_balance(user_id, 3)
    await callback.answer("🎉 +3 балла за видео!", show_alert=True)
    await callback.message.edit_text(
        f"{HEADER}{DIVIDER}📋 <b>Задания</b>\n{DIVIDER}Выбери задание:",
        reply_markup=tasks_keyboard(user_id),
    )


@dp.callback_query(F.data == "twitch_go")
async def twitch_go(callback: CallbackQuery):
    await callback.answer(url="https://www.twitch.tv/kastega")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я подписался (+5)", callback_data="task_twitch")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")],
    ])
    await callback.message.edit_text(
        f"{HEADER}{DIVIDER}📋 <b>Задание Twitch</b>\n{DIVIDER}"
        f"Ты перешёл на Twitch. Подпишись на канал и нажми «Я подписался».",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "task_twitch")
async def process_task_twitch(callback: CallbackQuery):
    if is_flooding(callback.from_user.id):
        await callback.answer("⏳ Не так быстро", show_alert=True)
        return
    user_id = callback.from_user.id
    db_add_user(user_id, callback.from_user.username)
    u = db_get_user(user_id)
    if u and u[6] == 1:
        await callback.answer("✅ Ты уже подписан на Twitch — задание выполнено", show_alert=True)
        return
    db_mark_task(user_id, "twitch")
    db_add_balance(user_id, 5)
    await callback.answer("🎉 +5 баллов за подписку на Twitch!", show_alert=True)
    await callback.message.edit_text(
        f"{HEADER}{DIVIDER}📋 <b>Задания</b>\n{DIVIDER}Выбери задание:",
        reply_markup=tasks_keyboard(user_id),
    )


# ==== ИГРЫ ====
@dp.callback_query(F.data == "game_rps")
async def game_rps_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    u = db_get_user(user_id)
    balance = u[0] if u else 0
    if balance < 3:
        await callback.answer(f"❌ Нужно 3 балла для игры. У тебя {balance}.", show_alert=True)
        return
    db_add_balance(user_id, -3)
    await callback.message.edit_text(
        f"✊✋✌️ <b>Камень-ножницы-бумага</b>\n{DIVIDER}"
        f"💸 Списано: <b>3</b> балла\n"
        f"Выбери свой вариант. Победа — <b>+3</b> балла.",
        reply_markup=rps_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"rps_rock", "rps_paper", "rps_scissors"}))
async def game_rps_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    choices = {"rps_rock": "✊ Камень", "rps_paper": "✋ Бумага", "rps_scissors": "✌️ Ножницы"}
    user_choice = callback.data
    bot_choice = random.choice(list(choices.keys()))
    beats = {"rps_rock": "rps_scissors", "rps_paper": "rps_rock", "rps_scissors": "rps_paper"}

    if user_choice == bot_choice:
        result = "🤝 Ничья!"
        reward = 0
    elif beats[user_choice] == bot_choice:
        result = "🎉 Ты победил!"
        reward = 3
    else:
        result = "😢 Ты проиграл"
        reward = 0

    if reward:
        db_add_balance(user_id, reward)

    await callback.message.edit_text(
        f"✊✋✌️ <b>Камень-ножницы-бумага</b>\n{DIVIDER}"
        f"Ты: <b>{choices[user_choice]}</b>\n"
        f"Бот: <b>{choices[bot_choice]}</b>\n\n"
        f"{result}" + (f"\n💎 +{reward} баллов" if reward else ""),
        reply_markup=shop_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "game_guess")
async def game_guess_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    u = db_get_user(user_id)
    balance = u[0] if u else 0
    if balance < 3:
        await callback.answer(f"❌ Нужно 3 балла для игры. У тебя {balance}.", show_alert=True)
        return
    db_add_balance(user_id, -3)
    await callback.message.edit_text(
        f"🔢 <b>Угадай число</b>\n{DIVIDER}"
        f"💸 Списано: <b>3</b> балла\n"
        f"Я загадал число от <b>1 до 10</b>.\n"
        f"Угадаешь — <b>+5</b> баллов.",
        reply_markup=guess_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("guess_"))
async def game_guess_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        user_num = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    secret = random.randint(1, 10)

    if user_num == secret:
        db_add_balance(user_id, 5)
        result = f"🎉 Угадал! Число было <b>{secret}</b>.\n💎 +5 баллов"
    else:
        result = f"😢 Не угадал. Число было <b>{secret}</b>."

    await callback.message.edit_text(
        f"🔢 <b>Угадай число</b>\n{DIVIDER}"
        f"Твой выбор: <b>{user_num}</b>\n\n"
        f"{result}",
        reply_markup=shop_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "game_dice")
async def game_dice_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    u = db_get_user(user_id)
    balance = u[0] if u else 0
    if balance < 3:
        await callback.answer(f"❌ Нужно 3 балла для игры. У тебя {balance}.", show_alert=True)
        return
    db_add_balance(user_id, -3)
    user_roll = random.randint(1, 6)
    bot_roll = random.randint(1, 6)

    if user_roll > bot_roll:
        db_add_balance(user_id, 6)
        result = "🎉 Ты победил! +3 балла (банк 6)"
    elif user_roll < bot_roll:
        result = "😢 Ты проиграл. Ставка 3 балла потеряна."
    else:
        db_add_balance(user_id, 3)
        result = "🤝 Ничья! Ставка возвращена."

    await callback.message.edit_text(
        f"🎲 <b>Кости</b>\n{DIVIDER}"
        f"Ты: <b>{user_roll}</b>  |  Бот: <b>{bot_roll}</b>\n\n"
        f"{result}",
        reply_markup=shop_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "game_hilo")
async def game_hilo_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    u = db_get_user(user_id)
    balance = u[0] if u else 0
    if balance < 3:
        await callback.answer(f"❌ Нужно 3 балла для игры. У тебя {balance}.", show_alert=True)
        return
    db_add_balance(user_id, -3)
    current = random.randint(1, 100)
    HILO_CURRENT[user_id] = current
    await callback.message.edit_text(
        f"🔢 <b>Больше-меньше</b>\n{DIVIDER}"
        f"💸 Списано: <b>3</b> балла\n"
        f"Текущее число: <b>{current}</b>\n\n"
        f"Следующее будет больше или меньше? Победа — <b>+5</b> баллов.",
        reply_markup=hilo_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.in_({"hilo_more", "hilo_less"}))
async def game_hilo_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    current = HILO_CURRENT.get(user_id)
    if current is None:
        await callback.answer("Начни игру заново", show_alert=True)
        return

    next_num = random.randint(1, 100)
    guess_more = callback.data == "hilo_more"
    win = (next_num > current and guess_more) or (next_num < current and not guess_more)

    if win:
        db_add_balance(user_id, 5)
        result = f"🎉 Угадал! Было {current}, стало {next_num}. +5 баллов"
    else:
        result = f"😢 Не угадал. Было {current}, стало {next_num}."

    HILO_CURRENT.pop(user_id, None)

    await callback.message.edit_text(
        f"🔢 <b>Больше-меньше</b>\n{DIVIDER}"
        f"{result}",
        reply_markup=shop_keyboard(),
    )
    await callback.answer()


# ==== ДЕПОЗИТ И ВЫВОД ====
@dp.message(F.text.regexp(r"^\d+$"))
async def handle_deposit_amount(message: Message):
    try:
        amount = int(message.text.strip())
    except ValueError:
        return
    if amount < 10:
        await message.answer("❌ Минимум для пополнения — 10 баллов.")
        return
    if amount > 100000:
        await message.answer("❌ Максимум — 100 000 баллов.")
        return
    db_create_deposit(message.from_user.id, amount)
    await message.answer(
        f"✅ Заявка на пополнение <b>{amount}</b> баллов создана.\n"
        f"Администратор подтвердит её после оплаты.",
        reply_markup=reply_menu(),
    )


@dp.message(F.text.regexp(r"^\d+\s+.+$"))
async def handle_withdraw_request(message: Message):
    parts = message.text.split(maxsplit=1)
    try:
        amount = int(parts[0])
        details = parts[1]
    except (ValueError, IndexError):
        return
    if amount < 100:
        await message.answer("❌ Минимум для вывода — 100 баллов.")
        return
    u = db_get_user(message.from_user.id)
    balance = u[0] if u else 0
    if balance < amount:
        await message.answer(f"❌ Недостаточно баллов. У тебя {balance}.")
        return
    db_create_withdrawal(message.from_user.id, amount, details)
    await message.answer(
        f"✅ Заявка на вывод <b>{amount} ₽</b> создана.\n"
        f"Реквизиты: <code>{details}</code>\n\n"
        f"Администратор обработает её в ближайшее время.",
        reply_markup=reply_menu(),
    )


# ==== ПРОМОКОДЫ ====
@dp.callback_query(F.data == "enter_promo")
async def enter_promo(callback: CallbackQuery):
    await callback.message.answer("Введи промокод сообщением:")
    await callback.answer()


@dp.message(F.text & ~F.text.startswith("/") & ~F.text.startswith("👤") & ~F.text.startswith("🛒") & ~F.text.startswith("🎁") & ~F.text.startswith("📋") & ~F.text.startswith("💳") & ~F.text.startswith("💸") & ~F.text.startswith("❓"))
async def handle_text(message: Message):
    text = message.text.strip().upper()
    if " " in text:
        return
    promo = db_get_promo(text)
    if not promo:
        return
    reward, uses_left = promo
    if uses_left <= 0:
        await message.answer("❌ Промокод закончился")
        return
    if db_promo_already_used(text, message.from_user.id):
        await message.answer("❌ Ты уже использовал этот промокод")
        return
    db_use_promo(text, message.from_user.id)
    db_add_balance(message.from_user.id, reward)
    await message.answer(f"✅ Промокод <code>{text}</code> активирован! +{reward} баллов")


# ==== АДМИН-ПАНЕЛЬ (ИНЛАЙН) ====
@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.answer("Формат: <code>/promo КОД награда использований</code>\nПример: <code>/promo GOLD100 100 10</code>")
    await callback.answer()


@dp.callback_query(F.data == "admin_list_promos")
async def admin_list_promos(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    promos = db_list_promos()
    if not promos:
        await callback.message.answer("Промокодов нет")
    else:
        lines = [f"{HEADER}{DIVIDER}📋 <b>Промокоды</b>\n"]
        for code, reward, left in promos:
            lines.append(f"<code>{code}</code> — {reward} баллов, осталось {left}")
        await callback.message.answer("\n".join(lines))
    await callback.answer()


@dp.callback_query(F.data == "admin_set_balance")
async def admin_set_balance(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.answer("Формат: <code>/balance user_id сумма</code>\nПример: <code>/balance 123456789 100</code>")
    await callback.answer()


@dp.callback_query(F.data == "admin_deposits")
async def admin_deposits(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    rows = db_get_pending_deposits()
    if not rows:
        await callback.message.answer("Заявок на депозит нет.")
        await callback.answer()
        return
    for did, uid, amount in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ddone_{did}")]
        ])
        await callback.message.answer(
            f"💳 <b>Депозит #{did}</b>\n"
            f"Пользователь: <code>{uid}</code>\n"
            f"Сумма: <b>{amount}</b> баллов",
            reply_markup=kb,
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("ddone_"))
async def admin_complete_deposit(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    try:
        did = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return
    db_complete_deposit(did)
    await callback.message.edit_text(callback.message.text + "\n\n✅ <b>Подтверждено, баллы начислены</b>")
    await callback.answer("Депозит подтверждён")


@dp.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    rows = db_get_pending_withdrawals()
    if not rows:
        await callback.message.answer("Заявок на вывод нет.")
        await callback.answer()
        return
    for wid, uid, amount, details in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"wdone_{wid}")]
        ])
        await callback.message.answer(
            f"💸 <b>Заявка #{wid}</b>\n"
            f"Пользователь: <code>{uid}</code>\n"
            f"Сумма: <b>{amount} ₽</b>\n"
            f"Реквизиты: <code>{details}</code>",
            reply_markup=kb,
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("wdone_"))
async def admin_complete_withdrawal(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    try:
        wid = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return
    db_complete_withdrawal(wid)
    await callback.message.edit_text(callback.message.text + "\n\n✅ <b>Выполнено</b>")
    await callback.answer("Заявка закрыта, баллы списаны")


@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔", show_alert=True)
        return
    users = db_get_all_users()
    total = len(users)
    total_bal = sum(u[2] for u in users)
    top = sorted(users, key=lambda x: x[2], reverse=True)[:5]
    lines = [
        f"{HEADER}{DIVIDER}📊 <b>Статистика</b>\n",
        f"👥 Пользователей: <code>{total}</code>",
        f"💎 Суммарный баланс: <code>{total_bal}</code>",
        "",
        "🏆 Топ-5:",
    ]
    for uid, uname, bal, refs in top:
        lines.append(f"• <code>{uid}</code> @{uname or '—'} — {bal} баллов, рефов {refs}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


# ==== ЗАПУСК ====
async def main():
    db_init()
    me = await bot.get_me()
    global BOT_USERNAME
    BOT_USERNAME = me.username
    print(f"Бот запущен... @{BOT_USERNAME}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
