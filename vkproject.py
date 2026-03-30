import sqlite3
# bot.py
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import schedule
import threading
import time
from schedule_data import days_ru
from datetime import datetime, timedelta
import logging
logging.basicConfig(level=logging.INFO)


class Database:
    def __init__(self):
        self.conn = sqlite3.connect('users.db', check_same_thread=False)
        self.create_table()

    def create_table(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                group_name TEXT,
                notifications_enabled BOOLEAN DEFAULT 1
            )
        ''')
        self.conn.commit()


class VKScheduleBot:
    def __init__(self, token):
        self.token = token
        self.vk_session = vk_api.VkApi(token=token)
        self.longpoll = VkLongPoll(self.vk_session)
        self.vk = self.vk_session.get_api()

        # Словарь для хранения выбранной группы пользователя
        self.user_groups = {}

    def send_message(self, user_id, message, keyboard=None):
        """Отправка сообщения пользователю"""
        params = {
            'user_id': user_id,
            'message': message,
            'random_id': 0
        }
        if keyboard:
            params['keyboard'] = keyboard.get_keyboard()
        self.vk.messages.send(**params)

    def create_main_keyboard(self):
        """Создание основной клавиатуры"""
        keyboard = VkKeyboard(one_time=False)
        keyboard.add_button('📅 Расписание на сегодня', color=VkKeyboardColor.POSITIVE)
        keyboard.add_line()
        keyboard.add_button('📆 Расписание на завтра', color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
        keyboard.add_button('📋 Полное расписание', color=VkKeyboardColor.SECONDARY)
        keyboard.add_button('⚙️ Выбрать группу', color=VkKeyboardColor.SECONDARY)
        return keyboard

    def create_groups_keyboard(self):
        """Создание клавиатуры с группами"""
        keyboard = VkKeyboard(one_time=True)
        for group in schedule.keys():
            keyboard.add_button(group, color=VkKeyboardColor.PRIMARY)
            keyboard.add_line()
        keyboard.add_button('🔙 Назад', color=VkKeyboardColor.NEGATIVE)
        return keyboard

    def format_schedule(self, schedule_list, day_name):
        """Форматирование расписания для вывода"""
        if not schedule_list:
            return f"📚 **{day_name}**\nПар нет (выходной день)"

        result = f"📚 **{day_name}**\n"
        result += "═" * 30 + "\n"

        for i, lesson in enumerate(schedule_list, 1):
            result += f"**{i}. {lesson['time']}**\n"
            result += f"📖 {lesson['subject']}\n"
            result += f"👨‍🏫 {lesson['teacher']}\n"
            result += f"🏛 Ауд. {lesson['room']}\n"
            result += "─" * 25 + "\n"

        return result

    def get_day_schedule(self, group, day_offset=0):
        """Получение расписания на определенный день"""
        today = datetime.now()
        target_date = today + timedelta(days=day_offset)
        day_name = target_date.strftime('%A').lower()

        if day_name not in days_ru:
            return "Не удалось определить день недели"

        if group not in schedule:
            return "Группа не найдена"

        day_schedule = schedule[group].get(day_name, [])
        return self.format_schedule(day_schedule, days_ru[day_name])

    def get_full_week_schedule(self, group):
        """Получение полного расписания на неделю"""
        if group not in schedule:
            return "Группа не найдена"

        result = f"📅 **Полное расписание для группы {group}**\n"
        result += "═" * 35 + "\n\n"

        for day_name_eng, day_name_ru in days_ru.items():
            day_schedule = schedule[group].get(day_name_eng, [])
            if day_schedule:
                result += self.format_schedule(day_schedule, day_name_ru)
                result += "\n"

        return result

    def handle_message(self, event):
        """Обработка входящих сообщений"""
        user_id = event.user_id
        message = event.text.lower()

        # Главное меню
        if message == "начать" or message == "старт":
            self.user_groups[user_id] = None
            welcome_text = (
                "👋 **Привет! Я бот с расписанием.**\n\n"
                "Я помогу тебе узнать расписание занятий.\n"
                "Сначала выбери свою группу, нажав кнопку ниже 👇"
            )
            self.send_message(user_id, welcome_text, self.create_groups_keyboard())

        # Выбор группы
        elif message in [g.lower() for g in schedule.keys()]:
            # Находим оригинальное название группы
            for group in schedule.keys():
                if group.lower() == message:
                    self.user_groups[user_id] = group
                    self.send_message(
                        user_id,
                        f"✅ Группа **{group}** выбрана!\nТеперь ты можешь узнать расписание.",
                        self.create_main_keyboard()
                    )
                    break

        # Расписание на сегодня
        elif message == "📅 расписание на сегодня" or message == "сегодня":
            if user_id not in self.user_groups or not self.user_groups[user_id]:
                self.send_message(
                    user_id,
                    "⚠️ Сначала выбери группу!",
                    self.create_groups_keyboard()
                )
            else:
                schedule_text = self.get_day_schedule(self.user_groups[user_id], 0)
                self.send_message(user_id, schedule_text, self.create_main_keyboard())

        # Расписание на завтра
        elif message == "📆 расписание на завтра" or message == "завтра":
            if user_id not in self.user_groups or not self.user_groups[user_id]:
                self.send_message(
                    user_id,
                    "⚠️ Сначала выбери группу!",
                    self.create_groups_keyboard()
                )
            else:
                schedule_text = self.get_day_schedule(self.user_groups[user_id], 1)
                self.send_message(user_id, schedule_text, self.create_main_keyboard())

        # Полное расписание
        elif message == "📋 полное расписание" or message == "полное":
            if user_id not in self.user_groups or not self.user_groups[user_id]:
                self.send_message(
                    user_id,
                    "⚠️ Сначала выбери группу!",
                    self.create_groups_keyboard()
                )
            else:
                schedule_text = self.get_full_week_schedule(self.user_groups[user_id])
                self.send_message(user_id, schedule_text, self.create_main_keyboard())

        # Выбрать группу
        elif message == "⚙️ выбрать группу" or message == "выбрать группу":
            self.send_message(user_id, "Выбери свою группу:", self.create_groups_keyboard())

        # Назад
        elif message == "🔙 назад":
            self.send_message(user_id, "Главное меню:", self.create_main_keyboard())

        # Помощь
        elif message == "помощь":
            help_text = (
                "📌 **Доступные команды:**\n"
                "• 'сегодня' - расписание на сегодня\n"
                "• 'завтра' - расписание на завтра\n"
                "• 'полное' - полное расписание на неделю\n"
                "• 'выбрать группу' - сменить группу\n"
                "• 'помощь' - показать это сообщение"
            )
            self.send_message(user_id, help_text, self.create_main_keyboard())

        else:
            self.send_message(
                user_id,
                "❓ Неизвестная команда. Напиши 'помощь' для списка команд.",
                self.create_main_keyboard()
            )

    def send_daily_schedule(self):
        """Автоматическая отправка расписания (напоминание каждое утро)"""
        for user_id, group in self.user_groups.items():
            if group:
                schedule_text = self.get_day_schedule(group, 0)
                self.send_message(
                    user_id,
                    f"☀️ **Доброе утро!**\n\n{schedule_text}"
                )

    def run_schedule(self):
        """Запуск планировщика для автоматических напоминаний"""
        # Отправка расписания каждый день в 8:00
        schedule.every().day.at("08:00").do(self.send_daily_schedule)

        while True:
            schedule.run_pending()
            time.sleep(60)

    def run(self):
        """Запуск бота"""
        # Запускаем планировщик в отдельном потоке
        schedule_thread = threading.Thread(target=self.run_schedule)
        schedule_thread.daemon = True
        schedule_thread.start()

        print("✅ Бот запущен и готов к работе!")

        # Основной цикл обработки сообщений
        for event in self.longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                self.handle_message(event)


if __name__ == "__main__":
    # Вставьте сюда ваш токен
    TOKEN = "ВАШ_ТОКЕН_СООБЩЕСТВА"

    bot = VKScheduleBot(TOKEN)
    bot.run()
