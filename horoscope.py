import requests
from bs4 import BeautifulSoup
from string import Template
from babel.dates import format_date
from datetime import date, datetime

site = Template("https://horoscopes.rambler.ru/$horo/")

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


def get_horoscope(horo):
    soup = BeautifulSoup(requests.get(site.substitute(horo=horo)).text, features="lxml")
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
