import sys
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import Mock

sys.modules.setdefault(
    "config",
    SimpleNamespace(
        client=Mock(),
        logger=Mock(),
        TELEGRAM_MAX_MESSAGE_LENGTH=4096,
        tz=timezone.utc,
    ),
)

from handlers.ai import _strip_markdown_wrappers


def test_strip_markdown_wrappers_from_zodiac_title():
    assert _strip_markdown_wrappers("**Овен**") == "Овен"
    assert _strip_markdown_wrappers("__Телец__") == "Телец"
    assert _strip_markdown_wrappers("`Близнецы`") == "Близнецы"
    assert _strip_markdown_wrappers("**Овен**\nПервое предложение") == (
        "Овен\nПервое предложение"
    )


def test_strip_markdown_wrappers_preserves_plain_title():
    assert _strip_markdown_wrappers("Рак") == "Рак"
