"""Shared application configuration: OpenAI client, timezone, logger, ConfigParser."""

from configparser import ConfigParser

from openai import AsyncOpenAI as OpenAI

from logger import get_logger
from time_utils import MOSCOW

logger = get_logger("Belmondo Logger")

config = ConfigParser()
config.read("auth.conf")

client = OpenAI(
    api_key=config["auth"]["openai_api_key"],
    base_url="https://api.deepseek.com",
)

tz = MOSCOW

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
