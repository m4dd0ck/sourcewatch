"""Probe registry: one callable per ``probe.kind``."""

from collections.abc import Mapping

from sourcewatch.probes import open_meteo, parquet_remote, usgs
from sourcewatch.probes.base import Probe, ProbeContext

PROBES: Mapping[str, Probe] = {
    "parquet_remote": parquet_remote.probe,
    "open_meteo": open_meteo.probe,
    "usgs": usgs.probe,
}

__all__ = ["PROBES", "Probe", "ProbeContext"]
