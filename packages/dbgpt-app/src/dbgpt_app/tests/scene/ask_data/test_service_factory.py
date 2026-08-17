from __future__ import annotations

from dbgpt.storage.metadata import db
from dbgpt_app.scene.ask_data.schemas.schema import ViewSchema
from dbgpt_app.scene.ask_data.service_factory import create_sql_ask_data_services


def test_sql_service_factory_creates_complete_service_bundle(tmp_path):
    db.init_db(f"sqlite:///{tmp_path / 'askdata-factory.db'}")
    bundle = create_sql_ask_data_services(
        db,
        lambda *_: ViewSchema.model_construct(
            dialect="mssql",
            data_source="ecology",
            view="dbo.vw_contracts",
            columns=[],
            schema_hash="sha256:schema",
        ),
    )
    assert bundle.scene_repository.db_manager is db
    assert bundle.snapshot_service.db_manager is db
    assert bundle.run_service.repository.db_manager is db
