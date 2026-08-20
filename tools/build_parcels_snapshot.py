"""Build the bounded Spartanburg residential parcel snapshot.

The county's CAMA service is authoritative and, unlike the 2021 ArcGIS Online mirror,
answers from this build environment.  It contains 181,531 parcels county-wide, so this
script deliberately does *not* commit a full county Parquet blob every night.  Binary
history makes every refresh permanent repository weight, even when the only changes are
assessor corrections.

The buyer profile names Spartanburg and anchors the commute at Spartanburg Medical Center.
The committed snapshot therefore contains residential parcels whose county `City` field is
SPARTANBURG.  This is a useful offline candidate index for the actual search area, not a
county archive.  The city/residential predicate also keeps the committed artifact well
under 10 MB.  A future county-wide product should publish a versioned release asset (or use
Git LFS) rather than commit a fresh binary file nightly; it must not quietly change this
scope because the downloader happened to work.

School attendance zone is intentionally absent.  CAMA_Parcels has no school field and the
county GIS catalog currently exposes no public school-attendance-zone layer, so a spatial
join would be a guess about a boundary source.  The metadata records that omission.

Usage:
    python tools/build_parcels_snapshot.py
    python tools/build_parcels_snapshot.py --check

`--check` is offline.  It verifies the committed artifact and calls a stale snapshot stale;
it does not make a county outage a CI failure.  A scheduled workflow treats an extraction
failure as degradation and preserves the prior known-good artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from analyzer.sources import http  # noqa: E402

LAYER_URL = (
    "https://maps.spartanburgcounty.org/server/rest/services/GIS/"
    "CAMA_Parcels/FeatureServer/0"
)
QUERY_URL = f"{LAYER_URL}/query"
SOURCE_DOCUMENTATION = "https://www.spartanburgcounty.org/172/Assessor"
LAYER_NAME = "CAMA Parcels"

PARQUET = REPO / "data" / "parcels.parquet"
META = REPO / "data" / "parcels.meta.json"
CHECKPOINT = REPO / "data" / ".parcels.checkpoint.json"

# One request at a time is intentional.  Pagination is available, but this is a public
# county service and a nightly refresh has no user-facing latency target.  Keeping the
# in-flight bound at one makes retries and resumption predictable and does not hammer it.
MAX_IN_FLIGHT = 1
PAGE_SIZE = 1_000  # service advertises maxRecordCount 2,000; leave headroom for responses.
MAX_AGE = timedelta(hours=36)
USER_AGENT = "home-buying-dashboard-parcels/0.1 (+https://github.com/flux1618/home-buying-dashboard)"

# See module docstring.  SQL uses only verified live fields and keeps the binary small.
SCOPE_WHERE = "City = 'SPARTANBURG' AND LandUse LIKE 'RESIDENTIAL%'"

# Required source fields, not desired display labels.  Validate these against layer metadata
# before starting: a renamed assessed field must stop the job, not yield plausible nulls.
REQUIRED_FIELDS = (
    "OBJECTID",
    "MAPNUMBER",  # county's map/parcel identifier; live layer does not expose legacy TAXPIN
    "District",
    "City",
    "LandUse",
    "CurrentAssessedLandValue",
    "CurrentAssessedBuildingValue",
    "SaleDate",
    "SaleAmount",
)
OUT_FIELDS = ",".join(REQUIRED_FIELDS)
OUTPUT_FIELDS = (
    "tax_pin",
    "assessed_value",
    "tax_district",
    "last_sale_date",
    "last_sale_amount",
)


class SnapshotError(RuntimeError):
    """The extractor refuses to publish a partial or schema-drifted snapshot."""


Fetch = Callable[[str], dict[str, Any]]


def fetch_json(url: str) -> dict[str, Any]:
    """Use the shared stdlib adapter: retries/backoff plus an explicit project UA."""
    payload = http.get_json(
        url,
        cache=False,
        headers={"User-Agent": USER_AGENT},
    ).data
    if not isinstance(payload, dict):
        raise SnapshotError(f"county response was {type(payload).__name__}, not a JSON object")
    if "error" in payload:
        error = payload["error"]
        raise SnapshotError(f"county ArcGIS error: {error.get('message', error)!s}")
    return payload


def _url(base: str, params: dict[str, Any]) -> str:
    return http.build_url(base, params)


def _metadata(fetch: Fetch) -> dict[str, Any]:
    metadata = fetch(_url(LAYER_URL, {"f": "json"}))
    advanced = metadata.get("advancedQueryCapabilities") or {}
    if not advanced.get("supportsPagination"):
        raise SnapshotError("CAMA_Parcels does not advertise supportsPagination")
    if not metadata.get("maxRecordCount"):
        raise SnapshotError("CAMA_Parcels metadata did not provide maxRecordCount")
    names = {field.get("name") for field in metadata.get("fields", [])}
    missing = [field for field in REQUIRED_FIELDS if field not in names]
    if missing:
        raise SnapshotError(
            "CAMA_Parcels field(s) disappeared: " + ", ".join(missing)
        )
    return metadata


def _checkpoint_load() -> dict[str, Any] | None:
    try:
        saved = json.loads(CHECKPOINT.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    # A changed scope or API schema is a different run.  Never blend it with old pages.
    if saved.get("scope_where") != SCOPE_WHERE or saved.get("output_fields") != list(OUTPUT_FIELDS):
        return None
    return saved


def _checkpoint_save(
    metadata: dict[str, Any], rows: list[dict[str, Any]], next_offset: int, failed_pages: list[dict[str, Any]]
) -> None:
    CHECKPOINT.write_text(
        json.dumps(
            {
                "scope_where": SCOPE_WHERE,
                "output_fields": list(OUTPUT_FIELDS),
                "layer_fields": [f.get("name") for f in metadata.get("fields", [])],
                "rows": rows,
                "next_offset": next_offset,
                "failed_pages": failed_pages,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _number(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _sale_date(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        # ArcGIS date fields are epoch milliseconds.  Store ISO dates, not local-midnight
        # timestamps, so the artifact cannot change date when viewed in another timezone.
        return datetime.fromtimestamp(float(raw) / 1_000, UTC).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def shape(attrs: dict[str, Any]) -> dict[str, Any]:
    land = _number(attrs.get("CurrentAssessedLandValue"))
    building = _number(attrs.get("CurrentAssessedBuildingValue"))
    assessed = None if land is None and building is None else round((land or 0.0) + (building or 0.0), 2)
    return {
        "tax_pin": attrs.get("MAPNUMBER"),
        "assessed_value": assessed,
        "tax_district": attrs.get("District"),
        "last_sale_date": _sale_date(attrs.get("SaleDate")),
        "last_sale_amount": _number(attrs.get("SaleAmount")),
    }


def extract(fetch: Fetch = fetch_json) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch every scoped page, checkpointing after each one for safe resumption."""
    metadata = _metadata(fetch)
    resumed = _checkpoint_load()
    rows = list(resumed.get("rows", [])) if resumed else []
    offset = int(resumed.get("next_offset", 0)) if resumed else 0
    failed_pages: list[dict[str, Any]] = []

    count_payload = fetch(_url(QUERY_URL, {"where": SCOPE_WHERE, "returnCountOnly": "true", "f": "json"}))
    expected = count_payload.get("count")
    if not isinstance(expected, int):
        raise SnapshotError("county count query did not return an integer `count`")
    if expected == 0:
        return metadata, [], failed_pages

    while offset < expected:
        params = {
            "where": SCOPE_WHERE,
            "outFields": OUT_FIELDS,
            "returnGeometry": "false",
            "orderByFields": "OBJECTID ASC",
            "resultOffset": offset,
            "resultRecordCount": min(PAGE_SIZE, expected - offset),
            "f": "json",
        }
        try:
            payload = fetch(_url(QUERY_URL, params))
            features = payload.get("features")
            if not isinstance(features, list):
                raise SnapshotError("county page did not include a feature list")
            if not features:
                raise SnapshotError(f"county page at offset {offset} was unexpectedly empty")
            page_rows = [shape(feature.get("attributes") or {}) for feature in features]
        except Exception as exc:
            failure = {"offset": offset, "limit": min(PAGE_SIZE, expected - offset), "error": str(exc)}
            failed_pages.append(failure)
            _checkpoint_save(metadata, rows, offset, failed_pages)
            raise SnapshotError(f"parcel extraction stopped at page offset {offset}: {exc}") from exc

        rows.extend(page_rows)
        offset += len(page_rows)
        _checkpoint_save(metadata, rows, offset, failed_pages)
        if len(page_rows) < min(PAGE_SIZE, expected - (offset - len(page_rows))):
            # Pagination should return full pages until the last one.  A short non-final
            # page means a changing server or query problem; do not publish a partial file.
            if offset < expected:
                raise SnapshotError(f"county returned a short page at offset {offset - len(page_rows)}")

    if len(rows) != expected:
        raise SnapshotError(f"county count was {expected}, but extraction shaped {len(rows)} rows")
    return metadata, rows, failed_pages


def _require_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # core remains stdlib-only; this tool owns the optional extra.
        raise SnapshotError(
            "pyarrow is required to write parcels.parquet; install the planned `.[parcels]` extra"
        ) from exc
    return pa, pq


def _content_sha256(rows: list[dict[str, Any]]) -> str:
    # The data hash deliberately excludes retrieval time and Parquet writer details.  A
    # timestamp-only commit is still a new binary blob in Git history and is not a data
    # change.  This is what lets the workflow commit only assessor changes.
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_snapshot(metadata: dict[str, Any], rows: list[dict[str, Any]], failed_pages: list[dict[str, Any]]) -> bool:
    content_sha256 = _content_sha256(rows)
    try:
        existing = json.loads(META.read_text())
    except (OSError, json.JSONDecodeError):
        existing = {}
    if PARQUET.exists() and existing.get("content_sha256") == content_sha256:
        # Keep the old metadata timestamp because it truthfully describes the committed
        # data artifact.  The schedule's log shows a successful no-change check instead.
        CHECKPOINT.unlink(missing_ok=True)
        return False

    pa, pq = _require_pyarrow()
    table = pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                pa.field("tax_pin", pa.string()),
                pa.field("assessed_value", pa.float64()),
                pa.field("tax_district", pa.string()),
                pa.field("last_sale_date", pa.string()),
                pa.field("last_sale_amount", pa.float64()),
            ]
        ),
    )
    temp_parquet = PARQUET.with_suffix(".parquet.tmp")
    pq.write_table(table, temp_parquet, compression="zstd")
    temp_parquet.replace(PARQUET)

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    meta = {
        "source_url": QUERY_URL,
        "source_documentation_url": SOURCE_DOCUMENTATION,
        "layer_name": metadata.get("name"),
        "service_version": metadata.get("currentVersion"),
        "fetched_at": now,
        "row_count": len(rows),
        "content_sha256": content_sha256,
        "field_list": list(OUTPUT_FIELDS),
        "source_field_list": [field.get("name") for field in metadata.get("fields", [])],
        "scope_where": SCOPE_WHERE,
        "scope_note": "Spartanburg city residential parcels; not a county-wide archive.",
        "field_notes": {
            "tax_pin": "MAPNUMBER (the live layer has no TAXPIN field; GISParcelNumber is available separately but is not used here)",
            "assessed_value": "CurrentAssessedLandValue + CurrentAssessedBuildingValue",
            "tax_district": "District",
            "last_sale_date": "SaleDate, converted from ArcGIS epoch milliseconds to ISO date",
            "last_sale_amount": "SaleAmount",
            "school_attendance_zone": "not included: no verified CAMA field or public county attendance-zone layer",
        },
        "pagination": {
            "supports_pagination": bool((metadata.get("advancedQueryCapabilities") or {}).get("supportsPagination")),
            "max_record_count": metadata.get("maxRecordCount"),
            "page_size": PAGE_SIZE,
            "max_in_flight": MAX_IN_FLIGHT,
        },
        "failed_pages": failed_pages,
        "status": "complete",
    }
    META.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    # Successful output supersedes the durable resume state.  A failed run leaves it intact.
    CHECKPOINT.unlink(missing_ok=True)
    return True


def check(now: datetime | None = None) -> int:
    """Offline CI check: structural correctness and an explicit freshness boundary."""
    if not PARQUET.exists() or not META.exists():
        print("parcel snapshot or metadata is missing; run tools/build_parcels_snapshot.py", file=sys.stderr)
        return 1
    try:
        meta = json.loads(META.read_text())
        fetched = datetime.fromisoformat(meta["fetched_at"].replace("Z", "+00:00"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"parcel metadata is unreadable: {exc}", file=sys.stderr)
        return 1
    problems: list[str] = []
    if meta.get("status") != "complete":
        problems.append("snapshot metadata is not complete")
    if meta.get("field_list") != list(OUTPUT_FIELDS):
        problems.append("snapshot fields do not match the bounded parcel contract")
    if not isinstance(meta.get("row_count"), int) or meta["row_count"] < 0:
        problems.append("snapshot row count is missing or invalid")
    if meta.get("failed_pages"):
        problems.append("snapshot records failed pages")
    age = (now or datetime.now(UTC)) - fetched
    if age > MAX_AGE:
        problems.append(f"snapshot is stale ({age.days} days old; maximum is {MAX_AGE})")
    if problems:
        print("parcel snapshot check failed: " + "; ".join(problems), file=sys.stderr)
        return 1
    print(f"parcel snapshot: {meta['row_count']} rows, fetched {meta['fetched_at']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed snapshot offline")
    args = parser.parse_args()
    if args.check:
        return check()
    try:
        metadata, rows, failed_pages = extract()
        changed = write_snapshot(metadata, rows, failed_pages)
    except (SnapshotError, http.SourceUnavailable, http.SourceRejected) as exc:
        print(f"parcel snapshot degraded: {exc}", file=sys.stderr)
        return 2
    action = "wrote" if changed else "left unchanged"
    print(f"{action} {PARQUET.relative_to(REPO)} with {len(rows)} scoped parcels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
