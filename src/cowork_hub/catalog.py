"""Read-only sequencing catalog. Private snapshots never enter the static website."""

import json
import math
from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import Depends, Query

from .models import Error

SortBy = Literal["default", "file", "sample", "species", "project", "data_type", "reads", "bases", "q30"]
SortOrder = Literal["asc", "desc"]


def can_view_catalog(config, principal):
    return bool(config and (config.catalog_access == "approved" or principal["user_id"] in config.admin_users))


class Catalog:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.lock = Lock()
        self.version = None
        self.data = None

    def load(self):
        if not self.path:
            return {"version": 1, "updated_at": None, "projects": [], "samples": [], "files": [], "unassigned_files": 0}
        with self.lock:
            try:
                info = self.path.stat()
                version = (info.st_mtime_ns, info.st_size)
                if version != self.version:
                    data = json.loads(self.path.read_text())
                    if data.get("version") != 1 or not all(isinstance(data.get(k), list) for k in ("projects", "samples", "files")):
                        raise ValueError("Unsupported catalog")
                    self.data, self.version = data, version
                return self.data
            except (OSError, ValueError, TypeError):
                raise Error("CATALOG_UNAVAILABLE", "Catalog is temporarily unavailable", 503) from None


class FileGroups:
    """Resolve recorded inputs within the same sample and data type only."""

    def __init__(self, files):
        lookup = {(r["sample_id"], r["dataset_id"], r["path"]): r for r in files}
        self.inputs = {}
        nested = set()
        for row in files:
            links, seen = [], {row["path"]}
            for link in row.get("inputs", []):
                if link["path"] in seen:
                    continue
                seen.add(link["path"])
                child = lookup.get((row["sample_id"], row["dataset_id"], link["path"]))
                links.append((link, child))
                if child:
                    nested.add(child["id"])
            self.inputs[row["id"]] = links
        self.roots = [row for row in files if row["id"] not in nested]
        covered = {r["id"] for row in self.roots for r in self.walk(row)}
        # A malformed cycle must not make its files disappear from the library.
        for row in files:
            if row["id"] not in covered:
                self.roots.append(row)
                covered.update(r["id"] for r in self.walk(row))

    def walk(self, row):
        pending, seen = [row], set()
        while pending:
            current = pending.pop()
            if current["id"] in seen:
                continue
            seen.add(current["id"])
            yield current
            pending.extend(child for _, child in self.inputs[current["id"]] if child)

    def expand(self, row, ancestors=frozenset()):
        ancestors = ancestors | {row["id"]}
        inputs = []
        for link, child in self.inputs[row["id"]]:
            if child and child["id"] in ancestors:
                continue
            inputs.append({**link, "file": self.expand(child, ancestors) if child else None})
        return {**row, "inputs": inputs}


def mount_catalog(app, config, user, *, catalog=None, prefix="/v1/web/catalog"):
    catalog = catalog if catalog is not None else Catalog(config.catalog_path if config else None)

    def reader(principal=Depends(user)):
        if not can_view_catalog(config, principal):
            raise Error("FORBIDDEN", "Catalog access is required", 403)
        return principal

    @app.get(prefix)
    def overview(principal=Depends(reader)):
        data = catalog.load()
        return {k: data[k] for k in ("version", "updated_at", "projects", "samples", "unassigned_files", "revision") if k in data}

    def file_page(data, sample_ids, dataset, q, role, page, page_size, grouped=False, view="all", sort_by="default", sort_order="asc"):
        needle = q.casefold().strip()
        projects = {p["id"]: p["name"] for p in data["projects"]}
        samples = {s["id"]: s for s in data["samples"]}
        labels = {}
        for sample in data["samples"]:
            if sample["id"] in sample_ids:
                labels[sample["id"]] = " ".join((sample.get("name", ""), sample.get("species", ""),
                                                    projects.get(sample.get("project_id"), ""))).casefold()
        datasets = {d["id"]: d["name"].casefold() for s in data["samples"] for d in s.get("datasets", [])}
        rows = [r for r in data["files"] if r["sample_id"] in sample_ids and (not dataset or r["dataset_id"] == dataset)]
        groups = FileGroups(rows) if grouped else None

        def matches(row):
            return (needle in row["path"].casefold() or needle in labels[row["sample_id"]]
                    or needle in datasets.get(row["dataset_id"], "")
                    or grouped and any(needle in link["path"].casefold() for link in row.get("inputs", [])))

        rows = [r for r in (groups.roots if groups else rows) if (role == "all" or role in r["roles"])
                and (not needle or any(matches(child) for child in (groups.walk(r) if groups else [r])))]
        retained = [r for r in rows if r.get("display_group") == "retained_original"]
        if view == "library":
            rows = [r for r in rows if r.get("display_group") != "retained_original"]
        elif view == "originals":
            rows = retained
        if sort_by == "default":
            rows.sort(key=lambda r: ("representative" not in r["roles"], "merged" not in r["roles"], r["path"], r["id"]))
        else:
            # Stable tie-breaks keep equal values from moving between pages.
            rows.sort(key=lambda r: (r["path"].casefold(), r["id"]))

            def value(row):
                sample = samples[row["sample_id"]]
                if sort_by == "file":
                    return (row.get("name") or Path(row["path"]).name).casefold()
                if sort_by in ("sample", "species"):
                    return sample["name" if sort_by == "sample" else "species"].casefold()
                if sort_by == "project":
                    return projects[sample["project_id"]].casefold()
                if sort_by == "data_type":
                    return datasets.get(row["dataset_id"], "")
                stats = row.get("stats") or {}
                number = stats.get("q30_percent" if sort_by == "q30" else sort_by)
                if stats.get("status") != "completed":
                    return None
                if sort_by == "q30":
                    bases = stats.get("bases")
                    return number if (type(number) in (int, float) and math.isfinite(number)
                                      and 0 <= number <= 100 and type(bases) is int and bases > 0) else None
                return number if type(number) is int and number >= 0 else None

            known, missing = [], []
            for row in rows:
                sort_value = value(row)
                if sort_value is None:
                    missing.append(row)
                else:
                    known.append((sort_value, row))
            known.sort(key=lambda entry: entry[0], reverse=sort_order == "desc")
            rows = [row for _, row in known] + missing
        start = (page - 1) * page_size
        items = rows[start:start + page_size]
        result = {"items": [groups.expand(r) for r in items] if groups else items,
                  "total": len(rows), "page": page, "page_size": page_size}
        if "revision" in data:
            result["revision"] = data["revision"]
        if view == "library":
            counts = Counter((r["sample_id"], r["dataset_id"]) for r in retained)
            result["original_groups"] = [{"sample_id": sid, "dataset_id": did, "count": count}
                                         for (sid, did), count in sorted(counts.items())]
        return result

    @app.get(prefix + "/files")
    def all_files(
        project: str = Query("", max_length=80),
        species: str = Query("", max_length=80),
        sample: str = Query("", max_length=80),
        dataset: str = Query("", max_length=80),
        q: str = Query("", max_length=200),
        role: str = Query("all", pattern="^(all|representative|merged|source)$"),
        grouped: bool = Query(False),
        view: str = Query("all", pattern="^(all|library|originals)$"),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=100),
        sort_by: SortBy = "default",
        sort_order: SortOrder = "asc",
        principal=Depends(reader),
    ):
        data = catalog.load()
        projects = [p for p in data["projects"] if not project or p["id"] == project]
        species_ids = {s["id"] for p in projects for s in p["species"] if not species or s["id"] == species}
        samples = [s for s in data["samples"] if s["project_id"] in {p["id"] for p in projects}
                   and s["species_id"] in species_ids and (not sample or s["id"] == sample)]
        if (project and not projects or species and not species_ids or sample and not samples
                or dataset and not any(d["id"] == dataset for s in samples for d in s["datasets"])):
            raise Error("NOT_FOUND", "Selection is not in the catalog", 404)
        return file_page(data, {s["id"] for s in samples}, dataset, q, role, page, page_size, grouped, view, sort_by, sort_order)

    @app.get(prefix + "/samples/{sample_id}/files")
    def files(
        sample_id: str,
        dataset: str = Query("", max_length=80),
        q: str = Query("", max_length=200),
        role: str = Query("all", pattern="^(all|representative|merged|source)$"),
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=100),
        sort_by: SortBy = "default",
        sort_order: SortOrder = "asc",
        principal=Depends(reader),
    ):
        data = catalog.load()
        if not any(s["id"] == sample_id for s in data["samples"]):
            raise Error("NOT_FOUND", "Sample is not in the catalog", 404)
        return file_page(data, {sample_id}, dataset, q, role, page, page_size, sort_by=sort_by, sort_order=sort_order)
