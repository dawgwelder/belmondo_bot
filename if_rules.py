from utils import *
from const import *
import json
import os
from random import choice

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


def process_trigger_response(update, context, trigger_text: str, trigger_type: str = "text") -> None:
    """Process trigger response based on type."""
    if trigger_type == "text":
        # Simple text response
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=trigger_text,
            parse_mode="markdown",
        )
    elif trigger_type == "image":
        # Image response
        try:
            with open(trigger_text, "rb") as f:
                context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    photo=f,
                    parse_mode="markdown",
                )
        except FileNotFoundError:
            logger.error(f"Image not found: {trigger_text}")
    elif trigger_type == "sticker":
        # Sticker response
        try:
            with open(trigger_text, "rb") as f:
                context.bot.send_sticker(
                    chat_id=update.effective_chat.id,
                    sticker=f
                )
        except FileNotFoundError:
            logger.error(f"Sticker not found: {trigger_text}")
    elif trigger_type == "mixed":
        # Mixed response (e.g., image + text)
        parts = trigger_text.split("|")
        for part in parts:
            part = part.strip()
            if part.startswith("img:"):
                img_path = part[4:]  # Remove "img:" prefix
                try:
                    with open(img_path, "rb") as f:
                        if img_path.endswith(('.webp', '.png', '.jpg', '.jpeg')):
                            context.bot.send_sticker(
                                chat_id=update.effective_chat.id,
                                sticker=f
                            )
                        else:
                            context.bot.send_photo(
                                chat_id=update.effective_chat.id,
                                reply_to_message_id=update.message.message_id,
                                photo=f,
                                parse_mode="markdown",
                            )
                except FileNotFoundError:
                    logger.error(f"Media file not found: {img_path}")
            else:
                # Text part
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=part,
                    parse_mode="markdown",
                )


def get_trigger_type(answer: str) -> str:
    """Determine the type of trigger response."""
    if answer.startswith("img:"):
        return "image"
    elif answer.startswith("sticker:"):
        return "sticker"
    elif "|" in answer:
        return "mixed"
    else:
        return "text"


def process_special_triggers(update, context, msg: str) -> None:
    """Process special triggers that require custom logic."""
    # Demobilization countdown
    if "дембель" in msg:
        from datetime import datetime
        td = datetime(2028, 11, 14, tzinfo=pytz.timezone("Europe/Moscow")) - datetime.now(pytz.timezone("Europe/Moscow"))
        text = f"Арбузу до пенсии осталось ровно {td_convert(td)}"
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=text,
            parse_mode="markdown",
        )
    
    # Dice rolling
    if "кубик" in msg:
        text = roll_custom_dice(msg)
        if text is not None:
            if text == "default":
                context.bot.send_dice(
                    chat_id=update.effective_message.chat_id,
                    reply_to_message_id=update.message.message_id,
                )
            else:
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
    
    # Diarrhea spell
    if msg.startswith("понос ") and " на " in msg:
        user = msg.split("понос ")[-1].split(" на")[0]
        reg_value = re.sub("[^0-9]", "", msg)
        reg_value = int(reg_value) if reg_value else -999
        value = msg[-1]
        text = "Вы допустили ошибку в заклинании - теперь ждите кару самопоноса"
        
        if value.isdigit():
            value = int(value)
            
            if 1 <= value <= 6 and reg_value == value:
                roll = context.bot.send_dice(chat_id=update.effective_message.chat_id)
                sleep(2.7)
                
                if roll.dice.value == value:
                    text = f"*Понос* {user} обеспечен"
                else:
                    text = "_Каст поноса был провален!_"
        
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=text,
            parse_mode="markdown",
        )
    
    # Pot drinking status
    if any(phrase in msg for phrase in ["горшок не пьет", "горшок не пьёт", "горшок держится"]):
        not_drink_choice = choice([
            "не пьет", "держится", "в завязке", "не бухает", "проявляет силу воли"
        ])
        
        from datetime import datetime
        not_drink = (
            datetime.now(pytz.timezone("Europe/Moscow")).date() - 
            datetime.strptime("19072013", "%d%m%Y").date()
        )
        not_drink_ending = td_convert(not_drink)
        
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=f"Горшок {not_drink_choice} уже {not_drink_ending}",
            parse_mode="markdown",
        )
    
    # Good night with text
    if "ой ночи" in msg:
        try:
            with open("img/GN.webp", "rb") as f:
                context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=choice([
                        "Good night!",
                        "Спокойной ночи",
                        "Сладких снов",
                        "Покасики!",
                    ]),
                    parse_mode="markdown",
                )
        except FileNotFoundError:
            logger.error("GN sticker not found")
    
    # Jackpot with text
    if "джекпот" in msg:
        try:
            with open("img/jackpot.webp", "rb") as f:
                context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=choice(["*ДЖЕКПОТ!*", "Джекпот! Хуй те в рот!"]),
                    parse_mode="markdown",
                )
        except FileNotFoundError:
            logger.error("Jackpot sticker not found")
