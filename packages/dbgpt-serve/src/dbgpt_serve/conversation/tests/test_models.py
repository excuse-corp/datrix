from datetime import datetime

import pytest

from dbgpt.storage.metadata import db
from dbgpt_serve.core.tests.conftest import (  # noqa: F401
    asystem_app,
    client,
    system_app,
)

from ..config import ServeConfig
from ..models.models import ServeDao, ServeEntity


@pytest.fixture(autouse=True)
def setup_and_teardown():
    db.init_db("sqlite:///:memory:")
    db.create_all()

    yield


@pytest.fixture
def server_config():
    # TODO : build your server config
    return ServeConfig()


@pytest.fixture
def dao(server_config):
    return ServeDao(server_config)


@pytest.fixture
def default_entity_dict():
    # TODO: build your default entity dict
    return {"conv_uid": "test_conv_uid", "summary": "hello", "chat_mode": "chat_normal"}


def test_table_exist():
    assert ServeEntity.__tablename__ in db.metadata.tables


def test_entity_create(default_entity_dict):
    with db.session() as session:
        entity = ServeEntity(**default_entity_dict)
        session.add(entity)


def test_response_serializes_naive_timestamps_as_utc(dao, default_entity_dict):
    timestamp = datetime(2026, 7, 18, 12, 29, 40)
    entity = ServeEntity(
        **default_entity_dict,
        gmt_created=timestamp,
        gmt_modified=timestamp,
    )

    response = dao.to_response(entity)

    assert response.gmt_created == "2026-07-18T12:29:40Z"
    assert response.gmt_modified == "2026-07-18T12:29:40Z"


def test_entity_unique_key(default_entity_dict):
    # TODO: implement your test case
    pass


def test_entity_get(default_entity_dict):
    # TODO: implement your test case
    pass


def test_entity_update(default_entity_dict):
    # TODO: implement your test case
    pass


def test_entity_delete(default_entity_dict):
    # TODO: implement your test case
    pass


def test_entity_all():
    # TODO: implement your test case
    pass


def test_get_dao_get_list(dao):
    # TODO: implement your test case
    pass


def test_dao_update(dao, default_entity_dict):
    # TODO: implement your test case
    pass


def test_dao_delete(dao, default_entity_dict):
    # TODO: implement your test case
    pass


def test_dao_get_list_page(dao):
    # TODO: implement your test case
    pass


# Add more test cases according to your own logic
