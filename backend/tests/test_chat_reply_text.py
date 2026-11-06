"""Chat model replies whose content is a list of parts, not a string.

Production hit HTTP 500 (AttributeError: 'list' object has no attribute 'strip')
when Gemini returned AIMessage.content as a list of content parts.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_google_genai")
pytest.importorskip("langchain_postgres")

from rag import chain  # noqa: E402
from rag.chain import NO_INFO_LINE, _reply_text  # noqa: E402


def reply(content):
    return SimpleNamespace(content=content)


def test_the_standard_line_is_the_one_the_prompt_asks_for():
    assert NO_INFO_LINE == "I don't have information about that."


@pytest.mark.parametrize(
    "content, expected",
    [
        ("  A nova is a white dwarf flare.  ", "A nova is a white dwarf flare."),
        ([{"type": "text", "text": "A nova is a white dwarf flare."}],
         "A nova is a white dwarf flare."),
        ([{"type": "thinking", "thinking": "Let me recall what a nova is..."},
          {"type": "text", "text": "A nova is a white dwarf flare."}],
         "A nova is a white dwarf flare."),
        (["A nova ", "is a white dwarf flare."], "A nova is a white dwarf flare."),
        ([{"type": "text", "text": "A nova "}, "is ", {"type": "text", "text": "a flare."}],
         "A nova is a flare."),
        ([], NO_INFO_LINE),
        (None, NO_INFO_LINE),
        ("", NO_INFO_LINE),
        ("   ", NO_INFO_LINE),
    ],
    ids=[
        "string", "one-text-part", "thinking-plus-text", "plain-strings", "mixed-parts",
        "empty-list", "none", "empty-string", "whitespace",
    ],
)
def test_reply_text_turns_any_content_shape_into_plain_text(content, expected):
    assert _reply_text(reply(content)) == expected


def test_only_thinking_parts_means_no_answer():
    assert _reply_text(reply([{"type": "thinking", "thinking": "hmm"}])) == NO_INFO_LINE


def test_unreadable_shapes_do_not_raise():
    assert _reply_text(reply(12345)) == NO_INFO_LINE
    assert _reply_text(reply([{"type": "text"}, {"type": "text", "text": None}, 7, None])) == NO_INFO_LINE
    assert _reply_text(SimpleNamespace()) == NO_INFO_LINE            # no .content at all
    assert _reply_text(None) == NO_INFO_LINE


def test_a_caller_can_choose_its_own_fallback():
    assert _reply_text(reply([]), default="original question") == "original question"


# --- through ask(): the 500 from production -----------------------------------------

class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def invoke(self, prompt_value):
        self.prompts.append(prompt_value)
        return self.replies.pop(0)


class FakeStore:
    def __init__(self):
        self.queries = []

    def similarity_search(self, query, k=4):
        self.queries.append(query)
        return [SimpleNamespace(page_content="A nova is a white dwarf flare.",
                                metadata={"source": "science-guide.md"})]


def _patch(monkeypatch, llm, store):
    monkeypatch.setattr(chain, "_get_components", lambda: {"llm": llm, "vectorstore": store})


def test_ask_survives_a_list_content_answer(monkeypatch):
    llm = FakeLLM(reply([{"type": "thinking", "thinking": "..."},
                         {"type": "text", "text": "A nova is a flare (science-guide.md)."}]))
    _patch(monkeypatch, llm, FakeStore())

    result = chain.ask("What is a nova?")

    assert result["answer"] == "A nova is a flare (science-guide.md)."
    assert result["sources"][0]["source"] == "science-guide.md"


def test_ask_survives_a_list_content_condense_step(monkeypatch):
    llm = FakeLLM(
        reply([{"type": "text", "text": "What is a kilonova?"}]),      # condense
        reply([{"type": "text", "text": "A merger flash."}]),          # answer
    )
    store = FakeStore()
    _patch(monkeypatch, llm, store)
    history = [{"role": "user", "content": "Tell me about mergers"},
               {"role": "assistant", "content": "Neutron stars can merge."}]

    result = chain.ask("and what is it called?", history=history)

    assert store.queries == ["What is a kilonova?"]                    # the rewritten query
    assert result["answer"] == "A merger flash."


def test_an_empty_condense_reply_searches_with_the_original_question(monkeypatch):
    llm = FakeLLM(reply([]), reply("A merger flash."))
    store = FakeStore()
    _patch(monkeypatch, llm, store)
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    chain.ask("what is a kilonova?", history=history)

    assert store.queries == ["what is a kilonova?"]                    # not the "no information" line


def test_an_empty_or_none_answer_becomes_the_standard_line(monkeypatch):
    for empty in ([], None, ""):
        _patch(monkeypatch, FakeLLM(reply(empty)), FakeStore())
        assert chain.ask("What is a nova?")["answer"] == NO_INFO_LINE
