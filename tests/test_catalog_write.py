import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_web import auth, settings, verify

from cowork_hub.api import create_app
from cowork_hub.catalog_models import CatalogRegister
from cowork_hub.catalog_store import CatalogStore
from cowork_hub.models import Error


@pytest.fixture
def seed(tmp_path):
    data = {
        "version": 1, "updated_at": "2026-10-05", "unassigned_files": 0,
        "projects": [{"id": "p", "name": "Study", "species": [{"id": "s", "name": "Oryza sativa"}]}],
        "samples": [{"id": "sample", "name": "Rice", "project_id": "p", "species_id": "s", "species": "Oryza sativa",
                     "datasets": [{"id": "ont", "name": "ONT", "file_count": 2,
                                   "availability": {"status": "present", "groups": []}}]}],
        "files": [
            {"id": "source", "sample_id": "sample", "dataset_id": "ont", "name": "source.fastq.gz",
             "path": "/old/source.fastq.gz", "bytes": 100, "roles": ["source"], "inputs": [],
             "stats": {"status": "completed", "reads": 10, "bases": 1000, "q30_percent": 90.1}},
            {"id": "merged", "sample_id": "sample", "dataset_id": "ont", "name": "total.fastq.gz",
             "path": "/old/total.fastq.gz", "bytes": 100, "roles": ["merged"],
             "inputs": [{"path": "/old/source.fastq.gz", "status": "confirmed"}], "stats": {"status": "pending"}},
        ],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def config(seed):
    return settings(admin_users=["alice"], catalog_access="approved", catalog_path=str(seed))


@pytest.fixture
def web(rig, config):
    with TestClient(create_app(rig.hub, background=False, web_config=config, web_verify=verify)) as client:
        client.headers.update(auth(rig.alice))
        yield client


def request(web, *, key="register-001", **changes):
    return {"request_key": key, "expected_revision": web.get("/v1/catalog").json()["revision"],
            "files": [{"project": "Study", "species": "Oryza sativa", "sample": "Rice", "data_type": "ONT",
                       "node_id": "A", "path": "/new/extra.fastq.gz", "bytes": 200}], **changes}


def move(web, **changes):
    return request(web, key="relocate-001", files=[{
        "file_id": "source", "expected_path": "/old/source.fastq.gz", "new_path": "/new/source.fastq.gz",
        "node_id": "A", "observed_bytes": 100, "content_unchanged": True, **changes,
    }])


def test_preview_commit_retry_web_consistency_and_restart(rig, web, config, seed):
    body = request(web)
    preview = web.post("/v1/catalog/register", json=body)
    assert preview.status_code == 200, preview.text
    assert not preview.json()["applied"]
    assert web.get("/v1/catalog/files").json()["total"] == 2
    with rig.hub.store.transaction(write=False) as db:
        assert not db.execute("SELECT * FROM catalog_state").fetchone()
        assert not db.execute("SELECT * FROM catalog_events").fetchone()
    body["dry_run"] = False
    response = web.post("/v1/catalog/register", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["applied"] and result["revision"] != body["expected_revision"]
    fid = result["changes"][0]["file_id"]
    record = web.get(f"/v1/catalog/files/{fid}").json()["file"]
    assert record["stats"]["status"] == "pending"
    assert record["node_id"] == "A" and record["path"] == body["files"][0]["path"]
    visible = web.get("/v1/web/catalog/files", headers=auth()).json()
    assert visible["total"] == 3 and visible["revision"] == result["revision"]
    history = web.get(f"/v1/catalog/files/{fid}/history").json()["items"]
    assert len(history) == 1 and history[0]["actor"] == "alice" and history[0]["before_value"] is None
    assert history[0]["after_value"] == record
    replay = web.post("/v1/catalog/register", json=body).json()
    assert replay == {**result, "replayed": True}
    body["files"][0]["bytes"] = 300
    assert web.post("/v1/catalog/register", json=body).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    seed.write_text("invalid legacy file")
    assert web.get("/v1/catalog/files").json()["total"] == 3
    from cowork_hub.service import Hub

    restarted = CatalogStore(Hub(rig.reopen_store()), config)
    assert restarted.file(fid)["file"] == record


def test_personal_token_permissions_and_revocation(rig, web, config):
    body = request(web, dry_run=False)
    for token in (rig.admin, rig.worker, "google-alice", "invalid"):
        assert web.post("/v1/catalog/register", json=body, headers=auth(token)).status_code in (401, 403)
    assert web.get("/v1/catalog", headers=auth(rig.bob)).status_code == 200
    assert web.post("/v1/catalog/register", json=body, headers=auth(rig.bob)).status_code == 403
    config.catalog_write_access = "approved"
    assert web.post("/v1/catalog/register", json=body, headers=auth(rig.bob)).status_code == 200
    with rig.hub.store.transaction() as db:
        db.execute("UPDATE principals SET enabled=0 WHERE id='alice'")
    assert web.get("/v1/catalog").status_code in (401, 403)
    assert web.post("/v1/catalog/register", json=request_without_revision(body)).status_code in (401, 403)


def request_without_revision(body):
    return {**body, "request_key": "disabled-001"}


def test_relocate_preserves_identity_statistics_and_updates_parent(web):
    original = web.get("/v1/catalog/files/source").json()["file"]
    body = move(web)
    preview = web.post("/v1/catalog/relocate", json=body).json()
    assert len(preview["changes"]) == 2 and not preview["applied"]
    assert web.get("/v1/catalog/files/source").json()["file"] == original
    body["dry_run"] = False
    result = web.post("/v1/catalog/relocate", json=body)
    assert result.status_code == 200, result.text
    updated = web.get("/v1/catalog/files/source").json()["file"]
    assert updated == {**original, "path": "/new/source.fastq.gz", "node_id": "A"}
    parent = web.get("/v1/catalog/files/merged").json()["file"]
    assert parent["inputs"] == [{"path": updated["path"], "status": "confirmed"}]
    grouped = web.get("/v1/catalog/files", params={"grouped": True}).json()
    assert grouped["total"] == 1 and grouped["items"][0]["inputs"][0]["file"]["id"] == "source"
    assert len(web.get("/v1/catalog/files/merged/history").json()["items"]) == 1


@pytest.mark.parametrize(("changes", "code"), [
    ({"observed_bytes": 101}, "CATALOG_IDENTITY_MISMATCH"),
    ({"expected_path": "/wrong/file.fastq.gz"}, "CATALOG_CONFLICT"),
    ({"new_path": "/old/total.fastq.gz"}, "CATALOG_PATH_EXISTS"),
    ({"node_id": "missing"}, "NOT_FOUND"),
    ({"file_id": "missing"}, "NOT_FOUND"),
])
def test_bad_moves_do_not_write(web, changes, code):
    body = move(web, **changes)
    before = web.get("/v1/catalog").json()
    response = web.post("/v1/catalog/relocate", json={**body, "dry_run": False})
    assert response.json()["error"]["code"] == code
    assert web.get("/v1/catalog").json() == before


def test_path_swap_and_batch_rollback(web):
    body = move(web, new_path="/old/total.fastq.gz")
    body["files"].append({**body["files"][0], "file_id": "merged", "expected_path": "/old/total.fastq.gz",
                          "new_path": "/old/source.fastq.gz"})
    response = web.post("/v1/catalog/relocate", json={**body, "dry_run": False})
    assert response.status_code == 200, response.text
    assert web.get("/v1/catalog/files/merged").json()["file"]["inputs"][0]["path"] == "/old/total.fastq.gz"
    before = web.get("/v1/catalog/files").json()
    body = request(web, dry_run=False)
    body["files"].append({**body["files"][0], "path": "/another.fastq.gz", "sample": "Unknown"})
    assert web.post("/v1/catalog/register", json=body).status_code == 404
    assert web.get("/v1/catalog/files").json() == before


def test_hierarchy_statistics_reports_and_paired_rna(web):
    body = request(web, dry_run=False)
    item = {**body["files"][0], "project": "New study", "species": "Wolffia globosa", "sample": "Geumsan",
            "data_type": "RNA-seq", "tissue": "stem", "condition": "Up", "replicate": "1",
            "replicate_status": "confirmed", "read_part": "R1", "path": "/new/R1.fq.gz",
            "stats": {"reads": 100, "bases": 15000, "q30_percent": 95.2, "computed_at": "2026-10-05T00:00:00Z"},
            "reports": [{"id": "/reports/report.pdf", "provider": "Novogene", "received_date": "2026-10-04"}]}
    body["files"] = [item, {**item, "path": "/new/R2.fq.gz", "read_part": "R2"}]
    assert web.post("/v1/catalog/register", json=body).status_code == 404
    result = web.post("/v1/catalog/register", json={**body, "create_missing": True})
    assert result.status_code == 200, result.text
    assert len(result.json()["created"]) == 4
    sample = next(s for s in web.get("/v1/catalog").json()["samples"] if s["name"] == "Geumsan")
    group = sample["datasets"][0]
    assert group["file_count"] == 2 and group["availability"]["status"] == "present"
    assert group["availability"]["groups"][0]["replicate_count"] == 1
    assert group["availability"]["groups"][0]["replicate_status"] == "confirmed"
    assert result.json()["changes"][0]["after"]["stats"]["q30_percent"] == 95.2
    body = request(web, key="pooling-001", dry_run=False)
    body["files"] = [{**item, "path": "/pool.fq.gz", "tissue": "stem+root+flower", "condition": "", "replicate": "",
                      "replicate_status": "unknown"}]
    assert web.post("/v1/catalog/register", json=body).status_code == 200
    sample = next(s for s in web.get("/v1/catalog").json()["samples"] if s["name"] == "Geumsan")
    pool = next(g for g in sample["datasets"][0]["availability"]["groups"] if g["tissue"] == "stem+root+flower")
    assert pool["replicate_count"] is None


def test_registration_duplicate_and_merged_links(web):
    body = request(web, dry_run=False)
    body["files"][0].update(role="merged", input_file_ids=["merged"])
    result = web.post("/v1/catalog/register", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["changes"][0]["after"]["inputs"][0]["path"] == "/old/total.fastq.gz"
    again = request(web, key="duplicate-001", dry_run=False)
    assert web.post("/v1/catalog/register", json=again).json()["error"]["code"] == "CATALOG_PATH_EXISTS"


@pytest.mark.parametrize("field,value", [("path", "relative.fastq.gz"), ("path", "/a/../b.fq.gz"),
    ("path", "/data.bam"), ("path", "/newline\n.fq.gz"), ("bytes", -1), ("sample", " "),
    ("input_file_ids", ["source"]), ("replicate_status", "confirmed")])
def test_invalid_registration(web, field, value):
    body = request(web, dry_run=False)
    body["files"][0][field] = value
    assert web.post("/v1/catalog/register", json=body).status_code == 422


def test_concurrent_writers_conflict_without_lost_updates(rig, config, web):
    repo = CatalogStore(rig.hub, config)
    first = request(web, dry_run=False)
    second = copy.deepcopy(first)
    second["request_key"] = "concurrent-002"
    second["files"][0]["path"] = "/second.fastq.gz"

    def write(body):
        try:
            return repo.write("alice", "register", CatalogRegister.model_validate(body))["applied"]
        except Error as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [first, second]))
    assert results.count(True) == 1 and results.count("CATALOG_CONFLICT") == 1
    assert len(repo.load()["files"]) == 3
