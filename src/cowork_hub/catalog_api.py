"""Personal-token catalog tools use the same data and access policy as the web."""

from fastapi import Depends, Query

from .catalog import can_view_catalog, mount_catalog
from .catalog_models import CatalogRegister, CatalogRelocate
from .models import Error, Identifier


def mount_catalog_api(app, catalog, config, user):
    def reader(principal=Depends(user)):
        mapped = {"user_id": principal["id"]}
        if not can_view_catalog(config, mapped):
            raise Error("FORBIDDEN", "Catalog access is required", 403)
        return mapped

    mount_catalog(app, config, reader, catalog=catalog, prefix="/v1/catalog")

    @app.get("/v1/catalog/files/{file_id}")
    def file(file_id: Identifier, principal=Depends(reader)):
        return catalog.file(file_id)

    @app.get("/v1/catalog/files/{file_id}/history")
    def history(file_id: Identifier, limit: int = Query(50, ge=1, le=100), principal=Depends(reader)):
        return catalog.history(file_id, limit)

    @app.post("/v1/catalog/register")
    def register(body: CatalogRegister, principal=Depends(reader)):
        return catalog.write(principal["user_id"], "register", body)

    @app.post("/v1/catalog/relocate")
    def relocate(body: CatalogRelocate, principal=Depends(reader)):
        return catalog.write(principal["user_id"], "relocate", body)
