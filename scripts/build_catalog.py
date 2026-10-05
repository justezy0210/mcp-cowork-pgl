#!/usr/bin/env python3
"""Export curated CSVs as a private, read-only web catalog snapshot (no FASTQ I/O)."""
import argparse
import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path


def identifier(*parts):
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_statistics(path):
    """Read the atomic SeqKit CSV export, without opening any sequencing files."""
    statistics = {}
    if path is None:
        return statistics
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"path", "bytes", "status", "num_seqs", "sum_len", "computed_at"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("SeqKit CSV is missing required columns")
        for row in reader:
            status = row["status"] if row["status"] in {"completed", "pending", "failed"} else "unavailable"
            reads = bases = q30 = None
            if status == "completed":
                try:
                    reads, bases = int(row["num_seqs"]), int(row["sum_len"])
                    if reads < 0 or bases < 0:
                        raise ValueError("Negative count")
                except (TypeError, ValueError):
                    status, reads, bases = "unavailable", None, None
                if status == "completed" and bases > 0 and row.get("format", "FASTQ") == "FASTQ":
                    try:
                        value = float(row.get("Q30(%)", ""))
                        if math.isfinite(value) and 0 <= value <= 100:
                            q30 = value
                    except (TypeError, ValueError):
                        pass
            statistics[row["path"]] = {
                "bytes": int(row["bytes"]), "status": status,
                "reads": reads, "bases": bases, "q30_percent": q30, "computed_at": row["computed_at"] or None,
            }
    return statistics


def file_statistics(statistics, path, size):
    stats = statistics.get(path)
    if stats is None:
        return {"status": "pending", "reads": None, "bases": None, "q30_percent": None, "computed_at": None}
    if stats["bytes"] != size:
        return {"status": "unavailable", "reads": None, "bases": None, "q30_percent": None, "computed_at": None}
    return {key: stats[key] for key in ("status", "reads", "bases", "q30_percent", "computed_at")}


def availability(rows, name):
    """Summarize registered data; file counts are never biological replicates."""
    eligible = [row for row in rows if row["pass_fail"] != "fail"
                and row["scope_status"] != "pending_green_rice_non_ont_scope"]
    if not eligible or name == "Unclassified":
        return {"status": "needs_review", "groups": []}
    groups = defaultdict(list)
    if name == "RNA-seq":
        for row in eligible:
            groups[(row["tissue"], row.get("condition", ""))].append(row)
    result = []
    for (tissue, condition), members in sorted(groups.items()):
        labels = {row["replicate_label"].strip() for row in members if row["replicate_label"].strip()}
        labels = {str(int(label)) if label.isdecimal() else label for label in labels}
        complete = all(row["replicate_label"].strip() for row in members)
        confirmed = complete and all(row.get("replicate_status") == "user_confirmed" for row in members)
        result.append({"tissue": tissue, "condition": condition,
                       "condition_description": next((row.get("condition_description", "") for row in members if row.get("condition_description")), ""),
                       "replicate_count": len(labels) if labels and complete else None,
                       "replicate_status": "confirmed" if confirmed else "inferred" if labels and complete else "unknown"})
    return {"status": "present", "groups": result}


def build(source, stats_path=None):
    statistics = read_statistics(stats_path)
    registry = read(source / "samples.csv")
    organized = source / "file-organization"
    summary = json.loads((organized / "summary.json").read_text())
    source_files = read(organized / "sample-files.csv")
    excluded = {row["path"] for row in source_files if row.get("catalog_exclusion") == "run_id_chunk"}
    projects, samples, datasets = {}, {}, defaultdict(dict)
    for row in registry:
        project, species, name = row["project"], row["species"], row["sample_id"]
        pid, spid, sid = identifier(project), identifier(project, species), identifier(project, name)
        projects.setdefault(pid, {"id": pid, "name": project, "species": {}})
        projects[pid]["species"].setdefault(spid, {"id": spid, "name": species})
        samples[(project, name)] = {"id": sid, "name": name, "project_id": pid, "species_id": spid, "species": species, "identity_status": "confirmed", "datasets": []}

    def dataset(sid, name):
        key = identifier(sid, name)
        return datasets[sid].setdefault(key, {"id": key, "name": name or "Unclassified", "file_count": 0, "representative_count": 0, "status": "inferred", "pending_scope": False})

    for row in read(source / "sample-data-types.csv"):
        sid = samples[(row["project"], row["sample_id"])]["id"]
        dataset(sid, row["data_type"])["status"] = "confirmed" if row["availability_status"] == "user_confirmed" else "provisional"

    relations = defaultdict(list)
    for row in read(organized / "file-relationships.csv"):
        relations[row["output_path"]].append({"path": row["input_path"], "status": "confirmed" if row["status"] == "user_confirmed" else "inferred"})
    reports = {r["report_id"]: r for r in read(source / "reports.csv")}
    files = []
    dataset_rows = defaultdict(list)
    for row in source_files:
        if not row["sample_id"] or row["path"] in excluded:
            continue
        sample = samples[(row["project"], row["sample_id"])]
        group = dataset(sample["id"], row["data_type"] or "Unclassified")
        dataset_rows[group["id"]].append(row)
        group["file_count"] += 1
        group["pending_scope"] |= row["scope_status"] == "pending_green_rice_non_ont_scope"
        if row["data_type_status"] in ("user_confirmed", "user_confirmed_group"):
            group["status"] = "confirmed"
        roles = []
        if row["representative_status"] == "user_confirmed":
            roles.append("representative")
            group["representative_count"] += 1
        if row["path"] in relations:
            roles.append("merged")
        if "delivery_file_candidate" in row["path_roles"] or "run_chunk" in row["path_roles"]:
            roles.append("source")
        file_reports = [{"id": rid, "provider": reports[rid]["provider"], "report_date": reports[rid]["report_date"], "received_date": reports[rid]["received_date"] or None} for rid in row["report_ids"].split(";") if rid in reports]
        files.append({"id": identifier(row["path"]), "sample_id": sample["id"], "dataset_id": group["id"], "name": Path(row["path"]).name, "path": row["path"], "bytes": int(row["inventory_bytes"]), "stats": file_statistics(statistics, row["path"], int(row["inventory_bytes"])), "roles": roles, "representative_candidate": row["representative_status"] == "organized_location_candidate", "match_status": "confirmed" if row["match_status"].startswith(("user_confirmed", "confirmed_")) else "inferred", "read_part": row["read_part"], "tissue": row["tissue"], "replicate": row["replicate_label"], "condition": row.get("condition", ""), "condition_description": row.get("condition_description", ""), "replicate_status": "confirmed" if row.get("replicate_status") == "user_confirmed" else "inferred" if row["replicate_label"] else "unknown", "pending_scope": row["scope_status"] == "pending_green_rice_non_ont_scope", "quality_group": row["pass_fail"], "older_version": "older_version_path" in row["path_roles"], "reports": file_reports, "inputs": [link for link in relations[row["path"]] if link["path"] not in excluded]})
        if row.get("display_group") == "retained_original":
            files[-1]["display_group"] = "retained_original"
    for sample in samples.values():
        sample["datasets"] = sorted(datasets[sample["id"]].values(), key=lambda d: d["name"])
        for group in sample["datasets"]:
            group["availability"] = availability(dataset_rows[group["id"]], group["name"])
    for project in projects.values():
        project["species"] = list(project["species"].values())
    return {"version": 1, "updated_at": summary["created_at"], "projects": list(projects.values()), "samples": list(samples.values()), "files": files, "unassigned_files": sum(not row["sample_id"] and row["path"] not in excluded for row in source_files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stats", type=Path, help="SeqKit file-stats.csv export; only exact paths with matching stored sizes are linked")
    args = parser.parse_args()
    data = build(args.source, args.stats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, args.output)
    print(json.dumps({"projects": len(data["projects"]), "samples": len(data["samples"]), "files": len(data["files"])}))
