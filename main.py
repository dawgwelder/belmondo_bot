import os
import re
import fire
import datetime
import telegram

from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

from time import sleep
from random import choice
from logger import get_logger

import pandas as pd

from if_rules import ifs
from markov import get_model
from utils import *
from const import *
from oxxxy_urls import oxxxy_playlist


logger = get_logger("Belmondo Logger")
model = get_model()

# TODO: команда квас - прокидывает картинку бомжа в ответ
# TODO: дебажить завод
# TODO: блочить спам стикерами от одного человека
# TODO: упдайтить счетчик фемосрача


def quote(update, context) -> None:
    text = quote_choice()
    logger.info(f"quote: {text[:10]}...")
    context.bot.send_message(chat_id=update.effective_chat.id, text=text)


# Вынести в отдельный файл? Написать класс?
# delete photo for godnobot
def parse_message(update, context) -> None:
    bot_data = context.bot_data
    text = ""
    prob = 0

    # delete shit
    if update.message.via_bot is not None:
        shit_bot = (
            update.message.via_bot.id == SHIT_BOT_ID
        )  # or update.message.via_bot.username == "HowYourBot"
        godnoscop_bot = update.message.via_bot.id == GODNOSCOP_ID

        if shit_bot:
            sleep_choice(CHOICES)
            name = "shit"
            context.bot.delete_message(
                update.effective_chat.id, update.message.message_id
            )
            logger.info(f"delete_message from {name} bot: {update.message.text}")

        elif godnoscop_bot:
            name = "godnoscop"
            try:
                context.bot.edit_message_media(
                    update.effective_chat.id,
                    update.message.message_id,
                    media=InputMediaPhoto("img/pixel.jpeg"),
                )
                logger.info(f"delete_message from {name} bot: {update.message.text}")
            except:
                context.bot.send_message(
                    update.effective_chat.id, update.message.text.replace("#NoWar", "")
                )
                context.bot.delete_message(
                    update.effective_chat.id, update.message.message_id
                )
                logger.info(f"edited_message from {name} bot: {update.message.text}")

    if update.message.from_user.id in men_squad and "нахуй баб" in update.message.text.lower():
        regex = r"(-?[0-9]|[1-9][0-9]|[1-9][0-9][0-9])"
        number = re.findall(regex, update.message.text)[0]

        if not number.isdigit():
            text = "Ты неправильно накастовал, дебил"
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )
        else:
            count = int(number)
            count = 100 if count > 999 else count
            for _ in range(count):
                text = choice(["НАХУЙ БАБ", "_НАХУЙ БАБ_", "*НАХУЙ БАБ*"])
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    parse_mode="markdown",
                )
                sleep(choice([.5, .25, 1, .75, .666]))
                
    if update.message.reply_to_message is not None and update.message.reply_to_message.from_user.id == SELF_ID:
        if "или" in update.message.text.lower():
            msg = clean_string(update.message.text.lower())
            words = msg.split()
            if words[len(words) // 2] == "или":
                text = clean_string(choice(update.message.text.split(" или ")))
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
            logger.info("parse_message: or sentence answered")
        else:
            text = model.make_short_sentence(280)
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )
            logger.info("markov model: generated text sent")
        
    if update.message.text is not None and not text:
        msg = clean_string(update.message.text.lower())
        _id = update.message.from_user.id

        if msg:
            text, prob = ifs(msg=msg, _id=_id, spam_mode=bot_data["spam_mode"])
            logger.info(f"triggered by: {msg}")
            logger.info(
                f"answer_message: {'EXISTS' if text else 'EMPTY'} and flag to show was {bool(prob)}"
            )
            if text and prob:
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
                log_text = text
                
                if len(log_text.split()) > 20:
                    log_text = (
                        f"{' '.join([log_text.split()[idx] for idx in range(5)])}"
                        f"...{' '.join([log_text.split()[idx] for idx in range(-3, 0)])}"
                    )
                logger.info(f"answer_message: replied with {log_text}")

        if msg.startswith("понос ") and " на " in msg:
            user = msg.split("понос ")[-1].split(" на")[0]
            reg_value = re.sub("[^0-9]", "", msg)
            reg_value = int(reg_value) if reg_value else -999
            value = msg[-1]
            text = "Вы допустили ошибку в заклинании - теперь ждите кару самопоноса"
            
            if value.isdigit():
                value = int(value)
                
                if 1 <= value <= 6 and reg_value == value:
                    roll = context.bot.send_dice(
                        chat_id=update.effective_message.chat_id
                    )
                    sleep(2.7)
                    
                    if roll.dice.value == value:
                        text = f"*Понос* {user} обеспечен"
                    else:
                        text = f"_Каст поноса был провален!_"
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )

        # send sticker
        if "любителям синтетики" in msg:
            with open("img/GM.webp", "rb") as f:
                context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=f
                ).sticker
                logger.info("answer_message: sticker sent")
                
        if text == "О, морская!" and prob:
            with open("img/snail.jpeg", "rb") as f:
                context.bot.send_photo(chat_id=update.effective_chat.id, photo=f)
            logger.info("answer_message: snail photo sent")
            
        if msg == "вот так вот":
            with open("img/nevsky.jpeg", "rb") as f:
                context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    photo=f,
                )
            logger.info("answer_message: nevsky photo sent")
            
        if msg == "доброе утро":
            with open("img/GM_SHUE.webp", "rb") as f:
                context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=f
                ).sticker
                logger.info("answer_message: good morning crackheads sticker sent")
                
        if "ой ночи" in msg:
            with open("img/GN.webp", "rb") as f:
                context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=f
                ).sticker
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=choice(
                        ["Good night!", "Спокойной ночи", "Сладких снов", "Покасики!"]
                    ),
                    parse_mode="markdown",
                )
                logger.info("answer_message: good night crackheads sticker sent")
                
        if "залуп" in msg:
            file = choice(["img/zalupa.webp", "img/zalupa_1.webp"])
            with open(file, "rb") as f:
                context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=f
                ).sticker
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=choice(["_Залупа-лупа!_", "_Залупу-лупу!_"]),
                    parse_mode="markdown",
                )
                logger.info("answer_message: zalupa sticker sent")
                
        if "джекпот" in msg:
            with open("img/jackpot.webp", "rb") as f:
                context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=f
                ).sticker
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=choice(["*ДЖЕКПОТ!*", "Джекпот! Хуй те в рот!"]),
                    parse_mode="markdown",
                )
                logger.info("answer_message: jackpot sticker sent")


def send_oxxxy(update, context) -> None:
    url = choice(oxxxy_playlist)
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text=f"{url}",
        parse_mode="markdown",
    )
    logger.info(f"send_oxxxy: oxxy mashup {url} sent")


def send_goblin(update, context) -> None:
    goblin_dir = "img/goblin/"
    mode = choice(["mp4", "img", "sticker", "text", "youtube"])
    urls = goblin_urls

    if mode == "mp4":
        animation = os.path.join(
            goblin_dir,
            choice([file for file in os.listdir(goblin_dir) if file.endswith(".mp4")]),
        )
        with open(animation, "rb") as f:
            context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=f,
                timeout=20,
                reply_to_message_id=update.message.message_id,
            )
            logger.info(f"send_goblin: mode {mode} file {animation} sent")
    if mode == "img":
        img = os.path.join(
            goblin_dir,
            choice([file for file in os.listdir(goblin_dir) if file.endswith(".jpeg")]),
        )
        with open(img, "rb") as f:
            context.bot.send_photo(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                photo=f,
            )
            logger.info(f"send_goblin: mode {mode} file {img} sent")
    if mode == "sticker":
        sticker = os.path.join(
            goblin_dir,
            choice([file for file in os.listdir(goblin_dir) if file.endswith(".webp")]),
        )
        with open(sticker, "rb") as f:
            context.bot.send_sticker(
                chat_id=update.effective_chat.id, sticker=f
            ).sticker
            logger.info(f"send_goblin: mode {mode} file {sticker} sent")
    if mode == "text":
        text = choice(goblin_pasta)
        print(text)
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=text,
            parse_mode="markdown",
        )
        logger.info(f"send_goblin: mode {mode} file text sent")
    if mode == "youtube":
        url = choice(urls)
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=f"СМОТРЕТЬ ВСЕМ\n{url}",
            parse_mode="markdown",
        )
        logger.info(f"send_goblin: mode {mode} file {url} sent")


def delete_dice(update, context) -> None:
    if update.message.dice.emoji in emojis:
        sleep_choice(CHOICES)
        context.bot.delete_message(update.effective_chat.id, update.message.message_id)
        logger.info(f"delete_dice: {update.message.text}")


def send_morning(update, context) -> None:
    bot_data = context.bot_data

    text = "Русские, в офис / на завод!\n" "..._loading_..."
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text=text,
        parse_mode="markdown",
    )
    logger.info(f"send_morning: preload")
    username = update.effective_user.username
    if bot_data["dt"] is None:
        bot_data["dt"] = datetime.datetime.now()
        bot_data["ZAVOD_CHECK"] = True
        bot_data["username"] = username
    else:
        bot_data["ZAVOD_CHECK"] = (
            datetime.datetime.now() - bot_data["dt"]
        ).days > 0 and (4 <= datetime.datetime.now().hour < 12)
        if bot_data["ZAVOD_CHECK"]:
            bot_data["username"] = username
    if bot_data["ZAVOD_CHECK"]:
        file = choice(
            ["img/zavodchanin.jpeg", "img/zombie_zavod.jpeg", "img/flower.jpeg"]
        )
        with open(file, "rb") as f:
            zavod_user = f"Офисчанин/Заводчанин дня - @{username}!"
            context.bot.send_photo(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                caption=zavod_user,
                photo=f,
            )
            logger.info(f"send_morning: zavod success!")

    else:
        zavod_user = bot_data["username"].replace("@", "")
        text = f"Поздно, другалёчек!\n" + f"Офисчанин/Заводчанин дня - @{zavod_user}!"
        context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        logger.info(f"send_morning: zavod success but late!")


def roll_dice(update, context) -> None:
    context.bot.send_dice(
        chat_id=update.effective_message.chat_id,
        reply_to_message_id=update.message.message_id,
    )
    logger.info(f"roll_dice: success")


def build_plotina(update, context) -> None:
    df = pd.read_parquet("plotina.parquet")
    _id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    last_name = update.effective_user.last_name
    dt = datetime.datetime.now()
    random_number = choice(range(1, 10))
    if choice(range(10)) > 9:
        random_number = choice(range(20, 101))
    if _id in df.id.values:
        record = df[df.id == _id]
        if (dt - pd.to_datetime(record.loc[0, "dt"])).seconds // 3600 >= 1:
            record["dt"] = pd.to_datetime(dt)
            record["last_build"] = random_number
            record["overall_build"] = record["overall_build"] + random_number
            df.update(record)
            text = get_length(df)
            context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        else:
            text = f"Бобер {first_name} все еще спит!"
            context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        
    else:
        int_dt = int(pd.Timestamp(dt).to_datetime64())
        record = pd.DataFrame({"id": [_id],
                               "username": [username],
                               "first_name": [first_name],
                               "last_name": [last_name],
                               "dt": [int_dt],
                               "last_build": [random_number],
                               "overall_build": [random_number]})
        text = f"Бобер {first_name} вступил в игру и сделал плотину выше на {random_number} см!"
        context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        df = df.append(record)
    df.to_parquet("plotina.parquet")


def stats_plotina(update, context) -> None:
    df = pd.read_parquet("plotina.parquet")
    text = get_length(df, stats=True)
    context.bot.send_message(chat_id=update.effective_chat.id, text=text)


def main(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    vars_dict["spam_mode"] = spam_mode
    if mode in ["dev", "prod"]:
        bot = Bot(token)
        updater = Updater(
            token=token,
            use_context=True,
            request_kwargs={"read_timeout": 1000, "connect_timeout": 1000},
        )
    else:
        logger.error(f"Bot start: FAIL!")
    logger.info(f"Bot start: success!")
    dispatcher = updater.dispatcher
    job = updater.job_queue
    dispatcher.bot_data.update(vars_dict)

    quote_handler = CommandHandler("quote", quote)
    dispatcher.add_handler(quote_handler)

    delete_dice_handler = MessageHandler(Filters.dice, delete_dice)
    dispatcher.add_handler(delete_dice_handler)

    build_handler = CommandHandler("build", build_plotina)
    dispatcher.add_handler(build_handler)

    stats_handler = CommandHandler("stats", stats_plotina)
    dispatcher.add_handler(stats_handler)

    goblin_handler = CommandHandler("goblin", send_goblin)
    dispatcher.add_handler(goblin_handler)

    oxxxy_handler = CommandHandler("oxxxy", send_oxxxy)
    dispatcher.add_handler(oxxxy_handler)

    morning_handler = CommandHandler("zavod", send_morning)
    dispatcher.add_handler(morning_handler)

    roll_handler = CommandHandler("roll", roll_dice)
    dispatcher.add_handler(roll_handler)

    parse_handler = MessageHandler(Filters.text & ~Filters.command, parse_message)
    dispatcher.add_handler(parse_handler)

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    fire.Fire(main)
