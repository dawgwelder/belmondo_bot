import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

telegram = __import__("types").ModuleType("telegram")
telegram.InlineKeyboardButton = lambda text, callback_data=None: SimpleNamespace(
    text=text, callback_data=callback_data
)
telegram.InlineKeyboardMarkup = lambda inline_keyboard: SimpleNamespace(
    inline_keyboard=inline_keyboard
)
telegram.Update = object
telegram_ext = __import__("types").ModuleType("telegram.ext")
telegram_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)

sys.modules.setdefault("telegram", telegram)
sys.modules.setdefault("telegram.ext", telegram_ext)
sys.modules.setdefault(
    "config",
    SimpleNamespace(client=Mock(), logger=Mock(), TELEGRAM_MAX_MESSAGE_LENGTH=4096),
)

import handlers.games as game_handlers
from games import GamePhase
from games.scenarios import SCENARIOS


class FakeJob:
    def __init__(self, name):
        self.name = name
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, **kwargs):
        job = FakeJob(kwargs["name"])
        job.callback = callback
        job.when = when
        job.data = kwargs["data"]
        job.chat_id = kwargs["chat_id"]
        self.jobs.append(job)
        return job

    def get_jobs_by_name(self, name):
        return tuple(job for job in self.jobs if job.name == name and not job.removed)


class FakeApplication:
    def __init__(self):
        self.tasks = []

    def create_task(self, coroutine, update=None, *, name=None):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    async def wait_tasks(self):
        if self.tasks:
            tasks = list(self.tasks)
            self.tasks.clear()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)


class FakeMessage:
    def __init__(self, message_id, user=None, text=None, reply_to_message=None):
        self.message_id = message_id
        self.from_user = user
        self.text = text
        self.reply_to_message = reply_to_message
        self.date = datetime.now(timezone.utc)
        self.replies = []

    async def reply_text(self, text, **kwargs):
        sent = FakeMessage(1000 + len(self.replies))
        sent.text = text
        sent.reply_markup = kwargs.get("reply_markup")
        self.replies.append(sent)
        return sent


class FakeBot:
    def __init__(self):
        self.sent = []
        self.deleted = []

    async def send_message(self, **kwargs):
        sent = FakeMessage(2000 + len(self.sent))
        sent.text = kwargs["text"]
        self.sent.append((kwargs, sent))
        return sent

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)


class FakeQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeScenario:
    game_type = "alibi"
    title = "Алиби, месье"
    command = "/alibi"
    lobby_intro = "Тестовое лобби."

    async def generate_opening(self, _game):
        return {"incident": "incident", "common_question": "question"}

    async def generate_round_two(self, _game):
        return {"questions": {"1": "q1", "2": "q2", "3": "q3"}}

    async def generate_verdict(self, _game):
        return {
            "analysis": "done",
            "winner_id": 1,
            "suspect_id": 2,
            "nominations": [],
        }

    def format_opening_message(self, _game, opening):
        return f"{opening['incident']}\n{opening['common_question']}"

    def format_round_two_message(self, _game, payload):
        return "\n".join(payload["questions"].values())

    def format_verdict_message(self, _game, verdict):
        return f"winner={verdict['winner_id']}"


def user(user_id, name):
    return SimpleNamespace(id=user_id, username=None, full_name=name, is_bot=False)


def context():
    return SimpleNamespace(
        chat_data={},
        bot_data={"master": 999, "paused": False, "self_id": 777, "spam_mode": "none"},
        job_queue=FakeJobQueue(),
        bot=FakeBot(),
        application=FakeApplication(),
    )


def message_update(user_value, message, chat_id=-100):
    return SimpleNamespace(
        message=message,
        callback_query=None,
        effective_user=user_value,
        effective_chat=SimpleNamespace(id=chat_id),
    )


def callback_update(user_value, data, chat_id=-100, message=None):
    query = FakeQuery(data, message=message)
    return (
        SimpleNamespace(
            message=None,
            callback_query=query,
            effective_user=user_value,
            effective_chat=SimpleNamespace(id=chat_id),
        ),
        query,
    )


@pytest.mark.asyncio
async def test_game_command_shows_menu():
    ctx = context()
    creator = user(1, "Анна")
    message = FakeMessage(10, creator, "/game")

    await game_handlers.game(message_update(creator, message), ctx)

    reply = message.replies[0]
    buttons = [button for row in reply.reply_markup.inline_keyboard for button in row]
    assert reply.text == "Выберите игру:"
    assert [(button.text, button.callback_data) for button in buttons] == [
        ("Шпионская операция", "game:select:operation"),
        ("Создай алиби", "game:select:alibi"),
        ("Продай это Бельмондо", "game:select:pitch"),
        ("Рулетка", "game:select:roulette"),
    ]


@pytest.mark.asyncio
async def test_game_menu_selection_creates_lobby(monkeypatch):
    monkeypatch.setattr(
        game_handlers, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True)
    )
    ctx = context()
    creator = user(1, "Анна")
    menu_message = FakeMessage(10, creator, "/game")
    update, query = callback_update(
        creator, "game:select:operation", message=menu_message
    )

    await game_handlers.game_callback(update, ctx)

    game = ctx.chat_data["llm_games"][-100]
    assert game.game_type == "operation"
    assert query.answers[0] == ("Запускаю игру.", {})
    assert menu_message.replies[0].reply_markup.inline_keyboard


@pytest.mark.asyncio
async def test_command_creates_lobby_for_each_registered_game(monkeypatch):
    monkeypatch.setattr(
        game_handlers, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True)
    )
    for command, game_type in (
        (game_handlers.alibi, "alibi"),
        (game_handlers.operation, "operation"),
        (game_handlers.pitch, "pitch"),
    ):
        ctx = context()
        creator = user(1, "Анна")
        message = FakeMessage(10, creator, f"/{game_type}")

        await command(message_update(creator, message), ctx)

        game = ctx.chat_data["llm_games"][-100]
        assert game.game_type == game_type
        assert game.participants == (1,)
        assert game.phase is GamePhase.LOBBY
        assert message.replies[0].reply_markup.inline_keyboard
        assert ctx.job_queue.jobs[0].name == game.timeout_job_name


@pytest.mark.asyncio
async def test_start_rejects_non_creator(monkeypatch):
    monkeypatch.setattr(
        game_handlers, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True)
    )
    ctx = context()
    creator = user(1, "Анна")
    message = FakeMessage(10, creator, "/alibi")
    await game_handlers.alibi(message_update(creator, message), ctx)
    game = ctx.chat_data["llm_games"][-100]

    update, query = callback_update(
        user(2, "Борис"), f"game:alibi:{game.game_id}:start"
    )
    await game_handlers.game_callback(update, ctx)

    assert game.phase is GamePhase.LOBBY
    assert query.answers[0] == (
        "Начать может только автор лобби.",
        {"show_alert": True},
    )


@pytest.mark.asyncio
async def test_join_is_answered_and_duplicate_is_friendly(monkeypatch):
    monkeypatch.setattr(
        game_handlers, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True)
    )
    ctx = context()
    creator = user(1, "Анна")
    message = FakeMessage(10, creator, "/alibi")
    await game_handlers.alibi(message_update(creator, message), ctx)
    game = ctx.chat_data["llm_games"][-100]

    joiner = user(2, "Борис")
    first_update, first_query = callback_update(
        joiner, f"game:alibi:{game.game_id}:join"
    )
    second_update, second_query = callback_update(
        joiner, f"game:alibi:{game.game_id}:join"
    )
    await game_handlers.game_callback(first_update, ctx)
    await game_handlers.game_callback(second_update, ctx)

    assert game.participants == (1, 2)
    assert first_query.answers[0][0] == "Ты в игре."
    assert "уже" in second_query.answers[0][0]


@pytest.mark.asyncio
async def test_reply_must_target_prompt_and_is_consumed_once():
    ctx = context()
    game = game_handlers.GameState("g1", "alibi", -100, 1, participants=[1, 2, 3])
    game.start(1)
    game.set_prompt_message(55)
    ctx.chat_data["llm_games"] = {-100: game}
    ctx.chat_data["llm_game_sessions"] = {
        "g1": {
            "players": {
                1: {"user_id": 1, "name": "Анна"},
                2: {"user_id": 2, "name": "Борис"},
                3: {"user_id": 3, "name": "Вера"},
            },
            "content": {"opening": {"incident": "i", "common_question": "q"}},
            "operation": None,
            "operation_token": None,
        }
    }
    wrong_reply = FakeMessage(54)
    right_reply = FakeMessage(55)

    wrong = FakeMessage(60, user(1, "Анна"), "Дома", wrong_reply)
    right = FakeMessage(61, user(1, "Анна"), "Дома", right_reply)

    assert (
        await game_handlers.process_game_reply(
            message_update(user(1, "Анна"), wrong), ctx
        )
        is False
    )
    assert (
        await game_handlers.process_game_reply(
            message_update(user(1, "Анна"), right), ctx
        )
        is True
    )
    assert game.submissions[GamePhase.ROUND_ONE] == {1: "Дома"}

    duplicate = FakeMessage(62, user(1, "Анна"), "Снова", right_reply)
    assert (
        await game_handlers.process_game_reply(
            message_update(user(1, "Анна"), duplicate), ctx
        )
        is True
    )
    assert game.submissions[GamePhase.ROUND_ONE] == {1: "Дома"}


@pytest.mark.asyncio
async def test_full_game_flow_advances_in_background(monkeypatch):
    monkeypatch.setattr(
        game_handlers, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True)
    )
    monkeypatch.setitem(SCENARIOS, "alibi", FakeScenario())
    ctx = context()
    creator = user(1, "Анна")
    message = FakeMessage(10, creator, "/alibi")
    await game_handlers.alibi(message_update(creator, message), ctx)
    game = ctx.chat_data["llm_games"][-100]

    for participant in (user(2, "Борис"), user(3, "Вера")):
        update, _query = callback_update(participant, f"game:alibi:{game.game_id}:join")
        await game_handlers.game_callback(update, ctx)

    update, _query = callback_update(creator, f"game:alibi:{game.game_id}:start")
    await game_handlers.game_callback(update, ctx)
    await ctx.application.wait_tasks()
    assert game.phase is GamePhase.ROUND_ONE
    assert game.current_prompt_message_id == 2000

    round_one_prompt = FakeMessage(game.current_prompt_message_id)
    for participant in (creator, user(2, "Борис"), user(3, "Вера")):
        move = FakeMessage(70 + participant.id, participant, "answer", round_one_prompt)
        assert await game_handlers.process_game_reply(
            message_update(participant, move), ctx
        )
    await ctx.application.wait_tasks()
    assert game.phase is GamePhase.ROUND_TWO
    assert game.current_prompt_message_id is not None

    round_two_prompt = FakeMessage(game.current_prompt_message_id)
    for participant in (creator, user(2, "Борис"), user(3, "Вера")):
        move = FakeMessage(80 + participant.id, participant, "answer", round_two_prompt)
        assert await game_handlers.process_game_reply(
            message_update(participant, move), ctx
        )
    await ctx.application.wait_tasks()

    assert ctx.chat_data["llm_games"] == {}
    assert any(sent.text == "winner=1" for _kwargs, sent in ctx.bot.sent)
