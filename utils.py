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


def check_is_in(msg: str, sentences: list) -> bool:
    for word in sentences:
        if word in msg:
            return True



def check_admin(uid: int, admins_list) -> bool:
    return uid in admins_list


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


def ifs(msg: str = None, _id: int = 0, spam_mode: str = "medium") -> Tuple[str, int]:
    text = None
    prob = 0
    keys_choice = ("josko", "pizdec", "duck", "no", "yes", "scene", "betrayal", "fuck_you",
                   "snail", "belmondo_hi", "davai", "a", "chicha", "pizdel", "balabama", "box")

    prob_dict = draw_probs(spam_mode, keys_choice)

    if check_is_in(msg, josko_conditions):
        text = choice(JOSKO)
        prob = roll_probability(prob_dict["josko"])
    elif check_is_in(msg, pizdec_ebok):
        text = choice(PIZDEC)
        prob = roll_probability(prob_dict["pizdec"])
    elif check_is_in(msg, oxxxy_list):
        print(oxxxy_rap)
        text = choice(oxxxy_rap)
        prob = True
    elif "жя" in msg:
        text = choice["Татьяна, иди нахрен.", "Нормы РУССКОГО языка, Татьяна!"]
        prob = True
    elif "кря" in msg:
        text = "Кря!"
        if _id == PENGUIN_ID:
            prob = True
        else:
            prob = roll_probability(prob_dict["duck"])
    elif "нет" == msg[:-3]:
        text = "Пидора ответ!"
        if msg == "нет":
            prob = roll_probability(prob_dict["no"])
        else:
            prob = roll_probability(.9)
    elif "да" == msg[:-2]:
        text = "Пизда!"
        if msg == "да":
            prob = roll_probability(prob_dict["yes"])
        else:
            prob = roll_probability(.911)
    elif check_is_in(msg, scene_msg):
        text = choice(movie_here)
        prob = True
    elif check_is_in(msg, betrayal):
        text = "в кинетическую спину потенциального друга!"
        prob = True
    elif "бельмор" in msg or msg == "бельморда" or msg == "бот лох" or msg == "бот лопух":
        text = choice(prob_dict["fuck_you"])
        prob = True
    elif "🐌" in msg:
        text = choice(snail)
        prob = True
    elif "улит" in msg:
        text = choice(snail)
        prob = roll_probability(prob_dict["snail"])
    elif check_is_in(msg, belmondo_hi):
        text = choice(profi_here)
        prob = True
    elif "ну давай" in msg:
        text = "аа ню давай!"
        prob = roll_probability(.5)
    elif "а?" == msg or "а" == msg:
        text = "A?"
        prob = roll_probability(.51488)
    elif "чича" in msg:
        text = "Лучший!"
        prob = roll_probability(prob_dict["chicha"])
    elif "пиздеть" in msg or "пиздел" in msg:
        text = "кто ПИЗДЕЛ?!"
        prob = roll_probability(prob_dict["pizdel"])
    elif "индидей" in msg:
        text = "Индидейка, чувак. Индидейка!"
        prob = roll_probability(.35)
    elif "индедей" in msg:
        text = "Индедейка, чувак. Индедейка!"
        prob = roll_probability(.35)
    elif check_is_in(msg, balabama):
        text = choice(balabama_here)
        prob = roll_probability(prob_dict["balabama"])
    elif "боксер" in msg:
        text = "он боксёр"
        prob = roll_probability(prob_dict["box"])
    return text, prob
