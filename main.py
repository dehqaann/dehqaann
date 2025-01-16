import os
import sqlite3
import logging
import csv
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# تنظیمات اولیه
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "YOUR_ADMIN_ID"))
BANK_CARD = os.getenv("BANK_CARD", "YOUR_BANK_CARD_NUMBER")
CHANNEL_ID = os.getenv("CHANNEL_ID", "YOUR_CHANNEL_ID")  # شناسه کانال جهت ارسال پست تبلیغاتی
DAILY_TRANSACTION_LIMIT = 5
DISCOUNT_THRESHOLD = 10
DISCOUNT_PERCENTAGE = 10
CONVERSION_RATE = 1300
TRANSACTION_EXPIRE_TIME = 15 * 60  # 15 دقیقه به ثانیه

# تنظیم لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# -------------------------------
# راه‌اندازی دیتابیس و جداول
# -------------------------------
def init_db():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    # جدول کاربران (با ستون loyalty_points برای برنامه وفاداری)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            join_date TEXT,
            transactions_count INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            loyalty_points INTEGER DEFAULT 0
        )
    ''')
    # جدول تراکنش‌ها
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount INTEGER,
            package_name TEXT,
            status TEXT,
            phone_number TEXT,
            created_at TEXT,
            payment_time TEXT,
            completed_at TEXT,
            rejected_at TEXT,
            expired_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    # جدول تیکت‌ها
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            user_id INTEGER,
            message TEXT,
            status TEXT,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    # جدول پاسخ‌های تیکت‌ها
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ticket_replies (
            reply_id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT,
            from_admin BOOLEAN,
            message TEXT,
            time TEXT,
            FOREIGN KEY(ticket_id) REFERENCES tickets(ticket_id)
        )
    ''')
    # جدول قیمت‌ها
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            package_name TEXT PRIMARY KEY,
            amount INTEGER,
            description TEXT
        )
    ''')
    # جدول بازخورد
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedbacks (
            feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            rating INTEGER,
            message TEXT,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    ''')
    conn.commit()
    conn.close()

def load_initial_prices():
    initial_prices = {
        "شارژ 50 افغانی": {"amount": 50, "description": "شارژ سریع و مستقیم 50 افغانی"},
        "شارژ 100 افغانی": {"amount": 100, "description": "شارژ سریع و مستقیم 100 افغانی"},
        "بسته 1GB": {"amount": 35000, "description": "بسته اینترنت 1 گیگابایتی با سرعت بالا"},
        "بسته 3GB": {"amount": 85000, "description": "بسته اینترنت 3 گیگابایتی با ظرفیت بیشتر"}
    }
    if not get_prices():
        for name, details in initial_prices.items():
            add_price(name, details['amount'], details['description'])

# -------------------------------
# توابع دیتابیس
# -------------------------------
def add_user(user_id, username):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, join_date) VALUES (?, ?, ?)',
                   (user_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def update_user_transaction(user_id, amount):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users
        SET transactions_count = transactions_count + 1,
            total_spent = total_spent + ?,
            loyalty_points = loyalty_points + 1
        WHERE user_id = ?
    ''', (amount, user_id))
    conn.commit()
    conn.close()

def add_transaction(transaction_id, user_id, amount, package_name):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO transactions (transaction_id, user_id, amount, package_name, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
    ''', (transaction_id, user_id, amount, package_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    notify_admin_new_transaction(transaction_id, user_id, amount, package_name)

def update_transaction_status(transaction_id, status, field):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute(f'''
        UPDATE transactions
        SET status = ?, {field} = ?
        WHERE transaction_id = ?
    ''', (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), transaction_id))
    conn.commit()
    conn.close()

def expire_transaction(transaction_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET status = "expired", expired_at = ? WHERE transaction_id = ?',
                   (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), transaction_id))
    conn.commit()
    conn.close()

def get_transactions_today(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM transactions WHERE user_id = ? AND created_at LIKE ?', (user_id, f"{datetime.now().strftime('%Y-%m-%d')}%"))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_completed_transactions(user_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(amount) FROM transactions WHERE user_id = ? AND status = "completed"', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (0, 0)

def add_ticket(ticket_id, user_id, message):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tickets (ticket_id, user_id, message, status, created_at)
        VALUES (?, ?, ?, 'pending', ?)
    ''', (ticket_id, user_id, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    notify_admin_new_ticket(ticket_id, user_id, message)

def get_ticket(ticket_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
    ticket = cursor.fetchone()
    conn.close()
    return ticket

def add_ticket_reply(ticket_id, from_admin, message):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO ticket_replies (ticket_id, from_admin, message, time) VALUES (?, ?, ?, ?)',
                   (ticket_id, from_admin, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def update_ticket_status(ticket_id, status):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE tickets SET status = ? WHERE ticket_id = ?', (status, ticket_id))
    conn.commit()
    conn.close()

def get_prices():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM prices')
    prices = cursor.fetchall()
    conn.close()
    return prices

def add_price(package_name, amount, description):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO prices (package_name, amount, description) VALUES (?, ?, ?)',
                   (package_name, amount, description))
    conn.commit()
    conn.close()

def delete_price(package_name):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM prices WHERE package_name = ?', (package_name,))
    conn.commit()
    conn.close()

def add_feedback(user_id, rating, message):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO feedbacks (user_id, rating, message, created_at) VALUES (?, ?, ?, ?)',
                   (user_id, rating, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

# -------------------------------
# توابع کمکی عمومی
# -------------------------------
def convert_to_english_digits(text: str) -> str:
    persian_digits = '۰۱۲۳۴۵۶۷۸۹'
    english_digits = '0123456789'
    return text.translate(str.maketrans(persian_digits, english_digits))

def validate_payment_image(photo):
    file_size = photo.file_size
    if file_size < 10240:
        return False, "❌ کیفیت تصویر رسید پایین است. لطفاً تصویری واضح ارسال کنید."
    if file_size > 5242880:
        return False, "❌ حجم تصویر بیش از حد است. لطفاً تصویری با حجم کمتر ارسال کنید."
    return True, None

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if update and hasattr(update, 'effective_user'):
            if update.effective_user.id != ADMIN_ID:
                await context.bot.send_message(chat_id=update.effective_user.id, text="❌ متأسفانه خطایی رخ داده است. لطفاً چند لحظه دیگر تلاش کنید.")
            else:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ خطا: {context.error}")
    except Exception as e:
        logger.error(f"Error in error_handler: {e}")

# -------------------------------
# توابع اطلاع‌رسانی به مدیر (گزارش‌های لحظه‌ای)
# -------------------------------
def notify_admin_new_transaction(transaction_id, user_id, amount, package_name):
    msg = (f"🆕 *تراکنش جدید:*\n"
           f"شناسه: `{transaction_id}`\n"
           f"کاربر: `{user_id}`\n"
           f"مبلغ: {amount:,} تومان\n"
           f"سرویس: {package_name}")
    app = Application.get_current()
    app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

def notify_admin_new_ticket(ticket_id, user_id, message):
    msg = (f"🆕 *تیکت جدید:*\n"
           f"شناسه: `{ticket_id}`\n"
           f"کاربر: `{user_id}`\n"
           f"پیام: {message}")
    app = Application.get_current()
    app.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

# -------------------------------
# توابع Broadcast و ارسال پست کانال
# -------------------------------
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه استفاده از این فرمان را ندارید.")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً متن پیام تبلیغاتی را وارد کنید.\nفرمت: /broadcast <پیام>")
        return
    message_text = " ".join(context.args)
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    count = 0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user[0], text=message_text)
            count += 1
        except Exception as e:
            logger.error(f"Broadcast error for user {user[0]}: {e}")
    await update.message.reply_text(f"✅ پیام تبلیغاتی به {count} کاربر ارسال شد.", parse_mode=ParseMode.MARKDOWN)

async def post_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه ارسال پست به کانال را ندارید.")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً متن پست را وارد کنید.\nفرمت: /post <متن پست>")
        return
    post_text = " ".join(context.args)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("بازدید از سایت", url="https://example.com")]
    ])
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=post_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("✅ پست به کانال ارسال شد.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ارسال پست به کانال: {e}")

# -------------------------------
# منوی اصلی
# -------------------------------
def build_main_menu(user_id: int):
    keyboard = [
        ['📱 خرید شارژ', '📦 بسته‌های اینترنت'],
        ['💰 تعرفه‌ها', '📞 پشتیبانی'],
        ['👤 پروفایل من', '🎫 تیکت جدید'],
        ['📄 تاریخچه تراکنش‌ها', '✍️ ثبت بازخورد']
    ]
    if user_id == ADMIN_ID:
        keyboard.append(['📊 آمار', '💾 بکاپ گیری', '📋 گزارش‌ها'])
        keyboard.append(['➕ افزودن بسته', '➖ حذف بسته'])
        keyboard.append(['تغییر نرخ تبدیل', '🔍 جستجو'])
        keyboard.append(['📣 پیام تبلیغاتی', '📢 پست کانال'])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def check_user_limits(user_id):
    count = get_transactions_today(user_id)
    if count >= DAILY_TRANSACTION_LIMIT:
        return False, "🚫 امروز به حداکثر تعداد تراکنش (۵ تراکنش) رسیده‌اید. لطفاً فردا امتحان کنید."
    return True, None

def calculate_discount(user_id, amount):
    completed_trans, _ = get_completed_transactions(user_id)
    if completed_trans >= DISCOUNT_THRESHOLD:
        discount = int(amount * (DISCOUNT_PERCENTAGE / 100))
        return amount - discount, f"{DISCOUNT_PERCENTAGE}% تخفیف ویژه"
    return amount, None

# -------------------------------
# دستورات اصلی ربات
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("admin_add_package", None)
    context.user_data.pop("admin_delete_package", None)
    context.user_data.pop("changing_conversion_rate", None)
    
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    add_user(user_id, username)
    
    reply_markup = build_main_menu(user_id)
    user_data = get_user(user_id)
    transactions_count = user_data[3]
    welcome_text = (
        "🌟 سلام! به ربات شارژ و اینترنت مستقیم خوش آمدید.\n\n"
        "📌 امکانات:\n"
        "• شارژ مستقیم\n"
        "• بسته اینترنت دلخواه\n"
        "• پشتیبانی ۲۴ ساعته\n"
        "• ثبت بازخورد\n\n"
        f"{'🎁 مشتری وفادار، امتیاز شما افزایش یافت!' if transactions_count >= DISCOUNT_THRESHOLD else '💡 به 10 تراکنش موفق نزدیک شوید تا تخفیف ویژه بگیرید.'}"
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    if not user_data:
        await update.message.reply_text("❌ اطلاعات کاربری یافت نشد.")
        return
    completed_trans, total_spent = get_completed_transactions(user_id)
    loyalty = user_data[5]
    profile_text = (
        f"👤 *پروفایل شما:*\n"
        f"🆔 شناسه: `{user_id}`\n"
        f"📅 تاریخ عضویت: {user_data[2]}\n"
        f"✅ تراکنش‌های موفق: {completed_trans}\n"
        f"💰 مجموع خرید: {total_spent:,} تومان\n"
        f"⭐ امتیاز وفاداری: {loyalty}\n\n"
        f"{'🌟 شما مشتری ویژه ما هستید!' if completed_trans >= DISCOUNT_THRESHOLD else f'🎯 تنها {DISCOUNT_THRESHOLD - completed_trans} تراکنش تا تخفیف ویژه!'}"
    )
    await update.message.reply_text(profile_text, parse_mode=ParseMode.MARKDOWN)

async def transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT transaction_id, amount, package_name, status, created_at
        FROM transactions
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 10
    ''', (user_id,))
    transactions = cursor.fetchall()
    conn.close()
    if not transactions:
        await update.message.reply_text("📄 تاکنون تراکنشی ثبت نشده است.")
        return
    history_text = "*📄 آخرین تراکنش‌های شما:*\n\n"
    for trans in transactions:
        history_text += (
            f"🔢 شناسه: `{trans[0]}`\n"
            f"💰 مبلغ: {trans[1]:,} تومان\n"
            f"📦 سرویس: {trans[2]}\n"
            f"🟢 وضعیت: {trans[3]}\n"
            f"📅 تاریخ: {trans[4]}\n\n"
        )
    await update.message.reply_text(history_text, parse_mode=ParseMode.MARKDOWN)

async def charge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    can_order, limit_msg = await check_user_limits(user_id)
    if not can_order:
        await update.message.reply_text(limit_msg)
        return
    prices = get_prices()
    keyboard = []
    for price in prices:
        name, amount, description = price
        if 'شارژ' in name:
            converted_price = amount * CONVERSION_RATE
            final_amount, discount_msg = calculate_discount(user_id, converted_price)
            btn_text = f"{name} - {final_amount:,} تومان"
            if discount_msg:
                btn_text += f" ({discount_msg})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"charge_{converted_price}_{name}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "*📱 لطفاً مبلغ شارژ مد نظر خود را انتخاب کنید:*\n\n⚠️ توجه: پس از انتخاب، شماره تماس مقصد را وارد خواهید کرد.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def internet_packages_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    can_order, limit_msg = await check_user_limits(user_id)
    if not can_order:
        await update.message.reply_text(limit_msg)
        return
    prices = get_prices()
    keyboard = []
    for price in prices:
        name, amount, description = price
        if 'GB' in name:
            final_amount, discount_msg = calculate_discount(user_id, amount)
            btn_text = f"{name} - {final_amount:,} تومان"
            if discount_msg:
                btn_text += f" ({discount_msg})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"net_{amount}_{name}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "*📦 بسته‌های اینترنت موجود:*\n\n⚠️ توجه: پس از انتخاب، شماره تماس مقصد را وارد نمایید.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prices = get_prices()
    text = "*💰 تعرفه‌های خدمات:*\n\n"
    for price in prices:
        name, amount, description = price
        if 'شارژ' in name:
            amount = amount * CONVERSION_RATE
        final_amount, discount_msg = calculate_discount(user_id, amount)
        text += f"*{name}*\n💵 قیمت: {final_amount:,} تومان"
        if discount_msg:
            text += f" ({discount_msg})"
        text += f"\n📝 {description}\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*📞 پشتیبانی فنی*\n\n"
        "برای ارتباط با تیم پشتیبانی از دکمه `🎫 تیکت جدید` استفاده کنید.\n"
        "یا از طریق آیدی زیر تماس بگیرید:\n"
        "`@admin_username`\n\n"
        "⏰ پشتیبانی: 8 صبح تا 8 شب"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data['awaiting_ticket_message'] = True
    keyboard = [[InlineKeyboardButton("❌ لغو", callback_data="cancel_ticket")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "*📝 لطفاً متن تیکت خود را ارسال کنید:*\n(در صورت تمایل، تصویر نیز ارسال کنید.)",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_ticket_message'):
        return
    ticket_id = f"TK{int(datetime.now().timestamp())}"
    if update.message.text:
        msg = update.message.text
    elif update.message.photo:
        msg = "تصویر ارسال شده"
    else:
        await update.message.reply_text("❌ لطفاً متن یا تصویر تیکت را ارسال کنید.")
        return
    add_ticket(ticket_id, user_id, msg)
    admin_msg = (
        f"*🎫 تیکت جدید:*\n\n"
        f"شناسه: `{ticket_id}`\n"
        f"کاربر: `{update.effective_user.username or user_id}`\n"
        f"پیام: {msg}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 پاسخ به تیکت", callback_data=f"reply_ticket_{ticket_id}")]
    ])
    if update.message.photo:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=admin_msg,
                                       reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(f"✅ تیکت شما با شناسه `{ticket_id}` ثبت شد.\nپشتیبانی در اسرع وقت پاسخ می‌دهد.", parse_mode=ParseMode.MARKDOWN)
    context.user_data.pop('awaiting_ticket_message', None)

async def cancel_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_ticket":
        context.user_data.pop('awaiting_ticket_message', None)
        await query.edit_message_text("❌ ساخت تیکت لغو شد.", parse_mode=ParseMode.MARKDOWN)

async def handle_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    ticket_id = query.data.split('_')[2]
    if not get_ticket(ticket_id):
        await query.edit_message_text("❌ تیکت یافت نشد.", parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data['replying_to_ticket'] = ticket_id
    await query.edit_message_text(
        f"✍️ لطفاً پاسخ خود برای تیکت `{ticket_id}` را ارسال کنید:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data=f"cancel_ticket_reply_{ticket_id}")]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )

async def send_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticket_id = context.user_data.get('replying_to_ticket')
    if not ticket_id or not get_ticket(ticket_id):
        return
    reply_msg = update.message.text
    add_ticket_reply(ticket_id, True, reply_msg)
    update_ticket_status(ticket_id, 'answered')
    user_id = get_ticket(ticket_id)[1]
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"*📨 پاسخ تیکت `{ticket_id}`:*\n\n"
            f"{reply_msg}\n\n"
            "برای ارسال تیکت جدید از دکمه `🎫 تیکت جدید` استفاده کنید."
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    await update.message.reply_text("✅ پاسخ شما ارسال شد.", parse_mode=ParseMode.MARKDOWN)
    context.user_data.pop('replying_to_ticket', None)

async def cancel_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    if len(parts) != 4:
        return
    ticket_id = parts[3]
    context.user_data.pop('replying_to_ticket', None)
    await query.edit_message_text(f"❌ پاسخ به تیکت `{ticket_id}` لغو شد.", parse_mode=ParseMode.MARKDOWN)

# -------------------------------
# مدیریت CallbackQuery‌ها
# -------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("confirm_invoice_"):
        transaction_id = data.split("_", 2)[-1]
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT amount, package_name, phone_number FROM transactions WHERE transaction_id = ?', (transaction_id,))
        trans = cursor.fetchone()
        conn.close()
        if not trans:
            await query.edit_message_text("❌ سفارش شما یافت نشد.", parse_mode=ParseMode.MARKDOWN)
            return
        amount, package_name, phone_number = trans
        payment_msg = (
            f"*💳 اطلاعات نهایی پرداخت:*\n\n"
            f"💰 مبلغ: `{amount:,} تومان`\n"
            f"💳 شماره کارت: `{BANK_CARD}`\n"
            f"🔢 شناسه تراکنش: `{transaction_id}`\n\n"
            "لطفاً پس از واریز وجه، رسید پرداخت را ارسال کنید."
        )
        await context.bot.send_message(chat_id=update.effective_user.id, text=payment_msg, parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_text("✅ پیش‌فاکتور تایید شد.", parse_mode=ParseMode.MARKDOWN)
        return
    if data.startswith("cancel_invoice_"):
        await query.edit_message_text("❌ پیش‌فاکتور لغو شد. برای تغییر شماره تماس یا سرویس، مجدداً اقدام کنید.", parse_mode=ParseMode.MARKDOWN)
        return
    if data.startswith('reply_ticket_'):
        await handle_ticket_reply(update, context)
        return
    if data.startswith('cancel_ticket_reply_'):
        await cancel_ticket_reply(update, context)
        return
    if data.startswith(('approve_', 'reject_')):
        await handle_admin_action(update, context)
        return
    if data.startswith(('charge_', 'net_')):
        parts = data.split('_')
        service_type = parts[0]
        amount = int(parts[1])
        package_name = '_'.join(parts[2:])
        user_id = update.effective_user.id
        transaction_id = f"TX{int(datetime.now().timestamp())}"
        add_transaction(transaction_id, user_id, amount, package_name)
        context.user_data['current_transaction'] = transaction_id
        msg = (
            f"🔰 *اطلاعات سفارش:*\n\n"
            f"📦 سرویس: {package_name}\n"
            f"💰 مبلغ: {amount:,} تومان\n\n"
            "لطفاً شماره تماس مقصد (مثال: 93791234567) را وارد نمایید."
        )
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        context.user_data['expecting_phone'] = True
        return
    await query.edit_message_text("❌ عملیات نامعتبر. لطفاً مجدداً تلاش کنید.", parse_mode=ParseMode.MARKDOWN)

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    action, transaction_id = query.data.split('_', 1)
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, amount, phone_number, package_name FROM transactions WHERE transaction_id = ?', (transaction_id,))
    trans = cursor.fetchone()
    conn.close()
    if not trans:
        await query.edit_message_caption("❌ این تراکنش دیگر معتبر نیست.", reply_markup=None)
        return
    user_id, amount, phone_number, package_name = trans
    if action == 'approve':
        update_transaction_status(transaction_id, 'completed', 'completed_at')
        update_user_transaction(user_id, amount)
        success_msg = (
            f"✅ *سفارش شما با موفقیت انجام شد!*\n\n"
            f"🔢 شناسه: `{transaction_id}`\n"
            f"📞 شماره تماس: `{phone_number}`\n"
            f"💰 مبلغ: `{amount:,} تومان`\n"
            f"📦 سرویس: {package_name}\n\n"
            "🙏 از خرید شما سپاسگزاریم."
        )
        await context.bot.send_message(chat_id=user_id, text=success_msg, parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_caption(query.message.caption + "\n\n✅ تایید شد", reply_markup=None)
    elif action == 'reject':
        update_transaction_status(transaction_id, 'rejected', 'rejected_at')
        reject_msg = (
            f"❌ *سفارش شما تایید نشد!*\n\n"
            f"🔢 شناسه: `{transaction_id}`\n"
            f"💰 مبلغ: `{amount:,} تومان`\n\n"
            "⚠️ جهت پیگیری با پشتیبانی تماس بگیرید."
        )
        await context.bot.send_message(chat_id=user_id, text=reject_msg, parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_caption(query.message.caption + "\n\n❌ رد شد", reply_markup=None)
    save_data()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    if user_id == ADMIN_ID and context.user_data.get("changing_conversion_rate"):
        new_rate_str = convert_to_english_digits(text)
        try:
            new_rate = int(new_rate_str)
            global CONVERSION_RATE
            CONVERSION_RATE = new_rate
            context.user_data.pop("changing_conversion_rate")
            await update.message.reply_text(f"✅ نرخ تبدیل به *{new_rate} تومان* تغییر یافت.", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await update.message.reply_text("❌ نرخ تبدیل باید یک عدد صحیح باشد.")
        return

    if user_id == ADMIN_ID and context.user_data.get("admin_add_package"):
        if "/" in text:
            parts = [p.strip() for p in text.split("/") if p.strip()]
            if len(parts) != 3:
                await update.message.reply_text("❌ فرمت نادرست. لطفاً به صورت: نام بسته / مبلغ / توضیحات وارد کنید.")
                return
            package_name, amount_str, description = parts
        else:
            args = text.split()
            if len(args) < 3:
                await update.message.reply_text("❌ فرمت نادرست. لطفاً به صورت: <نام بسته> <مبلغ> <توضیحات> وارد کنید.")
                return
            package_name = args[0]
            amount_str = convert_to_english_digits(args[1])
            description = " ".join(args[2:])
        try:
            amount = int(amount_str)
            add_price(package_name, amount, description)
            await update.message.reply_text(f"✅ بسته *{package_name}* افزوده شد.", parse_mode=ParseMode.MARKDOWN)
            context.user_data.pop("admin_add_package")
        except ValueError:
            await update.message.reply_text("❌ مبلغ باید عدد صحیح باشد.")
        return

    if user_id == ADMIN_ID and context.user_data.get("admin_delete_package"):
        package_name = text
        delete_price(package_name)
        await update.message.reply_text(f"✅ بسته *{package_name}* حذف شد.", parse_mode=ParseMode.MARKDOWN)
        context.user_data.pop("admin_delete_package")
        return

    if text.startswith('/feedback'):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await update.message.reply_text("❌ فرمت نادرست!\nفرمت: `/feedback <امتیاز (1-5)> <نظر شما>`", parse_mode=ParseMode.MARKDOWN)
            return
        try:
            rating = int(convert_to_english_digits(parts[1]))
            if rating < 1 or rating > 5:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ امتیاز باید عددی بین 1 تا 5 باشد.")
            return
        fb_msg = parts[2]
        add_feedback(user_id, rating, fb_msg)
        await update.message.reply_text("✅ بازخورد شما ثبت شد. متشکریم!")
        return

    if update.message.photo and context.user_data.get('expecting_payment'):
        await handle_payment_proof(update, context)
        return

    if context.user_data.get('replying_to_ticket'):
        await send_ticket_reply(update, context)
        return

    if text.startswith('/'):
        command = text.split('@')[0].lower()
        if command == '/start':
            await start(update, context)
        elif command == '/history':
            await transaction_history(update, context)
        elif command == '/stats' and user_id == ADMIN_ID:
            await detailed_stats(update, context)
        elif command == '/export' and user_id == ADMIN_ID:
            await export_transactions(update, context)
        elif command == '/search_transaction' and user_id == ADMIN_ID:
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ لطفاً شناسه تراکنش را وارد کنید.")
                return
            trans_id = parts[1]
            conn = sqlite3.connect('bot.db')
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM transactions WHERE transaction_id = ?', (trans_id,))
            trans = cursor.fetchone()
            conn.close()
            if trans:
                search_msg = (
                    f"*نتیجه جستجوی تراکنش:*\n\n"
                    f"شناسه: `{trans[0]}`\n"
                    f"کاربر: `{trans[1]}`\n"
                    f"مبلغ: {trans[2]:,} تومان\n"
                    f"سرویس: {trans[3]}\n"
                    f"وضعیت: {trans[4]}\n"
                    f"شماره تماس: {trans[5]}\n"
                    f"تاریخ: {trans[6]}"
                )
                await update.message.reply_text(search_msg, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ تراکنشی با این شناسه یافت نشد.")
        elif command == '/search_ticket' and user_id == ADMIN_ID:
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ لطفاً شناسه تیکت را وارد کنید.")
                return
            ticket_id = parts[1]
            conn = sqlite3.connect('bot.db')
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
            ticket = cursor.fetchone()
            conn.close()
            if ticket:
                search_msg = (
                    f"*نتیجه جستجوی تیکت:*\n\n"
                    f"شناسه: `{ticket[0]}`\n"
                    f"کاربر: `{ticket[1]}`\n"
                    f"پیام: {ticket[2]}\n"
                    f"وضعیت: {ticket[3]}\n"
                    f"تاریخ: {ticket[4]}"
                )
                await update.message.reply_text(search_msg, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ تیکتی با این شناسه یافت نشد.")
        elif command == '/broadcast' and user_id == ADMIN_ID:
            if not context.args:
                await update.message.reply_text("❌ لطفاً متن پیام تبلیغاتی را وارد کنید.\nفرمت: /broadcast <پیام>")
                return
            b_msg = " ".join(context.args)
            conn = sqlite3.connect('bot.db')
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            users = cursor.fetchall()
            conn.close()
            count = 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u[0], text=b_msg)
                    count += 1
                except Exception as e:
                    logger.error(f"Broadcast error for user {u[0]}: {e}")
            await update.message.reply_text(f"✅ پیام تبلیغاتی به {count} کاربر ارسال شد.", parse_mode=ParseMode.MARKDOWN)
        elif command == '/post' and user_id == ADMIN_ID:
            if not context.args:
                await update.message.reply_text("❌ لطفاً متن پست را وارد کنید.\nفرمت: /post <متن پست>")
                return
            post_text = " ".join(context.args)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("بازدید از سایت", url="https://example.com")]
            ])
            try:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=post_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
                await update.message.reply_text("✅ پست به کانال ارسال شد.", parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                await update.message.reply_text(f"❌ خطا در ارسال پست به کانال: {e}")
        return

    if user_id == ADMIN_ID and text == "تغییر نرخ تبدیل":
        context.user_data["changing_conversion_rate"] = True
        await update.message.reply_text("📝 لطفاً نرخ تبدیل جدید (به عدد صحیح) را وارد کنید (مثلاً 1300):")
        return

    if user_id == ADMIN_ID and text == "➕ افزودن بسته":
        context.user_data["admin_add_package"] = True
        await update.message.reply_text("📝 لطفاً بسته را به صورت: *نام بسته / مبلغ / توضیحات* وارد کنید.", parse_mode=ParseMode.MARKDOWN)
        return

    if user_id == ADMIN_ID and text == "➖ حذف بسته":
        context.user_data["admin_delete_package"] = True
        await update.message.reply_text("📝 لطفاً نام بسته مورد نظر را ارسال کنید:")
        return

    menu_handlers = {
        '📱 خرید شارژ': charge_menu,
        '📦 بسته‌های اینترنت': internet_packages_menu,
        '💰 تعرفه‌ها': show_prices,
        '📞 پشتیبانی': support,
        '👤 پروفایل من': profile,
        '🎫 تیکت جدید': support_ticket,
        '✍️ ثبت بازخورد': lambda u, c: u.message.reply_text("لطفاً از دستور `/feedback <امتیاز> <نظر>` استفاده کنید.", parse_mode=ParseMode.MARKDOWN),
        '📊 آمار': detailed_stats,
        '💾 بکاپ گیری': backup,
        '📋 گزارش‌ها': export_transactions,
        '📄 تاریخچه تراکنش‌ها': transaction_history,
        '🔍 جستجو': lambda u, c: u.message.reply_text("برای جستجو از /search_transaction یا /search_ticket استفاده کنید.", parse_mode=ParseMode.MARKDOWN),
        '📣 پیام تبلیغاتی': lambda u, c: u.message.reply_text("برای ارسال پیام تبلیغاتی از فرمان /broadcast استفاده کنید.", parse_mode=ParseMode.MARKDOWN),
        '📢 پست کانال': lambda u, c: u.message.reply_text("برای ارسال پست به کانال از فرمان /post استفاده کنید.", parse_mode=ParseMode.MARKDOWN)
    }
    if text in menu_handlers:
        if text in ['📊 آمار', '💾 بکاپ گیری', '📋 گزارش‌ها', '🔍 جستجو', '📣 پیام تبلیغاتی', '📢 پست کانال'] and user_id != ADMIN_ID:
            await update.message.reply_text("🚫 شما دسترسی به این بخش را ندارید.")
            return
        await menu_handlers[text](update, context)
        return

    if await auto_reply(update, context):
        return

    await update.message.reply_text("❓ لطفاً از منوی ربات استفاده کنید.")

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    transaction_id = context.user_data.get('current_transaction')
    if not transaction_id:
        await update.message.reply_text("❌ سفارش شما منقضی شده است. لطفاً دوباره تلاش کنید.")
        return
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM transactions WHERE transaction_id = ?', (transaction_id,))
    trans = cursor.fetchone()
    conn.close()
    if not trans or trans[0] != 'pending':
        await update.message.reply_text("❌ سفارش شما منقضی شده است. لطفاً دوباره اقدام کنید.")
        return
    if not (phone.startswith('93') and len(phone) == 11 and phone.isdigit()):
        await update.message.reply_text("❌ شماره تماس صحیح نیست!\nمثال: 93791234567")
        return
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE transactions SET phone_number = ? WHERE transaction_id = ?', (phone, transaction_id))
    conn.commit()
    conn.close()
    amount = get_transaction_amount(transaction_id)
    preview_text = (
        "*🧾 پیش‌فاکتور سفارش:*\n\n"
        f"📞 شماره تماس مقصد: `{phone}`\n"
        f"💳 شماره کارت: `{BANK_CARD}`\n"
        f"🔢 شناسه تراکنش: `{transaction_id}`\n"
        f"💰 مبلغ: `{amount:,} تومان`\n\n"
        "آیا مایل به ادامه پرداخت هستید؟"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید پرداخت", callback_data=f"confirm_invoice_{transaction_id}"),
         InlineKeyboardButton("❌ لغو", callback_data=f"cancel_invoice_{transaction_id}")]
    ])
    context.user_data.pop('expecting_phone', None)
    await update.message.reply_text(preview_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

def get_transaction_amount(transaction_id):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT amount FROM transactions WHERE transaction_id = ?', (transaction_id,))
    amount = cursor.fetchone()
    conn.close()
    return amount[0] if amount else 0

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ لطفاً تصویر رسید پرداخت را ارسال کنید.")
        return
    transaction_id = context.user_data.get('current_transaction')
    if not transaction_id:
        await update.message.reply_text("❌ سفارش شما منقضی شده است. لطفاً دوباره تلاش کنید.")
        return
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT status, phone_number, user_id, amount, package_name, created_at FROM transactions WHERE transaction_id = ?', (transaction_id,))
    trans = cursor.fetchone()
    if not trans or trans[0] != 'pending':
        conn.close()
        await update.message.reply_text("❌ سفارش شما منقضی شده است. لطفاً دوباره اقدام کنید.")
        return
    photo = update.message.photo[-1]
    is_valid, error_msg = validate_payment_image(photo)
    if not is_valid:
        await update.message.reply_text(error_msg)
        return
    cursor.execute('UPDATE transactions SET status = "pending_review", payment_time = ? WHERE transaction_id = ?',
                   (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), transaction_id))
    conn.commit()
    conn.close()
    admin_msg = (
        f"*💫 سفارش جدید:*\n\n"
        f"🔢 شناسه: {transaction_id}\n"
        f"👤 کاربر: {update.effective_user.username or update.effective_user.id}\n"
        f"📞 شماره تماس: {trans[1]}\n"
        f"💰 مبلغ: {trans[3]:,} تومان\n"
        f"📦 سرویس: {trans[4]}\n"
        f"⏰ زمان: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید", callback_data=f"approve_{transaction_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"reject_{transaction_id}")]
    ])
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=admin_msg, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("✅ رسید پرداخت شما ثبت شد.\n⏳ در حال بررسی توسط پشتیبانی...")
    context.user_data.pop('current_transaction', None)
    context.user_data.pop('expecting_payment', None)

# -------------------------------
# وظایف زمان‌بندی شده (Job Queue)
# -------------------------------
async def payment_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT transaction_id, created_at, status, user_id FROM transactions WHERE status = "pending"')
    pending = cursor.fetchall()
    conn.close()
    now = datetime.now()
    for trans in pending:
        transaction_id, created_at, status, user_id = trans
        created_time = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        if (now - created_time).total_seconds() > TRANSACTION_EXPIRE_TIME:
            expire_transaction(transaction_id)
            await context.bot.send_message(chat_id=user_id, text=(
                f"⏰ *توجه:* سفارش با شناسه `{transaction_id}` به دلیل عدم پرداخت در 15 دقیقه منقضی شده است.\n"
                "در صورت تمایل، لطفاً مجدداً اقدام نمایید."
            ), parse_mode=ParseMode.MARKDOWN)

async def payment_reminder(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT transaction_id, user_id, created_at, status FROM transactions WHERE status = "pending"')
    pending = cursor.fetchall()
    conn.close()
    now = datetime.now()
    for trans in pending:
        transaction_id, user_id, created_at, status = trans
        created_time = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        if (now - created_time).total_seconds() > 43200:
            continue
        await context.bot.send_message(chat_id=user_id, text=(
            f"*⏰ یادآوری پرداخت:*\n\n"
            f"سفارش با شناسه `{transaction_id}` هنوز در انتظار پرداخت است.\n"
            "لطفاً در صورت پرداخت، رسید خود را ارسال نمایید."
        ), parse_mode=ParseMode.MARKDOWN)

async def admin_notifications(context: ContextTypes.DEFAULT_TYPE):
    pending_trans = get_pending_transactions()
    pending_tickets = get_pending_tickets()
    if pending_trans > 0 or pending_tickets > 0:
        note = "*🔔 یادآوری مدیر:*\n\n"
        if pending_trans > 0:
            note += f"• {pending_trans} تراکنش در انتظار بررسی\n"
        if pending_tickets > 0:
            note += f"• {pending_tickets} تیکت در انتظار پاسخ"
        await context.bot.send_message(chat_id=ADMIN_ID, text=note, parse_mode=ParseMode.MARKDOWN)

def get_pending_transactions():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE status = 'pending_review'")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_pending_tickets():
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = 'pending'")
    count = cursor.fetchone()[0]
    conn.close()
    return count

async def detailed_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(amount) FROM transactions WHERE created_at LIKE ?', (f"{today}%",))
    today_trans, today_amount = cursor.fetchone()
    cursor.execute('SELECT COUNT(*), SUM(amount) FROM transactions WHERE created_at >= ?', (week_start,))
    week_trans, week_amount = cursor.fetchone()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM transactions WHERE created_at LIKE ?', (f"{today}%",))
    active_users_today = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM transactions WHERE status = "completed"')
    completed_trans = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM transactions WHERE status = "pending_review"')
    pending_review_trans = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM transactions WHERE status = "rejected"')
    rejected_trans = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM tickets')
    total_tickets = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM tickets WHERE status = "pending"')
    pending_tickets = cursor.fetchone()[0]
    conn.close()
    stats_text = (
        f"*📊 گزارش تفصیلی:*\n\n"
        f"*امروز:*\n• تراکنش: {today_trans}\n• مبلغ: {today_amount or 0:,} تومان\n\n"
        f"*هفته:*\n• تراکنش: {week_trans}\n• مبلغ: {week_amount or 0:,} تومان\n\n"
        f"*کاربران:*\n• کل: {total_users}\n• فعال امروز: {active_users_today}\n\n"
        f"*تراکنش‌ها:*\n• موفق: {completed_trans}\n• در انتظار: {pending_review_trans}\n• ناموفق: {rejected_trans}\n\n"
        f"*تیکت‌ها:*\n• کل: {total_tickets}\n• در انتظار پاسخ: {pending_tickets}\n\n"
        f"🕒 بروزرسانی: {datetime.now().strftime('%H:%M:%S')}"
    )
    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

async def export_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    filename = f"transactions_{datetime.now().strftime('%Y%m%d')}.csv"
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT created_at, transaction_id, user_id, amount, status, phone_number, package_name FROM transactions')
    transactions = cursor.fetchall()
    conn.close()
    with open(filename, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["تاریخ", "شناسه", "کاربر", "مبلغ", "وضعیت", "شماره تماس", "سرویس"])
        for trans in transactions:
            writer.writerow(trans)
    await context.bot.send_document(chat_id=ADMIN_ID, document=open(filename, 'rb'), caption="*📊 گزارش تراکنش‌ها*", parse_mode=ParseMode.MARKDOWN)
    os.remove(filename)

async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    conn = sqlite3.connect('bot.db')
    with open(filename, 'wb') as f:
        for chunk in conn.iterdump():
            f.write(f"{chunk}\n".encode())
    conn.close()
    await context.bot.send_document(chat_id=ADMIN_ID, document=open(filename, 'rb'), caption="*💾 بکاپ دیتابیس*", parse_mode=ParseMode.MARKDOWN)
    os.remove(filename)
    await update.message.reply_text("✅ بکاپ گیری با موفقیت انجام شد.", parse_mode=ParseMode.MARKDOWN)

async def add_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه دسترسی به این بخش را ندارید.")
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("❌ استفاده نادرست!\nفرمت: /addpackage <نام بسته> <مبلغ> <توضیحات>")
        return
    package_name = args[0]
    amount_str = convert_to_english_digits(args[1])
    try:
        amount = int(amount_str)
    except ValueError:
        await update.message.reply_text("❌ مقدار مبلغ باید عدد صحیح باشد.")
        return
    description = " ".join(args[2:])
    add_price(package_name, amount, description)
    await update.message.reply_text(f"✅ بسته *{package_name}* افزوده شد.", parse_mode=ParseMode.MARKDOWN)

async def delete_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه دسترسی به این بخش را ندارید.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ لطفاً نام بسته را وارد کنید.\nفرمت: /deletepackage <نام بسته>")
        return
    package_name = args[0]
    delete_price(package_name)
    await update.message.reply_text(f"✅ بسته *{package_name}* حذف شد.", parse_mode=ParseMode.MARKDOWN)

async def change_conversion_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه دسترسی به این بخش را ندارید.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ لطفاً نرخ تبدیل جدید را وارد کنید.\nفرمت: /changecvrate <نرخ جدید>")
        return
    new_rate_str = convert_to_english_digits(args[0])
    try:
        new_rate = int(new_rate_str)
        global CONVERSION_RATE
        CONVERSION_RATE = new_rate
        await update.message.reply_text(f"✅ نرخ تبدیل به *{new_rate} تومان* تغییر یافت.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ نرخ تبدیل باید یک عدد صحیح باشد.")

async def auto_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.lower()
    responses = {
        'قیمت': 'برای مشاهده تعرفه‌ها روی دکمه 💰 تعرفه‌ها کلیک کنید.',
        'پشتیبانی': 'برای تماس با پشتیبانی روی دکمه 📞 پشتیبانی کلیک کنید.',
        'شارژ': 'برای خرید شارژ روی دکمه 📱 خرید شارژ کلیک کنید.',
        'بسته': 'برای مشاهده بسته‌های اینترنت روی دکمه 📦 بسته‌های اینترنت کلیک کنید.'
    }
    for key, resp in responses.items():
        if key in message:
            await update.message.reply_text(resp)
            return True
    return False

# -------------------------------
# فرمان‌های جدید: Broadcast و پست کانال
# -------------------------------
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه استفاده از این فرمان را ندارید.")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً متن پیام تبلیغاتی را وارد کنید.\nفرمت: /broadcast <پیام>")
        return
    b_msg = " ".join(context.args)
    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    count = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=u[0], text=b_msg)
            count += 1
        except Exception as e:
            logger.error(f"Broadcast error for user {u[0]}: {e}")
    await update.message.reply_text(f"✅ پیام تبلیغاتی به {count} کاربر ارسال شد.", parse_mode=ParseMode.MARKDOWN)

async def post_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 شما اجازه ارسال پست به کانال را ندارید.")
        return
    if not context.args:
        await update.message.reply_text("❌ لطفاً متن پست را وارد کنید.\nفرمت: /post <متن پست>")
        return
    post_text = " ".join(context.args)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("بازدید از سایت", url="https://example.com")]
    ])
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=post_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("✅ پست به کانال ارسال شد.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ارسال پست به کانال: {e}")

# -------------------------------
# تابع اصلی
# -------------------------------
def main():
    init_db()
    load_initial_prices()
    application = Application.builder().token(TOKEN).build()

    # فرمان‌های اصلی
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("history", transaction_history))
    application.add_handler(CommandHandler("stats", detailed_stats))
    application.add_handler(CommandHandler("export", export_transactions))
    application.add_handler(CommandHandler("addpackage", add_package))
    application.add_handler(CommandHandler("deletepackage", delete_package))
    application.add_handler(CommandHandler("changecvrate", change_conversion_rate))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("post", post_to_channel))
    application.add_handler(CommandHandler("search_transaction", handle_message))
    application.add_handler(CommandHandler("search_ticket", handle_message))
    application.add_handler(CommandHandler("feedback", None))

    # CallbackQuery Handlerها
    application.add_handler(CallbackQueryHandler(handle_admin_action, pattern='^(approve|reject)_'))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(CallbackQueryHandler(cancel_ticket, pattern='^cancel_ticket$'))
    application.add_handler(CallbackQueryHandler(cancel_ticket_reply, pattern='^cancel_ticket_reply_'))

    # Handlerهای اختصاصی برای شماره تلفن و تصاویر
    application.add_handler(MessageHandler(filters.Regex(r'^\d{11}$'), handle_phone_number))
    application.add_handler(MessageHandler(filters.PHOTO, handle_payment_proof))

    # Handler عمومی
    application.add_handler(MessageHandler(filters.ALL, handle_message))
    application.add_error_handler(error_handler)

    # زمان‌بندی وظایف
    job_queue = application.job_queue
    job_queue.run_repeating(admin_notifications, interval=3600, first=10)
    job_queue.run_repeating(payment_reminder, interval=3600, first=10)
    job_queue.run_repeating(payment_expiry_job, interval=60, first=10)

    application.run_polling()

if __name__ == '__main__':
    main()
    
