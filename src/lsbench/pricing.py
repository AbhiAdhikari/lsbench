"""Estimated USD per run from token classes. Prices are the public Anthropic
API list (per million tokens) as of 2026-06; cache writes bill at 1.25x input,
cache reads at 0.1x. An estimate, not an invoice — the report labels it so."""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {   # model prefix -> (input $/M, output $/M)
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_usd(model: str, *, input_tokens: int, cache_write: int, cache_read: int,
                 output_tokens: int) -> float | None:
    for prefix, (inp, out) in PRICES.items():
        if model.startswith(prefix):
            return (input_tokens * inp + cache_write * inp * 1.25 + cache_read * inp * 0.1
                    + output_tokens * out) / 1_000_000
    return None
