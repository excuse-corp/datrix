from types import SimpleNamespace

import pytest

from dbgpt_serve.utils.auth import UserRequest


class FakeUploadFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content_type = "text/x-python"
        self._content = content

    async def read(self, size: int = -1) -> bytes:
        return self._content if size < 0 else self._content[:size]


@pytest.mark.asyncio
async def test_python_file_upload_rejects_traversal_filename(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_file_upload(
        FakeUploadFile("../../outside.py", b"print('escaped')"),
        UserRequest(user_id="alice"),
    )

    assert result.success is False
    assert not (tmp_path / "outside.py").exists()


@pytest.mark.asyncio
async def test_python_file_upload_rejects_absolute_filename(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_file_upload(
        FakeUploadFile(str(tmp_path / "outside.py"), b"print('escaped')"),
        UserRequest(user_id="alice"),
    )

    assert result.success is False
    assert not (tmp_path / "outside.py").exists()


@pytest.mark.asyncio
async def test_python_file_upload_rejects_attachment_manifest_filename(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_file_upload(
        FakeUploadFile(".attachments.json", b"not metadata"),
        UserRequest(user_id="alice"),
    )

    assert result.success is False
    assert not (tmp_path / "python_uploads" / "alice" / ".attachments.json").exists()


@pytest.mark.asyncio
async def test_python_file_upload_allows_plain_filename_inside_user_dir(
    tmp_path, monkeypatch
):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_file_upload(
        FakeUploadFile("inside.py", b"print('inside')"),
        UserRequest(user_id="alice"),
    )

    expected_path = tmp_path / "python_uploads" / "alice" / "inside.py"
    assert result.success is True
    assert result.data == str(expected_path)
    assert expected_path.read_bytes() == b"print('inside')"


@pytest.mark.asyncio
async def test_python_file_upload_rejects_symlink_escape(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    upload_dir = tmp_path / "python_uploads" / "alice"
    outside_dir = tmp_path / "outside"
    upload_dir.mkdir(parents=True)
    outside_dir.mkdir()
    try:
        (upload_dir / "linked").symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_file_upload(
        FakeUploadFile("linked/escaped.py", b"print('escaped')"),
        UserRequest(user_id="alice"),
    )

    assert result.success is False
    assert not (outside_dir / "escaped.py").exists()


def test_user_upload_dir_rejects_symlinked_upload_root(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    try:
        (tmp_path / "python_uploads").symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )
    with pytest.raises(ValueError, match="symbolic link|escape"):
        python_upload_api._user_upload_dir("alice")


def test_user_upload_dir_rejects_symlinked_user_scope(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    upload_base = tmp_path / "python_uploads"
    upload_base.mkdir()
    (upload_base / "bob").mkdir()
    try:
        (upload_base / "alice").symlink_to(
            upload_base / "bob", target_is_directory=True
        )
    except OSError as exc:
        pytest.skip(f"symlinks are not available in this environment: {exc}")

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )
    with pytest.raises(ValueError, match="symbolic link"):
        python_upload_api._user_upload_dir("alice")


@pytest.mark.asyncio
async def test_python_file_upload_rejects_oversized_file(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )
    monkeypatch.setattr(python_upload_api, "MAX_CHAT_UPLOAD_FILE_SIZE_BYTES", 3)

    result = await python_upload_api.python_file_upload(
        FakeUploadFile("large.txt", b"four"),
        UserRequest(user_id="alice"),
    )

    assert result.success is False
    assert not (tmp_path / "python_uploads" / "alice" / "large.txt").exists()


@pytest.mark.asyncio
async def test_python_files_upload_accepts_multiple_files(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_files_upload(
        [
            FakeUploadFile("first.txt", b"first"),
            FakeUploadFile("second.txt", b"second"),
        ],
        UserRequest(user_id="alice"),
    )

    expected_dir = tmp_path / "python_uploads" / "alice"
    assert result.success is True
    assert result.data == [
        str(expected_dir / "first.txt"),
        str(expected_dir / "second.txt"),
    ]
    assert len(result.attachments) == 2
    assert result.attachments[0]["attachment_id"].startswith("att_")
    assert result.attachments[0]["storage_path"] == result.data[0]
    assert (expected_dir / "first.txt").read_bytes() == b"first"
    assert (expected_dir / "second.txt").read_bytes() == b"second"


@pytest.mark.asyncio
async def test_python_files_upload_rejects_more_than_five_files(tmp_path, monkeypatch):
    from dbgpt_app.openapi.api_v1 import python_upload_api

    monkeypatch.setattr(
        python_upload_api.CFG, "SYSTEM_APP", SimpleNamespace(work_dir=str(tmp_path))
    )

    result = await python_upload_api.python_files_upload(
        [FakeUploadFile(f"{index}.txt", b"content") for index in range(6)],
        UserRequest(user_id="alice"),
    )

    assert result.success is False
    assert not (tmp_path / "python_uploads" / "alice").exists()
