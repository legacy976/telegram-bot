import logging
import pytz
import os
import json
import threading
import time
import schedule
import sqlite3
import re
from translations import TRANSLATIONS
from datetime import datetime, timedelta
from telebot.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telebot import TeleBot, types
from typing import List, Optional
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

bot = TeleBot(BOT_TOKEN)

DAYS = {
    'monday': 'Понедельник',
    'tuesday': 'Вторник',
    'wednesday': 'Среда',
    'thursday': 'Четверг',
    'friday': 'Пятница',
    'saturday': 'Суббота',
    'sunday': 'Воскресенье'
}

EN_TO_RU_DAY = {
    'Monday': 'monday',
    'Tuesday': 'tuesday',
    'Wednesday': 'wednesday',
    'Thursday': 'thursday',
    'Friday': 'friday',
    'Saturday': 'saturday',
    'Sunday': 'sunday'
}


def setup_commands():
    """Настроить меню команд Telegram"""
    try:
        bot.set_my_commands([
            BotCommand("start", "🏠 Main menu"),
            BotCommand("language", "🌍 Language"),
            BotCommand("schedule", "📅 Your schedule"),
            BotCommand("today", "📆 Today"),
            BotCommand("week", "📊 Week"),
            BotCommand("upcoming", "⏰ Upcoming events"),
            BotCommand("edit", "✏️ Edit schedule"),
            BotCommand("comment", "💬 Add comment"),
            BotCommand("notifications", "🔔 Notification settings"),
            BotCommand("autoclear", "🗑️ Auto-clear schedule"),
            BotCommand("settimezone", "🌍 Set timezone"),
            BotCommand("mytimezone", "🕐 My timezone"),
            BotCommand("help", "❓ Help"),
        ])
        logger.info("Commands menu configured")
    except Exception as e:
        logger.error(f"Failed to setup commands: {e}")


class Database:
    def __init__(self, db_path='bot.db'):
        if os.getenv('AMVERA'):
            self.db_path = '/data/bot.db'
        else:
            self.db_path = db_path
        self.init_db()

    def get_connection(self):
        """Получить соединение с БД"""
        return sqlite3.connect(self.db_path)

    def init_db(self):
        """Инициализация всех таблиц БД"""
        with self.get_connection() as conn:
            # Таблица для настроек уведомлений
            conn.execute('''CREATE TABLE IF NOT EXISTS notification_settings (
                            user_id INTEGER PRIMARY KEY,
                            enabled BOOLEAN DEFAULT 1,
                            notify_time TEXT DEFAULT '09:00',
                            notify_before_minutes INTEGER DEFAULT 60,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        )''')

            # Таблица для пользователей
            conn.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

            # Таблица для часовых поясов
            conn.execute('''CREATE TABLE IF NOT EXISTS user_timezones (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'UTC',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )''')

            # Таблица для личных расписаний пользователей
            conn.execute('''CREATE TABLE IF NOT EXISTS user_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                lessons TEXT DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, day),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )''')

            # Таблица для комментариев к мероприятиям
            conn.execute('''CREATE TABLE IF NOT EXISTS lesson_comments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        day TEXT NOT NULL,
                        lesson_index INTEGER NOT NULL,
                        comment TEXT DEFAULT '',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(user_id),
                        UNIQUE(user_id, day, lesson_index)
                    )''')

            # Индексы для быстрого поиска
            conn.execute('''CREATE INDEX IF NOT EXISTS idx_user_schedules 
                ON user_schedules(user_id, day)''')

            conn.execute('''CREATE INDEX IF NOT EXISTS idx_timezone 
                ON user_timezones(timezone)''')
            try:
                conn.execute('ALTER TABLE notification_settings ADD COLUMN auto_clear BOOLEAN DEFAULT 0')
                conn.execute('ALTER TABLE notification_settings ADD COLUMN clear_day TEXT DEFAULT "sunday"')
                logger.info("Added auto_clear columns to notification_settings")
            except sqlite3.OperationalError:
                pass  # Колонки уже существуют
            try:
                conn.execute('ALTER TABLE users ADD COLUMN language TEXT DEFAULT "ru"')
                logger.info("Added language column to users")
            except sqlite3.OperationalError:
                pass  # Колонка уже существует

    def register_user(self, user_id: int, username: str = None,
                      first_name: str = None, last_name: str = None):
        """Зарегистрировать пользователя"""
        with self.get_connection() as conn:
            conn.execute('''
                INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name))

            # Также создаем запись с часовым поясом по умолчанию
            conn.execute('''
                INSERT OR IGNORE INTO user_timezones (user_id, timezone)
                VALUES (?, 'UTC')
            ''', (user_id,))

            conn.execute('''
                            INSERT OR IGNORE INTO notification_settings (user_id)
                            VALUES (?)
                        ''', (user_id,))

    def set_user_timezone(self, user_id: int, timezone: str) -> bool:
        """Сохранить часовой пояс пользователя"""
        try:
            if timezone not in pytz.all_timezones:
                return False

            with self.get_connection() as conn:
                conn.execute('''
                    INSERT INTO user_timezones (user_id, timezone, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        timezone = excluded.timezone,
                        updated_at = CURRENT_TIMESTAMP
                ''', (user_id, timezone))
            return True
        except Exception as e:
            logger.error(f"Error setting timezone for user {user_id}: {e}")
            return False

    def get_user_timezone(self, user_id: int) -> str:
        """Получить часовой пояс пользователя"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT timezone FROM user_timezones WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return row[0] if row else 'UTC'

    def get_user_schedule(self, user_id: int, day: Optional[str] = None):
        """
        Получить расписание пользователя
        Если day=None, возвращает всё расписание
        """
        with self.get_connection() as conn:
            if day:
                cursor = conn.execute('''
                    SELECT lessons FROM user_schedules 
                    WHERE user_id = ? AND day = ?
                ''', (user_id, day))
                row = cursor.fetchone()
                return json.loads(row[0]) if row else []
            else:
                cursor = conn.execute('''
                    SELECT day, lessons FROM user_schedules 
                    WHERE user_id = ?
                ''', (user_id,))
                return {row[0]: json.loads(row[1]) for row in cursor.fetchall()}

    def add_lesson(self, user_id: int, day: str, lesson: str):
        """Добавить мероприятие в расписание пользователя"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT lessons FROM user_schedules 
                WHERE user_id = ? AND day = ?
            ''', (user_id, day))
            row = cursor.fetchone()

            if row:
                lessons = json.loads(row[0])
            else:
                lessons = []

            lessons.append(lesson)

            conn.execute('''
                INSERT INTO user_schedules (user_id, day, lessons, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, day) DO UPDATE SET
                    lessons = excluded.lessons,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, day, json.dumps(lessons, ensure_ascii=False)))

    def delete_lesson(self, user_id: int, day: str, index: int) -> bool:
        """Удалить мероприятие из расписания пользователя"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT lessons FROM user_schedules 
                WHERE user_id = ? AND day = ?
            ''', (user_id, day))
            row = cursor.fetchone()

            if not row:
                return False

            lessons = json.loads(row[0])
            if 0 <= index < len(lessons):
                lessons.pop(index)
                conn.execute('''
                    UPDATE user_schedules 
                    SET lessons = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND day = ?
                ''', (json.dumps(lessons, ensure_ascii=False), user_id, day))
                return True
            return False

    def get_notification_settings(self, user_id: int) -> dict:
        """Получить настройки уведомлений пользователя"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT enabled, notify_time, notify_before_minutes 
                FROM notification_settings WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'enabled': bool(row[0]),
                    'notify_time': str(row[1]),  # Приводим к строке
                    'notify_before_minutes': int(row[2])  # Приводим к int
                }
            # Если настроек нет, возвращаем значения по умолчанию
            return {'enabled': True, 'notify_time': '09:00', 'notify_before_minutes': 60}

    def update_notification_settings(self, user_id: int, **kwargs):
        """Обновить настройки уведомлений"""
        with self.get_connection() as conn:
            # Получаем текущие настройки
            settings = self.get_notification_settings(user_id)

            # Обновляем переданные параметры
            for key, value in kwargs.items():
                settings[key] = value

            # Сохраняем в БД
            conn.execute('''
                INSERT INTO notification_settings (user_id, enabled, notify_time, notify_before_minutes, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    notify_time = excluded.notify_time,
                    notify_before_minutes = excluded.notify_before_minutes,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, settings['enabled'], settings['notify_time'], settings['notify_before_minutes']))

            # Проверяем, что сохранилось
            cursor = conn.execute('''
                SELECT notify_time, notify_before_minutes FROM notification_settings WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            logger.info(f"Updated settings for user {user_id}: time={row[0]}, minutes={row[1]}")

    def get_all_users_with_notifications_enabled(self) -> List[int]:
        """Получить всех пользователей с включенными уведомлениями"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT user_id FROM notification_settings WHERE enabled = 1
            ''')
            return [row[0] for row in cursor.fetchall()]

    def get_auto_clear_settings(self, user_id: int) -> dict:
        """Получить настройки автоочистки пользователя"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT auto_clear, clear_day FROM notification_settings WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'auto_clear': bool(row[0]),
                    'clear_day': row[1] if row[1] else 'sunday'
                }
            return {'auto_clear': False, 'clear_day': 'sunday'}

    def update_auto_clear_settings(self, user_id: int, auto_clear: bool, clear_day: str = None):
        """Обновить настройки автоочистки"""
        with self.get_connection() as conn:
            if clear_day:
                conn.execute('''
                    UPDATE notification_settings 
                    SET auto_clear = ?, clear_day = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                ''', (auto_clear, clear_day, user_id))
            else:
                conn.execute('''
                    UPDATE notification_settings 
                    SET auto_clear = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                ''', (auto_clear, user_id))
            logger.info(f"Auto-clear settings updated for user {user_id}: auto_clear={auto_clear}, day={clear_day}")

    def get_users_with_auto_clear_enabled(self) -> List[tuple]:
        """Получить пользователей с включенной автоочисткой"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT user_id, clear_day FROM notification_settings 
                WHERE auto_clear = 1 AND enabled = 1
            ''')
            return cursor.fetchall()

    def clear_user_schedule(self, user_id: int) -> bool:
        """Очистить расписание пользователя"""
        try:
            with self.get_connection() as conn:
                conn.execute('DELETE FROM user_schedules WHERE user_id = ?', (user_id,))
            logger.info(f"Schedule cleared for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error clearing schedule for user {user_id}: {e}")
            return False

    def add_comment(self, user_id: int, day: str, lesson_index: int, comment: str):
        """Добавить или обновить комментарий к мероприятию"""
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO lesson_comments (user_id, day, lesson_index, comment, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, day, lesson_index) DO UPDATE SET
                    comment = excluded.comment,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, day, lesson_index, comment))
            logger.info(f"Comment added for user {user_id}, {day}, lesson {lesson_index}")

    def get_comment(self, user_id: int, day: str, lesson_index: int) -> str:
        """Получить комментарий к мероприятию"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT comment FROM lesson_comments 
                WHERE user_id = ? AND day = ? AND lesson_index = ?
            ''', (user_id, day, lesson_index))
            row = cursor.fetchone()
            return row[0] if row else ""

    def delete_comment(self, user_id: int, day: str, lesson_index: int) -> bool:
        """Удалить комментарий"""
        with self.get_connection() as conn:
            conn.execute('''
                DELETE FROM lesson_comments WHERE user_id = ? AND day = ? AND lesson_index = ?
            ''', (user_id, day, lesson_index))
            return True

    def set_user_language(self, user_id: int, language: str) -> bool:
        """Установить язык пользователя"""
        try:
            with self.get_connection() as conn:
                conn.execute('''
                    UPDATE users SET language = ? WHERE user_id = ?
                ''', (language, user_id))
            logger.info(f"Language set for user {user_id}: {language}")
            return True
        except Exception as e:
            logger.error(f"Error setting language for user {user_id}: {e}")
            return False

    def get_user_language(self, user_id: int) -> str:
        """Получить язык пользователя"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT language FROM users WHERE user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] else 'ru'


db = Database()


def get_text(user_id, key, *args):
    """Получить локализованный текст"""
    lang = db.get_user_language(user_id)
    text = TRANSLATIONS.get(lang, TRANSLATIONS['ru']).get(key, key)
    if args:
        text = text.format(*args)
    return text


def days_keyboard(callback_prefix: str = 'view', user_id: Optional[int] = None) -> types.InlineKeyboardMarkup:
    """Клавиатура с днями недели"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = []

    for day_key, day_name in DAYS.items():
        if user_id:
            button_text = get_text(user_id, day_key)
            callback = f"{callback_prefix}_{user_id}_{day_key}"
        else:
            button_text = day_name
            callback = f"{callback_prefix}_{day_key}"
        buttons.append(types.InlineKeyboardButton(button_text, callback_data=callback))

    keyboard.add(*buttons)

    # Добавляем кнопку для всей недели только для просмотра
    if callback_prefix == 'view':
        if user_id:
            keyboard.row(types.InlineKeyboardButton(
                get_text(user_id, 'view_week_btn'),
                callback_data=f"view_{user_id}_week"
            ))
        else:
            keyboard.row(types.InlineKeyboardButton(
                get_text(user_id, 'view_week_btn') if user_id else "📋 Вся неделя",
                callback_data="view_week"
            ))

    return keyboard


def send_notification(user_id: int, message: str, keyboard: Optional[types.InlineKeyboardMarkup] = None):
    """Отправить уведомление пользователю"""
    try:
        bot.send_message(
            user_id,
            message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        logger.info(f"Notification sent to user {user_id}")
    except Exception as e:
        logger.error(f"Failed to send notification to user {user_id}: {e}")


def send_daily_schedule_notification():
    """Отправить ежедневное уведомление с расписанием на сегодня"""
    users = db.get_all_users_with_notifications_enabled()

    for user_id in users:
        try:
            tz_name = db.get_user_timezone(user_id)
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)

            settings = db.get_notification_settings(user_id)
            notify_time = settings['notify_time']

            # Отладочный вывод
            logger.info(f"User {user_id} - Current time: {now.strftime('%H:%M')}, Notify time: {notify_time}")

            current_time = now.strftime('%H:%M')
            if current_time != notify_time:
                continue

            today_en = now.strftime('%A')
            day_key = EN_TO_RU_DAY.get(today_en)

            if not day_key:
                continue

            lessons = db.get_user_schedule(user_id, day_key)

            if lessons:
                # Формируем список мероприятий с указанием времени
                lessons_list = []
                for lesson in lessons:
                    time_match = re.search(r'(\d{1,2}):(\d{2})', lesson)
                    if time_match:
                        lesson_name = re.sub(r'\s*\d{1,2}:\d{2}', '', lesson).strip()
                        hour = int(time_match.group(1))
                        minute = int(time_match.group(2))
                        lessons_list.append(f"   ⏰ {hour:02d}:{minute:02d} - {lesson_name}")
                    else:
                        lessons_list.append(f"   📚 {lesson}")

                lessons_text = "\n".join(lessons_list)

                text = get_text(user_id, 'morning_schedule', get_text(user_id, day_key), lessons_text)
            else:
                text = get_text(user_id, 'no_morning_events', get_text(user_id, day_key))

            keyboard = types.InlineKeyboardMarkup()
            keyboard.row(
                types.InlineKeyboardButton(get_text(user_id, 'view_btn'), callback_data=f"view_{user_id}_{day_key}"),
                types.InlineKeyboardButton(get_text(user_id, 'edit_btn'), callback_data=f"edit_{user_id}")
            )

            send_notification(user_id, text, keyboard)

            logger.info(f"Morning schedule sent to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending daily notification to user {user_id}: {e}")


def send_upcoming_lesson_reminders():
    """Отправить напоминания о предстоящих мероприятиях"""
    users = db.get_all_users_with_notifications_enabled()

    for user_id in users:
        try:
            tz_name = db.get_user_timezone(user_id)
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)

            settings = db.get_notification_settings(user_id)
            minutes_before = settings['notify_before_minutes']

            today_en = now.strftime('%A')
            day_key = EN_TO_RU_DAY.get(today_en)

            if not day_key:
                continue

            lessons = db.get_user_schedule(user_id, day_key)

            for lesson in lessons:
                # Ищем время в формате ЧЧ:ММ
                time_match = re.search(r'(\d{1,2}):(\d{2})', lesson)

                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))

                    # Проверяем корректность времени
                    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                        continue

                    # Извлекаем название мероприятия без времени
                    lesson_name = re.sub(r'\s*\d{1,2}:\d{2}', '', lesson).strip()
                    if not lesson_name:
                        lesson_name = lesson

                    # Создаем время мероприятия на сегодня
                    lesson_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    # Если время уже прошло, пропускаем
                    if lesson_time < now:
                        continue

                    # Время напоминания
                    reminder_time = lesson_time - timedelta(minutes=minutes_before)

                    # Текущее время (без секунд)
                    current_time = now.replace(second=0, microsecond=0)

                    # Проверяем, наступило ли время напоминания
                    if current_time == reminder_time:
                        # Рассчитываем сколько осталось времени
                        time_left = lesson_time - now
                        minutes_left = int(time_left.total_seconds() / 60)
                        hours_left = minutes_left // 60
                        mins_left = minutes_left % 60

                        if hours_left > 0:
                            time_text = get_text(user_id, 'in_hours', hours_left, mins_left)
                        else:
                            time_text = get_text(user_id, 'in_minutes', mins_left)

                        # Формируем красивое уведомление
                        text = get_text(user_id, 'reminder', lesson_name, f"{hour:02d}:{minute:02d}", time_text)

                        keyboard = types.InlineKeyboardMarkup()
                        keyboard.add(types.InlineKeyboardButton(
                            get_text(user_id, 'ack_btn'),
                            callback_data=f"ack_{user_id}"
                        ))

                        send_notification(user_id, text, keyboard)

                        logger.info(f"Reminder sent: {lesson_name} at {hour:02d}:{minute:02d} to user {user_id}")

        except Exception as e:
            logger.error(f"Error sending reminder to user {user_id}: {e}")


def notification_worker():
    """Фоновый поток для проверки и отправки уведомлений"""
    logger.info("Notification worker started")

    schedule.every().minute.do(send_upcoming_lesson_reminders)
    schedule.every().minute.do(send_daily_schedule_notification)
    schedule.every().minute.do(check_and_clear_schedules)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except Exception as e:
            logger.error(f"Error in notification worker: {e}")
            time.sleep(60)


@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    """Обработчик команды start"""
    user_id = message.from_user.id

    # Регистрируем пользователя
    db.register_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )

    text = get_text(user_id, 'welcome')
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['schedule'])
def cmd_schedule(message):
    """Показать расписание пользователя"""
    user_id = message.from_user.id
    keyboard = days_keyboard('view', user_id)
    text = get_text(user_id, 'choose_day')
    bot.send_message(message.chat.id, text, reply_markup=keyboard)


@bot.message_handler(commands=['settimezone'])
def cmd_set_timezone(message):
    """Установка часового пояса"""
    user_id = message.from_user.id
    markup = types.InlineKeyboardMarkup(row_width=2)
    zones = [
        ("🇷🇺 Москва (UTC+3)", "Europe/Moscow"),
        ("🇷🇺 Калининград (UTC+2)", "Europe/Kaliningrad"),
        ("🇷🇺 Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
        ("🇷🇺 Новосибирск (UTC+7)", "Asia/Novosibirsk"),
        ("🇷🇺 Владивосток (UTC+10)", "Asia/Vladivostok"),
        ("🌍 UTC+0", "UTC"),
        ("🇺🇸 Нью-Йорк (UTC-5)", "America/New_York"),
        ("🇪🇺 Лондон (UTC+0)", "Europe/London"),
        ("🇩🇪 Берлин (UTC+1)", "Europe/Berlin"),
        ("🇯🇵 Токио (UTC+9)", "Asia/Tokyo"),
    ]

    for label, tz in zones:
        callback_data = f"tz_{tz}"
        # Обрезаем если слишком длинное
        if len(callback_data.encode('utf-8')) > 64:
            callback_data = f"tz_{tz[:50]}"
        markup.add(types.InlineKeyboardButton(label, callback_data=callback_data))

    markup.row(types.InlineKeyboardButton(
        "✏️ Ввести вручную",
        callback_data="tz_manual"
    ))

    bot.send_message(
        message.chat.id,
        get_text(user_id, 'timezone_title'),
        reply_markup=markup,
        parse_mode='Markdown'
    )


@bot.message_handler(commands=['mytimezone'])
def cmd_my_timezone(message):
    """Показать текущий часовой пояс"""
    user_id = message.from_user.id
    tz = db.get_user_timezone(user_id)

    try:
        tz_obj = pytz.timezone(tz)
        current_time = datetime.now(tz_obj).strftime('%H:%M:%S')

        response = (
            f"🕐 *Ваш часовой пояс:* `{tz}`\n"
            f"📅 *Текущее время:* {current_time}"
        )
    except Exception:
        response = f"🕐 *Ваш часовой пояс:* `{tz}`"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "✏️ Изменить",
        callback_data="change_timezone"
    ))

    bot.send_message(
        message.chat.id,
        response,
        parse_mode='Markdown',
        reply_markup=markup
    )


@bot.message_handler(commands=['today'])
def cmd_today(message):
    """Показать сегодняшнее расписание"""
    user_id = message.from_user.id
    tz_name = db.get_user_timezone(user_id)

    try:
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        today_en = now.strftime('%A')
        day_key = EN_TO_RU_DAY.get(today_en)

        if not day_key:
            bot.reply_to(message, "❌ Не удалось определить день недели")
            return

        lessons = db.get_user_schedule(user_id, day_key)

        day_name_localized = get_text(user_id, day_key)

        if not lessons:
            reply = get_text(user_id, 'no_events_in_day', day_name_localized)
        else:
            reply = f"📅 *{day_name_localized}*\n\n"
            for i, lesson in enumerate(lessons, 1):
                reply += f"{i}. {lesson}\n"

        bot.send_message(message.chat.id, reply, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error in today for user {user_id}: {e}")
        bot.reply_to(message, "❌ Произошла ошибка. Проверьте /my_timezone")


@bot.message_handler(commands=['week'])
def cmd_week(message):
    """Показать расписание на неделю"""
    user_id = message.from_user.id
    schedule = db.get_user_schedule(user_id)

    text = get_text(user_id, 'week_schedule') + "\n\n"
    for day_key, day_name in DAYS.items():
        day_name_localized = get_text(user_id, day_key)
        lessons = schedule.get(day_key, [])
        text += f"*{day_name_localized}:*\n"
        if lessons:
            for lesson in lessons:
                text += f"  • {lesson}\n"
        else:
            text += f"  _{get_text(user_id, 'no_events')}_ \n"
        text += "\n"

    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['upcoming'])
def cmd_upcoming(message):
    user_id = message.from_user.id
    tz_name = db.get_user_timezone(user_id)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)

    today_en = now.strftime('%A')
    day_key = EN_TO_RU_DAY.get(today_en)

    lessons = db.get_user_schedule(user_id, day_key)

    upcoming = []

    for lesson in lessons:
        time_match = re.search(r'(\d{1,2}):(\d{2})', lesson)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            lesson_time = now.replace(hour=hour, minute=minute, second=0)

            if lesson_time > now:
                time_left = lesson_time - now
                hours = time_left.seconds // 3600
                minutes = (time_left.seconds % 3600) // 60

                lesson_name = re.sub(r'\s*\d{1,2}:\d{2}', '', lesson).strip()
                if not lesson_name:
                    lesson_name = lesson

                if hours > 0:
                    time_str = get_text(user_id, 'in_hours', hours, minutes)
                else:
                    time_str = get_text(user_id, 'in_minutes', minutes)

                upcoming.append(get_text(user_id, 'upcoming_item', lesson_name, hour, minute, time_str))

    if upcoming:
        text = get_text(user_id, 'upcoming_today') + "\n\n" + "\n".join(upcoming)
    else:
        text = get_text(user_id, 'no_upcoming')

    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['edit'])
def cmd_edit(message):
    user_id = message.from_user.id
    keyboard = days_keyboard('edit', user_id)
    text = get_text(user_id, 'edit_day')
    bot.send_message(message.chat.id, text, reply_markup=keyboard)


@bot.message_handler(commands=['notifications'])
def cmd_notifications(message):
    """Настройка уведомлений"""
    user_id = message.from_user.id
    settings = db.get_notification_settings(user_id)

    # Добавляем отладочную информацию
    logger.info(f"User {user_id} settings: {settings}")

    status = get_text(user_id, 'notif_enabled') if settings['enabled'] else get_text(user_id, 'notif_disabled')

    text = (
        f"🔔 *{get_text(user_id, 'notif_title')}*\n\n"
        f"{get_text(user_id, 'notif_status')}: {status}\n"
        f"{get_text(user_id, 'notif_time')}: *{settings['notify_time']}*\n"
        f"{get_text(user_id, 'notif_before')}: *{settings['notify_before_minutes']}* {get_text(user_id, 'minutes')}\n\n"
        f"{get_text(user_id, 'notif_choose_action')}:"
    )

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton(
            get_text(user_id, 'notif_toggle_btn'),
            callback_data=f"notif_toggle_{user_id}"
        ),
        types.InlineKeyboardButton(
            get_text(user_id, 'notif_time_btn'),
            callback_data=f"notif_time_{user_id}"
        )
    )
    keyboard.row(
        types.InlineKeyboardButton(
            get_text(user_id, 'notif_interval_btn'),
            callback_data=f"notif_interval_{user_id}"
        ),
        types.InlineKeyboardButton(
            get_text(user_id, 'notif_test_btn'),
            callback_data=f"notif_test_{user_id}"
        )
    )

    # Отправляем новое сообщение
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)


@bot.message_handler(commands=['autoclear'])
def cmd_autoclear(message):
    """Настройка автоматической очистки расписания"""
    user_id = message.from_user.id
    settings = db.get_auto_clear_settings(user_id)

    day_names = {
        'monday': 'Понедельник',
        'tuesday': 'Вторник',
        'wednesday': 'Среда',
        'thursday': 'Четверг',
        'friday': 'Пятница',
        'saturday': 'Суббота',
        'sunday': 'Воскресенье'
    }

    status = get_text(user_id, 'auto_clear_enabled') if settings['auto_clear'] else get_text(user_id,
                                                                                             'auto_clear_disabled')
    clear_day_name = day_names.get(settings['clear_day'], get_text(user_id, 'sunday'))

    text = (
        f"🗑️ *{get_text(user_id, 'auto_clear_title')}*\n\n"
        f"{get_text(user_id, 'notif_status')}: {status}\n"
        f"{get_text(user_id, 'clear_day')}: *{clear_day_name}*\n\n"
        f"{get_text(user_id, 'auto_clear_info')}\n\n"
        f"{get_text(user_id, 'notif_choose_action')}:"
    )

    keyboard = InlineKeyboardMarkup(row_width=2)

    if settings['auto_clear']:
        keyboard.add(InlineKeyboardButton(
            get_text(user_id, 'disable_btn'),
            callback_data=f"autoclear_toggle_{user_id}"
        ))
    else:
        keyboard.add(InlineKeyboardButton(
            get_text(user_id, 'enable_btn'),
            callback_data=f"autoclear_toggle_{user_id}"
        ))

    keyboard.add(InlineKeyboardButton(
        get_text(user_id, 'choose_clear_day'),
        callback_data=f"autoclear_day_{user_id}"
    ))

    keyboard.add(InlineKeyboardButton(
        get_text(user_id, 'clear_now_btn'),
        callback_data=f"autoclear_now_{user_id}"
    ))

    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)


@bot.message_handler(commands=['debug'])
def cmd_debug(message):
    """Показать текущие настройки из БД (для отладки)"""
    user_id = message.from_user.id

    with sqlite3.connect('bot.db') as conn:
        cursor = conn.execute('''
            SELECT user_id, enabled, notify_time, notify_before_minutes, updated_at 
            FROM notification_settings WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()

        if row:
            text = (
                "🔍 *Настройки из БД:*\n\n"
                f"User ID: `{row[0]}`\n"
                f"Включены: `{bool(row[1])}`\n"
                f"Время: `{row[2]}`\n"
                f"Интервал: `{row[3]}` мин\n"
                f"Обновлено: `{row[4]}`"
            )
        else:
            text = "❌ Настройки не найдены"

    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['comment'])
def cmd_comment(message):
    user_id = message.from_user.id
    keyboard = days_keyboard('comment_select', user_id)
    text = get_text(user_id, 'choose_weekday')
    bot.send_message(message.chat.id, text, reply_markup=keyboard)


@bot.message_handler(commands=['language'])
def cmd_language(message):
    """Выбор языка"""
    user_id = message.from_user.id

    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🇷🇺 Русский", callback_data=f"lang_ru_{user_id}"),
        InlineKeyboardButton("🇬🇧 English", callback_data=f"lang_en_{user_id}")
    )

    bot.send_message(
        message.chat.id,
        get_text(user_id, 'choose_language'),
        reply_markup=keyboard
    )


def show_schedule_with_comments(chat_id, user_id, day_key):
    """Показать расписание с комментариями"""
    lessons = db.get_user_schedule(user_id, day_key)

    if not lessons:
        reply = get_text(user_id, 'no_events_in_day', get_text(user_id, day_key))
    else:
        reply = f"📅 *{get_text(user_id, day_key)}*\n\n"
        for i, lesson in enumerate(lessons, 1):
            comment = db.get_comment(user_id, day_key, i - 1)
            reply += f"{i}. {lesson}\n"
            if comment:
                reply += f"   📎 *{get_text(user_id, 'comment_label')}:* {comment}\n"

    # Кнопка для добавления/редактирования комментария
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("📝 Добавить комментарий", callback_data=f"comment_{user_id}_{day_key}")
    )

    bot.send_message(chat_id, reply, parse_mode='Markdown', reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    """Обработка нажатий на кнопки"""
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    def safe_edit_text(text, reply_markup=None, parse_mode='Markdown'):
        try:
            bot.edit_message_text(
                text,
                chat_id,
                call.message.message_id,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")

    # Обработка кнопок "Понятно" из уведомлений
    if data.startswith('ack_'):
        bot.answer_callback_query(call.id, "✅ Отлично!")
        bot.edit_message_text(
            get_text(user_id, 'reminder_received'),
            chat_id,
            call.message.message_id
        )
        return

    # Обработка уведомлений
    if data.startswith('notif_'):
        parts = data.split('_')
        action = parts[1]

        if action == 'toggle':
            settings = db.get_notification_settings(user_id)
            db.update_notification_settings(user_id, enabled=not settings['enabled'])
            status = "включены" if not settings['enabled'] else "отключены"
            bot.answer_callback_query(call.id, f"✅ Уведомления {status}")

            # 👇 ОБНОВЛЯЕМ СООБЩЕНИЕ
            settings = db.get_notification_settings(user_id)
            status_text = "✅ Включены" if settings['enabled'] else "❌ Отключены"

            text = (
                f"🔔 *{get_text(user_id, 'notif_title')}*\n\n"
                f"{get_text(user_id, 'notif_status')}: {status_text}\n"
                f"{get_text(user_id, 'notif_time')}: *{settings['notify_time']}*\n"
                f"{get_text(user_id, 'notif_before')}: *{settings['notify_before_minutes']}* {get_text(user_id, 'minutes')}\n\n"
                f"{get_text(user_id, 'notif_choose_action')}:"
            )

            keyboard = types.InlineKeyboardMarkup(row_width=2)
            keyboard.row(
                types.InlineKeyboardButton(get_text(user_id, 'notif_toggle_btn'),
                                           callback_data=f"notif_toggle_{user_id}"),
                types.InlineKeyboardButton(get_text(user_id, 'notif_time_btn'), callback_data=f"notif_time_{user_id}")
            )
            keyboard.row(
                types.InlineKeyboardButton(get_text(user_id, 'notif_interval_btn'),
                                           callback_data=f"notif_interval_{user_id}"),
                types.InlineKeyboardButton(get_text(user_id, 'notif_test_btn'), callback_data=f"notif_test_{user_id}")
            )

            bot.edit_message_text(
                text,
                chat_id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )

        elif action == 'time':
            markup = types.InlineKeyboardMarkup(row_width=3)
            times = ["08:00", "09:00", "10:00", "18:00", "19:00", "20:00"]
            for t in times:
                markup.add(types.InlineKeyboardButton(
                    t,
                    callback_data=f"notif_settime_{t}"
                ))
            markup.add(types.InlineKeyboardButton(get_text(user_id, 'cancel_btn'), callback_data="notif_cancel"))

            bot.edit_message_text(
                get_text(user_id, 'choose_notify_time'),
                chat_id,
                call.message.message_id,
                reply_markup=markup
            )


        elif action == 'settime':
            notify_time = parts[2]
            if len(notify_time) == 5 and notify_time[2] == ':':
                db.update_notification_settings(user_id, notify_time=notify_time)
                bot.answer_callback_query(call.id, f"✅ Время установлено: {notify_time}")

                settings = db.get_notification_settings(user_id)
                status = get_text(user_id, 'notif_enabled') if settings['enabled'] else get_text(user_id,
                                                                                                 'notif_disabled')

                text = (
                    f"🔔 *{get_text(user_id, 'notif_title')}*\n\n"
                    f"{get_text(user_id, 'notif_status')}: {status}\n"
                    f"{get_text(user_id, 'notif_time')}: *{settings['notify_time']}*\n"
                    f"{get_text(user_id, 'notif_before')}: *{settings['notify_before_minutes']}* {get_text(user_id, 'minutes')}\n\n"
                    f"{get_text(user_id, 'notif_choose_action')}:"
                )

                keyboard = types.InlineKeyboardMarkup(row_width=2)
                keyboard.row(
                    types.InlineKeyboardButton("🔛 Вкл/Выкл", callback_data=f"notif_toggle_{user_id}"),
                    types.InlineKeyboardButton("⏰ Время уведомления", callback_data=f"notif_time_{user_id}")
                )
                keyboard.row(
                    types.InlineKeyboardButton("⏱ Интервал напоминания", callback_data=f"notif_interval_{user_id}"),
                    types.InlineKeyboardButton("📝 Тест", callback_data=f"notif_test_{user_id}")
                )

                # Обновляем существующее сообщение
                bot.edit_message_text(
                    text,
                    chat_id,
                    call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )
            else:
                bot.answer_callback_query(call.id, "❌ Неверный формат времени", show_alert=True)

        elif action == 'interval':
            markup = types.InlineKeyboardMarkup(row_width=3)
            intervals = [15, 30, 60, 120, 180, 1440]
            for m in intervals:
                if m < 1440:
                    label = f"{m} {get_text(user_id, 'minutes_short')}"
                else:
                    label = get_text(user_id, 'hours_24')
                markup.add(types.InlineKeyboardButton(label, callback_data=f"notif_setinterval_{m}"))

            markup.add(types.InlineKeyboardButton(get_text(user_id, 'cancel_btn'), callback_data="notif_cancel"))

            bot.edit_message_text(
                get_text(user_id, 'interval_title'),
                chat_id,
                call.message.message_id,
                reply_markup=markup
            )


        elif action == 'setinterval':
            minutes = int(parts[2])
            db.update_notification_settings(user_id, notify_before_minutes=minutes)
            bot.answer_callback_query(call.id, get_text(user_id, 'interval_set', minutes))

            settings = db.get_notification_settings(user_id)

            status_text = get_text(user_id, 'notif_enabled') if settings['enabled'] else get_text(user_id,
                                                                                                  'notif_disabled')

            text = (
                f"🔔 *{get_text(user_id, 'notif_title')}*\n\n"
                f"{get_text(user_id, 'notif_status')}: {status_text}\n"
                f"{get_text(user_id, 'notif_time')}: *{settings['notify_time']}*\n"
                f"{get_text(user_id, 'notif_before')}: *{settings['notify_before_minutes']}* {get_text(user_id, 'minutes')}\n\n"
                f"{get_text(user_id, 'notif_choose_action')}:"
            )

            keyboard = types.InlineKeyboardMarkup(row_width=2)
            keyboard.row(
                types.InlineKeyboardButton(get_text(user_id, 'notif_toggle_btn'),
                                           callback_data=f"notif_toggle_{user_id}"),
                types.InlineKeyboardButton(get_text(user_id, 'notif_time_btn'), callback_data=f"notif_time_{user_id}")

            )
            keyboard.row(
                types.InlineKeyboardButton(get_text(user_id, 'notif_interval_btn'),
                                           callback_data=f"notif_interval_{user_id}"),
                types.InlineKeyboardButton(get_text(user_id, 'notif_test_btn'), callback_data=f"notif_test_{user_id}")
            )

            bot.edit_message_text(
                text,
                chat_id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )

        elif action == 'test':
            send_notification(
                user_id,
                get_text(user_id, 'test_notification')
            )
            bot.answer_callback_query(call.id, "✅ Тестовое уведомление отправлено")

        elif action == 'cancel':
            cmd_notifications(call.message)

        return

    # Обработка автоочистки
    if data.startswith('autoclear_'):
        parts = data.split('_')
        action = parts[1]

        if action == 'toggle':
            user_id = int(parts[2])
            settings = db.get_auto_clear_settings(user_id)
            new_value = not settings['auto_clear']
            db.update_auto_clear_settings(user_id, new_value)

            status = "включена" if new_value else "отключена"
            bot.answer_callback_query(call.id, f"✅ Автоочистка {status}")

            # Обновляем сообщение
            updated_settings = db.get_auto_clear_settings(user_id)
            day_names = {
                'monday': 'Понедельник', 'tuesday': 'Вторник',
                'wednesday': 'Среда', 'thursday': 'Четверг',
                'friday': 'Пятница', 'saturday': 'Суббота',
                'sunday': 'Воскресенье'
            }
            status_text = "✅ Включена" if updated_settings['auto_clear'] else "❌ Отключена"
            clear_day_name = day_names.get(updated_settings['clear_day'], 'Воскресенье')

            text = (
                "🗑️ *Автоматическая очистка расписания*\n\n"
                f"Статус: {status_text}\n"
                f"День очистки: *{clear_day_name}*\n\n"
                "Расписание будет автоматически очищаться в выбранный день недели в 00:00\n\n"
                "Выберите действие:"
            )

            keyboard = InlineKeyboardMarkup(row_width=2)

            if updated_settings['auto_clear']:
                keyboard.add(InlineKeyboardButton(
                    "🔴 Отключить автоочистку",
                    callback_data=f"autoclear_toggle_{user_id}"
                ))
            else:
                keyboard.add(InlineKeyboardButton(
                    "🟢 Включить автоочистку",
                    callback_data=f"autoclear_toggle_{user_id}"
                ))

            keyboard.add(InlineKeyboardButton(
                "📅 Выбрать день очистки",
                callback_data=f"autoclear_day_{user_id}"
            ))

            keyboard.add(InlineKeyboardButton(
                "🧹 Очистить сейчас",
                callback_data=f"autoclear_now_{user_id}"
            ))

            bot.edit_message_text(
                text,
                chat_id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            return

        elif action == 'day':
            user_id = int(parts[2])
            markup = InlineKeyboardMarkup(row_width=2)
            days = [
                ('monday', 'Понедельник'), ('tuesday', 'Вторник'),
                ('wednesday', 'Среда'), ('thursday', 'Четверг'),
                ('friday', 'Пятница'), ('saturday', 'Суббота'),
                ('sunday', 'Воскресенье')
            ]
            for day_key, day_name in days:
                markup.add(InlineKeyboardButton(
                    day_name,
                    callback_data=f"autoclear_setday_{user_id}_{day_key}"
                ))
            markup.add(InlineKeyboardButton("❌ Отмена", callback_data="autoclear_cancel"))

            bot.edit_message_text(
                "📅 Выберите день недели для автоматической очистки:",
                chat_id,
                call.message.message_id,
                reply_markup=markup
            )
            return

        elif action == 'setday':
            user_id = int(parts[2])
            day_key = parts[3]
            settings = db.get_auto_clear_settings(user_id)
            db.update_auto_clear_settings(user_id, settings['auto_clear'], day_key)

            day_names = {
                'monday': 'Понедельник', 'tuesday': 'Вторник',
                'wednesday': 'Среда', 'thursday': 'Четверг',
                'friday': 'Пятница', 'saturday': 'Суббота',
                'sunday': 'Воскресенье'
            }
            bot.answer_callback_query(call.id, f"✅ День очистки: {day_names[day_key]}")

            # Обновляем главное сообщение
            updated_settings = db.get_auto_clear_settings(user_id)
            status_text = "✅ Включена" if updated_settings['auto_clear'] else "❌ Отключена"
            clear_day_name = day_names.get(updated_settings['clear_day'], 'Воскресенье')

            text = (
                "🗑️ *Автоматическая очистка расписания*\n\n"
                f"Статус: {status_text}\n"
                f"День очистки: *{clear_day_name}*\n\n"
                "Расписание будет автоматически очищаться в выбранный день недели в 00:00\n\n"
                "Выберите действие:"
            )

            keyboard = InlineKeyboardMarkup(row_width=2)

            if updated_settings['auto_clear']:
                keyboard.add(InlineKeyboardButton(
                    "🔴 Отключить автоочистку",
                    callback_data=f"autoclear_toggle_{user_id}"
                ))
            else:
                keyboard.add(InlineKeyboardButton(
                    "🟢 Включить автоочистку",
                    callback_data=f"autoclear_toggle_{user_id}"
                ))

            keyboard.add(InlineKeyboardButton(
                "📅 Выбрать день очистки",
                callback_data=f"autoclear_day_{user_id}"
            ))

            keyboard.add(InlineKeyboardButton(
                "🧹 Очистить сейчас",
                callback_data=f"autoclear_now_{user_id}"
            ))

            bot.edit_message_text(
                text,
                chat_id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            return

        elif action == 'now':
            user_id = int(parts[2])
            if db.clear_user_schedule(user_id):
                bot.answer_callback_query(call.id, "✅ Расписание очищено!")
                text = "🗑️ *Расписание очищено*\n\nДобавьте новые мероприятия командой /edit"
                bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка при очистке", show_alert=True)
            return

        elif action == 'cancel':
            cmd_autoclear(call.message)
            return


    # Обработка выбора языка
    elif data.startswith('lang_'):
        parts = data.split('_')
        lang = parts[1]
        user_id = int(parts[2])

        db.set_user_language(user_id, lang)
        bot.answer_callback_query(call.id, "✅ Language changed!" if lang == 'en' else "✅ Язык изменён!")

        text = "🌍 Language changed to English!" if lang == 'en' else "🌍 Язык изменён на русский!"
        bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
        return


    # Обработка выбора дня для комментария
    elif data.startswith('comment_select_'):
        bot.answer_callback_query(call.id)
        parts = data.split('_')
        user_id = int(parts[2])
        day_key = parts[3]

        lessons = db.get_user_schedule(user_id, day_key)
        if not lessons:
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton(
                get_text(user_id, 'add_event_btn'),
                callback_data=f"add_{user_id}_{day_key}"
            ))
            keyboard.add(types.InlineKeyboardButton(
                get_text(user_id, 'back_btn_short'),
                callback_data=f"view_{user_id}"
            ))

            bot.edit_message_text(
                f"📅 *{get_text(user_id, day_key)}*\n\n"
                f"{get_text(user_id, 'no_events_in_day')}\n\n"
                f"{get_text(user_id, 'add_event_question')}",
                chat_id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            return

        # Показываем список мероприятий для выбора
        keyboard = types.InlineKeyboardMarkup()
        for idx, lesson in enumerate(lessons):
            short = lesson if len(lesson) <= 30 else lesson[:27] + '...'
            keyboard.add(types.InlineKeyboardButton(
                short,
                callback_data=f"comment_lesson_{user_id}_{day_key}_{idx}"
            ))
        keyboard.add(types.InlineKeyboardButton(get_text(user_id, 'back_btn_short'), callback_data=f"view_{user_id}"))
        text = get_text(user_id, 'choose_event_for_comment')

        bot.edit_message_text(
            f"✏️ {text} *{get_text(user_id, day_key)}*:",
            chat_id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        return

        # Обработка выбора мероприятия
    elif data.startswith('comment_lesson_'):
        bot.answer_callback_query(call.id)
        parts = data.split('_')
        user_id = int(parts[2])
        day_key = parts[3]
        lesson_idx = int(parts[4])

        lessons = db.get_user_schedule(user_id, day_key)
        lesson_name = lessons[lesson_idx]

        # Сохраняем данные во временном хранилище
        if not hasattr(bot, 'temp_comment_data'):
            bot.temp_comment_data = {}
        bot.temp_comment_data[user_id] = {
            'day_key': day_key,
            'lesson_idx': lesson_idx,
            'lesson_name': lesson_name
        }

        current_comment = db.get_comment(user_id, day_key, lesson_idx)

        text = f"✏️ {get_text(user_id, 'enter_comment')}\n\n*{lesson_name}*"
        if current_comment:
            text += f"\n\n📎 *{get_text(user_id, 'current_comment')}:* {current_comment}\n\n_{get_text(user_id, 'comment_hint')}_"

        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            parse_mode='Markdown'
        )

        # Ждём следующий шаг
        bot.register_next_step_handler_by_chat_id(
            chat_id,
            process_comment_input,
            user_id,
            day_key,
            lesson_idx,
            lesson_name
        )
        return

    # Обработка часовых поясов
    if data.startswith('tz_'):
        if data == 'tz_manual':
            msg = bot.send_message(
                chat_id,
                "✏️ Введите название часового пояса (например: Europe/Moscow, America/New_York):"
            )
            bot.register_next_step_handler(msg, process_manual_timezone)
            bot.answer_callback_query(call.id)
            return
        else:
            tz_name = data[3:]
            if tz_name in pytz.all_timezones:
                if db.set_user_timezone(user_id, tz_name):
                    bot.answer_callback_query(call.id, f"✅ Часовой пояс сохранён: {tz_name}")
                    bot.edit_message_text(
                        f"✅ Часовой пояс установлен: *{tz_name}*",
                        chat_id,
                        call.message.message_id,
                        parse_mode='Markdown'
                    )
                else:
                    bot.answer_callback_query(call.id, "❌ Ошибка сохранения", show_alert=True)
            else:
                bot.answer_callback_query(call.id, "❌ Неверный часовой пояс", show_alert=True)
            return

    elif data == 'change_timezone':
        cmd_set_timezone(call.message)
        bot.answer_callback_query(call.id)
        return


    elif data.startswith('edit_') and len(data.split('_')) == 3:
        # Формат: edit_123456789_monday
        bot.answer_callback_query(call.id)
        parts = data.split('_')
        user_id = int(parts[1])
        day_key = parts[2]

        # Показываем конкретный день с кнопками
        lessons = db.get_user_schedule(user_id, day_key)
        day_name_localized = get_text(user_id, day_key)
        text = f"*{day_name_localized}*\n\n"
        if lessons:
            for i, lesson in enumerate(lessons, 1):
                comment = db.get_comment(user_id, day_key, i - 1)
                text += f"{i}. {lesson}\n"
                if comment:
                    text += f"   📎 *{get_text(user_id, 'comment_label')}:* {comment[:50]}...\n"
        else:
            text += f"_{get_text(user_id, 'no_events')}_ \n"
        text += f"\n{get_text(user_id, 'notif_choose_action')}:"

        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton(get_text(user_id, 'add_event'), callback_data=f"add_{user_id}_{day_key}"),
            types.InlineKeyboardButton(get_text(user_id, 'delete_event'), callback_data=f"remove_{user_id}_{day_key}"),
            types.InlineKeyboardButton(get_text(user_id, 'comment_btn'),
                                       callback_data=f"comment_select_{user_id}_{day_key}")
        )
        keyboard.row(types.InlineKeyboardButton(get_text(user_id, 'back_btn'), callback_data=f"edit_{user_id}"))

        safe_edit_text(text, keyboard)
        return



    elif data.startswith('edit_') and len(data.split('_')) == 2:
        bot.answer_callback_query(call.id)
        user_id = int(data.split('_')[1])
        keyboard = days_keyboard('edit', user_id)
        safe_edit_text(get_text(user_id, 'edit_day'), keyboard)
        return

    # Обработка расписания
    if data.startswith(('view_', 'add_', 'remove_', 'del_', 'done_', 'ack_')):
        parts = data.split('_')
        action = parts[0]

        if action == 'done':
            bot.answer_callback_query(call.id, "✅ Отмечено!")
            bot.edit_message_text(
                get_text(user_id, 'good_day'),
                chat_id,
                call.message.message_id
            )

        elif action == 'ack':
            bot.answer_callback_query(call.id, "✅ Принято!")

        # Парсим user_id и day_key из callback
        if len(parts) >= 3 and parts[1].isdigit():
            callback_user_id = int(parts[1])
            if len(parts) >= 4 and parts[2] == 'week':
                day_key = 'week'
                idx = None
            else:
                day_key = parts[2]
                idx = int(parts[3]) if len(parts) > 3 and action == 'del' else None
        else:
            callback_user_id = user_id
            day_key = parts[1]
            idx = int(parts[2]) if len(parts) > 2 and action == 'del' else None

        # Проверка прав
        if callback_user_id != user_id:
            bot.answer_callback_query(call.id, "❌ Это не ваше расписание!", show_alert=True)
            return

        try:
            if action == 'view':
                if day_key == 'week':
                    schedule = db.get_user_schedule(user_id)
                    text = get_text(user_id, 'week_schedule') + "\n\n"
                    for d_key, d_name in DAYS.items():
                        day_localized = get_text(user_id, d_key)
                        lessons = schedule.get(d_key, [])
                        text += f"*{day_localized}:*\n"
                        if lessons:
                            for lesson in lessons:
                                text += f"  • {lesson}\n"
                        else:
                            text += f"  _{get_text(user_id, 'no_events')}_ \n"
                        text += "\n"
                    bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
                else:
                    lessons = db.get_user_schedule(user_id, day_key)
                    day_name_localized = get_text(user_id, day_key)
                    if not lessons:
                        reply = get_text(user_id, 'no_events_in_day', day_name_localized)
                    else:
                        reply = f"📅 *{day_name_localized}*\n\n"
                        for i, lesson in enumerate(lessons, 1):
                            reply += f"{i}. {lesson}\n"
                    bot.edit_message_text(reply, chat_id, call.message.message_id, parse_mode='Markdown')

            # elif action == 'edit':
            #     lessons = db.get_user_schedule(user_id, day_key)
            #     day_name_localized = get_text(user_id, day_key)
            #     text = f"*{day_name_localized}*\n\n"
            #     if lessons:
            #         for i, lesson in enumerate(lessons, 1):
            #             comment = db.get_comment(user_id, day_key, i - 1)
            #             text += f"{i}. {lesson}\n"
            #             if comment:
            #                 text += f"   📎 *{get_text(user_id, 'comment_label')}:* {comment[:50]}...\n"
            #     else:
            #         text += f"_{get_text(user_id, 'no_events')}_ \n"
            #     text += f"\n{get_text(user_id, 'notif_choose_action')}:"
            #
            #     keyboard = types.InlineKeyboardMarkup()
            #     keyboard.row(
            #         types.InlineKeyboardButton(get_text(user_id, 'add_event'),
            #                                    callback_data=f"add_{user_id}_{day_key}"),
            #         types.InlineKeyboardButton(get_text(user_id, 'delete_event'),
            #                                    callback_data=f"remove_{user_id}_{day_key}"),
            #         types.InlineKeyboardButton(get_text(user_id, 'comment_btn'),
            #                                    callback_data=f"comment_select_{user_id}_{day_key}")
            #     )
            #     keyboard.row(types.InlineKeyboardButton(get_text(user_id, 'back_btn'), callback_data=f"edit_{user_id}"))
            #
            #     bot.edit_message_text(text, chat_id, call.message.message_id,
            #                           parse_mode='Markdown', reply_markup=keyboard)
            #     bot.answer_callback_query(call.id)

            elif action == 'add':
                day_name_localized = get_text(user_id, day_key)

                bot.send_message(chat_id,
                                 get_text(user_id, 'add_event_msg', get_text(user_id, day_key)),
                                 parse_mode='Markdown')
                bot.register_next_step_handler_by_chat_id(
                    chat_id,
                    process_add_lesson,
                    user_id,
                    day_key
                )
                bot.answer_callback_query(call.id)

            elif action == 'remove':
                lessons = db.get_user_schedule(user_id, day_key)
                if not lessons:
                    bot.answer_callback_query(call.id, "❌ Нет мероприятий для удаления", show_alert=True)
                    return

                keyboard = types.InlineKeyboardMarkup()
                for idx, lesson in enumerate(lessons):
                    short = lesson if len(lesson) <= 25 else lesson[:22] + '...'
                    callback = f"del_{user_id}_{day_key}_{idx}"
                    keyboard.add(types.InlineKeyboardButton(short, callback_data=callback))

                keyboard.row(types.InlineKeyboardButton(get_text(user_id, 'cancel_btn'),
                                                        callback_data=f"edit_{user_id}_{day_key}"))

                day_name_localized = get_text(user_id, day_key)

                bot.edit_message_text(
                    get_text(user_id, 'remove_event_msg', day_name_localized),
                    chat_id,
                    call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )
                bot.answer_callback_query(call.id)

            elif action == 'del':
                if db.delete_lesson(user_id, day_key, idx):
                    bot.answer_callback_query(call.id, "✅ Мероприятие удалено")
                    lessons = db.get_user_schedule(user_id, day_key)
                    text = f"✅ {get_text(user_id, 'event_deleted_from')} *{get_text(user_id, day_key)}*.\n\n{get_text(user_id, 'current_schedule')}:\n"
                    if lessons:
                        for i, lesson in enumerate(lessons, 1):
                            text += f"{i}. {lesson}\n"
                    else:
                        text += "Мероприятий нет."
                    bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
                else:
                    bot.answer_callback_query(call.id, "❌ Ошибка при удалении", show_alert=True)

        except Exception as e:
            logger.error(f"Error in callback {data}: {e}")
            bot.answer_callback_query(call.id, "❌ Произошла ошибка", show_alert=True)


    elif data == "cancel":
        settings = db.get_notification_settings(user_id)
        status_text = get_text(user_id, 'notif_enabled') if settings['enabled'] else get_text(user_id, 'notif_disabled')

        text = (
            f"🔔 *{get_text(user_id, 'notif_title')}*\n\n"
            f"{get_text(user_id, 'notif_status')}: {status_text}\n"
            f"{get_text(user_id, 'notif_time')}: *{settings['notify_time']}*\n"
            f"{get_text(user_id, 'notif_before')}: *{settings['notify_before_minutes']}* {get_text(user_id, 'minutes')}\n\n"
            f"{get_text(user_id, 'notif_choose_action')}:"
        )

        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.row(
            types.InlineKeyboardButton(get_text(user_id, 'notif_toggle_btn'), callback_data=f"notif_toggle_{user_id}"),
            types.InlineKeyboardButton(get_text(user_id, 'notif_time_btn'), callback_data=f"notif_time_{user_id}")
        )
        keyboard.row(
            types.InlineKeyboardButton(get_text(user_id, 'notif_interval_btn'),
                                       callback_data=f"notif_interval_{user_id}"),
            types.InlineKeyboardButton(get_text(user_id, 'notif_test_btn'), callback_data=f"notif_test_{user_id}")
        )

        bot.edit_message_text(
            text,
            chat_id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        bot.answer_callback_query(call.id)
    else:
        logger.warning(f"Unknown callback data: {data}")
        bot.answer_callback_query(call.id, "⚠️ Неизвестная команда")


@bot.message_handler(commands=['download_db'])
def cmd_download_db(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав")
        return
    with open('bot.db', 'rb') as f:
        bot.send_document(message.chat.id, f, caption="📁 База данных")


@bot.message_handler(commands=['list_users'])
def cmd_list_users(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Нет прав")
        return

    conn = sqlite3.connect('bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name, created_at FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()
    conn.close()

    if not users:
        bot.reply_to(message, "Нет пользователей")
        return

    text = "👥 *Список пользователей:*\n\n"
    for user in users:
        text += f"🆔 ID: `{user[0]}`\n"
        text += f"📝 Имя: {user[2] or 'Не указано'}\n"
        text += f"🔗 Username: @{user[1] if user[1] else 'Нет'}\n"
        text += f"📅 Зарегистрирован: {user[3]}\n"
        text += "───────────\n"

    bot.send_message(message.chat.id, text, parse_mode=None)


def check_and_clear_schedules():
    """Проверить и очистить расписания пользователей с автоочисткой"""
    try:
        users = db.get_users_with_auto_clear_enabled()
        if not users:
            return

        # Определяем текущий день недели
        current_day = datetime.now().strftime('%A').lower()

        for user_id, clear_day in users:
            try:
                # Проверяем, наступил ли день очистки
                if current_day == clear_day:
                    # Проверяем, не отправляли ли уже сегодня уведомление
                    with sqlite3.connect('bot.db') as conn:
                        cursor = conn.execute('''
                            SELECT updated_at FROM notification_settings WHERE user_id = ?
                        ''', (user_id,))
                        row = cursor.fetchone()

                    # Если последнее обновление было сегодня - пропускаем
                    if row and row[0]:
                        last_update = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
                        if last_update.date() == datetime.now().date():
                            continue

                    # Очищаем расписание
                    if db.clear_user_schedule(user_id):
                        day_names = {
                            'monday': 'Понедельник', 'tuesday': 'Вторник',
                            'wednesday': 'Среда', 'thursday': 'Четверг',
                            'friday': 'Пятница', 'saturday': 'Суббота',
                            'sunday': 'Воскресенье'
                        }

                        # Отправляем уведомление
                        day_name_localized = get_text(user_id, clear_day)
                        text = (
                            f"🗑️ *{get_text(user_id, 'weekly_clear_title')}*\n\n"
                            f"{get_text(user_id, 'schedule_auto_cleared')} {day_name_localized}.\n\n"
                            f"{get_text(user_id, 'add_new_events_hint')}"
                        )

                        keyboard = InlineKeyboardMarkup()
                        keyboard.add(InlineKeyboardButton(
                            "📅 Добавить расписание",
                            callback_data=f"edit_{user_id}"
                        ))

                        send_notification(user_id, text, keyboard)

            except Exception as e:
                logger.error(f"Error checking auto-clear for user {user_id}: {e}")

    except Exception as e:
        logger.error(f"Error in check_and_clear_schedules: {e}")


def process_manual_timezone(message):
    """Обработка ручного ввода часового пояса"""
    tz_name = message.text.strip()
    user_id = message.from_user.id

    if tz_name in pytz.all_timezones:
        if db.set_user_timezone(user_id, tz_name):
            bot.reply_to(
                message,
                f"✅ Часовой пояс установлен: *{tz_name}*",
                parse_mode='Markdown'
            )
        else:
            bot.reply_to(message, "❌ Ошибка при сохранении")
    else:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "🌍 Выбрать из списка",
            callback_data="tz_manual"
        ))
        bot.reply_to(
            message,
            f"❌ Часовой пояс '{tz_name}' не найден.\n"
            "Проверьте название или выберите из списка.",
            reply_markup=markup
        )


def process_add_lesson(message, user_id, day_key):
    """Обработка добавления мероприятия"""
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, введите текст мероприятия")
        return

    lesson_text = message.text.strip()

    if not lesson_text:
        bot.send_message(message.chat.id, "❌ Мероприятие не может быть пустым.")
        return

    if len(lesson_text) > 200:
        bot.send_message(message.chat.id, "❌ Слишком длинное название (макс. 200 символов)")
        return

    db.add_lesson(user_id, day_key, lesson_text)

    lessons = db.get_user_schedule(user_id, day_key)
    text = f"✅ {get_text(user_id, 'event_added_to')} *{get_text(user_id, day_key)}*.\n\n{get_text(user_id, 'current_schedule')}:\n"
    for i, lesson in enumerate(lessons, 1):
        text += f"{i}. {lesson}\n"

    # Добавляем кнопки для дальнейших действий
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton(get_text(user_id, 'add_more_btn'), callback_data=f"add_{user_id}_{day_key}"),
        types.InlineKeyboardButton(get_text(user_id, 'back_btn'), callback_data=f"view_{user_id}_{day_key}")
    )

    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)


def process_comment_input(message, user_id, day_key, lesson_idx, lesson_name):
    """Обработка введённого комментария"""
    comment = message.text.strip()

    # Если пользователь отправил "-", удаляем комментарий
    if comment == '-':
        db.delete_comment(user_id, day_key, lesson_idx)
        bot.send_message(
            message.chat.id,
            get_text(user_id, 'comment_deleted', lesson_name),
            parse_mode='Markdown'
        )
        return

    # Ограничиваем длину комментария
    if len(comment) > 500:
        bot.send_message(
            message.chat.id,
            get_text(user_id, 'comment_too_long')
        )
        return

    # Сохраняем комментарий
    db.add_comment(user_id, day_key, lesson_idx, comment)

    bot.send_message(
        message.chat.id,
        get_text(user_id, 'comment_added', lesson_name, comment),
        parse_mode='Markdown'
    )


if __name__ == '__main__':
    logger.info("Бот запущен...")

    try:
        bot.remove_webhook()
        logger.info("Webhook removed")
    except Exception as e:
        logger.warning(f"Failed to remove webhook: {e}")

    setup_commands()

    try:
        notification_thread = threading.Thread(target=notification_worker, daemon=True)
        notification_thread.start()
        logger.info("Notification worker started")
    except Exception as e:
        logger.error(f"Failed to start notification worker: {e}")

    try:
        logger.info("Starting bot polling...")
        bot.infinity_polling(
            timeout=60,
            long_polling_timeout=60,
            skip_pending=True
        )
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")
