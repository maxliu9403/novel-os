"""Strict, public input contracts for isolated Scribe-stage experiments."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelSnapshot(Record):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    connection_id: str = Field(min_length=1, max_length=128)
    base_url: str
    configured_base_url: str
    reasoning_effort: str
    max_tokens: int = Field(ge=256, le=32000)
    timeout_seconds: int = Field(ge=30, le=1800)
    azure_endpoint: str = ""
    azure_api_version: str = ""

    @model_validator(mode="after")
    def endpoints(self):
        for url in (self.base_url, self.configured_base_url, self.azure_endpoint):
            parts = urlsplit(url)
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("model endpoints must not contain credentials or query parameters")
            if url and (parts.scheme not in {"http", "https"} or not parts.netloc):
                raise ValueError("invalid model endpoint")
        if self.provider != "codex" and not self.base_url:
            raise ValueError("API experiments require an explicit frozen effective endpoint")
        if self.provider == "azure" and not (self.azure_endpoint and self.azure_api_version):
            raise ValueError("Azure experiments require endpoint and API version")
        return self


class Chapter(Record):
    number: int = Field(ge=1, le=1000)
    outline: str = Field(min_length=1, max_length=12000)
    min_words: int = Field(ge=1, le=5000)
    max_words: int = Field(ge=1, le=6000)

    @model_validator(mode="after")
    def word_range(self):
        if self.min_words > self.max_words:
            raise ValueError("word range is reversed")
        return self


class Case(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    kind: Literal["free_trial_window", "volume_transition"]
    free_trial_end: int | None
    volume_boundary_after: int | None = Field(default=None, ge=1, le=999)
    language: Literal["en"]
    audience: str = Field(min_length=1, max_length=1000)
    facts: str = Field(min_length=1, max_length=16000)
    prior_context: str = Field(max_length=24000)
    rules: list[str] = Field(min_length=1, max_length=3)
    chapters: list[Chapter] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def scope(self):
        numbers = [c.number for c in self.chapters]
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise ValueError("experiment chapters must be ordered and contiguous")
        if self.kind == "free_trial_window":
            if self.volume_boundary_after is not None or self.free_trial_end not in {3, 4} or numbers != list(range(1, self.free_trial_end + 1)):
                raise ValueError("free-window experiment must contain the complete approved 3/4 chapter window")
        elif (self.free_trial_end is not None or not self.prior_context.strip()
              or self.volume_boundary_after not in numbers or self.volume_boundary_after + 1 not in numbers):
            raise ValueError("volume probe needs prior context and both sides of an explicit volume boundary")
        if len(set(self.rules)) != len(self.rules):
            raise ValueError("duplicate experiment rule")
        return self


class Specification(Record):
    schema_version: Literal["novel-method-experiment.v1"]
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    seed: int = Field(ge=0, le=2147483647)
    repetitions: int = Field(ge=1, le=3)
    model: ModelSnapshot
    input_max_chars: int = Field(default=64000, ge=1000, le=128000)
    cases: list[Case] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("duplicate experiment case")
        return self
