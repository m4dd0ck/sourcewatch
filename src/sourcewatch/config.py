"""Source definitions: one YAML per source under ``sources/``, validated strictly on load."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from sourcewatch.errors import ConfigError
from sourcewatch.observation import SchemaField
from sourcewatch.types import Severity

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CadenceKind = Literal["daily", "business_days", "monthly", "continuous"]


class Duration(BaseModel):
    """A span given in whole days and hours."""

    model_config = ConfigDict(extra="forbid")

    days: int = 0
    hours: int = 0

    def to_timedelta(self) -> timedelta:
        return timedelta(days=self.days, hours=self.hours)

    @property
    def total_hours(self) -> float:
        return self.days * 24 + self.hours

    @property
    def total_days(self) -> float:
        return self.days + self.hours / 24


def _validate_tz(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone {tz!r}") from exc
    return tz


class CadenceConfig(BaseModel):
    """How often the source is expected to move, and how late is too late."""

    model_config = ConfigDict(extra="forbid")

    kind: CadenceKind
    tz: str = "UTC"
    ok_within: Duration
    fail_after: Duration

    @field_validator("tz")
    @classmethod
    def _tz(cls, value: str) -> str:
        return _validate_tz(value)

    @model_validator(mode="after")
    def _ordered(self) -> "CadenceConfig":
        if self.fail_after.to_timedelta() < self.ok_within.to_timedelta():
            raise ValueError("fail_after must not be shorter than ok_within")
        return self


class VolumeConfig(BaseModel):
    """Band around the rolling median that today's count must fall in."""

    model_config = ConfigDict(extra="forbid")

    metric: str = "count"
    band: tuple[float, float] = (0.5, 2.0)
    weekday_aware: bool = False
    min_samples: int = 7
    window: int = 28

    @model_validator(mode="after")
    def _band(self) -> "VolumeConfig":
        low, high = self.band
        if not (0 < low < 1 < high):
            raise ValueError("band must be (low < 1 < high) with low > 0")
        if self.min_samples < 1 or self.window < self.min_samples:
            raise ValueError("window must be at least min_samples, and min_samples at least 1")
        return self


class SchemaConfig(BaseModel):
    """Where the accepted schema lives and how hard drift counts."""

    model_config = ConfigDict(extra="forbid")

    expected_file: str | None = None
    severity: Severity = Severity.WARNING


class ParquetRemoteProbe(BaseModel):
    """Monthly Parquet files on a public bucket, probed by HEAD and footer only."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["parquet_remote"]
    base_url: str
    pattern: str
    lookback_months: int = 4
    tz: str = "UTC"

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, value: str) -> str:
        if "{yyyy}" not in value or "{mm}" not in value:
            raise ValueError("pattern needs {yyyy} and {mm} placeholders")
        return value

    @field_validator("tz")
    @classmethod
    def _tz(cls, value: str) -> str:
        return _validate_tz(value)


class CfpbProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cfpb"]
    endpoint: str
    sample_size: int = 200
    count_lag_days: int = 2


class TreasuryProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["treasury"]
    endpoint: str
    page_size: int = 50


class OpenMeteoProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["open_meteo"]
    endpoint: str
    latitude: float
    longitude: float
    tz: str
    daily: list[str]
    hourly: list[str]
    days: int = 10

    @field_validator("tz")
    @classmethod
    def _tz(cls, value: str) -> str:
        return _validate_tz(value)


class SocrataProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["socrata"]
    domain: str
    dataset_id: str
    timestamp_field: str
    tz: str
    app_token_env: str = "SOCRATA_APP_TOKEN"
    sample_size: int = 200
    count_lag_days: int = 2

    @field_validator("tz")
    @classmethod
    def _tz(cls, value: str) -> str:
        return _validate_tz(value)


class UsgsProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["usgs"]
    feed_url: str


ProbeConfig = Annotated[
    ParquetRemoteProbe | CfpbProbe | TreasuryProbe | OpenMeteoProbe | SocrataProbe | UsgsProbe,
    Field(discriminator="kind"),
]


class SourceConfig(BaseModel):
    """One monitored source, as written in ``sources/<id>.yaml``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    name: str
    url: str
    description: str = ""
    probe: ProbeConfig
    cadence: CadenceConfig
    volume: VolumeConfig = Field(default_factory=VolumeConfig)
    schema_: SchemaConfig = Field(default_factory=SchemaConfig, alias="schema")
    table_checks: list[dict[str, Any]] = Field(default_factory=list)
    request_budget: int = 6

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG.match(value):
            raise ValueError("id must be a lowercase slug (a-z, 0-9, -)")
        return value

    @property
    def schema_file(self) -> str:
        return self.schema_.expected_file or f"{self.id}.schema.json"


class SchemaFile(BaseModel):
    """The accepted schema for a source, written by ``accept-schema``."""

    model_config = ConfigDict(extra="forbid")

    fields: list[SchemaField]
    accepted_at: datetime
    fingerprint: str


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: invalid source definition"]
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  {where}: {err['msg']}")
    return "\n".join(lines)


def load_source(path: Path) -> SourceConfig:
    """Load and validate one source YAML.

    Raises:
        ConfigError: if the file is not valid YAML or not a valid source definition.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    try:
        return SourceConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def load_sources(directory: Path) -> list[SourceConfig]:
    """Load every ``*.yaml`` under ``directory``, sorted by id.

    Raises:
        ConfigError: on any invalid file, a duplicate id, or a filename that does not match its id.
    """
    if not directory.is_dir():
        raise ConfigError(f"sources directory not found: {directory}")
    sources: dict[str, SourceConfig] = {}
    for path in sorted(directory.glob("*.yaml")):
        source = load_source(path)
        if path.stem != source.id:
            raise ConfigError(f"{path}: filename must match id {source.id!r}")
        if source.id in sources:
            raise ConfigError(f"{path}: duplicate source id {source.id!r}")
        sources[source.id] = source
    if not sources:
        raise ConfigError(f"no source definitions found in {directory}")
    return [sources[key] for key in sorted(sources)]


def load_expected_schema(directory: Path, source: SourceConfig) -> SchemaFile | None:
    """Read ``sources/<id>.schema.json`` if it exists.

    Raises:
        ConfigError: if the file exists but is not a valid schema file.
    """
    path = directory / source.schema_file
    if not path.exists():
        return None
    try:
        return SchemaFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ConfigError(f"{path}: invalid schema file: {exc}") from exc


def write_expected_schema(directory: Path, source: SourceConfig, schema: SchemaFile) -> Path:
    """Write the accepted schema sidecar and return its path."""
    path = directory / source.schema_file
    path.write_text(schema.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
