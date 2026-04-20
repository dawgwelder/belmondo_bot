import requests
from bs4 import BeautifulSoup
from string import Template
from babel.dates import format_date
from datetime import datetime

mail = Template("https://horo.mail.ru/prediction/$horo/today/")
rambler = Template("https://horoscopes.rambler.ru/$horo/")

# (connect timeout, read timeout) for requests.get
_HTTP_TIMEOUT = (5, 20)

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
    
    prompt += "Требования к гаданию:\n"
    prompt += "- Создай связное, целостное гадание, объединяющее все три карты в единую историю\n"
    prompt += "- Объясни, как карты связаны между собой и как они влияют друг на друга\n"
    prompt += "- Для карты прошлого - расскажи о том, что было и как это влияет на настоящее\n"
    prompt += "- Для карты настоящего - опиши текущую ситуацию и её особенности\n"
    prompt += "- Для карты будущего - дай предсказание, основанное на значении карты и связи с предыдущими картами\n"
    prompt += "- Используй мистический, но понятный язык\n"
    prompt += "- Сделай гадание личным и значимым\n"
    prompt += "- Не используй HTML-разметку и markdown-разметку\n"
    prompt += "- Пиши обычным текстом\n"
    prompt += "- Длина гадания должна быть достаточной для полноценного расклада (примерно 300-500 слов)\n"
    
    return prompt


def _cdata_escape(text: str) -> str:
    """Экранирует последовательность ]]> внутри XML CDATA."""
    return text.replace("]]>", "]]]]><![CDATA[>")


def build_reference_horoscopes_xml() -> str:
    """Эталонные тексты по знакам: пары sign / horoscope в CDATA."""
    parts = []
    for horo in horo_list:
        raw = get_horoscope_mail(horo)
        safe = _cdata_escape(raw)
        parts.append(f"<sign>{horo}</sign>\n<horoscope><![CDATA[{safe}]]></horoscope>")
    return "\n".join(parts)


def build_ai_horoscope_user_message(history=None):
    """
    Минимальный user-промпт: дата, эталонные гороскопы в XML, при необходимости ключевые слова прошлого ответа.
    Полные правила заданы в professional_prompt_ai_horoscope (system).
    """
    dt = datetime.now().date().strftime("%d.%m.%Y")
    chunks = [f"<date>{dt}</date>", build_reference_horoscopes_xml()]
    if history and len(history) >= 1:
        kw = _extract_keywords(history[-1])
        kw_safe = _cdata_escape(kw)
        chunks.append(f"<previous_keywords><![CDATA[{kw_safe}]]></previous_keywords>")
    return "\n".join(chunks)


def get_ai_horoscope_prompt(history=None):
    """Совместимость: то же, что build_ai_horoscope_user_message."""
    return build_ai_horoscope_user_message(history)


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
    try:
        r = requests.get(mail.substitute(horo=horo), timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, features="lxml")
        text = "\n".join(p.text for p in soup.find_all("p"))
        return text
    except requests.RequestException:
        return f"(не удалось загрузить эталон для {horo})"


def get_horoscope_rambler(horo):
    soup = BeautifulSoup(requests.get(rambler.substitute(horo=horo), timeout=_HTTP_TIMEOUT).text, features="lxml")
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
    try:
        r = requests.get(mail.substitute(horo=horo), timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, features="lxml")
        paras = soup.find_all("p")
        if not paras:
            return "Текст гороскопа временно недоступен."
        return paras[0].text
    except requests.RequestException:
        return "Не удалось загрузить гороскоп. Попробуйте позже."


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

