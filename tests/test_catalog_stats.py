import csv
import importlib.util
import json

import pytest
from pathlib import Path

from cowork_hub.catalog import FileGroups

SPEC = importlib.util.spec_from_file_location("build_catalog", Path(__file__).resolve().parents[1] / "scripts/build_catalog.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_statistics_preserve_zero_and_reject_missing_failed_or_mismatched_results(tmp_path):
    fields = ["path", "bytes", "status", "num_seqs", "sum_len", "computed_at"]
    cases = [
        ("/one/reads.fq", "completed", "3", "18"),
        ("/two/reads.fq", "completed", "7", "70"),
        ("/empty.fq", "completed", "0", "0"),
        ("/pending.fq", "pending", "999", "999"),
        ("/failed.fq", "failed", "999", "999"),
        ("/invalid.fq", "completed", "invalid", "18"),
        ("/negative.fq", "completed", "-1", "18"),
    ]
    path = tmp_path / "file-stats.csv"
    write_csv(path, fields, [dict(zip(fields, [name, 100, status, reads, bases, "2026-10-03T13:00:00Z"], strict=True)) for name, status, reads, bases in cases])
    statistics = builder.read_statistics(path)
    assert builder.file_statistics(statistics, "/one/reads.fq", 100)["reads"] == 3
    assert builder.file_statistics(statistics, "/two/reads.fq", 100)["bases"] == 70
    assert builder.file_statistics(statistics, "/empty.fq", 100)["reads"] == 0
    assert builder.file_statistics(statistics, "/empty.fq", 100)["bases"] == 0
    for name, status in [("pending", "pending"), ("failed", "failed"), ("invalid", "unavailable"), ("negative", "unavailable"), ("missing", "pending")]:
        result = builder.file_statistics(statistics, f"/{name}.fq", 100)
        assert result["status"] == status
        assert result["reads"] is None and result["bases"] is None
    assert builder.file_statistics(statistics, "/one/reads.fq", 101)["status"] == "unavailable"
    assert builder.file_statistics(statistics, "reads.fq", 100)["reads"] is None


def test_export_attaches_per_file_counts_without_summing_pending_merge(tmp_path):
    source = tmp_path / "catalog"
    organized = source / "file-organization"
    write_csv(source / "samples.csv", ["project", "species", "sample_id"], [{"project": "Study", "species": "Oryza sativa", "sample_id": "one"}])
    write_csv(source / "sample-data-types.csv", ["project", "sample_id", "data_type", "availability_status"], [])
    write_csv(source / "reports.csv", ["report_id"], [])
    write_csv(organized / "file-relationships.csv", ["output_path", "input_path", "status"], [{"output_path": "/merged.fq", "input_path": "/raw.fq", "status": "user_confirmed"}])
    (organized / "summary.json").write_text(json.dumps({"created_at": "2026-10-03", "unresolved_paths": 0}))
    fields = ["project", "sample_id", "path", "data_type", "inventory_bytes", "scope_status", "data_type_status", "representative_status", "path_roles", "report_ids", "match_status", "read_part", "tissue", "replicate_label", "pass_fail"]
    fields.append("display_group")
    rows = [{**dict.fromkeys(fields, ""), "project": "Study", "sample_id": "one", "path": name, "data_type": "HiFi", "inventory_bytes": "100"} for name in ["/raw.fq", "/merged.fq", "/fail.fq"]]
    rows[-1]["display_group"] = "retained_original"
    write_csv(organized / "sample-files.csv", fields, rows)
    stats_path = tmp_path / "stats.csv"
    write_csv(stats_path, ["path", "bytes", "status", "num_seqs", "sum_len", "computed_at"], [
        {"path": "/raw.fq", "bytes": 100, "status": "completed", "num_seqs": 12, "sum_len": 180, "computed_at": "2026-10-03T13:00:00Z"},
        {"path": "/merged.fq", "bytes": 100, "status": "pending", "num_seqs": "", "sum_len": "", "computed_at": ""},
    ])
    catalog = builder.build(source, stats_path)
    assert catalog["samples"][0]["datasets"][0]["availability"] == {"status": "present", "groups": []}
    assert catalog["files"][-1]["display_group"] == "retained_original"
    assert "display_group" not in catalog["files"][0]
    groups = FileGroups(catalog["files"])
    merged = groups.expand(groups.roots[0])
    assert merged["path"] == "/merged.fq"
    assert merged["stats"]["status"] == "pending" and merged["stats"]["reads"] is None
    assert merged["inputs"][0]["file"]["stats"] == {"status": "completed", "reads": 12, "bases": 180, "q30_percent": None, "computed_at": "2026-10-03T13:00:00Z"}

    # Explicit catalog exclusions remove both table rows and nested links while
    # retaining merged identity and the private provenance CSVs for later audits.
    fields.append("catalog_exclusion")
    for row in rows:
        row["catalog_exclusion"] = "run_id_chunk" if row["path"] == "/raw.fq" else ""
    rows.extend([
        {**rows[0], "sample_id": "", "path": "/unassigned-chunk.fq"},
        {**rows[0], "sample_id": "", "path": "/unassigned-library.fq", "catalog_exclusion": ""},
    ])
    write_csv(organized / "sample-files.csv", fields, rows)
    provenance = (organized / "file-relationships.csv").read_bytes()
    reduced = builder.build(source, stats_path)
    assert [f["path"] for f in reduced["files"]] == ["/merged.fq", "/fail.fq"]
    assert reduced["files"][0]["inputs"] == []
    assert reduced["files"][0]["roles"] == ["merged"]
    assert reduced["files"][0]["stats"] == merged["stats"]
    assert reduced["samples"][0]["datasets"][0]["file_count"] == 2
    assert reduced["unassigned_files"] == 1
    assert (organized / "file-relationships.csv").read_bytes() == provenance


@pytest.mark.parametrize("quality,expected", [
    ("0", 0), ("100", 100), ("96.25", 96.25), ("", None), ("-", None),
    ("nan", None), ("inf", None), ("-1", None), ("100.01", None),
])
def test_q30_percentage_is_optional_finite_and_within_range(tmp_path, quality, expected):
    path = tmp_path / "stats.csv"
    row = {"path": "/reads.fq", "bytes": 100, "status": "completed", "num_seqs": 5,
           "sum_len": 100, "computed_at": "2026-10-05T03:59:25Z", "Q30(%)": quality}
    write_csv(path, list(row), [row])
    stats = builder.read_statistics(path)
    result = builder.file_statistics(stats, row["path"], 100)
    assert result["q30_percent"] == expected
    assert result["reads"] == 5 and result["bases"] == 100
    assert builder.file_statistics(stats, row["path"], 101)["q30_percent"] is None


@pytest.mark.parametrize("status,reads,bases", [
    ("pending", 5, 100), ("failed", 5, 100), ("completed", 0, 0), ("completed", "invalid", 100),
])
def test_q30_not_imported_from_incomplete_invalid_or_empty_files(tmp_path, status, reads, bases):
    path = tmp_path / "stats.csv"
    row = {"path": "/reads.fq", "bytes": 100, "status": status, "num_seqs": reads,
           "sum_len": bases, "computed_at": "", "Q30(%)": "99"}
    write_csv(path, list(row), [row])
    assert builder.file_statistics(builder.read_statistics(path), row["path"], 100)["q30_percent"] is None


def test_fasta_zero_quality_placeholder_is_not_a_measured_q30(tmp_path):
    path = tmp_path / "stats.csv"
    row = {"path": "/reads.fq", "bytes": 100, "status": "completed", "num_seqs": 5,
           "sum_len": 100, "computed_at": "", "Q30(%)": "0", "format": "FASTA"}
    write_csv(path, list(row), [row])
    result = builder.file_statistics(builder.read_statistics(path), row["path"], 100)
    assert result["q30_percent"] is None and result["reads"] == 5


def test_availability_counts_labels_per_tissue_and_condition_not_files():
    rows = []
    for tissue, condition, confirmed in [("stem", "", False), ("flower", "", False), ("", "Up", True), ("", "Down", True)]:
        for replicate in ["1", "2", "3"]:
            # Paired reads, two lanes and a merged copy still describe one label.
            for copy in range(6):
                rows.append({"tissue": tissue, "condition": condition, "replicate_label": replicate.zfill(2) if copy == 0 else replicate,
                             "replicate_status": "user_confirmed" if confirmed else "", "pass_fail": "pass", "scope_status": "in_scope"})
    result = builder.availability(rows, "RNA-seq")
    assert result["status"] == "present"
    assert len(result["groups"]) == 4
    assert all(group["replicate_count"] == 3 for group in result["groups"])
    assert {(g["tissue"], g["condition"], g["replicate_status"]) for g in result["groups"]} == {
        ("stem", "", "inferred"), ("flower", "", "inferred"), ("", "Up", "confirmed"), ("", "Down", "confirmed")}


def test_availability_does_not_invent_replicates_or_confirm_unreviewed_data():
    row = {"tissue": "stem", "condition": "", "replicate_label": "1", "pass_fail": "pass", "scope_status": "in_scope"}
    result = builder.availability([row, {**row, "replicate_label": ""}], "RNA-seq")
    assert result["groups"][0]["replicate_count"] is None
    assert result["groups"][0]["replicate_status"] == "unknown"
    assert builder.availability([], "ONT")["status"] == "needs_review"
    assert builder.availability([row], "Unclassified")["status"] == "needs_review"
    pending = {**row, "scope_status": "pending_green_rice_non_ont_scope"}
    failed = {**row, "pass_fail": "fail"}
    assert builder.availability([pending, failed], "RNA-seq") == {"status": "needs_review", "groups": []}
    assert builder.availability([pending, failed, row], "RNA-seq")["groups"][0]["replicate_count"] == 1
