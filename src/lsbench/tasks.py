"""Task definitions, loaded from tasks/<domain>.yaml.

A task is scored on exactly one dimension. Correctness and recovery tasks carry
claims; routing tasks carry an expected first tool *role* (resolved per server
through servers.yaml, because servers name their tools differently); budget tasks
carry a response cap and whether truncation must be disclosed.

Ground truth is a first-class field, not a comment. `ground_truth.verified`
gates whether the task is scored at all: an unverified task never contributes
to a published number. A wrong expected answer makes the suite worse than no
suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from .dimensions import Dimension
from .rules import ScoringRules, default_rules

TASKS_DIR = Path(__file__).resolve().parent.parent.parent / "tasks"


class Claim(BaseModel):
    id: str
    kind: Literal["exact", "numeric", "contains", "any", "absent", "fuzzy"]
    expected: object
    tolerance: float = 0.0

    @property
    def fuzzy(self) -> bool:
        return self.kind == "fuzzy"


class GroundTruth(BaseModel):
    verified: bool = False
    method: str = ""          # how it was checked, e.g. "NCBI esummary db=gds, 2026-09-20"
    date: str = ""
    release: str = ""         # source release the truth was checked against
    values: dict = Field(default_factory=dict)


class Task(BaseModel):
    id: str
    dimension: Dimension
    prompt: str
    domain: str = ""
    notes: str = ""
    claims: list[Claim] = Field(default_factory=list)
    ground_truth: GroundTruth = Field(default_factory=GroundTruth)

    # routing
    expect_first_role: str | None = None      # role name, mapped per server in servers.yaml
    expect_server: str | None = None          # a distractor id: the server under test must NOT go first

    # budget
    max_response_tokens: int | None = None
    expect_truncation_disclosed: bool | None = None

    # recovery
    expect_error: bool = True                 # the trajectory must contain a surfaced tool error
    expect_retry_without: str | None = None   # a later call must omit this substring from its args

    @model_validator(mode="after")
    def _shape(self) -> Task:
        d = self.dimension
        if d is Dimension.ROUTING and not (self.expect_first_role or self.expect_server):
            raise ValueError(f"{self.id}: routing task needs expect_first_role or expect_server")
        if d is Dimension.BUDGET and self.max_response_tokens is None:
            raise ValueError(f"{self.id}: budget task needs max_response_tokens")
        if d in (Dimension.CORRECTNESS,) and not self.claims:
            raise ValueError(f"{self.id}: correctness task needs claims")
        if d in (Dimension.LATENCY, Dimension.COST):
            raise ValueError(f"{self.id}: latency and cost are measured on every task, not authored")
        return self

    @property
    def fuzzy_fraction(self) -> float:
        if not self.claims:
            return 0.0
        return sum(c.fuzzy for c in self.claims) / len(self.claims)


def _resolve_sets(item: dict, rules: ScoringRules) -> dict:
    """`expected: {set: not_found}` -> the list from _scoring.yaml."""
    for claim in item.get("claims", []):
        exp = claim.get("expected")
        if isinstance(exp, dict) and set(exp) == {"set"}:
            claim["expected"] = rules.claim_set(exp["set"])
    return item


def load_tasks(
    domain: str | None = None,
    dimension: str | Dimension | None = None,
    *,
    include_unverified: bool = False,
    tasks_dir: Path = TASKS_DIR,
    rules: ScoringRules | None = None,
) -> list[Task]:
    rules = rules or default_rules()
    tasks: list[Task] = []
    for path in sorted(Path(tasks_dir).glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        dom = path.stem
        if domain and dom != domain:
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for item in raw:
            item.setdefault("domain", dom)
            tasks.append(Task.model_validate(_resolve_sets(item, rules)))

    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate task ids: {sorted(dupes)}")

    if dimension:
        dim = Dimension(dimension)
        tasks = [t for t in tasks if t.dimension is dim]
    if not include_unverified:
        tasks = [t for t in tasks if t.ground_truth.verified]
    return tasks
