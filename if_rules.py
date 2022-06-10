from utils import *
from const import *


def ifs(msg: str = None, _id: int = 0, spam_mode: str = "medium") -> Tuple[str, int]:
    def _none_sum(text, new_text):
        textes = [text, new_text]
        if text is None:
            return new_text
        elif new_text is None:
            return text
        else:
            first = choice(textes)
            _ = textes.pop(first)
            second = textes.pop()
        return f"{first} {second}"

    #  test func for less code -> test in dev first!
    def _if(msg: str,
            words: list,
            answers: list,
            trigger_type: str,
            text: str,
            prob: float,
            prob_dict: dict,
            use_prob: float = 0,
            excepts_uids: tuple = (),
            update_uid: int = 0,
            use_end: int = 0,
            exact: bool = False):
        put_answer = check_is_in(msg, words, exact=exact)

        if put_answer:
            text = choice(answers)
            if use_prob:
                prob = use_prob
            elif excepts_uids:
                prob = check_is_in(update_uid, excepts_uids)  # wrong usage but who cares?
            elif use_end:
                if msg == msg[:-use_end]:
                    prob = roll_probabilty(.9)
            else:
                prob = roll_probability((prob_dict[trigger_type]))
            # if not text:
            #     text, prob = _text, _prob
            # else:
            #     choice_dict = {text: prob, _text: _prob}
            #     key = choice(choice_dict.keys())
            #     text, prob = key, choice_dict[key]
        return text, prob

    text = ""
    prob = 0
    keys_choice = ("josko", "pizdec", "duck", "no", "yes",
                   "scene", "betrayal", "fuck_you",
                   "snail", "profi", "davai", "a", "chicha", "brat",
                   "pizdel", "balabama", "box",
                   "house_woman", "###", "trap")

    prob_dict = draw_probs(spam_mode, keys_choice)
    # test at dev first

    text, prob = _if(msg=msg, words=josko_conditions, answers=JOSKO,
                     trigger_type="josko", text=text, prob=prob, prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=pizdec_ebok, answers=PIZDEC,
                     trigger_type="pizdec", text=text, prob=prob, prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=oxxxy_list, answers=oxxxy_rap,
                     trigger_type="oxxxy", text=text, prob=prob,
                     prob_dict=prob_dict, use_prob=1)

    text, prob = _if(msg=msg, words=["жя"], answers=["Татьяна, иди учись.",
                                                     "Нормы РУССКОГО языка, Татьяна!"],
                     trigger_type="tosya", text=text,
                     prob=prob, prob_dict=prob_dict, use_prob=1)
    
    text, prob = _if(msg=msg, words=["кря"], answers=["Кря!"],
                     trigger_type="duck", text=text, prob=prob,
                     prob_dict=prob_dict,
                     excepts_uids=[PENGUIN_ID], update_uid=_id)

    text, prob = _if(msg=msg, words=["нет"], answers=["Пидора ответ!"],
                     trigger_type="no", text=text, prob=prob,
                     prob_dict=prob_dict, use_end=3)

    text, prob = _if(msg=msg, words=["да"], answers=["Пизда!"],
                     trigger_type="yes", text=text, prob=prob,
                     prob_dict=prob_dict, use_end=2)

    text, prob = _if(msg=msg, words=scene_msg, answers=movie_here,
                     trigger_type="scene", text=text, prob=prob,
                     prob_dict=prob_dict, use_prob=1
                     )

    text, prob = _if(msg=msg, words=betrayal, answers=["Вот так вот ты в спину",
                                                       "вот так вот ты в кинетическую спину потенциального друга!",
                                                       "вот так вот ты в за спиной у потенциольного друга"],
                     trigger_type="betrayal", text=text, prob=prob,
                     prob_dict=prob_dict, use_prob=1
                     )

    text, prob = _if(msg=msg, words=["бельмор", "бот лох", "бот лопух"], answers=fuck_you,
                     trigger_type="fuck_you", text=text, prob=prob,
                     prob_dict=prob_dict, use_prob=1
                     )

    text, prob = _if(msg=msg, words=["🐌"], answers=snail,
                     trigger_type="snail", text=text, prob=prob,
                     prob_dict=prob_dict, use_prob=1)
    
    text, prob = _if(msg=msg, words=["улит"], answers=snail,
                     trigger_type="snail", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=belmondo_hi, answers=profi_here,
                     trigger_type="profii", text=text, prob=prob,
                     prob_dict=prob_dict, use_prob=1)
    text, prob = _if(msg=msg, words=["ну давай"], answers=["аа ню давай!"],
                     trigger_type="davai", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["а?", "a", "a?", "а"], answers=["аа ню давай!"],
                     trigger_type="davai", text=text, prob=prob,
                     prob_dict=prob_dict, exact=True)

    text, prob = _if(msg=msg, words=["чича"], answers=["Лучший!", "Топ", "Чиченвагенс"],
                     trigger_type="chicha", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["пиздеть", "пиздел"], answers=["кто ПИЗДЕЛ?!", "Кто пиздел?",
                                                                    "Это точно был не я."],
                     trigger_type="pizdel", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["индидей", "индедей"], answers=["Индидейка, чувак. Индидейка!",
                                                                     "Индедейка, чувак. Индедейка!",
                                                                     "Да какая нахуй индедейка, чувак. Я профессионал."],
                     trigger_type="indi", text=text, prob=prob,
                     use_prob=.35,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["любишь медок"], answers=["люби и холодок"],
                     trigger_type="brat", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=balabama, answers=balabama_here,
                    trigger_type="balabama", text=text, prob=prob,
                    prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["боксер"], answers=["он боксёр",
                                                         "Он - боксер, а я - Профессионал.",
                                                         "Хуй с ним с этим муай блять таем, газ поможет потушить лопуха"
                                                         "Боксер-то он боксер, а шорты обосраны."
                                                         "Опять эта ебатория?"],
                     trigger_type="box", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["как говорится", "починил парашу"], answers=["как говорится - починил парашу, "
                                                                                  "и выеби хозяйку",
                                                                                  "починил парашу, и выеби хозяйку"],
                     trigger_type="house_woman", text=text, prob=prob,
                     prob_dict=prob_dict)

    text, prob = _if(msg=msg, words=["скажи слово прикольное"], answers=["пиздола",
                                                                         "пиздопроебище хуеплетское"],
                     trigger_type="###", text=text, prob=prob,
                     prob_dict=prob_dict)
    text, prob = _if(msg=msg, words=["бельмондо траншея"], answers=["И тут ловушка для Арбуза!",
                                                                    "Выкопал, профессионал свое дело знает",
                                                                    "Да в пизду эту хуйню"],
                     trigger_type="trap", text=text, prob=prob,
                     prob_dict=prob_dict)
    text, prob = _if(msg=msg, words=["оля"], answers=["Оля топ!"],
                     trigger_type="trap", text=text, prob=prob,
                     prob_dict=prob_dict, exact=True)

    return text, prob