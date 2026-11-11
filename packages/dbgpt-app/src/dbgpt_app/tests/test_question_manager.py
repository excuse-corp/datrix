from dbgpt_app.openapi.api_v1.tools.question_manager import QuestionManager


def test_reply_stores_answers_and_releases_pending_question():
    manager = QuestionManager()
    pending = manager.create(
        "conv-a", [{"question": "Choose one"}], user_id="alice"
    )

    manager.reply(pending.request_id, [["Option A"]], user_id="alice")

    assert pending.answers == [["Option A"]]
    assert pending.event.is_set()


def test_reply_rejects_a_different_user_without_releasing_question():
    manager = QuestionManager()
    pending = manager.create(
        "conv-a", [{"question": "Choose one"}], user_id="alice"
    )

    try:
        manager.reply(pending.request_id, [["Option A"]], user_id="bob")
    except KeyError:
        pass
    else:
        raise AssertionError("reply from a different user must be rejected")

    assert pending.answers is None
    assert not pending.event.is_set()


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
