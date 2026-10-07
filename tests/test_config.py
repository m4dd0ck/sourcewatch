from pathlib import Path

import pytest

from sourcewatch.config import SourceConfig, load_source, load_sources
from sourcewatch.errors import ConfigError
from tests.conftest import SOURCES_DIR

MINIMAL = """
id: {id}
name: Example
url: https://example.org
probe:
  kind: usgs
  feed_url: https://example.org/feed.geojson
cadence:
  kind: continuous
  ok_within: {{hours: 6}}
  fail_after: {{hours: 24}}
"""


def test_the_six_real_definitions_load_and_are_sorted():
    sources = load_sources(SOURCES_DIR)
    assert [s.id for s in sources] == [
        "cfpb-complaints",
        "nyc-311",
        "nyc-tlc",
        "open-meteo-archive",
        "treasury-dts",
        "usgs-earthquakes",
    ]
    kinds = {s.id: s.probe.kind for s in sources}
    assert kinds["nyc-tlc"] == "parquet_remote"
    assert kinds["treasury-dts"] == "treasury"
    assert all(s.request_budget >= 1 for s in sources)


def test_defaults_and_schema_file_name(tmp_path: Path):
    path = tmp_path / "example.yaml"
    path.write_text(MINIMAL.format(id="example"))
    source = load_source(path)
    assert isinstance(source, SourceConfig)
    assert source.volume.band == (0.5, 2.0)
    assert source.schema_file == "example.schema.json"
    assert source.table_checks == []


@pytest.mark.parametrize(
    ("replacement", "fragment"),
    [
        (
            "id: example",
            "id: Not A Slug",
        ),
        ("kind: continuous", "kind: weekly"),
        ("ok_within: {hours: 6}", "ok_within: {hours: 48}"),
        ("name: Example", "name: Example\nunexpected: 1"),
    ],
)
def test_invalid_definitions_raise_config_error(tmp_path: Path, replacement: str, fragment: str):
    text = MINIMAL.format(id="example").replace(replacement, fragment)
    path = tmp_path / "example.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError) as exc:
        load_source(path)
    assert "example.yaml" in str(exc.value)


def test_filename_must_match_id_and_ids_are_unique(tmp_path: Path):
    (tmp_path / "wrong.yaml").write_text(MINIMAL.format(id="example"))
    with pytest.raises(ConfigError, match="filename must match"):
        load_sources(tmp_path)


def test_bad_yaml_and_bad_timezone(tmp_path: Path):
    path = tmp_path / "example.yaml"
    path.write_text("id: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_source(path)
    text = MINIMAL.format(id="example").replace(
        "kind: continuous", "kind: continuous\n  tz: Mars/Olympus"
    )
    path.write_text(text)
    with pytest.raises(ConfigError, match="unknown timezone"):
        load_source(path)


def test_empty_directory_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="no source definitions"):
        load_sources(tmp_path)
