import logging
from datetime import datetime, timedelta
import pytz
import os
import json
import sqlite3
import threading
import time
import schedule
from telebot import TeleBot, types
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загружаем переменные из .env
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

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


class Database:
    def __init__(self, db_path='bot.db'):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        """Получить соединение с БД"""
        return sqlite3.connect(self.db_path)

    def init_db(self):
        """Инициализация всех таблиц БД"""
        with self.get_connection() as conn:
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

            # Таблица для настроек уведомлений
            conn.execute('''CREATE TABLE IF NOT EXISTS notification_settings (
                user_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                notify_time TEXT DEFAULT '09:00',
                notify_before_minutes INTEGER DEFAULT 60,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )''')

            # Индексы для быстрого поиска
            conn.execute('''CREATE INDEX IF NOT EXISTS idx_user_schedules 
                ON user_schedules(user_id, day)''')

            conn.execute('''CREATE INDEX IF NOT EXISTS idx_timezone 
                ON user_timezones(timezone)''')

    # ===== МЕТОДЫ ДЛЯ РАБОТЫ С ПОЛЬЗОВАТЕЛЯМИ =====

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

            # И настройки уведомлений по умолчанию
            conn.execute('''
                INSERT OR IGNORE INTO notification_settings (user_id)
                VALUES (?)
            ''', (user_id,))

    # ===== МЕТОДЫ ДЛЯ РАБОТЫ С ЧАСОВЫМИ ПОЯСАМИ =====

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

    # ===== МЕТОДЫ ДЛЯ РАБОТЫ С РАСПИСАНИЕМ =====

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
        """Добавить занятие в расписание пользователя"""
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
        """Удалить занятие из расписания пользователя"""
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

    # ===== МЕТОДЫ ДЛЯ НАСТРОЙКИ УВЕДОМЛЕНИЙ =====

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
                    'notify_time': row[1],
                    'notify_before_minutes': row[2]
                }
            return {'enabled': True, 'notify_time': '09:00', 'notify_before_minutes': 60}

    def update_notification_settings(self, user_id: int, **kwargs):
        """Обновить настройки уведомлений"""
        with self.get_connection() as conn:
            settings = self.get_notification_settings(user_id)
            settings.update(kwargs)

            conn.execute('''
                INSERT INTO notification_settings (user_id, enabled, notify_time, notify_before_minutes, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    notify_time = excluded.notify_time,
                    notify_before_minutes = excluded.notify_before_minutes,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, settings['enabled'], settings['notify_time'], settings['notify_before_minutes']))

    def get_all_users_with_notifications_enabled(self) -> List[int]:
        """Получить всех пользователей с включенными уведомлениями"""
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT user_id FROM notification_settings WHERE enabled = 1
            ''')
            return [row[0] for row in cursor.fetchall()]


# Создаем единый экземпляр БД
db = Database()


def days_keyboard(callback_prefix: str = 'view', user_id: Optional[int] = None) -> types.InlineKeyboardMarkup:
    """Клавиатура с днями недели"""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = []

    for day_key, day_name in DAYS.items():
        if user_id:
            callback = f"{callback_prefix}_{user_id}_{day_key}"
        else:
            callback = f"{callback_prefix}_{day_key}"
        buttons.append(types.InlineKeyboardButton(day_name, callback_data=callback))

    keyboard.add(*buttons)

    # Добавляем кнопку для всей недели только для просмотра
    if callback_prefix == 'view':
        if user_id:
            keyboard.row(types.InlineKeyboardButton(
                "📋 Вся неделя",
                callback_data=f"view_{user_id}_week"
            ))
        else:
            keyboard.row(types.InlineKeyboardButton(
                "📋 Вся неделя",
                callback_data="view_week"
            ))

    return keyboard


# ==================== ФУНКЦИИ УВЕДОМЛЕНИЙ ====================

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

            # Получаем настройки уведомлений
            settings = db.get_notification_settings(user_id)
            notify_time = settings['notify_time']

            # Проверяем, нужно ли отправлять сейчас
            current_time = now.strftime('%H:%M')
            if current_time != notify_time:
                continue

            today_en = now.strftime('%A')
            day_key = EN_TO_RU_DAY.get(today_en)

            if not day_key:
                continue

            lessons = db.get_user_schedule(user_id, day_key)

            if lessons:
                text = f"🌅 *Доброе утро!*\n\n📅 *{DAYS[day_key]}*\n\n📚 *Ваши занятия сегодня:*\n"
                for i, lesson in enumerate(lessons, 1):
                    text += f"{i}. {lesson}\n"

                # Добавляем кнопки для быстрых действий
                keyboard = types.InlineKeyboardMarkup()
                keyboard.row(
                    types.InlineKeyboardButton("📋 Посмотреть", callback_data=f"view_{user_id}_{day_key}"),
                    types.InlineKeyboardButton("✅ Отметить", callback_data=f"done_{user_id}_{day_key}")
                )

                send_notification(user_id, text, keyboard)
            else:
                text = f"🌅 *Доброе утро!*\n\n📅 *{DAYS[day_key]}*\n\n✨ Сегодня у вас нет запланированных занятий."
                send_notification(user_id, text)

        except Exception as e:
            logger.error(f"Error sending daily notification to user {user_id}: {e}")


def send_upcoming_lesson_reminders():
    """Отправить напоминания о предстоящих занятиях"""
    users = db.get_all_users_with_notifications_enabled()

    for user_id in users:
        try:
            tz_name = db.get_user_timezone(user_id)
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)

            # Получаем настройки
            settings = db.get_notification_settings(user_id)
            minutes_before = settings['notify_before_minutes']

            today_en = now.strftime('%A')
            day_key = EN_TO_RU_DAY.get(today_en)

            if not day_key:
                continue

            lessons = db.get_user_schedule(user_id, day_key)

            # Проверяем каждое занятие
            for lesson in lessons:
                # Пытаемся извлечь время из названия занятия
                import re
                time_match = re.search(r'(\d{1,2}):(\d{2})', lesson)

                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))

                    lesson_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    reminder_time = lesson_time - timedelta(minutes=minutes_before)

                    # Если сейчас время напоминания
                    if now.hour == reminder_time.hour and now.minute == reminder_time.minute:
                        text = f"⏰ *Напоминание!*\n\nЧерез {minutes_before} минут:\n📚 *{lesson}*"

                        keyboard = types.InlineKeyboardMarkup()
                        keyboard.add(types.InlineKeyboardButton(
                            "✅ Понятно",
                            callback_data=f"ack_{user_id}"
                        ))

                        send_notification(user_id, text, keyboard)

        except Exception as e:
            logger.error(f"Error sending reminder to user {user_id}: {e}")


def send_weekly_summary():
    """Отправить еженедельную сводку по воскресеньям"""
    users = db.get_all_users_with_notifications_enabled()

    for user_id in users:
        try:
            tz_name = db.get_user_timezone(user_id)
            tz = pytz.timezone(tz_name)
            now = datetime.now(tz)

            # Отправляем только в воскресенье вечером
            if now.strftime('%A') != 'Sunday' or now.hour != 20:
                continue

            schedule = db.get_user_schedule(user_id)

            text = "📊 *Итоги недели*\n\n"

            total_lessons = 0
            for day_key, lessons in schedule.items():
                total_lessons += len(lessons)

            text += f"📚 Всего занятий на неделе: *{total_lessons}*\n\n"

            if total_lessons > 0:
                text += "Распределение по дням:\n"
                for day_key, day_name in DAYS.items():
                    count = len(schedule.get(day_key, []))
                    if count > 0:
                        text += f"• {day_name}: *{count}*\n"

            text += "\nХорошей недели! 🌟"

            send_notification(user_id, text)

        except Exception as e:
            logger.error(f"Error sending weekly summary to user {user_id}: {e}")


def notification_worker():
    """Фоновый поток для проверки и отправки уведомлений"""
    logger.info("Notification worker started")

    # Планируем задачи
    schedule.every().minute.do(send_upcoming_lesson_reminders)
    schedule.every().minute.do(send_daily_schedule_notification)
    schedule.every().sunday.at("20:00").do(send_weekly_summary)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)  # Проверяем каждые 30 секунд
        except Exception as e:
            logger.error(f"Error in notification worker: {e}")
            time.sleep(60)


# ==================== КОМАНДЫ ДЛЯ УВЕДОМЛЕНИЙ ====================

@bot.message_handler(commands=['notifications'])
def cmd_notifications(message):
    """Настройка уведомлений"""
    user_id = message.from_user.id
    settings = db.get_notification_settings(user_id)

    status = "✅ Включены" if settings['enabled'] else "❌ Отключены"

    text = (
        "🔔 *Настройки уведомлений*\n\n"
        f"Статус: {status}\n"
        f"Время уведомления: {settings['notify_time']}\n"
        f"Напоминать за: {settings['notify_before_minutes']} минут\n\n"
        "Выберите действие:"
    )

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton(
            "🔛 Вкл/Выкл",
            callback_data=f"notif_toggle_{user_id}"
        ),
        types.InlineKeyboardButton(
            "⏰ Время",
            callback_data=f"notif_time_{user_id}"
        )
    )
    keyboard.row(
        types.InlineKeyboardButton(
            "⏱ Интервал",
            callback_data=f"notif_interval_{user_id}"
        ),
        types.InlineKeyboardButton(
            "📝 Тест",
            callback_data=f"notif_test_{user_id}"
        )
    )

    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)


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

    text = (
        "👋 *Привет! Я бот с личным расписанием*\n\n"
        "📝 *Команды:*\n"
        "/schedule - посмотреть своё расписание\n"
        "/today - расписание на сегодня\n"
        "/week - расписание на всю неделю\n"
        "/edit - редактировать своё расписание\n"
        "/settimezone - установить часовой пояс\n"
        "/mytimezone - показать текущий часовой пояс\n"
        "/notifications - настройка уведомлений\n"
        "/help - это сообщение"
    )
    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['schedule'])
def cmd_schedule(message):
    """Показать расписание пользователя"""
    user_id = message.from_user.id
    keyboard = days_keyboard('view', user_id)
    bot.send_message(message.chat.id, "Выберите день:", reply_markup=keyboard)


@bot.message_handler(commands=['settimezone'])
def cmd_set_timezone(message):
    """Установка часового пояса"""
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
        "🌍 *Выберите ваш часовой пояс:*\n\n"
        "От этого зависит время для команды /today",
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

        if not lessons:
            reply = f"📅 *{DAYS[day_key]}*\n\nМероприятий нет."
        else:
            reply = f"📅 *{DAYS[day_key]}*\n\n"
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

    text = "📆 *Ваше расписание на неделю:*\n\n"
    for day_key, day_name in DAYS.items():
        lessons = schedule.get(day_key, [])
        text += f"*{day_name}:*\n"
        if lessons:
            for lesson in lessons:
                text += f"  • {lesson}\n"
        else:
            text += "  _Нет мероприятий_\n"
        text += "\n"

    bot.send_message(message.chat.id, text, parse_mode='Markdown')


@bot.message_handler(commands=['edit'])
def cmd_edit(message):
    """Редактирование личного расписания"""
    user_id = message.from_user.id
    keyboard = days_keyboard('edit', user_id)
    bot.send_message(
        message.chat.id,
        "✏️ Выберите день для редактирования:",
        reply_markup=keyboard
    )


@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    """Обработка нажатий на кнопки"""
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    # Обработка уведомлений
    if data.startswith('notif_'):
        parts = data.split('_')
        action = parts[1]

        if action == 'toggle':
            settings = db.get_notification_settings(user_id)
            db.update_notification_settings(user_id, enabled=not settings['enabled'])
            status = "включены" if not settings['enabled'] else "отключены"
            bot.answer_callback_query(call.id, f"✅ Уведомления {status}")
            cmd_notifications(call.message)  # Обновляем сообщение

        elif action == 'time':
            markup = types.InlineKeyboardMarkup(row_width=3)
            times = ["08:00", "09:00", "10:00", "18:00", "19:00", "20:00"]
            for t in times:
                markup.add(types.InlineKeyboardButton(
                    t,
                    callback_data=f"notif_settime_{t}"
                ))
            markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="notif_cancel"))

            bot.edit_message_text(
                "⏰ Выберите время для ежедневных уведомлений:",
                chat_id,
                call.message.message_id,
                reply_markup=markup
            )

        elif action == 'settime':
            notify_time = parts[2]
            db.update_notification_settings(user_id, notify_time=notify_time)
            bot.answer_callback_query(call.id, f"✅ Время установлено: {notify_time}")
            cmd_notifications(call.message)

        elif action == 'interval':
            markup = types.InlineKeyboardMarkup(row_width=3)
            intervals = [15, 30, 60, 120, 180, 1440]
            for m in intervals:
                label = f"{m} мин" if m < 1440 else "24 часа"
                markup.add(types.InlineKeyboardButton(
                    label,
                    callback_data=f"notif_setinterval_{m}"
                ))
            markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="notif_cancel"))

            bot.edit_message_text(
                "⏱ За сколько минут напоминать о занятиях?",
                chat_id,
                call.message.message_id,
                reply_markup=markup
            )

        elif action == 'setinterval':
            minutes = int(parts[2])
            db.update_notification_settings(user_id, notify_before_minutes=minutes)
            bot.answer_callback_query(call.id, f"✅ Интервал установлен: {minutes} мин")
            cmd_notifications(call.message)

        elif action == 'test':
            send_notification(
                user_id,
                "🔔 *Тестовое уведомление*\n\nЕсли вы это видите, значит уведомления работают правильно!"
            )
            bot.answer_callback_query(call.id, "✅ Тестовое уведомление отправлено")

        elif action == 'cancel':
            cmd_notifications(call.message)

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

    # Обработка расписания
    if data.startswith(('view_', 'edit_', 'add_', 'remove_', 'del_', 'done_', 'ack_')):
        parts = data.split('_')
        action = parts[0]

        # Парсим user_id и day_key из callback
        if len(parts) >= 3 and parts[1].isdigit():
            callback_user_id = int(parts[1])
            if len(parts) >= 4 and parts[2] == 'week':
                day_key = 'week'
                idx = None
            else:
                day_key = parts[2]
                idx = int(parts[3]) if len(parts) > 3 and action in ['del', 'done'] else None
        else:
            callback_user_id = user_id
            day_key = parts[1]
            idx = int(parts[2]) if len(parts) > 2 and action in ['del', 'done'] else None

        # Проверка прав
        if callback_user_id != user_id:
            bot.answer_callback_query(call.id, "❌ Это не ваше расписание!", show_alert=True)
            return

        try:
            if action == 'view':
                if day_key == 'week':
                    # Показать всю неделю
                    schedule = db.get_user_schedule(user_id)
                    text = "📆 *Ваше расписание на неделю:*\n\n"
                    for d_key, d_name in DAYS.items():
                        lessons = schedule.get(d_key, [])
                        text += f"*{d_name}:*\n"
                        if lessons:
                            for lesson in lessons:
                                text += f"  • {lesson}\n"
                        else:
                            text += "  _Нет мероприятий_\n"
                        text += "\n"
                    bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
                else:
                    lessons = db.get_user_schedule(user_id, day_key)
                    if not lessons:
                        reply = f"📅 *{DAYS[day_key]}*\n\nМероприятий нет."
                    else:
                        reply = f"📅 *{DAYS[day_key]}*\n\n"
                        for i, lesson in enumerate(lessons, 1):
                            reply += f"{i}. {lesson}\n"
                    bot.edit_message_text(reply, chat_id, call.message.message_id, parse_mode='Markdown')
                bot.answer_callback_query(call.id)

            elif action == 'edit':
                lessons = db.get_user_schedule(user_id, day_key)
                text = f"*{DAYS[day_key]}*\n\n"
                if lessons:
                    for i, lesson in enumerate(lessons, 1):
                        text += f"{i}. {lesson}\n"
                else:
                    text += "Мероприятий нет.\n"
                text += "\nВыберите действие:"

                keyboard = types.InlineKeyboardMarkup()
                keyboard.row(
                    types.InlineKeyboardButton("➕ Добавить", callback_data=f"add_{user_id}_{day_key}"),
                    types.InlineKeyboardButton("❌ Удалить", callback_data=f"remove_{user_id}_{day_key}")
                )
                keyboard.row(types.InlineKeyboardButton("🔙 Назад", callback_data=f"view_{user_id}"))

                bot.edit_message_text(text, chat_id, call.message.message_id,
                                      parse_mode='Markdown', reply_markup=keyboard)
                bot.answer_callback_query(call.id)

            elif action == 'add':
                bot.send_message(chat_id,
                                 f"Введите новое занятие для дня *{DAYS[day_key]}*:",
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
                    bot.answer_callback_query(call.id, "❌ Нет занятий для удаления", show_alert=True)
                    return

                keyboard = types.InlineKeyboardMarkup()
                for idx, lesson in enumerate(lessons):
                    short = lesson if len(lesson) <= 25 else lesson[:22] + '...'
                    callback = f"del_{user_id}_{day_key}_{idx}"
                    keyboard.add(types.InlineKeyboardButton(short, callback_data=callback))

                keyboard.row(types.InlineKeyboardButton("❌ Отмена", callback_data=f"edit_{user_id}_{day_key}"))

                bot.edit_message_text(
                    f"Выберите занятие для удаления из *{DAYS[day_key]}*:",
                    chat_id,
                    call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )
                bot.answer_callback_query(call.id)

            elif action == 'del':
                if db.delete_lesson(user_id, day_key, idx):
                    bot.answer_callback_query(call.id, "✅ Занятие удалено")
                    lessons = db.get_user_schedule(user_id, day_key)
                    text = f"✅ Мероприятие удалено из *{DAYS[day_key]}*.\n\nТекущее расписание:\n"
                    if lessons:
                        for i, lesson in enumerate(lessons, 1):
                            text += f"{i}. {lesson}\n"
                    else:
                        text += "Мероприятий нет."
                    bot.edit_message_text(text, chat_id, call.message.message_id, parse_mode='Markdown')
                else:
                    bot.answer_callback_query(call.id, "❌ Ошибка при удалении", show_alert=True)

            elif action == 'done':
                bot.answer_callback_query(call.id, "✅ Отмечено!")
                bot.edit_message_text(
                    "✅ Хорошего дня!",
                    chat_id,
                    call.message.message_id
                )

            elif action == 'ack':
                bot.answer_callback_query(call.id, "✅ Принято!")

        except Exception as e:
            logger.error(f"Error in callback {data}: {e}")
            bot.answer_callback_query(call.id, "❌ Произошла ошибка", show_alert=True)

    elif data == "cancel":
        bot.edit_message_text("❌ Действие отменено.", chat_id, call.message.message_id)
        bot.answer_callback_query(call.id)


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
    """Обработка добавления занятия"""
    if not message.text:
        bot.send_message(message.chat.id, "❌ Пожалуйста, введите текст занятия")
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
    text = f"✅ Мероприятие добавлено в *{DAYS[day_key]}*.\n\nТекущее расписание:\n"
    for i, lesson in enumerate(lessons, 1):
        text += f"{i}. {lesson}\n"

    # Добавляем кнопки для дальнейших действий
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("➕ Ещё", callback_data=f"add_{user_id}_{day_key}"),
        types.InlineKeyboardButton("🔙 Назад", callback_data=f"view_{user_id}_{day_key}")
    )

    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=keyboard)


if __name__ == '__main__':
    logger.info("Бот запущен...")

    # Запускаем поток с уведомлениями
    notification_thread = threading.Thread(target=notification_worker, daemon=True)
    notification_thread.start()

    try:
        bot.infinity_polling()
    except Exception as e:
        logger.error(f"Бот остановлен с ошибкой: {e}")
