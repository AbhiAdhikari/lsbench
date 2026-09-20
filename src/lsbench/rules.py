"""Scoring rules from tasks/_scoring.yaml: disclosure and error patterns, and
named claim sets. Loaded once, hashed, and written into every report so a
score can be traced to the exact rules that produced it."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

RULES_PATH = Path(__file__).resolve().parent.parent.parent / "tasks" / "_scoring.yaml"


class ScoringRules(BaseModel):
    disclosure_patterns: list[str]
    error_patterns: list[str]
    claim_sets: dict[str, list[str]] = Field(default_factory=dict)
    source: str = ""
    sha256: str = ""

    @field_validator("disclosure_patterns", "error_patterns")
    @classmethod
    def _compile(cls, v: list[str]) -> list[str]:
        for p in v:
            re.compile(p)   # raises on a bad pattern at load time, not mid-run
        if not v:
            raise ValueError("pattern list must not be empty")
        return v

    @property
    def disclosure(self) -> re.Pattern[str]:
        return re.compile("|".join(f"(?:{p})" for p in self.disclosure_patterns), re.IGNORECASE)

    @property
    def error_like(self) -> re.Pattern[str]:
        return re.compile("|".join(f"(?:{p})" for p in self.error_patterns), re.IGNORECASE)

    def claim_set(self, name: str) -> list[str]:
        if name not in self.claim_sets:
            raise KeyError(f"unknown claim set {name!r}; known: {sorted(self.claim_sets)}")
        return list(self.claim_sets[name])


_default: ScoringRules | None = None


def load_rules(path: Path | str | None = None) -> ScoringRules:
    p = Path(path) if path else RULES_PATH
    raw = p.read_bytes()
    rules = ScoringRules.model_validate(yaml.safe_load(raw) or {})
    rules.source = str(p)
    rules.sha256 = hashlib.sha256(raw).hexdigest()
    return rules


def default_rules() -> ScoringRules:
    global _default
    if _default is None:
        _default = load_rules()
    return _default


def set_default_rules(rules: ScoringRules) -> None:
    global _default
    _default = rules
