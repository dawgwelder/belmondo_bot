import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from string import Template

import aiohttp
from babel.dates import format_date
from bs4 import BeautifulSoup

mail = Template("https://horo.mail.ru/prediction/$horo/today/")
rambler = Template("https://horoscopes.rambler.ru/$horo/")

_HTTP_TIMEOUT = aiohttp.ClientTimeout(connect=5, sock_read=20, total=25)
_HTTP_CONCURRENCY = 4

_http_client = None
_mail_cache_date = None
_mail_cache = {}
_mail_cache_lock = asyncio.Lock()

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
    prompt += "- Используй мистический, но понятный язык\n"
    prompt += "- Сделай гадание личным и значимым\n"
    prompt += "- Не используй HTML-разметку и markdown-разметку\n"
    prompt += "- Пиши обычным текстом\n"
    prompt += "- Длина каждого раздела — несколько предложений (в сумме примерно 300-500 слов)\n"
    prompt += "\nСтрого соблюдай структуру ответа (заголовки разделов — отдельной строкой, без markdown):\n"
    prompt += "1) Короткое вступление в 1–2 предложения (без заголовка)\n"
    prompt += "2) Строка «Прошлое» — затем текст о прошлом и влиянии на настоящее\n"
    prompt += "3) Строка «Настоящее» — затем текст о текущей ситуации\n"
    prompt += "4) Строка «Будущее» — затем предсказание\n"
    prompt += "5) Строка «Итог» — затем общий вывод по раскладу\n"
    prompt += "6) Строка «Совет» — затем практический совет\n"
    prompt += "7) В самом конце отдельным абзацем короткая французская фраза "
    prompt += "с переводом в скобках, например: Courage, mon ami (Смелее, друг мой).\n"
    
    return prompt


def _cdata_escape(text: str) -> str:
    """Экранирует последовательность ]]> внутри XML CDATA."""
    return text.replace("]]>", "]]]]><![CDATA[>")


@dataclass(frozen=True)
class HoroscopePage:
    full_text: str
    first_paragraph: str


def _get_http_client() -> aiohttp.ClientSession:
    global _http_client

    if _http_client is None or _http_client.closed:
        connector = aiohttp.TCPConnector(limit=_HTTP_CONCURRENCY)
        _http_client = aiohttp.ClientSession(
            connector=connector,
            timeout=_HTTP_TIMEOUT,
        )
    return _http_client


async def close_horoscope_http_client() -> None:
    """Close the shared HTTP client during application shutdown."""
    global _http_client

    if _http_client is not None and not _http_client.closed:
        await _http_client.close()
    _http_client = None


async def _fetch_page(url: str) -> HoroscopePage:
    async with _get_http_client().get(url) as response:
        response.raise_for_status()
        html = await response.text()

    paragraphs = [p.text for p in BeautifulSoup(html, features="lxml").find_all("p")]
    if not paragraphs:
        return HoroscopePage("", "")
    return HoroscopePage("\n".join(paragraphs), paragraphs[0])


async def _fetch_mail_page(horo: str) -> HoroscopePage:
    return await _fetch_page(mail.substitute(horo=horo))


async def _get_mail_pages(signs) -> dict[str, HoroscopePage]:
    """Return cached Mail.ru pages, fetching missing signs concurrently."""
    global _mail_cache_date, _mail_cache

    today = date.today()
    requested_signs = tuple(dict.fromkeys(signs))

    async with _mail_cache_lock:
        if _mail_cache_date != today:
            _mail_cache_date = today
            _mail_cache = {}

        missing_signs = [sign for sign in requested_signs if sign not in _mail_cache]
        semaphore = asyncio.Semaphore(_HTTP_CONCURRENCY)

        async def fetch_limited(sign):
            async with semaphore:
                try:
                    return sign, await _fetch_mail_page(sign)
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    return sign, None

        if missing_signs:
            results = await asyncio.gather(
                *(fetch_limited(sign) for sign in missing_signs)
            )
            _mail_cache.update(
                (sign, page) for sign, page in results if page is not None
            )

        return {
            sign: _mail_cache[sign]
            for sign in requested_signs
            if sign in _mail_cache
        }


async def build_reference_horoscopes_xml() -> str:
    """Эталонные тексты по знакам: пары sign / horoscope в CDATA."""
    pages = await _get_mail_pages(horo_list)
    parts = []
    for horo in horo_list:
        page = pages.get(horo)
        raw = page.full_text if page else f"(не удалось загрузить эталон для {horo})"
        safe = _cdata_escape(raw)
        parts.append(f"<sign>{horo}</sign>\n<horoscope><![CDATA[{safe}]]></horoscope>")
    return "\n".join(parts)


async def build_ai_horoscope_user_message(history=None):
    """
    Минимальный user-промпт: дата, эталонные гороскопы в XML, при необходимости ключевые слова прошлого ответа.
    Полные правила заданы в professional_prompt_ai_horoscope (system).
    """
    dt = datetime.now().date().strftime("%d.%m.%Y")
    chunks = [f"<date>{dt}</date>", await build_reference_horoscopes_xml()]
    if history and len(history) >= 1:
        kw = _extract_keywords(history[-1])
        kw_safe = _cdata_escape(kw)
        chunks.append(f"<previous_keywords><![CDATA[{kw_safe}]]></previous_keywords>")
    return "\n".join(chunks)


async def get_ai_horoscope_prompt(history=None):
    """Совместимость: то же, что build_ai_horoscope_user_message."""
    return await build_ai_horoscope_user_message(history)


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


async def get_horoscope_mail(horo):
    pages = await _get_mail_pages([horo])
    page = pages.get(horo)
    return page.full_text if page else f"(не удалось загрузить эталон для {horo})"


async def get_horoscope_rambler(horo):
    try:
        page = await _fetch_page(rambler.substitute(horo=horo))
        return page.full_text
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return f"(не удалось загрузить эталон для {horo})"


async def get_horoscopes():
    pages = await _get_mail_pages(horo_list)
    chunks = []
    for horo in horo_list:
        page = pages.get(horo)
        text = page.full_text if page else f"(не удалось загрузить эталон для {horo})"
        chunks.append(f"{horo}\n{text}")
    return "\n".join(chunks)


async def get_horoscope(horo):
    pages = await _get_mail_pages([horo])
    page = pages.get(horo)
    if page is None:
        return "Не удалось загрузить гороскоп. Попробуйте позже."
    return page.first_paragraph or "Текст гороскопа временно недоступен."


async def generate_horo_message(horo):
    ru_horo = dict(zip(horo_list, horo_ru_list))[horo]
    emoji = dict(zip(horo_list, horo_emojis))[horo]
    dt = datetime.now().date()
    dt = format_date(dt, locale="ru", format="full").capitalize()

    horo_text = await get_horoscope(horo)
    message = f"{dt}\n\n{emoji}{ru_horo}:\n{horo_text}"
    return message


async def generate_post():
    dt = datetime.now().date()
    dt = format_date(dt, locale="ru", format="full").capitalize()
    first_post = f"{dt}\n\n"
    second_post = ""

    pages = await _get_mail_pages(horo_list)
    for idx, (horo, ru_horo, emoji) in enumerate(
        zip(horo_list, horo_ru_list, horo_emojis)
    ):
        page = pages.get(horo)
        if page is None:
            horo_text = "Не удалось загрузить гороскоп. Попробуйте позже."
        else:
            horo_text = page.first_paragraph or "Текст гороскопа временно недоступен."
        if idx < 5:
            first_post = f"{first_post}{emoji}{ru_horo}:\n{horo_text}\n\n"
        else:
            second_post = f"{second_post}{emoji}{ru_horo}:\n{horo_text}\n\n"
    return first_post.strip(), second_post.strip()
