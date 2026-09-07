"""Stable Telegram entry points for Spy Clicker.

Implementations live in handlers.spy, separated by presentation and use case.
"""

from .spy.constants import SPY_CALLBACK_PATTERN as SPY_CALLBACK_PATTERN
from .spy.constants import SPY_HTML5_GAME_PATTERN as SPY_HTML5_GAME_PATTERN
from .spy.menu_views import build_contact_blocks as build_contact_blocks
from .spy.menu_views import build_menu_blocks as build_menu_blocks
from .spy.menu_views import build_profile_blocks as build_profile_blocks
from .spy.menu_views import build_agents_blocks as build_agents_blocks
from .spy.menu_views import build_inventory_blocks as build_inventory_blocks
from .spy.menu_views import build_status_blocks as build_status_blocks
from .spy.menu_views import build_leaderboard_blocks as build_leaderboard_blocks
from .spy.event_views import build_event_blocks as build_event_blocks
from .spy.menu import spy_menu as spy_menu
from .spy.publication import publish_spy_event as publish_spy_event
from .spy.html5 import spy_html5_game_launch as spy_html5_game_launch
from .spy.callbacks import spy_callback as spy_callback
from .spy.jobs import track_spy_activity as track_spy_activity
from .spy.jobs import spy_game_tick as spy_game_tick
from .spy.admin import build_activity_admin_text as build_activity_admin_text
from .spy.admin import spy_admin as spy_admin
from .spy.event_views import _recruitment_message_text as _recruitment_message_text

__all__ = [
    "SPY_CALLBACK_PATTERN",
    "SPY_HTML5_GAME_PATTERN",
    "build_contact_blocks",
    "build_menu_blocks",
    "build_profile_blocks",
    "build_agents_blocks",
    "build_inventory_blocks",
    "build_status_blocks",
    "build_leaderboard_blocks",
    "build_event_blocks",
    "spy_menu",
    "publish_spy_event",
    "spy_html5_game_launch",
    "spy_callback",
    "track_spy_activity",
    "spy_game_tick",
    "build_activity_admin_text",
    "spy_admin",
    "_recruitment_message_text",
]
