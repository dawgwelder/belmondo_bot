from numpy.random import normal, uniform
from time import sleep
from random import choice
from quotes import quotes
import string
from const import *
from typing import Tuple
from pprint import pprint


def sleep_choice(choices):
    sleep(choice(choices))


def quote_choice() -> str:
    return choice(quotes)


def clean_string(s: str = '') -> str:
    return s.translate(str.maketrans('', '', string.punctuation))


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


def check_id(uid: int, update_uid: int):
    return uid == update_uid


def roll_probability(percent: float = 0.5) -> bool:
    mu, sigma = 0.5, 0.15
    value = abs(normal(mu, sigma))
    return value >= percent


def answer_probability(spam_mode: str,):
    if spam_mode == "chaos":
        return uniform(0, 1) > 0
    elif spam_mode == "soft":
        return uniform(0, 1) > .5
    elif spam_mode == "medium":
        return uniform(0, 1) >= .75
    elif spam_mode == "rare":
        return uniform(0, .1) > .8
    return .75
