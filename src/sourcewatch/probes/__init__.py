"""Probe registry: one callable per ``probe.kind``."""

from collections.abc import Mapping

from sourcewatch.probes import cfpb, open_meteo, parquet_remote, socrata, treasury, usgs
from sourcewatch.probes.base import Probe, ProbeContext

PROBES: Mapping[str, Probe] = {
    "parquet_remote": parquet_remote.probe,
    "cfpb": cfpb.probe,
    "treasury": treasury.probe,
    "open_meteo": open_meteo.probe,
    "socrata": socrata.probe,
    "usgs": usgs.probe,
}

__all__ = ["PROBES", "Probe", "ProbeContext"]
