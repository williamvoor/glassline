"""dataload: load CSV, Excel and Parquet files into Postgres as they are (dataload/SPEC.md)."""

from dataload.load import LoadError, TableResult, run
from dataload.manifest import Manifest, ManifestError, Source, load_manifest, parse_manifest

__all__ = ["LoadError", "Manifest", "ManifestError", "Source", "TableResult", "load_manifest", "parse_manifest", "run"]
