"""Immutable repository and source content identities."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import SHA256_PATTERN


class SourceVersion(BaseModel):
    """A repository source at one content digest; its version ID is not that digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1)
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    commit: str | None = None

    @classmethod
    def from_content(cls, source: str, content: str, commit: str | None = None) -> "SourceVersion":
        """Create a version using the same newline normalization as document ingest."""
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return cls(source=source, content_sha256=digest, commit=commit)


class RepositorySnapshot(BaseModel):
    """A repository state with its own identity and pinned source versions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID = Field(default_factory=uuid4)
    repository: str = Field(min_length=1)
    commit: str = Field(min_length=1)
    source_versions: tuple[SourceVersion, ...] = ()
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _unique_sources(self) -> "RepositorySnapshot":
        sources = [version.source for version in self.source_versions]
        if len(sources) != len(set(sources)):
            raise ValueError("a repository snapshot may contain only one version per source")
        return self
