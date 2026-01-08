import requests
from bs4 import BeautifulSoup
from string import Template
from babel.dates import format_date
from datetime import datetime

mail = Template("https://horo.mail.ru/prediction/$horo/today/")
rambler = Template("https://horoscopes.rambler.ru/$horo/")

horo_list = [
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces",
]

horo_ru_list = [
    "Овен",
    "Телец",
    "Близнецы",
    "Рак",
    "Лев",
    "Дева",
    "Весы",
    "Скорпион",
    "Стрелец",
    "Козерог",
    "Водолей",
    "Рыбы",
]

horo_emojis = ["♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓", "⛎"]


def generate_tarot_prompt(result):
    """
    Генерирует промпт для гадания на таро на основе выпавших карт.
    
    Args:
        result: Список словарей с информацией о картах, каждый содержит:
            - "card": название карты
            - "orientation": "прямая" или "перевернутая"
            - "meaning": значение карты
            - "time": "прошлое", "настоящее" или "будущее"
    
    Returns:
        str: Промпт для генерации гадания на таро
    """
    prompt = "Создай гадание на таро на основе выпавших карт.\n\n"
    prompt += "**Выпавшие карты:**\n\n"
    
    for card_info in result:
        time_period = card_info["time"]
        card_name = card_info["card"]
        orientation = card_info["orientation"]
        meaning = card_info["meaning"]
        
        prompt += f"**{time_period.capitalize()}:**\n"
        prompt += f"- Карта: {card_name}\n"
        prompt += f"- Положение: {orientation}\n"
        prompt += f"- Значение: {meaning}\n\n"
    
    prompt += "**Требования к гаданию:**\n"
    prompt += "- Создай связное, целостное гадание, объединяющее все три карты в единую историю\n"
    prompt += "- Объясни, как карты связаны между собой и как они влияют друг на друга\n"
    prompt += "- Для карты прошлого - расскажи о том, что было и как это влияет на настоящее\n"
    prompt += "- Для карты настоящего - опиши текущую ситуацию и её особенности\n"
    prompt += "- Для карты будущего - дай предсказание, основанное на значении карты и связи с предыдущими картами\n"
    prompt += "- Используй мистический, но понятный язык\n"
    prompt += "- Сделай гадание личным и значимым\n"
    prompt += "- Заверши общим выводом и советом\n"
    prompt += "- Используй форматирование Markdown для выделения важных моментов\n"
    prompt += "- Не используй HTML теги\n"
    prompt += "- Длина гадания должна быть достаточной для полноценного расклада (примерно 300-500 слов)\n"
    
    return prompt


def get_ai_horoscope_prompt(history=None):
    """
    Генерирует промпт для создания гороскопа с учетом истории предыдущих гороскопов.
    
    Args:
        history: Список предыдущих гороскопов для предотвращения повторений
    
    Returns:
        str: Промпт для генерации гороскопа
    """
    dt = datetime.now().date().strftime("%d.%m.%Y")
    prompt = f"Построй гороскоп для всех знаков зодиака на {dt}"
    horo_text = get_horoscopes()
    
    # Добавляем инструкции по предотвращению повторений
    prompt += "\n\nВАЖНЫЕ ТРЕБОВАНИЯ:"
    prompt += "\n- Не повторяй информацию из своих предыдущих гороскопов"
    prompt += "\n- Используй разные формулировки и подходы"
    prompt += "\n- Создавай уникальный контент для каждого знака зодиака"
    prompt += "\n- Избегай шаблонных фраз и клише"
    prompt += "\n- Не используй таблицы для форматирования текста"
    prompt += "\n- Не используй HTML теги"
    prompt += "\n- Можешь использовать жирный текст для выделения важных слов в формате Markdown"
    prompt += "\n- Гороскопы на сегодня на других сайтах, используй эту информацию:"
    prompt += "\n" + horo_text
    
    # Если есть история, добавляем конкретные инструкции
    if history and len(history) > 0:
        prompt += f"\n\nУ тебя есть история из {len(history)} предыдущих гороскопов."
        prompt += "\nОбязательно создавай НОВЫЙ контент, отличающийся от предыдущих версий."
        
        # Добавляем краткое описание предыдущих гороскопов для контекста
        if len(history) >= 1:
            prompt += f"\n\nПоследний гороскоп содержал информацию о: {_extract_keywords(history[-1])}"
        # if len(history) >= 2:
        #     prompt += f"\nПредыдущий гороскоп содержал информацию о: {_extract_keywords(history[-2])}"
            
        prompt += "\n\nСоздай совершенно новый гороскоп с другими темами и подходами!"
    
    return prompt


def _extract_keywords(text, max_keywords=5):
    """
    Извлекает ключевые слова из текста гороскопа для анализа повторений.
    
    Args:
        text: Текст гороскопа
        max_keywords: Максимальное количество ключевых слов
    
    Returns:
        str: Ключевые слова через запятую
    """
    # Простое извлечение ключевых слов (можно улучшить)
    words = text.lower().split()
    # Фильтруем короткие слова и стоп-слова
    stop_words = {'и', 'в', 'на', 'с', 'по', 'для', 'от', 'до', 'за', 'из', 'к', 'о', 'у', 'а', 'но', 'да', 'нет', 'это', 'то', 'что', 'как', 'где', 'когда', 'почему', 'который', 'которая', 'которое', 'которые'}
    keywords = [word for word in words if len(word) > 3 and word not in stop_words]
    
    # Берем уникальные слова
    unique_keywords = list(dict.fromkeys(keywords))[:max_keywords]
    return ', '.join(unique_keywords) if unique_keywords else "общие темы"


def get_horoscope_mail(horo):
    soup = BeautifulSoup(requests.get(mail.substitute(horo=horo)).text, features="lxml")
    text = "\n".join(p.text for p in soup.find_all("p"))
    return text


def get_horoscope_rambler(horo):
    soup = BeautifulSoup(requests.get(rambler.substitute(horo=horo)).text, features="lxml")
    text = "\n".join(p.text for p in soup.find_all("p"))
    return text


def get_horoscopes():
    horo_text = ""
    for horo in horo_list:
        horo_text += horo + "\n"
        horo_text += get_horoscope_mail(horo)
        # horo_text += "\n\n" + get_horoscope_rambler(horo) + "\n\n"
    return horo_text


def get_horoscope(horo):
    soup = BeautifulSoup(requests.get(mail.substitute(horo=horo)).text, features="lxml")
    text = soup.find_all("p")[0].text
    return text


def generate_horo_message(horo):
    ru_horo = dict(zip(horo_list, horo_ru_list))[horo]
    emoji = dict(zip(horo_list, horo_emojis))[horo]
    dt = datetime.now().date()
    dt = format_date(dt, locale="ru", format="full").capitalize()

    horo_text = get_horoscope(horo)
    message = f"{dt}\n\n{emoji}{ru_horo}:\n{horo_text}"
    return message


def generate_post():
    dt = datetime.now().date()
    dt = format_date(dt, locale="ru", format="full").capitalize()
    first_post = f"{dt}\n\n"
    second_post = ""

    for idx, (horo, ru_horo, emoji) in enumerate(
        zip(horo_list, horo_ru_list, horo_emojis)
    ):
        horo_text = get_horoscope(horo)
        if idx < 5:
            first_post = f"{first_post}{emoji}{ru_horo}:\n{horo_text}\n\n"
        else:
            second_post = f"{second_post}{emoji}{ru_horo}:\n{horo_text}\n\n"
    return first_post.strip(), second_post.strip()

