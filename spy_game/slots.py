"""Server-owned slot rules: three independent, uniformly distributed reels."""
from collections import Counter

STAKES = (1, 3, 5)
COOLDOWN_SECONDS = 2
RULES_VERSION = "v1"
# id, emoji, name, payout for a triple (including the original stake).
SYMBOLS = (
    ("file", "📁", "Досье", 5),
    ("key", "🔑", "Ключ", 8),
    ("radio", "📻", "Рация", 12),
    ("case", "💼", "Дипломат", 16),
    ("diamond", "💎", "Алмаз", 24),
    ("spy", "🕵️", "Шпион", 40),
)


def multiplier(symbols: tuple[str, str, str]) -> int:
    counts = Counter(symbols)
    if max(counts.values()) == 3:
        return next(value for key, _, _, value in SYMBOLS if key == symbols[0])
    return 1 if max(counts.values()) == 2 else 0


def rules_payload() -> dict:
    # 90 ordered pairs return ×1; six triples pay 105 in total out of 216 outcomes.
    return {
        "version": RULES_VERSION,
        "stakes": STAKES,
        "agent_type": "informant",
        "cooldown_seconds": COOLDOWN_SECONDS,
        "rtp_percent": round((90 + sum(s[3] for s in SYMBOLS)) / 216 * 100, 2),
        "symbols": [
            {"id": key, "emoji": emoji, "name": name, "multiplier": amount}
            for key, emoji, name, amount in SYMBOLS
        ],
        "pair_multiplier": 1,
    }
