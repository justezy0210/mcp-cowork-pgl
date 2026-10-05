"""Transactional catalog metadata with a read-only legacy snapshot fallback."""

import copy
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import PurePosixPath

from .catalog import Catalog
from .models import Error
from .service import digest, encode, new_id


def identifier(*parts):
    return digest("\0".join(parts))[:16]


class CatalogStore:
    def __init__(self, hub, config):
        self.hub, self.config = hub, config
        self.legacy = Catalog(config.catalog_path if config else None)

    def _read(self, db):
        import json

        row = db.execute("SELECT revision,payload FROM catalog_state WHERE id=1").fetchone()
        if row:
            return json.loads(row["payload"]), row["revision"]
        data = copy.deepcopy(self.legacy.load())
        return data, digest(encode(data))

    def load(self):
        with self.hub.store.transaction(write=False) as db:
            data, revision = self._read(db)
        return {**data, "revision": revision}

    def file(self, file_id):
        data = self.load()
        row = next((f for f in data["files"] if f["id"] == file_id), None)
        if row is None:
            raise Error("NOT_FOUND", "Catalog file not found", 404)
        return {"file": row, "revision": data["revision"]}

    def history(self, file_id, limit):
        import json

        self.file(file_id)
        with self.hub.store.transaction(write=False) as db:
            rows = db.execute(
                "SELECT id,actor,action,before_value,after_value,created_at FROM catalog_events "
                "WHERE file_id=? ORDER BY created_at DESC,id DESC LIMIT ?", (file_id, limit),
            ).fetchall()
        return {"items": [{**dict(r), "before_value": json.loads(r["before_value"]),
                           "after_value": json.loads(r["after_value"])} for r in rows]}

    def _writer(self, db, user_id):
        principal = db.execute("SELECT role,enabled FROM principals WHERE id=?", (user_id,)).fetchone()
        if (not self.config or not principal or principal["role"] != "user" or not principal["enabled"]
                or self.config.catalog_access != "approved" and user_id not in self.config.admin_users
                or self.config.catalog_write_access != "approved" and user_id not in self.config.admin_users):
            raise Error("FORBIDDEN", "Catalog write access is required", 403)

    def write(self, user_id, operation, request):
        import json

        request_hash = digest(encode({"operation": operation, **request.model_dump(exclude={"dry_run"})}))
        with self.hub.store.transaction(write=not request.dry_run) as db:
            self._writer(db, user_id)
            prior = db.execute("SELECT request_hash,result FROM catalog_requests WHERE user_id=? AND request_key=?",
                               (user_id, request.request_key)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise Error("IDEMPOTENCY_CONFLICT", "Request key was used with different content", 409)
                return {**json.loads(prior["result"]), "replayed": True}
            data, revision = self._read(db)
            if request.expected_revision != revision:
                raise Error("CATALOG_CONFLICT", "Catalog changed; read and preview the current revision", 409)
            for item in request.files:
                if not db.execute("SELECT 1 FROM nodes WHERE id=? AND enabled=1", (item.node_id,)).fetchone():
                    raise Error("NOT_FOUND", "Storage server is not registered or enabled", 404)
            changes, created = (self._register(data, user_id, request) if operation == "register"
                                else self._relocate(data, request))
            result = {"applied": not request.dry_run, "request_key": request.request_key,
                      "previous_revision": revision, "changes": changes, "created": created,
                      "raw_files_modified": False}
            if request.dry_run:
                return {**result, "revision": revision}
            now = self.hub.clock()
            data["updated_at"] = datetime.fromtimestamp(now, UTC).isoformat()
            payload = encode(data)
            result["revision"] = digest(payload)
            db.execute("INSERT INTO catalog_state(id,revision,payload,updated_at) VALUES(1,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at",
                       (result["revision"], payload, now))
            db.execute("INSERT INTO catalog_requests(user_id,request_key,request_hash,result,created_at) VALUES(?,?,?,?,?)",
                       (user_id, request.request_key, request_hash, encode(result), now))
            for change in changes:
                db.execute("INSERT INTO catalog_events(id,file_id,actor,action,before_value,after_value,request_key,created_at) "
                           "VALUES(?,?,?,?,?,?,?,?)",
                           (new_id(), change["file_id"], user_id, operation, encode(change["before"]),
                            encode(change["after"]), request.request_key, now))
            return result

    @staticmethod
    def _named(rows, name):
        matches = [r for r in rows if r["name"].casefold() == name.casefold()]
        if len(matches) > 1:
            raise Error("CATALOG_AMBIGUOUS", "Multiple catalog records match this name", 409)
        return matches[0] if matches else None

    def _register(self, data, user_id, request):
        paths = {r["path"] for r in data["files"]}
        by_id = {r["id"]: r for r in data["files"]}
        changes, created, affected = [], [], set()
        for item in request.files:
            if item.path in paths:
                raise Error("CATALOG_PATH_EXISTS", "This absolute path is already registered", 409)
            paths.add(item.path)
            project = self._named(data["projects"], item.project)
            if project is None:
                if not request.create_missing:
                    raise Error("NOT_FOUND", "Project missing; explicitly allow creation", 404)
                project = {"id": identifier(item.project), "name": item.project, "species": []}
                data["projects"].append(project)
                created.append({"kind": "project", "id": project["id"], "name": item.project})
            species = self._named(project["species"], item.species)
            if species is None:
                if not request.create_missing:
                    raise Error("NOT_FOUND", "Species missing; explicitly allow creation", 404)
                species = {"id": identifier(project["name"], item.species), "name": item.species}
                project["species"].append(species)
                created.append({"kind": "species", **species})
            sample = self._named([s for s in data["samples"] if s["project_id"] == project["id"]], item.sample)
            if sample and sample["species_id"] != species["id"]:
                raise Error("CATALOG_CONFLICT", "Sample already belongs to a different species", 409)
            if sample is None:
                if not request.create_missing:
                    raise Error("NOT_FOUND", "Sample missing; explicitly allow creation", 404)
                sample = {"id": identifier(project["name"], item.sample), "name": item.sample,
                          "project_id": project["id"], "species_id": species["id"], "species": species["name"],
                          "identity_status": item.match_status, "datasets": []}
                data["samples"].append(sample)
                created.append({"kind": "sample", "id": sample["id"], "name": item.sample})
            group = self._named(sample["datasets"], item.data_type)
            if group is None:
                group = {"id": identifier(sample["id"], item.data_type), "name": item.data_type,
                         "status": item.match_status, "pending_scope": False, "file_count": 0, "representative_count": 0}
                sample["datasets"].append(group)
                sample["datasets"].sort(key=lambda d: d["name"])
                created.append({"kind": "dataset", "id": group["id"], "name": item.data_type})
            inputs = []
            for fid in item.input_file_ids:
                source = by_id.get(fid)
                if not source or source["sample_id"] != sample["id"] or source["dataset_id"] != group["id"]:
                    raise Error("CATALOG_CONFLICT", "Merged inputs must exist in the same sample and data type", 409)
                inputs.append({"path": source["path"], "status": "confirmed"})
            fid = identifier("registered", user_id, request.request_key, item.path)
            if fid in by_id:
                raise Error("CATALOG_CONFLICT", "Catalog file identity already exists", 409)
            row = {"id": fid, "sample_id": sample["id"], "dataset_id": group["id"],
                   "name": PurePosixPath(item.path).name, "path": item.path, "node_id": item.node_id,
                   "bytes": item.bytes, "roles": [] if item.role == "independent" else [item.role],
                   "representative_candidate": False, "match_status": item.match_status,
                   "read_part": item.read_part, "tissue": item.tissue, "condition": item.condition,
                   "condition_description": item.condition_description, "replicate": item.replicate,
                   "replicate_status": item.replicate_status, "quality_group": item.quality_group,
                   "pending_scope": False, "older_version": False, "reports": [r.model_dump() for r in item.reports],
                   "inputs": inputs, "notes": item.notes,
                   "stats": {"status": "completed", **item.stats.model_dump()} if item.stats else {
                       "status": "pending", "reads": None, "bases": None, "q30_percent": None, "computed_at": None}}
            data["files"].append(row)
            by_id[fid] = row
            affected.add(group["id"])
            changes.append({"file_id": fid, "before": None, "after": copy.deepcopy(row)})
        for sample in data["samples"]:
            for group in sample["datasets"]:
                if group["id"] in affected:
                    self._summarize(group, [r for r in data["files"] if r["dataset_id"] == group["id"]])
        return changes, created

    @staticmethod
    def _summarize(group, rows):
        if group["name"] == "RNA-seq" and group.get("availability", {}).get("groups"):
            if any(not {"condition", "replicate_status", "pending_scope"} <= r.keys() for r in rows):
                raise Error("CATALOG_METADATA_INCOMPLETE", "Import per-file RNA metadata before adding to this dataset", 409)
        group["file_count"] = len(rows)
        group["representative_count"] = sum("representative" in r["roles"] for r in rows)
        group["pending_scope"] = any(r.get("pending_scope", False) for r in rows)
        eligible = [r for r in rows if r.get("quality_group") != "fail" and not r.get("pending_scope", False)]
        present = bool(eligible) and group["name"] != "Unclassified"
        groups = defaultdict(list)
        if present and group["name"] == "RNA-seq":
            for row in eligible:
                groups[(row.get("tissue", ""), row.get("condition", ""))].append(row)
        summaries = []
        for (tissue, condition), members in sorted(groups.items()):
            labels = {r.get("replicate", "").strip() for r in members}
            complete = "" not in labels
            labels = {str(int(label)) if label.isdecimal() else label for label in labels}
            confirmed = complete and all(r.get("replicate_status") == "confirmed" for r in members)
            summaries.append({"tissue": tissue, "condition": condition,
                              "condition_description": next((r.get("condition_description", "") for r in members if r.get("condition_description")), ""),
                              "replicate_count": len(labels) if complete else None,
                              "replicate_status": "confirmed" if confirmed else "inferred" if complete else "unknown"})
        group["availability"] = {"status": "present" if present else "needs_review", "groups": summaries}

    @staticmethod
    def _relocate(data, request):
        by_id = {f["id"]: f for f in data["files"]}
        moving = {item.file_id for item in request.files}
        if len(moving) != len(request.files):
            raise Error("CATALOG_CONFLICT", "A file can be moved only once per request", 409)
        paths = {f["path"] for f in data["files"] if f["id"] not in moving}
        replacements, changes = {}, []
        for item in request.files:
            row = by_id.get(item.file_id)
            if row is None:
                raise Error("NOT_FOUND", "Catalog file not found", 404)
            if row["path"] != item.expected_path:
                raise Error("CATALOG_CONFLICT", "File path changed; inspect its current record", 409)
            if row["bytes"] != item.observed_bytes:
                raise Error("CATALOG_IDENTITY_MISMATCH", "Destination size differs; this is not an unchanged-file relocation", 409)
            if item.new_path in paths:
                raise Error("CATALOG_PATH_EXISTS", "Destination path is already registered", 409)
            paths.add(item.new_path)
            replacements[(row["sample_id"], row["dataset_id"], row["path"])] = item.new_path
            changes.append({"file_id": row["id"], "before": copy.deepcopy(row)})
            row.update(path=item.new_path, name=PurePosixPath(item.new_path).name, node_id=item.node_id)
        # Resolve against original paths so swaps and directory moves are one atomic operation.
        for row in data["files"]:
            affected = any((row["sample_id"], row["dataset_id"], link["path"]) in replacements for link in row.get("inputs", []))
            if affected and row["id"] not in moving:
                changes.append({"file_id": row["id"], "before": copy.deepcopy(row)})
            for link in row.get("inputs", []):
                link["path"] = replacements.get((row["sample_id"], row["dataset_id"], link["path"]), link["path"])
        for change in changes:
            change["after"] = copy.deepcopy(by_id[change["file_id"]])
        return changes, []
