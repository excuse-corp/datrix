from dbgpt_app.openapi.api_v1.react_streaming import ReActFinalResultStreamParser


def test_react_final_stream_parser_hides_thought_and_streams_result_incrementally():
    parser = ReActFinalResultStreamParser()

    assert parser.feed("Thought: I will query the data.\nAction: terminate\n") == ""
    assert parser.feed('Action Input: {"result": "第一部分') == "第一部分"
    assert parser.feed(r'，第二部分\n带有\"引号\"') == '，第二部分\n带有"引号"'
    assert parser.feed('"}') == ""


def test_react_final_stream_parser_does_not_stream_tool_action_result_fields():
    parser = ReActFinalResultStreamParser()

    text = 'Thought: query.\nAction: ask_data_query\nAction Input: {"result": "hidden"}'
    assert parser.feed(text) == ""


def test_react_final_stream_parser_decodes_split_unicode_surrogate_pairs():
    parser = ReActFinalResultStreamParser()

    first = 'Thought: done.\nAction: terminate\nAction Input: {"result": "\\ud83d'
    assert parser.feed(first) == ""
    assert parser.feed(r'\ude42 and text"}') == "🙂 and text"


def test_react_final_stream_parser_emits_before_long_answer_finishes():
    parser = ReActFinalResultStreamParser()
    answer = "这是逐段返回的答复。" * 100
    raw = f'Thought: done.\nAction: terminate\nAction Input: {{"result": "{answer}"}}'
    received = []
    first_output_index = None

    for index, char in enumerate(raw):
        chunk = parser.feed(char)
        if chunk and first_output_index is None:
            first_output_index = index
        received.append(chunk)

    assert "".join(received) == answer
    assert first_output_index is not None
    assert first_output_index < len(raw) - len(answer) // 2
