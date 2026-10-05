"""Explicit, bounded inputs for sequencing metadata; never filesystem commands."""

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .models import Identifier, Input

Label = Annotated[str, Field(min_length=1, max_length=200)]
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FastqPath = Annotated[str, Field(min_length=1, max_length=4096)]


def fastq_path(value):
    path = PurePosixPath(value)
    if (not value.startswith("/") or str(path) != value or ".." in path.parts
            or re.search(r"[\x00-\x1f\x7f]", value)
            or not value.lower().endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz"))):
        raise ValueError("Use a normalized absolute FASTQ path")
    return value


class CatalogStatistics(Input):
    reads: int = Field(ge=0)
    bases: int = Field(ge=0)
    q30_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    computed_at: str = Field(min_length=1, max_length=50)

    @field_validator("computed_at")
    @classmethod
    def timestamp(cls, value):
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("Statistics timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def nonempty_quality(self):
        if not self.bases and self.q30_percent is not None:
            raise ValueError("Empty files have no measured Q30")
        return self


class CatalogReport(Input):
    id: Label
    provider: str = Field(default="", max_length=200)
    report_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    received_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("report_date", "received_date")
    @classmethod
    def date(cls, value):
        if value is not None:
            datetime.strptime(value, "%Y-%m-%d")
        return value


class CatalogNewFile(Input):
    project: Label
    species: Label
    sample: Label
    data_type: Label
    node_id: Identifier
    path: FastqPath
    bytes: int = Field(ge=0)
    read_part: Literal["", "R1", "R2", "I1", "I2"] = ""
    tissue: str = Field(default="", max_length=200)
    condition: str = Field(default="", max_length=200)
    condition_description: str = Field(default="", max_length=500)
    replicate: str = Field(default="", max_length=80)
    replicate_status: Literal["unknown", "inferred", "confirmed"] = "unknown"
    match_status: Literal["inferred", "confirmed"] = "inferred"
    quality_group: Literal["", "pass", "fail"] = ""
    role: Literal["independent", "merged", "source"] = "independent"
    input_file_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    stats: CatalogStatistics | None = None
    reports: list[CatalogReport] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2000)

    _path = field_validator("path")(fastq_path)

    @field_validator("project", "species", "sample", "data_type")
    @classmethod
    def label(cls, value):
        if value != value.strip() or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Labels cannot contain outer whitespace or control characters")
        return value

    @model_validator(mode="after")
    def metadata_consistency(self):
        if self.replicate_status != "unknown" and not self.replicate:
            raise ValueError("Replicate evidence requires a replicate label")
        if self.input_file_ids and self.role != "merged":
            raise ValueError("Input relationships require an explicitly merged file")
        if len(self.input_file_ids) != len(set(self.input_file_ids)):
            raise ValueError("Duplicate input file IDs")
        return self


class CatalogWrite(Input):
    request_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    expected_revision: Revision
    dry_run: bool = True


class CatalogRegister(CatalogWrite):
    create_missing: bool = False
    files: list[CatalogNewFile] = Field(min_length=1, max_length=100)


class CatalogMove(Input):
    file_id: Identifier
    expected_path: FastqPath
    new_path: FastqPath
    node_id: Identifier
    observed_bytes: int = Field(ge=0)
    content_unchanged: Literal[True]

    _paths = field_validator("expected_path", "new_path")(fastq_path)


class CatalogRelocate(CatalogWrite):
    files: list[CatalogMove] = Field(min_length=1, max_length=100)
