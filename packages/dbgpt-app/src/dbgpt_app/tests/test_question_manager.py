from dbgpt_app.openapi.api_v1.tools.question_manager import QuestionManager


def test_reject_by_conv_releases_only_matching_pending_questions():
    manager = QuestionManager()
    first = manager.create("conv-a", [{"question": "A"}])
    second = manager.create("conv-b", [{"question": "B"}])
    third = manager.create("conv-a", [{"question": "C"}])

    assert manager.reject_by_conv("conv-a") == 2
    assert first.rejected is True
    assert first.event.is_set()
    assert third.rejected is True
    assert third.event.is_set()
    assert second.rejected is False
    assert not second.event.is_set()

    assert manager.reject_by_conv("missing") == 0
