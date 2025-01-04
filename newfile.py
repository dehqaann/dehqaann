import telebot
from telebot import types
import sqlite3
import os
from datetime import datetime
from flask import Flask
from threading import Thread
import logging

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "6527538495:AAH85hVHxSrHECVepMqoIW4_IBYNxl_oLzc"
if not BOT_TOKEN:
    logging.error("توکن ربات تنظیم نشده است. لطفاً BOT_TOKEN را در متغیرهای محیطی تعریف کنید.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

def setup_database():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                  username TEXT,
                  join_date TEXT)''')
    conn.commit()
    conn.close()

def register_user(user_id, username):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?)",
              (user_id, username, join_date))
    conn.commit()
    conn.close()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username
    register_user(user_id, username)
    
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = types.KeyboardButton('📸 ارسال عکس')
    btn2 = types.KeyboardButton('📹 ارسال ویدیو')
    btn3 = types.KeyboardButton('📄 ارسال فایل')
    btn4 = types.KeyboardButton('ℹ️ راهنما')
    markup.add(btn1, btn2, btn3, btn4)
    
    bot.reply_to(message, 
                 "سلام من ربات چت جی پی تی او یک مینی هستم از برنامه هوش مصنوعی جدیترین و پیشرفته ترین ربات اوپن ای آی با قابلیت پاسخ به سوالات و کد های طولانی و پیچیده. به ربات ما خوش آمدید.\nاز منوی زیر گزینه مورد نظر را انتخاب کنید.",
                 reply_markup=markup)

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """
📌 راهنمای استفاده از ربات:
/start - شروع مجدد ربات
/help - نمایش این راهنما
/profile - مشاهده پروفایل
/upload - آپلود فایل
/stats - آمار ربات
    """
    bot.reply_to(message, help_text)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_name = f"photos/photo_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        with open(file_name, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        bot.reply_to(message, "✅ عکس با موفقیت ذخیره شد!")
    except Exception as e:
        logging.error(f"Error saving photo: {e}")
        bot.reply_to(message, "❌ خطا در ذخیره عکس")

@bot.message_handler(content_types=['video'])
def handle_video(message):
    try:
        file_info = bot.get_file(message.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_name = f"videos/video_{message.from_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        with open(file_name, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        bot.reply_to(message, "✅ ویدیو با موفقیت ذخیره شد!")
    except Exception as e:
        logging.error(f"Error saving video: {e}")
        bot.reply_to(message, "❌ خطا در ذخیره ویدیو")

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_name = f"files/{message.document.file_name}"
        with open(file_name, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        bot.reply_to(message, "✅ فایل با موفقیت ذخیره شد!")
    except Exception as e:
        logging.error(f"Error saving document: {e}")
        bot.reply_to(message, "❌ خطا در ذخیره فایل")

@bot.message_handler(commands=['profile'])
def show_profile(message):
    try:
        conn = sqlite3.connect('bot_database.db')
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
        user_data = c.fetchone()
        conn.close()
        
        if user_data:
            profile_text = f"""
👤 اطلاعات پروفایل شما:
🆔 شناسه: {user_data[0]}
📝 نام کاربری: @{user_data[1]}
📅 تاریخ عضویت: {user_data[2]}
            """
            bot.reply_to(message, profile_text)
        else:
            bot.reply_to(message, "❌ اطلاعات پروفایل یافت نشد!")
    except Exception as e:
        logging.error(f"Error showing profile: {e}")
        bot.reply_to(message, "❌ خطا در دریافت پروفایل")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    if message.text == '📸 ارسال عکس':
        bot.reply_to(message, "لطفاً عکس مورد نظر خود را ارسال کنید.")
    elif message.text == '📹 ارسال ویدیو':
        bot.reply_to(message, "لطفاً ویدیو مورد نظر خود را ارسال کنید.")
    elif message.text == '📄 ارسال فایل':
        bot.reply_to(message, "لطفاً فایل مورد نظر خود را ارسال کنید.")
    elif message.text == 'ℹ️ راهنما':
        help_command(message)
    else:
        bot.reply_to(message, "متوجه نشدم! لطفاً از منو استفاده کنید.")

def create_directories():
    directories = ['photos', 'videos', 'files']
    for directory in directories:
        os.makedirs(directory, exist_ok=True)

@app.route('/')
def main():
    return "Bot is alive!"


def run_bot():
    logging.info("شروع به Polling ربات...")
    bot.polling(none_stop=True)

if __name__ == "__main__":
    create_directories()
    setup_database()

    bot_thread = Thread(target=run_bot)
    bot_thread.start()

    logging.info("ربات در حال اجراست...")

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))