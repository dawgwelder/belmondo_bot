import requests
from bs4 import BeautifulSoup
import json
from collections import OrderedDict

anecdote_site = "https://baneks.ru/random"


def get_anecdote():
    soup = BeautifulSoup(requests.get(anecdote_site).text, features="lxml")
    text = soup.find(attrs={"name": "description"})["content"]
    return text


def get_holidays(dt):
    with open("holidays.json") as f:
        holidays = json.load(f)
    site = holidays["site"]
    holiday = holidays.get(dt.strftime("%m-%d"), "Чёт нет ничего по праздникам... Скучнярский день")

    if isinstance(holiday, list):
        holidays = list(OrderedDict.fromkeys(holiday))
        holiday = "\n".join(holidays)
    
    return f"""
    Сегодня {dt.strftime('%d %B %Y')}\n
Список праздников:\n{holiday}\n
---\n
Взято с пидорского сайта {site}, где исключительное авторское право и бла-бла-бла
    """