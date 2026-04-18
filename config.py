"""Shared application configuration: OpenAI client, timezone, logger, ConfigParser."""

from configparser import ConfigParser

import pytz
from openai import AsyncOpenAI as OpenAI

from logger import get_logger

logger = get_logger("Belmondo Logger")

config = ConfigParser()
config.read("auth.conf")

client = OpenAI(
    api_key=config["auth"]["openai_api_key"],
    base_url="https://api.deepseek.com",
)

tz = pytz.timezone("Europe/Moscow")

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
