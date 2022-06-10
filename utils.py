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
    return abs(normal(mu, sigma)) >= percent


def draw_probs(spam_mode: str,
               keys: tuple):
    if spam_mode == "chaos":
        prob_dict = {key: uniform(0, 1) for key in keys}
    elif spam_mode == "soft":
        prob_dict = {key: uniform(0, .5) for key in keys}
    elif spam_mode == "medium":
        prob_dict = {key: uniform(.5, .8) for key in keys}
    elif spam_mode == "rare":
        prob_dict = {key: uniform(.8, 1) for key in keys}
    else:
        prob_dict = {key: .75 for key in keys}
    return prob_dict
