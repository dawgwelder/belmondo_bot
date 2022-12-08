import re
from numpy.random import normal, uniform
from time import sleep
from random import choice
from quotes import quotes
import string
from const import *
from typing import Tuple
from pprint import pprint
import pandas as pd


def sleep_choice(choices):
    sleep(choice(choices))


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


def check_admin(uid: int, admins_list) -> bool:
    return uid in admins_list


def check_id(uid: int, update_uid: int) -> bool:
    return uid == update_uid


def roll_probability(percent: float = 0.5) -> bool:
    mu, sigma = 0.5, 0.15
    value = abs(normal(mu, sigma))
    return value >= percent


def answer_probability(spam_mode: str,) -> float:
    if spam_mode == "chaos":
        return uniform(0, 1) > 0
    elif spam_mode == "soft":
        return uniform(0, 1) > 0.5
    elif spam_mode == "medium":
        return uniform(0, 1) >= 0.75
    elif spam_mode == "rare":
        return uniform(0, 0.1) > 0.8
    return 0.75


def get_last_record(df, _id):
    if _id in df.id.values:
        return df.where(df.id == _id).max()
    else:
        return None


def parse_length(length):
    text = f"{length} см"
    meter = length // 100
    kilometer = length // 100000

    if kilometer:
        text = f"{kilometer} км {meter} м " + text
    elif meter:
        text = f" {meter} м " + text

    return text


def get_length(df, stats=False):
    plotina_length = df["overall_build"].sum()
    plotina = parse_length(plotina_length)

    if not stats:
        text = f"Бобер {first_name} сделал плотину выше на {random_number} см! " \
               f"Общая высота плотины {plotina}"
    else:
        active_length = df["overall_build"].max()
        active = df[df["overall_build"] == active_length].loc[0, "first_name"]
        active_length = parse_length(active_length)
        text = f"Общая высота плотины - {plotina}! \n" \
               f"Самый активный бобёр - {active}, он построил {active_length}."
    return text


from random import choice


def roll_custom_dice(text):
    regex_find = re.findall("кубик \d+", text)
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
