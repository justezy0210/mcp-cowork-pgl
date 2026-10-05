import json

import pytest
from fastapi.testclient import TestClient
from test_web import auth, settings, verify

from cowork_hub.api import create_app


@pytest.fixture
def catalog_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({
        "version": 1, "updated_at": "2026-10-03", "unassigned_files": 2,
        "projects": [
            {"id": "project", "name": "Study", "species": [{"id": "rice", "name": "Oryza sativa"}, {"id": "empty", "name": "Empty species"}]},
            {"id": "other-project", "name": "Other study", "species": [{"id": "wolffia", "name": "Wolffia globosa"}]},
        ],
        "samples": [
            {"id": "one", "name": "Sample alpha", "project_id": "project", "species_id": "rice", "species": "Oryza sativa",
             "datasets": [{"id": "rna", "name": "RNA-seq"}, {"id": "dna", "name": "HiFi"}, {"id": "pending", "name": "ONT"}]},
            {"id": "two", "name": "Sample beta", "project_id": "other-project", "species_id": "wolffia", "species": "Wolffia globosa",
             "datasets": [{"id": "other-rna", "name": "RNA-seq"}]},
        ],
        "files": [
            {"id": "raw", "sample_id": "one", "dataset_id": "rna", "roles": ["source"], "path": "/private/flower_R1.fq.gz"},
            {"id": "merged", "sample_id": "one", "dataset_id": "rna", "roles": ["merged", "representative"], "path": "/private/all_R1.fq.gz"},
            {"id": "dna", "sample_id": "one", "dataset_id": "dna", "roles": [], "path": "/private/dna.fq.gz"},
            {"id": "other", "sample_id": "two", "dataset_id": "other-rna", "roles": [], "path": "/private/other.fq.gz"},
        ],
    }))
    return path


def client(rig, path, access="admin"):
    return TestClient(create_app(rig.hub, background=False, web_config=settings(
        admin_users=["alice"], catalog_path=str(path), catalog_access=access,
    ), web_verify=verify))


def test_catalog_is_private_and_admin_only_by_default(rig, catalog_file):
    with client(rig, catalog_file) as web:
        for route in ["/catalog", "/catalog/files", "/catalog/samples/one/files"]:
            assert web.get("/v1/web" + route).status_code == 401
            assert web.get("/v1/web" + route, headers=auth(rig.admin)).status_code == 401
            assert web.get("/v1/web" + route, headers=auth("google-bob")).status_code == 403
        overview = web.get("/v1/web/catalog", headers=auth())
        assert overview.status_code == 200
        assert overview.headers["cache-control"] == "no-store"
        assert "files" not in overview.json() and "/private/" not in overview.text
        assert "catalog_path" not in web.get("/web/config.json").text
        assert web.get("/v1/web/profile", headers=auth()).json()["can_view_catalog"]
        assert not web.get("/v1/web/profile", headers=auth("google-bob")).json()["can_view_catalog"]


def test_catalog_approved_access_still_requires_a_linked_enabled_account(rig, catalog_file):
    with client(rig, catalog_file, "approved") as web:
        assert web.get("/v1/web/catalog", headers=auth("google-bob")).status_code == 200
        assert web.get("/v1/web/catalog", headers=auth("google-stranger")).status_code == 403
        with rig.hub.store.transaction() as db:
            db.execute("UPDATE principals SET enabled=0 WHERE id='bob'")
        assert web.get("/v1/web/catalog", headers=auth("google-bob")).status_code == 403


def test_file_filters_paging_and_sample_boundaries(rig, catalog_file):
    with client(rig, catalog_file) as web:
        url = "/v1/web/catalog/samples/one/files"
        first = web.get(url, params={"dataset": "rna", "page_size": 1}, headers=auth()).json()
        assert first["total"] == 2 and first["items"][0]["id"] == "merged"
        assert web.get(url, params={"dataset": "rna", "page_size": 1, "page": 2}, headers=auth()).json()["items"][0]["id"] == "raw"
        assert [r["id"] for r in web.get(url, params={"role": "source", "q": "FLOWER"}, headers=auth()).json()["items"]] == ["raw"]
        assert web.get(url, params={"q": "other"}, headers=auth()).json()["total"] == 0
        assert web.get(url, params={"page_size": 101}, headers=auth()).status_code == 422
        assert web.get(url, params={"role": "unknown"}, headers=auth()).status_code == 422
        assert web.get("/v1/web/catalog/samples/missing/files", headers=auth()).status_code == 404


def test_snapshot_reload_and_failure_do_not_return_stale_private_data(rig, catalog_file):
    with client(rig, catalog_file) as web:
        assert web.get("/v1/web/catalog", headers=auth()).status_code == 200
        catalog_file.write_text("invalid snapshot")
        response = web.get("/v1/web/catalog", headers=auth())
        assert response.status_code == 503
        assert str(catalog_file) not in response.text
        assert "/private/" not in response.text


def test_all_files_narrow_by_hierarchy_and_search_metadata(rig, catalog_file):
    with client(rig, catalog_file) as web:
        url = "/v1/web/catalog/files"
        def result(**params):
            response = web.get(url, params=params, headers=auth())
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            return response.json()

        assert result()["total"] == 4
        assert result(page_size=1)["items"][0]["id"] == "merged"
        assert result(page_size=1, page=2)["items"][0]["id"] != "merged"
        assert result(project="project")["total"] == 3
        assert result(project="project", species="rice", sample="one", dataset="rna")["total"] == 2
        assert result(species="wolffia")["items"][0]["id"] == "other"
        assert result(sample="one", dataset="pending")["total"] == 0
        assert result(species="empty")["total"] == 0
        assert result(q="ALPHA")["total"] == 3
        assert result(q="ORYZA SATIVA")["total"] == 3
        assert result(q="other study")["total"] == 1
        assert result(q="HiFi")["items"][0]["id"] == "dna"
        assert result(q="FLOWER", role="source")["items"][0]["id"] == "raw"
        assert result(project="project", q="beta")["total"] == 0
        for params in [{"project": "missing"}, {"species": "missing"}, {"sample": "missing"},
                       {"project": "project", "species": "wolffia"}, {"project": "project", "sample": "two"},
                       {"sample": "one", "dataset": "other-rna"}]:
            assert web.get(url, params=params, headers=auth()).status_code == 404
        for params in [{"page": 0}, {"page_size": 101}, {"role": "invalid"}, {"q": "x" * 201}]:
            assert web.get(url, params=params, headers=auth()).status_code == 422


@pytest.mark.parametrize("sort_by,ascending,descending", [
    ("file", ["merged", "dna", "raw", "other"], ["other", "raw", "dna", "merged"]),
    ("sample", ["merged", "dna", "raw", "other"], ["other", "merged", "dna", "raw"]),
    ("species", ["merged", "dna", "raw", "other"], ["other", "merged", "dna", "raw"]),
    ("project", ["other", "merged", "dna", "raw"], ["merged", "dna", "raw", "other"]),
    ("data_type", ["dna", "merged", "raw", "other"], ["merged", "raw", "other", "dna"]),
])
def test_text_sorting_covers_all_results_before_paging(rig, catalog_file, sort_by, ascending, descending):
    with client(rig, catalog_file) as web:
        for order, expected in [("asc", ascending), ("desc", descending)]:
            ids = []
            for page in [1, 2]:
                response = web.get("/v1/web/catalog/files", headers=auth(), params={
                    "sort_by": sort_by, "sort_order": order, "page": page, "page_size": 2,
                })
                assert response.status_code == 200
                assert response.json()["total"] == 4
                ids.extend(row["id"] for row in response.json()["items"])
            assert ids == expected


@pytest.mark.parametrize("sort_by,field,values", [
    ("reads", "reads", [2, 10, 0]), ("bases", "bases", [2, 10, 0]),
    ("q30", "q30_percent", [9.5, 95.25, 0]),
])
def test_numeric_sorting_keeps_unknown_last_and_zero_measured(rig, catalog_file, sort_by, field, values):
    data = json.loads(catalog_file.read_text())
    for row, value in zip(data["files"], values):
        row["stats"] = {"status": "completed", "reads": 1, "bases": 100, field: value}
    data["files"][3]["stats"] = {"status": "pending", field: 999}
    for i, stats in enumerate([
        {"status": "failed", field: 999}, {"status": "completed", field: -1},
        {"status": "completed", field: "88"}, {"status": "completed", field: True}, {},
    ]):
        data["files"].append({**data["files"][0], "id": f"unknown-{i}", "path": f"/private/unknown-{i}.fq", "stats": stats})
    catalog_file.write_text(json.dumps(data))
    with client(rig, catalog_file) as web:
        for order, first in [("asc", ["dna", "raw", "merged"]), ("desc", ["merged", "raw", "dna"])]:
            params = {"sort_by": sort_by, "sort_order": order}
            result = web.get("/v1/web/catalog/files", headers=auth(), params=params).json()
            ids = [r["id"] for r in result["items"]]
            assert ids == first + ["other"] + [f"unknown-{i}" for i in range(5)]
            page = web.get("/v1/web/catalog/files", headers=auth(), params={**params, "page": 2, "page_size": 2}).json()
            assert [r["id"] for r in page["items"]] == [first[2], "other"]


def test_sorting_preserves_filters_grouped_inputs_and_validates_parameters(rig, catalog_file):
    data = json.loads(catalog_file.read_text())
    raw, merged = data["files"][:2]
    merged["inputs"] = [{"path": raw["path"], "status": "confirmed"}]
    raw["stats"] = {"status": "completed", "reads": 999}
    merged["stats"] = {"status": "completed", "reads": 2}
    data["files"][3]["stats"] = {"status": "completed", "reads": 10}
    catalog_file.write_text(json.dumps(data))
    with client(rig, catalog_file) as web:
        params = {"grouped": True, "view": "library", "sort_by": "reads", "sort_order": "desc", "page_size": 1}
        url = "/v1/web/catalog/files"
        assert web.get(url, params=params, headers=auth()).json()["items"][0]["id"] == "other"
        second = web.get(url, params={**params, "page": 2}, headers=auth()).json()["items"][0]
        assert second["id"] == "merged" and second["inputs"][0]["file"]["id"] == "raw"
        filtered = web.get(url, params={**params, "project": "project", "q": "flower", "role": "merged"}, headers=auth()).json()
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == "merged"
        sample = web.get("/v1/web/catalog/samples/one/files", params={"sort_by": "reads", "sort_order": "desc"}, headers=auth()).json()
        assert [r["id"] for r in sample["items"]] == ["raw", "merged", "dna"]
        for route in [url, "/v1/web/catalog/samples/one/files"]:
            for invalid in [{"sort_by": "path;DROP"}, {"sort_order": "backward"}]:
                assert web.get(route, params=invalid, headers=auth()).status_code == 422
            assert web.get(route, params={"sort_by": "reads"}).status_code == 401


def test_grouped_files_hide_inputs_before_paging_and_search_through_ancestors(rig, catalog_file):
    data = json.loads(catalog_file.read_text())
    raw, merged = data["files"][:2]
    merged["inputs"] = [{"path": raw["path"], "status": "confirmed"}]
    data["files"].extend([
        {**merged, "id": "final", "path": "/private/final.fq.gz",
         "inputs": [{"path": merged["path"], "status": "inferred"}]},
        {**raw, "id": "unlinked", "path": "/private/unlinked.fq.gz"},
    ])
    catalog_file.write_text(json.dumps(data))
    with client(rig, catalog_file) as web:
        url = "/v1/web/catalog/files"
        params = {"grouped": True, "role": "merged", "page_size": 1}
        result = web.get(url, params=params, headers=auth()).json()
        assert result["total"] == 1 and result["items"][0]["id"] == "final"
        intermediate = result["items"][0]["inputs"][0]
        assert intermediate["status"] == "inferred" and intermediate["file"]["id"] == "merged"
        source = intermediate["file"]["inputs"][0]
        assert source["status"] == "confirmed" and source["file"]["id"] == "raw"
        assert web.get(url, params={**params, "q": "FLOWER"}, headers=auth()).json()["items"][0]["id"] == "final"
        assert web.get(url, params={**params, "page": 2}, headers=auth()).json()["items"] == []
        all_files = web.get(url, params={"grouped": True}, headers=auth()).json()
        assert all_files["total"] == 4
        assert {r["id"] for r in all_files["items"]} == {"final", "unlinked", "dna", "other"}
        flat = web.get(url, headers=auth()).json()
        assert flat["total"] == 6
        assert "file" not in next(r for r in flat["items"] if r["id"] == "merged")["inputs"][0]


def test_grouping_never_resolves_inputs_across_samples_or_data_types(rig, catalog_file):
    data = json.loads(catalog_file.read_text())
    merged = data["files"][1]
    merged["inputs"] = [{"path": path, "status": "inferred"} for path in [
        "/private/dna.fq.gz", "/private/other.fq.gz", "/private/unlisted.fq.gz",
    ]]
    catalog_file.write_text(json.dumps(data))
    with client(rig, catalog_file) as web:
        response = web.get("/v1/web/catalog/files", params={"grouped": True, "sample": "one"}, headers=auth()).json()
        assert response["total"] == 3
        parent = next(r for r in response["items"] if r["id"] == "merged")
        assert all(link["file"] is None for link in parent["inputs"])
        found = web.get("/v1/web/catalog/files", params={"grouped": True, "q": "unlisted"}, headers=auth()).json()
        assert found["items"][0]["id"] == "merged"


def test_cyclic_or_duplicate_inputs_do_not_hide_files_or_recurse_forever(rig, catalog_file):
    data = json.loads(catalog_file.read_text())
    raw, merged = data["files"][:2]
    raw["inputs"] = [{"path": merged["path"], "status": "inferred"}]
    raw["roles"] = ["merged"]
    merged["inputs"] = [{"path": p, "status": "inferred"} for p in [raw["path"], raw["path"], merged["path"]]]
    catalog_file.write_text(json.dumps(data))
    with client(rig, catalog_file) as web:
        response = web.get("/v1/web/catalog/files", params={"grouped": True}, headers=auth())
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 3
        parent = next(r for r in result["items"] if r["id"] in {"raw", "merged"})
        assert len(parent["inputs"]) == 1
        assert parent["inputs"][0]["file"]["id"] in {"raw", "merged"} - {parent["id"]}
        assert parent["inputs"][0]["file"]["inputs"] == []


def test_retained_originals_have_separate_searchable_pagination_without_merge_links(rig, catalog_file):
    data = json.loads(catalog_file.read_text())
    raw = data["files"][0]
    retained = [{**raw, "id": f"fail-{i}", "path": f"/private/fail/chunk_{i:02}.fq.gz",
                 "display_group": "retained_original", "quality_group": "fail"} for i in range(27)]
    data["files"].extend(retained)
    data["files"].append({**raw, "id": "independent-rna", "path": "/private/treated_rep2.fq.gz"})
    catalog_file.write_text(json.dumps(data))
    with client(rig, catalog_file) as web:
        url = "/v1/web/catalog/files"
        def result(**params):
            response = web.get(url, params=params, headers=auth())
            assert response.status_code == 200
            return response.json()

        assert result()["total"] == 32  # Existing flat API remains complete.
        library = result(view="library", grouped=True)
        assert library["total"] == 5
        assert library["original_groups"] == [{"sample_id": "one", "dataset_id": "rna", "count": 27}]
        assert "independent-rna" in {r["id"] for r in library["items"]}
        assert all(not r.get("inputs") for r in library["items"])
        assert result(view="library", sample="two")["original_groups"] == []
        assert result(view="library", dataset="dna")["original_groups"] == []
        assert result(view="library", role="merged")["original_groups"] == []
        found = result(view="library", q="chunk_26")
        assert found["total"] == 0 and found["original_groups"][0]["count"] == 1
        first = result(view="originals", sample="one", dataset="rna")
        second = result(view="originals", sample="one", dataset="rna", page=2)
        assert first["total"] == second["total"] == 27
        assert len(first["items"]) == 25 and len(second["items"]) == 2
        assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
        assert result(view="originals", q="chunk_26")["items"][0]["id"] == "fail-26"
        assert result(view="originals", sample="two")["total"] == 0
        assert web.get(url, params={"view": "invalid"}, headers=auth()).status_code == 422
        assert web.get(url, params={"view": "originals"}).status_code == 401
        assert web.get(url, params={"view": "originals"}, headers=auth("google-bob")).status_code == 403
