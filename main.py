import fire
import datetime
import telegram
from telegram import Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from time import sleep
from random import choice
from logger import get_logger
from utils import *
from const import *


logger = get_logger("Belmondo Logger")
# TODO: команда квас - прокидывает картинку бомжа в ответ
# TODO: дебажить завод
# TODO: не работает удалялка говнобота - нужен фастфикс
# TODO: playlist c oxхxy в ответ на команду окси
# TODO: если кря крутит пингвин - то единичка вероятность
# TODO: переписать глобалы на bot_data
# TODO: блочить спам стикерами от одного человека
# TODO: упдайтить счетчик фемосрача
# TODO: удалять гороскопу фото? Удалять гороскопы?
# HINT: Копировать текст гороскопа, присылать, исходное удалять


def quote(update, context) -> None:
    text = quote_choice()
    logger.info(f"quote: {text[:10]}...")
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=text)


# Вынести в отдельный файл? Написать класс?
# delete photo for godnobot
def parse_message(update, context) -> None:
    text = None
    prob = 0
    print(update)

    # delete shit
    if update.message.via_bot is not None:
        shit_bot = update.message.via_bot.id == SHIT_BOT_ID  # or update.message.via_bot.username == "HowYourBot"
        godnoscop_bot = update.message.via_bot.id == GODNOSCOP_ID
        if shit_bot:
            sleep_choice(CHOICES)
            name = "shit"
            context.bot.delete_message(update.effective_chat.id, update.message.message_id)
            logger.info(f"delete_message from {name} bot: {update.message.text}")
        elif godnoscop_bot:
            name = "godnoscop"
            try:
                context.bot.edit_message_media(update.effective_chat.id,
                                               update.message.message_id,
                                               media=InputMediaPhoto('img/pixel.jpeg'))
                logger.info(f"delete_message from {name} bot: {update.message.text}")
            except:
                context.bot.send_message(update.effective_chat.id, update.message.text.replace('#No War', ''))
                context.bot.delete_message(update.effective_chat.id, update.message.message_id)
                logger.info(f"edited_message from {name} bot: {update.message.text}")
    msg = clean_string(update.message.text.lower())
    _id = update.message.from_user.id
    text, prob = ifs(msg, _id, context.bot_data["spam_mode"])
    logger.info(f"answer_message: {text} and flag to show was {bool(prob)}")
    # send sticker
    if "любителям синтетики" in msg:
        with open("GM.webp", "rb") as f:
            context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f).sticker
            logger.info("answer_message: sticker sended")
    if text == "О, морская!" and prob:
            with open("img/snail.jpeg", "rb") as f:
                context.bot.send_photo(chat_id=update.effective_chat.id, photo=f)
            logger.info("answer_message: snail photo sended")
    if msg == "вот так вот":
            with open("img/nevsky.jpeg", "rb") as f:
                context.bot.send_photo(chat_id=update.effective_chat.id,
                                       reply_to_message_id=update.message.message_id,
                                       photo=f)
            logger.info("answer_message: nevsky photo sended")
    if text is not None and prob:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 reply_to_message_id=update.message.message_id,
                                 text=text,
                                 parse_mode="markdown")


def delete_dice(update, context) -> None:
    if update.message.dice.emoji in emojis:
        sleep_choice(CHOICES)
        context.bot.delete_message(update.effective_chat.id,
                                   update.message.message_id)
        logger.info(f"delete_dice: {update.message.text}")


def send_morning(update, context) -> None:
    bot_data = context.bot_data

    text = "Русские, в офис / на завод!\n" \
           "..._loading_..."
    context.bot.send_message(chat_id=update.effective_chat.id,
                             reply_to_message_id=update.message.message_id,
                             text=text,
                             parse_mode="markdown")
    logger.info(F"send_morning: preload")
    username = update.effective_user.username
    if bot_data["dt"] is None:
        bot_data["dt"] = datetime.datetime.now()
        bot_data["ZAVOD_CHECK"] = True
    else:
        bot_data["ZAVOD_CHECK"] = (datetime.datetime.now() - bot_data["dt"]).days > 0
        bot_data["username"] = username
    if bot_data["ZAVOD_CHECK"]:
        file = choice(["img/zavodchanin.jpeg", "img/zombie_zavod.jpeg", "img/flower.jpeg"])
        with open(file, "rb") as f:
            zavod_user = f"Офисчанин/Заводчанин дня - @{username}!"
            context.bot.send_photo(chat_id=update.effective_chat.id,
                                   reply_to_message_id=update.message.message_id,
                                   caption=zavod_user,
                                   photo=f)
            logger.info(F"send_morning: zavod success!")

    else:
        zavod_user = bot_data["username"].replace("@", '')
        text = f"Поздно, другалёчек!\n" + f"Офисчанин/Заводчанин дня - @{zavod_user}!"
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=text)
        logger.info(F"send_morning: zavod success but late!")


def roll_dice(update, context) -> None:
    context.bot.send_dice(chat_id=update.effective_message.chat_id,
                          reply_to_message_id=update.message.message_id)
    logger.info(f"roll_dice: success")


def main(mode: str = "dev",
         spam_mode: str = "medium",
         token: str = None) -> None:
    vars_dict["spam_mode"] = spam_mode
    if mode == "dev" or mode is None:
        bot = Bot(token)
        updater = Updater(token=token, use_context=True)
    elif mode == "prod":
        bot = Bot(token)
        updater = Updater(token=token, use_context=True)
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

    morning_handler = CommandHandler("zavod", send_morning)
    dispatcher.add_handler(morning_handler)

    roll_handler = CommandHandler("roll", roll_dice)
    dispatcher.add_handler(roll_handler)

    parse_handler = MessageHandler(Filters.text & ~Filters.command, parse_message)
    dispatcher.add_handler(parse_handler)

    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    fire.Fire(main)
