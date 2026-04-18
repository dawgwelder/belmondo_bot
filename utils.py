import re
from datetime import timedelta
from numpy.random import normal, uniform
from time import sleep
from random import choice
from quotes import quotes
import string
import asyncio
import threading


def sleep_choice(choices):
    sleep(choice(choices))


def sleep_choice_async(choices):
    """Асинхронная версия sleep_choice, которая не блокирует основной поток"""
    def delayed_action():
        sleep(choice(choices))
    
    thread = threading.Thread(target=delayed_action)
    thread.daemon = True
    thread.start()


async def sleep_choice_asyncio(choices, bot=None, chat_id=None, message_id=None, logger=None):
    """
    Асинхронная версия sleep_choice с автоматическим удалением сообщения
    
    Args:
        choices: список/кортеж значений для случайного выбора задержки
        bot: объект бота для удаления сообщения
        chat_id: ID чата
        message_id: ID сообщения для удаления
        logger: объект логгера для записи
    """
    delay = choice(choices)
    
    if bot and chat_id and message_id:
        # Если переданы параметры для удаления, ждем и удаляем
        await asyncio.sleep(delay)
        try:
            await bot.delete_message(chat_id, message_id)
            if logger:
                logger.info(f"Сообщение удалено после задержки {delay} секунд")
        except Exception as e:
            if logger:
                logger.error(f"Ошибка при удалении сообщения: {e}")
    else:
        # Если параметры не переданы, просто ждем
        await asyncio.sleep(delay)


def quote_choice() -> str:
    return choice(quotes)


def clean_string(s: str = "") -> str:
    return s.translate(str.maketrans("", "", string.punctuation))


def check_is_in(msg: str, sentences: list, exact: bool = False) -> bool:
    for word in sentences:
        if not exact:
            if word in msg:
                return True
        else:
            if word == msg:
                return True
    return False


def check_admin(uid: int, admins_list) -> bool:
    return uid in admins_list


def check_id(uid: int, update_uid: int) -> bool:
    return uid == update_uid


def roll_probability(percent: float = 0.5) -> bool:
    mu, sigma = 0.5, 0.15
    value = abs(normal(mu, sigma))
    return value >= percent


def answer_probability(
    spam_mode: str,
) -> float:
    if spam_mode == "chaos":
        return uniform(0, 1) > 0
    elif spam_mode == "soft":
        return uniform(0, 1) > 0.5
    elif spam_mode == "medium":
        return uniform(0, 1) >= 0.75
    elif spam_mode == "rare":
        return uniform(0, 1) >= 0.95
    return 0.75


def td_convert(td):
    def dummy_converter(number, first, interval, others):
        formatted = ""
        if number:
            end = number % 10
            if 5 <= number <= 20:
                formatted = f"{number} {others} "
            elif end == 1:
                formatted = f"{number} {first} "
            elif 1 < end < 5:
                formatted = f"{number} {interval} "
            else:
                formatted = f"{number} {others} "
        return formatted

    def minutes_convert(minutes):
        formatted = ""
        if minutes:
            if 5 <= minutes <= 20:
                formatted = f"{minutes} минут"
            elif minutes % 10 == 1:
                formatted = f"{minutes} минута"
            elif 2 <= minutes % 10 < 5:
                formatted = f"{minutes} минуты"
            else:
                formatted = f"{minutes} минут"

        return formatted

    days = td.days
    hours, minutes, seconds = [
        int(v) for v in str(timedelta(seconds=td.seconds)).split(":")
    ]
    microseconds = td.microseconds

    return f"{dummy_converter(days, 'день', 'дня', 'дней')} {dummy_converter(hours, 'час', 'часа', 'часов')} {minutes_convert(minutes)} {dummy_converter(seconds, 'секунда', 'секунды', 'секунд')} {dummy_converter(microseconds, 'микросекунда', 'микросекунды', 'микросекунд')}".rstrip()


def roll_custom_dice(text):
    regex_find = re.findall(r"кубик \d+", text)
    if regex_find:
        number = int(regex_find[0].split()[-1])
        if 1 < number:
            if number == 6:
                return "default"
            else:
                chosen_number = choice(range(1, number + 1))
                sentence = f"Я кинул {number}-гранный кубик.\nВыпало {chosen_number}"
                return sentence
    return None
