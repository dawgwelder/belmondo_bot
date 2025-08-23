import os
import random
import re
import datetime
import pytz

from collections import deque
from time import sleep


import fire
import pandas as pd
from configparser import ConfigParser
from openai import OpenAI
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
)

from logger import get_logger
from if_rules import ifs, process_special_triggers, process_trigger_response, get_trigger_type
from utils import *
from const import *
from oxxxy_urls import oxxxy_playlist
from horoscope import generate_post
from site_parser import get_holidays
from godnoscop.godnoscop_tracker import GodnoscopTracker

# Initialize logger
logger = get_logger("Belmondo Logger")

# Load configuration
config = ConfigParser()
config.read("auth.conf")

# Initialize OpenAI client
client = OpenAI(
    api_key=config["auth"]["openai_api_key"],
    base_url="https://api.deepseek.com"
)

# Initialize Godnoscop tracker
tracker = GodnoscopTracker(config)

# Set timezone
tz = pytz.timezone("Europe/Moscow")


class MessageProcessor:
    """Handles message processing and bot responses."""
    
    def __init__(self, bot_data: dict):
        self.bot_data = bot_data
    
    async def process_bot_messages(self, update, context) -> None:
        """Process messages from other bots."""
        if update.message.via_bot is None:
            return
            
        godnoscop_bot = update.message.via_bot.id == GODNOSCOP_ID
        
        if update.message.via_bot.id != GODNOSCOP_ID and update.message.via_bot.id != SELF_ID:
            # Асинхронная задержка с автоматическим удалением сообщения
            await sleep_choice_asyncio(
                DELAY_CHOICES, 
                context.bot, 
                update.effective_chat.id, 
                update.message.message_id, 
                logger
            )
            logger.info(f"delete_message from shit bot: {update.message.text}")
            
        elif godnoscop_bot:
            context.bot.send_message(
                update.effective_chat.id,
                update.message.text.replace("#NoWar", "")
            )
            context.bot.delete_message(
                update.effective_chat.id, update.message.message_id
            )
            logger.info(f"edited_message from godnoscop bot: {update.message.text}")
    
    def process_men_squad_message(self, update, context) -> None:
        """Process special messages from men squad."""
        if (update.message.from_user.id in men_squad and 
            "нахуй баб" in update.message.text.lower()):
            
            regex = r"(-?[0-9]|[1-9][0-9]|[1-9][0-9][0-9])"
            numbers = re.findall(regex, update.message.text)
            
            if not numbers or not numbers[0].isdigit():
                text = "Ты неправильно накастовал, дебил"
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
            else:
                count = min(int(numbers[0]), 10)
                for _ in range(count):
                    text = choice(["НАХУЙ БАБ", "_НАХУЙ БАБ_", "*НАХУЙ БАБ*"])
                    context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=text,
                        parse_mode="markdown",
                    )
                    # Асинхронная задержка без блокировки
                    sleep_choice_async([0.5, 0.25, 1, 0.75, 0.666])
    
    def process_ai_response(self, update, context) -> None:
        """Process AI chat responses."""
        if (update.message.reply_to_message is not None and
            update.message.reply_to_message.from_user.id == context.bot_data["self_id"]):
            
            content = update.message.text
            

            content = f"{professional_prompt}\n{content}"
            
            context.bot_data["chat_deque"].append({"role": "user", "content": content})
            
            try:
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=context.bot_data["chat_deque"],
                    stream=False
                )
                
                text = response.choices[0].message.content
                context.bot_data["chat_deque"].append({"role": "assistant", "content": text})
                
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
                logger.info(f"chatGPT: generated text sent text:{text}")
                
            except Exception as e:
                logger.error(f"Error generating AI response: {e}")
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text="Извините, произошла ошибка при генерации ответа.",
                    parse_mode="markdown",
                )
    
    def process_special_commands(self, update, context, msg: str) -> None:
        """Process special command patterns in messages."""
        # Demobilization countdown
        if "дембель" in msg:
            td = datetime.datetime(2028, 11, 14, tzinfo=tz) - datetime.datetime.now(tz)
            text = f"Арбузу до пенсии осталось ровно {td_convert(td)}"
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )
        
        # Scary life response
        if "страшно жить" in msg:
            text = "ВАЩЕ ПИЗДЕЦ"
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
    
    def process_media_responses(self, update, context, msg: str) -> None:
        """Process media responses based on message content."""
        # Cola response
        if (self._should_send_cola(msg) and 
            not update.message.forward_from_message_id):
            
            reply_to = (update.message.reply_to_message.message_id 
                       if update.message.reply_to_message else update.message.message_id)
            
            try:
                with open("img/colocola.jpg", "rb") as f:
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=reply_to,
                        caption=colocola,
                        photo=f,
                        parse_mode="markdown",
                    )
            except FileNotFoundError:
                logger.error("Cola image not found")
        
        # Elephant response
        if self._should_send_elephant(msg):
            reply_to = (update.message.reply_to_message.message_id 
                       if update.message.reply_to_message else update.message.message_id)
            
            try:
                with open("img/slon.jpg", "rb") as f:
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=reply_to,
                        photo=f,
                        parse_mode="markdown",
                    )
            except FileNotFoundError:
                logger.error("Elephant image not found")
        
        # Nazi response
        if "нацист" in msg:
            try:
                with open("img/nz.jpg", "rb") as f:
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        photo=f,
                        parse_mode="markdown",
                    )
            except FileNotFoundError:
                logger.error("Nazi image not found")
    
    def _should_send_cola(self, msg: str) -> bool:
        """Check if cola response should be sent."""
        return ("колокол" not in msg.split() and 
                "колокольн" not in msg and 
                "колокол" in msg)
    
    def _should_send_elephant(self, msg: str) -> bool:
        """Check if elephant response should be sent."""
        return ("слон" in msg and 
                "слонн" not in msg and 
                "прислон" not in msg)
    
    def process_diarrhea_spell(self, update, context, msg: str) -> None:
        """Process the diarrhea spell command."""
        if not (msg.startswith("понос ") and " на " in msg):
            return
            
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
    
    def process_sticker_responses(self, update, context, msg: str) -> None:
        """Process sticker responses based on message content."""
        # Synthetic lovers
        if "любителям синтетики" in msg:
            try:
                with open("img/GM.webp", "rb") as f:
                    context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                    logger.info("answer_message: sticker sent")
            except FileNotFoundError:
                logger.error("GM sticker not found")
        
        # Nevsky photo
        if msg == "вот так вот":
            try:
                with open("img/nevsky.jpeg", "rb") as f:
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        photo=f,
                    )
                    logger.info("answer_message: nevsky photo sent")
            except FileNotFoundError:
                logger.error("Nevsky image not found")
        
        # Good morning
        if msg == "доброе утро":
            try:
                with open("img/GM_SHUE.webp", "rb") as f:
                    context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                    logger.info("answer_message: good morning crackheads sticker sent")
            except FileNotFoundError:
                logger.error("GM_SHUE sticker not found")
        
        # Yandex
        if "хуяндекс" in msg:
            try:
                with open("img/yandex.webp", "rb") as f:
                    context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
            except FileNotFoundError:
                logger.error("Yandex sticker not found")
        
        # Good night
        if re.search(r'\b(?:спокойной?|доброй?|сладкой?|ой)\s+(?:ночи|ночки|ночью)\b', msg, re.IGNORECASE):
            try:
                with open("img/GN.webp", "rb") as f:
                    context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                    logger.info("answer_message: yandex sticker sent")
                    
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
                    logger.info("answer_message: good night crackheads sticker sent")
            except FileNotFoundError:
                logger.error("GN sticker not found")
    
    def process_pot_drinking(self, update, context, msg: str) -> None:
        """Process pot drinking status messages."""
        if not any(phrase in msg for phrase in [
            "горшок не пьет", "горшок не пьёт", "горшок держится"
        ]):
            return
            
        not_drink_choice = choice([
            "не пьет", "держится", "в завязке", "не бухает", "проявляет силу воли"
        ])
        
        not_drink = (
            datetime.datetime.now(tz).date() - 
            POT_DATE
        )
        not_drink_ending = td_convert(not_drink)
        
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=f"Горшок {not_drink_choice} уже {not_drink_ending}",
            parse_mode="markdown",
        )
    
    def process_zalupa_stickers(self, update, context, msg: str) -> None:
        """Process zalupa sticker responses."""
        if "залуп" in msg:
            file = choice(["img/zalupa.webp", "img/zalupa_1.webp"])
            try:
                with open(file, "rb") as f:
                    context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                    logger.info("answer_message: zalupa sticker sent")
            except FileNotFoundError:
                logger.error(f"Zalupa sticker not found: {file}")
    
    def process_jackpot(self, update, context, msg: str) -> None:
        """Process jackpot responses."""
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
                    logger.info("answer_message: jackpot sticker sent")
            except FileNotFoundError:
                logger.error("Jackpot sticker not found")


class ContentSender:
    """Handles sending various types of content."""
    
    @staticmethod
    def send_oxxxy(update, context) -> None:
        """Send random Oxxxy mashup URL."""
        url = choice(oxxxy_playlist)
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=url,
            parse_mode="markdown",
        )
        logger.info(f"send_oxxxy: oxxy mashup {url} sent")
    
    @staticmethod
    def send_goblin(update, context) -> None:
        """Send random goblin content."""
        goblin_dir = "img/goblin/"
        mode = choice(["mp4", "img", "sticker", "text", "youtube"])
        urls = goblin_urls
        
        try:
            if mode == "mp4":
                animation = os.path.join(
                    goblin_dir,
                    choice([f for f in os.listdir(goblin_dir) if f.endswith(".mp4")])
                )
                with open(animation, "rb") as f:
                    context.bot.send_animation(
                        chat_id=update.effective_chat.id,
                        animation=f,
                        timeout=20,
                        reply_to_message_id=update.message.message_id,
                    )
                    logger.info(f"send_goblin: mode {mode} file {animation} sent")
                    
            elif mode == "img":
                img = os.path.join(
                    goblin_dir,
                    choice([f for f in os.listdir(goblin_dir) if f.endswith(".jpeg")])
                )
                with open(img, "rb") as f:
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        photo=f,
                    )
                    logger.info(f"send_goblin: mode {mode} file {img} sent")
                    
            elif mode == "sticker":
                sticker = os.path.join(
                    goblin_dir,
                    choice([f for f in os.listdir(goblin_dir) if f.endswith(".webp")])
                )
                with open(sticker, "rb") as f:
                    context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                    logger.info(f"send_goblin: mode {mode} file {sticker} sent")
                    
            elif mode == "text":
                text = choice(goblin_pasta)
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
                logger.info(f"send_goblin: mode {mode} file text sent")
                
            elif mode == "youtube":
                url = choice(urls)
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=f"СМОТРЕТЬ ВСЕМ\n{url}",
                    parse_mode="markdown",
                )
                logger.info(f"send_goblin: mode {mode} file {url} sent")
                
        except FileNotFoundError as e:
            logger.error(f"Goblin file not found: {e}")
        except Exception as e:
            logger.error(f"Error sending goblin content: {e}")
    
    @staticmethod
    def send_morning(update, context) -> None:
        """Send morning factory/office message."""
        bot_data = context.bot_data
        
        text = "Русские, в офис / на завод!\n..._loading_..."
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=text,
            parse_mode="markdown",
        )
        logger.info("send_morning: preload")
        
        username = update.effective_user.username
        
        if bot_data["dt"] is None:
            bot_data["dt"] = datetime.datetime.now()
            bot_data["ZAVOD_CHECK"] = True
            bot_data["username"] = username
        else:
            bot_data["ZAVOD_CHECK"] = (
                (datetime.datetime.now() - bot_data["dt"]).days > 0 and
                (4 <= datetime.datetime.now().hour < 12)
            )
            if bot_data["ZAVOD_CHECK"]:
                bot_data["username"] = username
        
        if bot_data["ZAVOD_CHECK"]:
            file = choice([
                "img/zavodchanin.jpeg", "img/zombie_zavod.jpeg", "img/flower.jpeg"
            ])
            try:
                with open(file, "rb") as f:
                    zavod_user = f"Офисчанин/Заводчанин дня - @{username}!"
                    context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        caption=zavod_user,
                        photo=f,
                    )
                    logger.info("send_morning: zavod success!")
            except FileNotFoundError:
                logger.error(f"Zavod image not found: {file}")
        else:
            zavod_user = bot_data["username"].replace("@", "")
            text = (f"Поздно, другалёчек!\n"
                   f"Офисчанин/Заводчанин дня - @{zavod_user}!")
            context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            logger.info("send_morning: zavod success but late!")
    
    @staticmethod
    def show_day(update, context) -> None:
        """Show day-specific sticker."""
        weekday = pd.Timestamp(datetime.datetime.now(tz)).weekday()
        sticker = os.path.join("img/eva", f"{weekday}.webp")
        
        try:
            with open(sticker, "rb") as f:
                context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=f)
                logger.info(f"show_day: file {sticker} sent")
        except FileNotFoundError:
            logger.error(f"Day sticker not found: {sticker}")
    
    @staticmethod
    def show_holidays(update, context) -> None:
        """Show current holidays."""
        dt = datetime.datetime.now(tz)
        text = get_holidays(dt)
        
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="markdown",
        )
        logger.info("show_day: sent holidays list")


class PlotinaManager:
    """Manages the plotina (dam) building game."""
    
    def __init__(self, file_path: str = "plotina.parquet"):
        self.file_path = file_path
    
    def build_plotina(self, update, context) -> None:
        """Process plotina building command."""
        try:
            df = pd.read_parquet(self.file_path)
        except FileNotFoundError:
            df = pd.DataFrame(columns=[
                "id", "username", "first_name", "last_name", 
                "dt", "last_build", "overall_build"
            ])
        
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
            record = pd.DataFrame({
                "id": [_id],
                "username": [username],
                "first_name": [first_name],
                "last_name": [last_name],
                "dt": [int_dt],
                "last_build": [random_number],
                "overall_build": [random_number],
            })
            text = (f"Бобер {first_name} вступил в игру и "
                   f"сделал плотину выше на {random_number} см!")
            context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            df = pd.concat([df, record], ignore_index=True)
        
        df.to_parquet(self.file_path)
    
    def show_stats(self, update, context) -> None:
        """Show plotina building statistics."""
        try:
            df = pd.read_parquet(self.file_path)
            text = get_length(df, stats=True)
            context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        except FileNotFoundError:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Статистика пока недоступна."
            )


def pause(func):
    """Decorator to pause bot functionality."""
    def wrapper(update, context):
        if not context.bot_data.get("paused", False):
            func(update, context)
    return wrapper


@pause
def quote(update, context) -> None:
    """Send random quote."""
    text = quote_choice()
    logger.info(f"quote: {text[:10]}...")
    context.bot.send_message(chat_id=update.effective_chat.id, text=text)


@pause
def get_horoscope(update, context) -> None:
    """Send horoscope posts."""
    first_post, second_post = generate_post()
    logger.info("sending horoscopes")
    context.bot.send_message(chat_id=update.effective_chat.id, text=first_post)
    context.bot.send_message(chat_id=update.effective_chat.id, text=second_post)


@pause
def godnoscope(update: Update, context) -> None:
    """Show horoscope sign selection keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("Овен", callback_data="ОВЕН"),
            InlineKeyboardButton("Телец", callback_data="ТЕЛЕЦ"),
            InlineKeyboardButton("Близнецы", callback_data="БЛИЗНЕЦЫ"),
        ],
        [
            InlineKeyboardButton("Рак", callback_data="РАК"),
            InlineKeyboardButton("Лев", callback_data="ЛЕВ"),
            InlineKeyboardButton("Дева", callback_data="ДЕВА"),
        ],
        [
            InlineKeyboardButton("Весы", callback_data="ВЕСЫ"),
            InlineKeyboardButton("Скорпион", callback_data="СКОРПИОН"),
            InlineKeyboardButton("Стрелец", callback_data="СТРЕЛЕЦ"),
        ],
        [
            InlineKeyboardButton("Козерог", callback_data="КОЗЕРОГ"),
            InlineKeyboardButton("Водолей", callback_data="ВОДОЛЕЙ"),
            InlineKeyboardButton("Рыбы", callback_data="РЫБЫ"),
        ],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выбирай епте:", reply_markup=reply_markup)


@pause
def button_godnoscope(update: Update, context) -> None:
    """Handle horoscope sign selection."""
    query = update.callback_query
    query.answer()
    
    message = tracker.get_horoscope(query.data)
    context.bot.send_message(chat_id=update.effective_chat.id, text=message)


def spam_gif_detector(update, context) -> None:
    """Detect and remove spam GIFs."""
    if update.message.document.mime_type == "video/mp4":
        last_msg = {
            "from": update.message.from_user.id,
            "date": update.message.date
        }
        context.bot_data["msg_deque"].append(last_msg)
        
        for idx in range(len(context.bot_data["msg_deque"]) - 1):
            msg = context.bot_data["msg_deque"][idx]
            if (msg["from"] == last_msg["from"] and
                (last_msg["date"] - msg["date"]).seconds < 3):
                context.bot.delete_message(
                    update.effective_chat.id, update.message.message_id
                )


async def _parse_message_async(update, context) -> None:
    """Async version of main message parsing function."""
    bot_data = context.bot_data
    text = ""
    prob = 0
    
    # Initialize message processor
    processor = MessageProcessor(bot_data)
    
    # Process bot messages
    await processor.process_bot_messages(update, context)
    
    # Process men squad messages
    processor.process_men_squad_message(update, context)
    
    # Process AI responses
    processor.process_ai_response(update, context)
    
    # Process regular messages
    if update.message.text is not None and not text:
        msg = clean_string(update.message.text.lower())
        _id = update.message.from_user.id
        ts = update.message.date
        prev_ts = context.bot_data["spam_stopper"].get(_id, None)
        
        # Spam protection
        if (prev_ts is not None and
            (ts - prev_ts).seconds < 3 and
            _id != context.bot_data["master"]):
            msg = False
        
        context.bot_data["spam_stopper"][_id] = ts
        
        if msg:
            # Process triggers from triggers.json
            text, prob = ifs(msg=msg, _id=_id, spam_mode=bot_data["spam_mode"])
            if text:
                logger.info(f"triggered by: {msg}")
                logger.info(f"scripted answer_message: flag to show was {bool(prob)}")
            
            if text and prob:
                # Determine trigger type and process accordingly
                trigger_type = get_trigger_type(text)
                process_trigger_response(update, context, text, trigger_type)
                
                log_text = text
                if len(log_text.split()) > 20:
                    log_text = (
                        f"{' '.join([log_text.split()[idx] for idx in range(5)])}"
                        f"...{' '.join([log_text.split()[idx] for idx in range(-3, 0)])}"
                    )
                logger.info(f"scripted answer_message: replied with {log_text}")
            
            # Process special triggers that require custom logic
            process_special_triggers(update, context, msg)
            
            # Process media responses that are not covered by triggers
            processor.process_media_responses(update, context, msg)
            
            # Process sticker responses that are not covered by triggers
            processor.process_sticker_responses(update, context, msg)
            
            # Process zalupa stickers
            processor.process_zalupa_stickers(update, context, msg)
            
            # Process jackpot
            processor.process_jackpot(update, context, msg)


@pause
def parse_message(update, context) -> None:
    """Main message parsing function - sync wrapper for async function."""
    # Create event loop and run async function
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(_parse_message_async(update, context))
    except Exception as e:
        logger.error(f"Error in parse_message: {e}")
    finally:
        if loop.is_running():
            loop.close()


@pause
def send_oxxxy(update, context) -> None:
    """Send random Oxxxy content."""
    ContentSender.send_oxxxy(update, context)


@pause
def send_goblin(update, context) -> None:
    """Send random goblin content."""
    ContentSender.send_goblin(update, context)


def delete_dice(update, context) -> None:
    """Delete dice messages."""
    if update.message.dice.emoji in emojis:
        sleep_choice_async(CHOICES)
        context.bot.delete_message(update.effective_chat.id, update.message.message_id)
        logger.info(f"delete_dice: {update.message.text}")


@pause
def send_morning(update, context) -> None:
    """Send morning factory message."""
    ContentSender.send_morning(update, context)


@pause
def roll_dice(update, context) -> None:
    """Roll a dice."""
    context.bot.send_dice(
        chat_id=update.effective_message.chat_id,
        reply_to_message_id=update.message.message_id,
    )
    logger.info("roll_dice: success")


@pause
def show_day(update, context) -> None:
    """Show day sticker."""
    ContentSender.show_day(update, context)


@pause
def show_holidays(update, context) -> None:
    """Show holidays."""
    ContentSender.show_holidays(update, context)


@pause
def build_plotina(update, context) -> None:
    """Build plotina."""
    plotina_manager = PlotinaManager()
    plotina_manager.build_plotina(update, context)


@pause
def stats_plotina(update, context) -> None:
    """Show plotina statistics."""
    plotina_manager = PlotinaManager()
    plotina_manager.show_stats(update, context)


def paused(update, context) -> None:
    """Toggle bot pause state."""
    if update.message.from_user.id == context.bot_data["master"]:
        context.bot_data["paused"] = not context.bot_data.get("paused", False)
        if context.bot_data["paused"]:
            context.bot.send_message(
                chat_id=update.effective_chat.id, text="Бельмондо спит"
            )
    else:
        if random.randint(0, 10) == 10:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ты чёт ошибся, другалек, я только по команде хозяина сплю",
            )


def main(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Main bot initialization and setup."""
    # Initialize bot data
    vars_dict["spam_mode"] = spam_mode
    vars_dict["chat_deque"] = deque(maxlen=100)
    vars_dict["msg_deque"] = deque(maxlen=100)
    
    if mode not in ["dev", "prod"]:
        logger.error("Bot start: FAIL! Invalid mode")
        return
    
    if mode == "dev":
        vars_dict["self_id"] = vars_dict["self_id_dev"]
    
    try:
        bot = Bot(token)
        updater = Updater(
            token=token,
            use_context=True,
            request_kwargs={"read_timeout": 1000, "connect_timeout": 1000},
        )
    except Exception as e:
        logger.error(f"Bot start: FAIL! {e}")
        return
    
    logger.info("Bot start: success!")
    
    dispatcher = updater.dispatcher
    job = updater.job_queue
    dispatcher.bot_data.update(vars_dict)
    
    # Register command handlers
    handlers = [
        CommandHandler("quote", quote),
        CommandHandler("goblin", send_goblin),
        CommandHandler("oxxxy", send_oxxxy),
        CommandHandler("day", show_day),
        CommandHandler("holiday", show_holidays),
        CommandHandler("zavod", send_morning),
        CommandHandler("roll", roll_dice),
        CommandHandler("horoscope", godnoscope),
        CommandHandler("pause", paused),
        CommandHandler("plotina", build_plotina),
        CommandHandler("stats", stats_plotina),
    ]
    
    for handler in handlers:
        dispatcher.add_handler(handler)
    
    # Register message handlers
    dispatcher.add_handler(MessageHandler(Filters.dice, delete_dice))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, parse_message))
    dispatcher.add_handler(MessageHandler(Filters.document, spam_gif_detector))
    
    # Register callback handlers
    dispatcher.add_handler(CallbackQueryHandler(button_godnoscope))
    
    # Start bot
    updater.start_polling(drop_pending_updates=True)
    updater.idle()


if __name__ == "__main__":
    fire.Fire(main)
