import os
import random
import re
import datetime
import pytz
import asyncio

from collections import deque
import aiofiles
import aiohttp

import fire
import pandas as pd
from configparser import ConfigParser
from openai import OpenAI
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
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

# Initialize OpenAI client (async)
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
    
    async def process_bot_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process messages from other bots."""
        if update.message.via_bot is None:
            return
            
        godnoscop_bot = update.message.via_bot.id == GODNOSCOP_ID
        
        if update.message.via_bot.id not in [GODNOSCOP_ID, SELF_ID, PREDSKAZ_ID]:
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
            await context.bot.send_message(
                update.effective_chat.id,
                update.message.text.replace("#NoWar", "")
            )
            await context.bot.delete_message(
                update.effective_chat.id, update.message.message_id
            )
            logger.info(f"edited_message from godnoscop bot: {update.message.text}")
    
    async def process_men_squad_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process special messages from men squad."""
        if (update.message.from_user.id in men_squad and
            "нахуй баб" in update.message.text.lower()):
            
            regex = r"(-?[0-9]|[1-9][0-9]|[1-9][0-9][0-9])"
            numbers = re.findall(regex, update.message.text)
            
            if not numbers or not numbers[0].isdigit():
                text = "Ты неправильно накастовал, дебил"
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
            else:
                count = min(int(numbers[0]), 10)
                for _ in range(count):
                    text = choice(["НАХУЙ БАБ", "_НАХУЙ БАБ_", "*НАХУЙ БАБ*"])
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=text,
                        parse_mode="markdown",
                    )
                    # Асинхронная задержка без блокировки
                    await asyncio.sleep(choice([0.5, 0.25, 1, 0.75, 0.666]))
    
    async def process_ai_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process AI chat responses."""
        if (update.message.reply_to_message is not None and
            update.message.reply_to_message.from_user.id == context.bot_data["self_id"]):
            
            content = update.message.text
            
            context.bot_data["chat_deque"].append({"role": "system", "content": professional_prompt})
            context.bot_data["chat_deque"].append({"role": "user", "content": content})
            
            try:
                response = await client.chat.completions.create(
                    model="deepseek-chat",
                    messages=list(context.bot_data["chat_deque"]),
                    stream=False
                )
                
                text = response.choices[0].message.content
                context.bot_data["chat_deque"].append({"role": "assistant", "content": text})
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
                logger.info(f"chatGPT: generated text sent text:{text}")
                
            except Exception as e:
                logger.error(f"Error generating AI response: {e}")
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text="Извините, произошла ошибка при генерации ответа.",
                    parse_mode="markdown",
                )
    
    async def process_special_commands(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
        """Process special command patterns in messages."""
        # Demobilization countdown
        if "дембель" in msg:
            td = datetime.datetime(2028, 11, 14, tzinfo=tz) - datetime.datetime.now(tz)
            text = f"Арбузу до пенсии осталось ровно {td_convert(td)}"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )
        
        # Scary life response
        if "страшно жить" in msg:
            text = "ВАЩЕ ПИЗДЕЦ"
            await context.bot.send_message(
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
                    await context.bot.send_dice(
                        chat_id=update.effective_message.chat_id,
                        reply_to_message_id=update.message.message_id,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        text=text,
                        parse_mode="markdown",
                    )
    
    async def process_media_responses(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
        """Process media responses based on message content."""
        # Cola response
        if (self._should_send_cola(msg) and
            not update.message.forward_from_message_id):
            
            reply_to = (update.message.reply_to_message.message_id
                       if update.message.reply_to_message else update.message.message_id)
            
            try:
                async with aiofiles.open("img/colocola.jpg", "rb") as f:
                    photo_data = await f.read()
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=reply_to,
                        caption=colocola,
                        photo=photo_data,
                        parse_mode="markdown",
                    )
            except FileNotFoundError:
                logger.error("Cola image not found")
        
        # Elephant response
        if self._should_send_elephant(msg):
            reply_to = (update.message.reply_to_message.message_id
                       if update.message.reply_to_message else update.message.message_id)
            
            try:
                async with aiofiles.open("img/slon.jpg", "rb") as f:
                    photo_data = await f.read()
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=reply_to,
                        photo=photo_data,
                        parse_mode="markdown",
                    )
            except FileNotFoundError:
                logger.error("Elephant image not found")
        
        # Nazi response
        if "нацист" in msg:
            try:
                async with aiofiles.open("img/nz.jpg", "rb") as f:
                    photo_data = await f.read()
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        photo=photo_data,
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
    
    async def process_diarrhea_spell(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
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
                roll = await context.bot.send_dice(chat_id=update.effective_message.chat_id)
                await asyncio.sleep(2.7)
                
                if roll.dice.value == value:
                    text = f"*Понос* {user} обеспечен"
                else:
                    text = "_Каст поноса был провален!_"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=text,
            parse_mode="markdown",
        )
    
    async def process_sticker_responses(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
        """Process sticker responses based on message content."""
        # Synthetic lovers
        if "любителям синтетики" in msg:
            try:
                async with aiofiles.open("img/GM.webp", "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                    logger.info("answer_message: sticker sent")
            except FileNotFoundError:
                logger.error("GM sticker not found")
        
        # Nevsky photo
        if msg == "вот так вот":
            try:
                async with aiofiles.open("img/nevsky.jpeg", "rb") as f:
                    photo_data = await f.read()
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        photo=photo_data,
                    )
                    logger.info("answer_message: nevsky photo sent")
            except FileNotFoundError:
                logger.error("Nevsky image not found")
        
        # Good morning
        if msg == "доброе утро":
            try:
                async with aiofiles.open("img/GM_SHUE.webp", "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                    logger.info("answer_message: good morning crackheads sticker sent")
            except FileNotFoundError:
                logger.error("GM_SHUE sticker not found")
        
        # Yandex
        if "хуяндекс" in msg:
            try:
                async with aiofiles.open("img/yandex.webp", "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
            except FileNotFoundError:
                logger.error("Yandex sticker not found")
        
        # Good night
        if re.search(r'\b(?:спокойной?|доброй?|сладкой?|ой)\s+(?:ночи|ночки|ночью)\b', msg, re.IGNORECASE):
            try:
                async with aiofiles.open("img/GN.webp", "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                    logger.info("answer_message: yandex sticker sent")
                    
                    await context.bot.send_message(
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
    
    async def process_pot_drinking(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
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
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=f"Горшок {not_drink_choice} уже {not_drink_ending}",
            parse_mode="markdown",
        )
    
    async def process_zalupa_stickers(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
        """Process zalupa sticker responses."""
        if "залуп" in msg:
            file = choice(["img/zalupa.webp", "img/zalupa_1.webp"])
            try:
                async with aiofiles.open(file, "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                    logger.info("answer_message: zalupa sticker sent")
            except FileNotFoundError:
                logger.error(f"Zalupa sticker not found: {file}")
    
    async def process_jackpot(self, update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
        """Process jackpot responses."""
        if "джекпот" in msg:
            try:
                async with aiofiles.open("img/jackpot.webp", "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                    await context.bot.send_message(
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
    async def send_oxxxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send random Oxxxy mashup URL."""
        url = choice(oxxxy_playlist)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=url,
            parse_mode="markdown",
        )
        logger.info(f"send_oxxxy: oxxy mashup {url} sent")
    
    @staticmethod
    async def send_goblin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                async with aiofiles.open(animation, "rb") as f:
                    animation_data = await f.read()
                    await context.bot.send_animation(
                        chat_id=update.effective_chat.id,
                        animation=animation_data,
                        read_timeout=20,
                        reply_to_message_id=update.message.message_id,
                    )
                    logger.info(f"send_goblin: mode {mode} file {animation} sent")
                    
            elif mode == "img":
                img = os.path.join(
                    goblin_dir,
                    choice([f for f in os.listdir(goblin_dir) if f.endswith(".jpeg")])
                )
                async with aiofiles.open(img, "rb") as f:
                    photo_data = await f.read()
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        photo=photo_data,
                    )
                    logger.info(f"send_goblin: mode {mode} file {img} sent")
                    
            elif mode == "sticker":
                sticker = os.path.join(
                    goblin_dir,
                    choice([f for f in os.listdir(goblin_dir) if f.endswith(".webp")])
                )
                async with aiofiles.open(sticker, "rb") as f:
                    sticker_data = await f.read()
                    await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                    logger.info(f"send_goblin: mode {mode} file {sticker} sent")
                    
            elif mode == "text":
                text = choice(goblin_pasta)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=text,
                    parse_mode="markdown",
                )
                logger.info(f"send_goblin: mode {mode} file text sent")
                
            elif mode == "youtube":
                url = choice(urls)
                await context.bot.send_message(
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
    async def send_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send morning factory/office message."""
        bot_data = context.bot_data
        
        text = "Русские, в офис / на завод!\n..._loading_..."
        await context.bot.send_message(
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
                async with aiofiles.open(file, "rb") as f:
                    photo_data = await f.read()
                    zavod_user = f"Офисчанин/Заводчанин дня - @{username}!"
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        reply_to_message_id=update.message.message_id,
                        caption=zavod_user,
                        photo=photo_data,
                    )
                    logger.info("send_morning: zavod success!")
            except FileNotFoundError:
                logger.error(f"Zavod image not found: {file}")
        else:
            zavod_user = bot_data["username"].replace("@", "")
            text = (f"Поздно, другалёчек!\n"
                   f"Офисчанин/Заводчанин дня - @{zavod_user}!")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            logger.info("send_morning: zavod success but late!")
    
    @staticmethod
    async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show day-specific sticker."""
        weekday = pd.Timestamp(datetime.datetime.now(tz)).weekday()
        sticker = os.path.join("img/eva", f"{weekday}.webp")
        
        try:
            async with aiofiles.open(sticker, "rb") as f:
                sticker_data = await f.read()
                await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=sticker_data)
                logger.info(f"show_day: file {sticker} sent")
        except FileNotFoundError:
            logger.error(f"Day sticker not found: {sticker}")
    
    @staticmethod
    async def show_holidays(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current holidays."""
        dt = datetime.datetime.now(tz)
        text = get_holidays(dt)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="markdown",
        )
        logger.info("show_day: sent holidays list")


class PlotinaManager:
    """Manages the plotina (dam) building game."""
    
    def __init__(self, file_path: str = "plotina.parquet"):
        self.file_path = file_path
    
    async def build_plotina(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            if (dt - pd.to_datetime(record.iloc[0]["dt"])).seconds // 3600 >= 1:
                df.loc[df.id == _id, "dt"] = pd.to_datetime(dt)
                df.loc[df.id == _id, "last_build"] = random_number
                df.loc[df.id == _id, "overall_build"] = df.loc[df.id == _id, "overall_build"] + random_number
                text = get_length(df, first_name, random_number)
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            else:
                text = f"Бобер {first_name} все еще спит!"
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
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
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            df = pd.concat([df, record], ignore_index=True)
        
        # Save to file asynchronously (using thread executor for pandas operations)
        await asyncio.get_event_loop().run_in_executor(None, lambda: df.to_parquet(self.file_path))
    
    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show plotina building statistics."""
        try:
            df = pd.read_parquet(self.file_path)
            text = get_length(df, None, None, stats=True)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        except FileNotFoundError:
            await context.bot.send_message(
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
async def quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send random quote."""
    text = quote_choice()
    logger.info(f"quote: {text[:10]}...")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


@pause
async def get_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send horoscope posts."""
    first_post, second_post = generate_post()
    logger.info("sending horoscopes")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=first_post)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=second_post)


@pause
async def godnoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await update.message.reply_text("Выбирай епте:", reply_markup=reply_markup)


@pause
async def button_godnoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle horoscope sign selection."""
    query = update.callback_query
    await query.answer()
    
    message = tracker.get_horoscope(query.data)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=message)


async def spam_gif_detector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                await context.bot.delete_message(
                    update.effective_chat.id, update.message.message_id
                )


@pause
async def parse_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main message parsing function - now fully async."""
    bot_data = context.bot_data
    text = ""
    prob = 0
    
    # Initialize message processor
    processor = MessageProcessor(bot_data)
    
    # Process bot messages
    await processor.process_bot_messages(update, context)
    
    # Process men squad messages
    await processor.process_men_squad_message(update, context)
    
    # Process AI responses
    await processor.process_ai_response(update, context)
    
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
                await process_trigger_response(update, context, text, trigger_type)
                
                log_text = text
                if len(log_text.split()) > 20:
                    log_text = (
                        f"{' '.join([log_text.split()[idx] for idx in range(5)])}"
                        f"...{' '.join([log_text.split()[idx] for idx in range(-3, 0)])}"
                    )
                logger.info(f"scripted answer_message: replied with {log_text}")
            
            # Process special triggers that require custom logic
            await process_special_triggers(update, context, msg)
            
            # Process media responses that are not covered by triggers
            await processor.process_media_responses(update, context, msg)
            
            # Process sticker responses that are not covered by triggers
            await processor.process_sticker_responses(update, context, msg)
            
            # Process zalupa stickers
            await processor.process_zalupa_stickers(update, context, msg)
            
            # Process jackpot
            await processor.process_jackpot(update, context, msg)


@pause
async def send_oxxxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send random Oxxxy content."""
    await ContentSender.send_oxxxy(update, context)


@pause
async def send_goblin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send random goblin content."""
    await ContentSender.send_goblin(update, context)


async def delete_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete dice messages."""
    if update.message.dice.emoji in emojis:
        await asyncio.sleep(choice(CHOICES))
        await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
        logger.info(f"delete_dice: {update.message.text}")


@pause
async def send_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send morning factory message."""
    await ContentSender.send_morning(update, context)


@pause
async def roll_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roll a dice."""
    await context.bot.send_dice(
        chat_id=update.effective_message.chat_id,
        reply_to_message_id=update.message.message_id,
    )
    logger.info("roll_dice: success")


@pause
async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show day sticker."""
    await ContentSender.show_day(update, context)


@pause
async def show_holidays(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show holidays."""
    await ContentSender.show_holidays(update, context)


@pause
async def build_plotina(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Build plotina."""
    plotina_manager = PlotinaManager()
    await plotina_manager.build_plotina(update, context)


@pause
async def stats_plotina(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show plotina statistics."""
    plotina_manager = PlotinaManager()
    await plotina_manager.show_stats(update, context)


async def paused(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle bot pause state."""
    if update.message.from_user.id == context.bot_data["master"]:
        context.bot_data["paused"] = not context.bot_data.get("paused", False)
        if context.bot_data["paused"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Бельмондо спит"
            )
    else:
        if random.randint(0, 10) == 10:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ты чёт ошибся, другалек, я только по команде хозяина сплю",
            )


async def main(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Main bot initialization and setup - now fully async."""
    application = None
    vars_dict["spam_mode"] = spam_mode
    vars_dict["chat_deque"] = deque(maxlen=100)
    vars_dict["msg_deque"] = deque(maxlen=100)

    if mode not in ["dev", "prod"]:
        logger.error("Bot start: FAIL! Invalid mode")
        return

    if mode == "dev":
        vars_dict["self_id"] = vars_dict["self_id_dev"]

    # Create Application instead of Updater
    application = (
        Application.builder()
        .token(token)
        .read_timeout(1000)
        .connect_timeout(1000)
        .build()
    )
        
    logger.info("Bot start: success!")
    
    # Update bot_data
    application.bot_data.update(vars_dict)
        
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
        application.add_handler(handler)
    
    # Register message handlers (note: filters instead of Filters)
    application.add_handler(MessageHandler(filters.Dice.ALL, delete_dice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, parse_message))
    application.add_handler(MessageHandler(filters.Document.ALL, spam_gif_detector))
        
        # Register callback handlers
    application.add_handler(CallbackQueryHandler(button_godnoscope))
    
    # Start the application with polling
    logger.info("Bot is running... Press Ctrl+C to stop")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
        try:
            await application.stop()
            await application.shutdown()
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")
        logger.info("Bot shutdown complete")
        


def run_bot(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Wrapper function to run the async main function."""
    try:
        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Run the main function
        loop.run_until_complete(main(mode, spam_mode, token))
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up the event loop
        try:
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    fire.Fire(main)
