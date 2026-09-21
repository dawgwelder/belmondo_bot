"""Server-owned slot rules: three independent reels with a weighted symbol strip."""
from collections import Counter
from fractions import Fraction

STAKES = (1, 3, 5)
COOLDOWN_SECONDS = 2
RULES_VERSION = "v2"
# id, emoji, name, payout for a triple (including the original stake), reel stops.
# Common symbols pay little and often; rare ones carry the big multipliers.
SYMBOLS = (
    ("file", "📁", "Досье", 2, 12),
    ("key", "🔑", "Ключ", 6, 5),
    ("radio", "📻", "Рация", 15, 3),
    ("case", "💼", "Дипломат", 50, 2),
    ("diamond", "💎", "Алмаз", 100, 1),
    ("spy", "🕵️", "Шпион", 150, 1),
)
PAIR_MULTIPLIER = 1
# One physical strip shared by all three reels: every symbol once, then the extra
# stops of the common symbols. The RNG picks a uniformly random stop on the strip.
REEL = tuple(key for key, *_ in SYMBOLS) + tuple(
    key for key, _, _, _, weight in SYMBOLS for _ in range(weight - 1)
)
TRIPLE_MULTIPLIERS = {key: value for key, _, _, value, _ in SYMBOLS}


def multiplier(symbols: tuple[str, str, str]) -> int:
    counts = Counter(symbols)
    if max(counts.values()) == 3:
        return TRIPLE_MULTIPLIERS[symbols[0]]
    return PAIR_MULTIPLIER if max(counts.values()) == 2 else 0


def symbol_probability(key: str) -> Fraction:
    return Fraction(REEL.count(key), len(REEL))


def return_to_player() -> Fraction:
    """Exact expected payout per unit of stake."""
    probabilities = [symbol_probability(key) for key, *_ in SYMBOLS]
    triples = sum(p**3 * TRIPLE_MULTIPLIERS[key] for p, (key, *_) in zip(probabilities, SYMBOLS))
    pairs = sum(3 * p * p * (1 - p) for p in probabilities) * PAIR_MULTIPLIER
    return triples + pairs


def rules_payload() -> dict:
    return {
        "version": RULES_VERSION,
        "stakes": STAKES,
        "agent_type": "informant",
        "cooldown_seconds": COOLDOWN_SECONDS,
        "rtp_percent": round(float(return_to_player()) * 100, 2),
        "reel_stops": len(REEL),
        "symbols": [
            {
                "id": key,
                "emoji": emoji,
                "name": name,
                "multiplier": amount,
                "weight": weight,
                "chance_percent": round(float(symbol_probability(key)) * 100, 2),
            }
            for key, emoji, name, amount, weight in SYMBOLS
        ],
        "pair_multiplier": PAIR_MULTIPLIER,
    }
