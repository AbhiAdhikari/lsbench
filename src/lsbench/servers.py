"""servers.yaml — servers under test and distractors, pinned.

This is published work about other people's code. Every entry carries an exact
version, the date it was tested, and whether the maintainer has been told.
`roles` maps the benchmark's abstract tool roles (summary, search, sample, ...)
to that server's actual tool names, so routing tasks can be written once and
scored fairly against servers that name things differently. The mapping is
public and reviewable — if a maintainer disputes it, that goes in `notes`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from provmcp.config import DownstreamServer
from pydantic import BaseModel, Field

SERVERS_PATH = Path(__file__).resolve().parent / "servers.yaml"


class ServerSpec(BaseModel):
    id: str
    name: str = ""
    domain: str = ""                                  # which tasks/<domain>.yaml applies
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    repo: str = ""
    version_tested: str = ""
    date_tested: str = ""
    maintainer_notified: bool = False
    maintainer_response: str = ""
    source: str | None = None                         # provmcp release resolver
    source_options: dict[str, Any] = Field(default_factory=dict)
    roles: dict[str, list[str]] = Field(default_factory=dict)
    notes: str = ""
    inproc: Any = None                                # tests only

    def tools_for_role(self, role: str) -> list[str]:
        return self.roles.get(role, [])

    def missing_env(self) -> list[str]:
        """`${VAR}` references in env that the runner's environment does not define."""
        return sorted({v for val in self.env.values() for v in re.findall(r"\$\{(\w+)\}", val)
                       if not os.environ.get(v)})

    def to_downstream(self) -> DownstreamServer:
        missing = self.missing_env()
        if missing:
            raise RuntimeError(f"server {self.id!r} needs environment variables {missing} "
                               "(see its notes in servers.yaml)")
        env = {k: os.path.expandvars(v) for k, v in self.env.items()}
        return DownstreamServer(
            id=self.id, command=self.command, url=self.url, env=env,
            source=self.source, source_options=self.source_options, inproc=self.inproc,
        )

    def pinned(self) -> list[str]:
        """Fairness problems that block publishing scores for this server."""
        problems = []
        if not self.version_tested or "TODO" in self.version_tested:
            problems.append("version_tested not pinned")
        if not self.date_tested or "TODO" in self.date_tested:
            problems.append("date_tested missing")
        if any("TODO" in c for c in (self.command or [])):
            problems.append("command has TODO")
        if self.command and not any(ch in " ".join(self.command) for ch in ("==", "@")):
            problems.append("command does not pin a version (no == or @)")
        return problems


class ServerRegistry(BaseModel):
    servers: list[ServerSpec]
    distractors: list[ServerSpec] = Field(default_factory=list)

    def get(self, server_id: str) -> ServerSpec:
        for s in [*self.servers, *self.distractors]:
            if s.id == server_id:
                return s
        raise KeyError(f"unknown server {server_id!r}; known: {[s.id for s in self.servers]}")

    def distractors_for(self, server: ServerSpec) -> list[ServerSpec]:
        """Everything that isn't the server under test and isn't in its domain
        (a same-domain server would be a competitor, not a distractor)."""
        return [d for d in self.distractors if d.id != server.id and d.domain != server.domain]


def load_registry(path: Path = SERVERS_PATH) -> ServerRegistry:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ServerRegistry.model_validate(raw)
