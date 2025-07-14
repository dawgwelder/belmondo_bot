from utils import *
from const import *
import json

with open("speaking/triggers.json") as f:
    speaking = json.load(f)


def ifs(msg: str = None, _id: int = 0, spam_mode: str = "medium") -> Tuple[str, int]:
    #  test func for less code -> test in dev first!
    def _if(
        msg: str,
        words: list,
        answers: list,
        exclude_words: list,
        prob: float = 0,
        exclude_uids: tuple = (),
        update_uid: int = 0,
        exact: bool = False,
    ):
        put_answer = check_is_in(msg, words, exact=exact)
        text = ""
        _prob = 0
        if put_answer:
            text = choice(answers)

            if prob == -1:
                # _prob = draw_prob(spam_mode=spam_mode)
                prob = answer_probability(spam_mode)

            if exclude_uids:
                if update_uid in exclude_uids:  #
                    prob = 1

            if exclude_words:
                for word in exclude_words:
                    if word in msg:
                        prob = 0

        return text, prob

    text, prob = "", 0

    for key in speaking:
        _text, _prob = _if(
            msg,
            words=speaking[key]["triggers"],
            answers=speaking[key]["answers"],
            exclude_words=speaking[key]["exclude_words"],
            prob=speaking[key]["prob"],
            exclude_uids=speaking[key]["exclude_uids"],
            update_uid=_id,
            exact=speaking[key]["exact"],
        )
        if _text != "":
            if text != "":
                new = choice([0, 1])
                text = [text, _text][new]
                prob = [prob, _prob][new]
            else:
                text, prob = _text, _prob
    return text, prob
