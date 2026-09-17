"""Generate a complete investigation; derive clues and answers from checked facts."""
from __future__ import annotations

import asyncio
import uuid

from config import logger
from games.llm import request_json
from .models import MoleCaseTemplate, MoleSuspect


class MoleCaseGenerator:
    def __init__(self, settings, rng, request=request_json):
        self.settings, self.rng, self.request = settings, rng, request

    @staticmethod
    def validate(payload):
        if not isinstance(payload, dict) or set(payload) != {
            "title",
            "briefing",
            "features",
            "required",
            "suspects",
        }:
            return None

        def text(value, limit):
            return (
                isinstance(value, str)
                and 1 <= len(value) <= limit
                and value == " ".join(value.split())
                and not any(c in value for c in "<>\n\r")
            )

        if not text(payload["title"], 80) or not text(payload["briefing"], 300):
            return None
        features, required, suspects = (
            payload["features"],
            payload["required"],
            payload["suspects"],
        )
        if (
            not isinstance(features, list)
            or len(features) != 3
            or not all(text(x, 45) for x in features)
            or len({feature.casefold() for feature in features}) != 3
        ):
            return None
        if (
            not isinstance(required, list)
            or len(required) != 3
            or not all(text(x, 70) for x in required)
        ):
            return None
        if not isinstance(suspects, list) or len(suspects) != 4:
            return None
        matches, names = [], []
        for suspect in suspects:
            if not isinstance(suspect, dict) or set(suspect) != {
                "codename",
                "role",
                "facts",
            }:
                return None
            if not text(suspect["codename"], 24) or not text(suspect["role"], 45):
                return None
            facts = suspect["facts"]
            if (
                not isinstance(facts, list)
                or len(facts) != 3
                or not all(text(x, 70) for x in facts)
            ):
                return None
            matches.append(
                tuple(
                    i for i in range(3) if facts[i].casefold() == required[i].casefold()
                )
            )
            names.append(suspect["codename"].casefold())
        # Every clue matters: removing any clue leaves a second plausible suspect.
        if len(set(names)) != 4 or set(matches) != {(0, 1, 2), (0, 1), (0, 2), (1, 2)}:
            return None
        return payload

    async def generate(self):
        if self.settings.llm_mole_enabled:
            prompt = """Создай новое шпионское расследование на русском языке, полностью в JSON.
Схема: {"title":"название", "briefing":"завязка без ответа", "features":["признак1","признак2","признак3"],
"required":["значение1","значение2","значение3"], "suspects":[{"codename":"позывной","role":"роль","facts":["значение1","значение2","значение3"]}]}.
Ровно четыре подозреваемых с разными позывными, три независимых признака.
У одного facts совпадают со всеми required. Каждый из остальных отличается ровно одним значением:
первый по признаку 1, второй по признаку 2, третий по признаку 3. Порядок подозреваемых произвольный.
Используй конкретные проверяемые детали (маршрут, доступ, след, время, предмет), создай связную историю.
features — названия признаков, facts — значения в том же порядке. Совпадающие значения пиши дословно одинаково.
В завязке нет дополнительных улик, обвинений или имени виновного. Никакой игровой механики, наград, HTML или Markdown.
Название до 80 символов, завязка до 300, признак до 45, значение до 70, позывной до 24, роль до 45.
"""
            try:
                payload = await asyncio.wait_for(
                    self.request(
                        prompt,
                        self.validate,
                        corrective_hint="Соблюдай схему и матрицу совпадений: 3, 2, 2, 2; все три улики необходимы.",
                    ),
                    timeout=self.settings.llm_mole_timeout_seconds,
                )
                if self.validate(payload) is not None:
                    return self.to_case(payload)
            except Exception as error:
                logger.warning(
                    "spy_mole: generation fallback reason=%s", type(error).__name__
                )
            else:
                logger.warning("spy_mole: generation fallback reason=invalid_case")
        return self.settings.mole_cases[
            self.rng.randint(0, len(self.settings.mole_cases) - 1)
        ]

    def to_case(self, payload):
        suspects = list(payload["suspects"])
        for index in range(len(suspects) - 1, 0, -1):
            other = self.rng.randint(0, index)
            suspects[index], suspects[other] = suspects[other], suspects[index]
        features = payload["features"]
        return MoleCaseTemplate(
            id=f"generated_{uuid.uuid4().hex}",
            version=1,
            title=payload["title"],
            briefing=payload["briefing"]
            + " Все факты в досье проверены. Крот соответствует всем трём уликам.",
            clues=tuple(
                f"{key}: {value}." for key, value in zip(features, payload["required"])
            ),
            suspects=tuple(
                MoleSuspect(
                    id=f"s{index + 1}",
                    codename=suspect["codename"],
                    role=suspect["role"],
                    dossier="; ".join(
                        f"{key}: {value}"
                        for key, value in zip(features, suspect["facts"])
                    )
                    + ".",
                    evidence_tags=tuple(
                        f"{i}:{fact.casefold()}"
                        for i, fact in enumerate(suspect["facts"])
                    ),
                )
                for index, suspect in enumerate(suspects)
            ),
            solution_tags=tuple(
                f"{i}:{fact.casefold()}" for i, fact in enumerate(payload["required"])
            ),
        )
