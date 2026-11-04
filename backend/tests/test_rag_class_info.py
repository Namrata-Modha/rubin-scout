"""CLASS_INFO parsing for the RAG knowledge base, against the real cosmos.js."""

from pathlib import Path

import pytest

from rag.class_info import ClassInfoError, class_info_texts, parse_class_info

COSMOS_JS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "cosmos.js"


@pytest.fixture(scope="module")
def class_info():
    return parse_class_info(COSMOS_JS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def chunks(class_info):
    return dict(class_info_texts(class_info))


def test_every_class_yields_a_chunk_containing_its_description(class_info, chunks):
    assert len(class_info) >= 23
    assert set(chunks) == set(class_info)
    for key, entry in class_info.items():
        assert entry["description"] in chunks[key], key
        assert chunks[key].startswith(f"Transient class: {key}")


@pytest.mark.parametrize(
    "key",
    ["SNIa", "SNII", "SNIbc", "SLSN", "TDE", "AGN", "Blazar", "QSO", "KN", "FRB"],
)
def test_unquoted_keys_are_parsed(key, class_info, chunks):
    """These ten used to produce no chunk at all."""
    assert key in class_info
    assert "Description: " in chunks[key]
    assert "Name: " in chunks[key]


@pytest.mark.parametrize(
    "key",
    ["CV/Nova", "most_likely_sn", "sn_near_galaxy_candidate", "in_tns",
     "extragalactic_new_candidate", "extragalactic_lt20mag_candidate", "Unknown"],
)
def test_quoted_keys_are_parsed_with_more_than_their_name(key, class_info, chunks):
    """These used to produce a chunk holding only the class name."""
    assert key in class_info
    assert "Description: " in chunks[key]
    assert len(chunks[key].splitlines()) >= 3


def test_snia_and_most_likely_sn_chunks_carry_their_text(chunks):
    assert "white dwarf" in chunks["SNIa"]
    assert "SuperNNova and CATS" in chunks["most_likely_sn"]


def test_presentation_only_fields_stay_out_of_chunks(chunks):
    assert "#" not in chunks["SNIa"]          # the colour
    assert "💥" not in chunks["SNIa"]         # the emoji


# --- parser behaviour and loud failure --------------------------------------

def _wrap(body: str) -> str:
    return f"// header\nexport const CLASS_INFO = {{\n{body}\n}};\nexport function f() {{ return {{ description: 'x' }}; }}\n"


def test_quoted_unquoted_comments_and_trailing_commas():
    src = _wrap('''
  A: { name: "Alpha", description: "It's alpha.", },
  // a comment between entries
  "B/C": { "name": 'Beta', description: "Say \\"hi\\"" },
''')
    info = parse_class_info(src)
    assert list(info) == ["A", "B/C"]
    assert info["A"]["description"] == "It's alpha."
    assert info["B/C"]["description"] == 'Say "hi"'


def test_the_description_inside_the_fallback_function_is_not_a_class():
    """cosmos.js has 24 `description:` fields: 23 are classes, the 24th is the
    fallback object inside getClassInfo, outside CLASS_INFO."""
    src = COSMOS_JS.read_text(encoding="utf-8")
    assert src.count("description:") == len(parse_class_info(src)) + 1


def test_a_class_without_a_description_aborts():
    with pytest.raises(ClassInfoError, match=r"CLASS_INFO\['B'\] has no description"):
        parse_class_info(_wrap('A: { description: "ok" }, B: { name: "no text" }'))
    with pytest.raises(ClassInfoError, match="has no description"):
        parse_class_info(_wrap('A: { description: "   " }'))


def test_unreadable_syntax_aborts_instead_of_dropping_content():
    with pytest.raises(ClassInfoError, match="template literals"):
        parse_class_info(_wrap("A: { description: `back ticks` }"))
    with pytest.raises(ClassInfoError, match="expected .,. or"):
        parse_class_info(_wrap('A: { description: "x" + "y" }'))
    with pytest.raises(ClassInfoError, match="duplicate key"):
        parse_class_info(_wrap('A: { description: "x" }, A: { description: "y" }'))


def test_a_missing_class_info_aborts():
    with pytest.raises(ClassInfoError, match="could not find"):
        parse_class_info("export const OTHER = {};")


def test_parsed_count_disagreeing_with_the_file_aborts(monkeypatch):
    import rag.class_info as ci

    monkeypatch.setattr(ci, "_count_top_level_entries", lambda text, start: 99)
    with pytest.raises(ClassInfoError, match="parsed 1 CLASS_INFO classes but counted 99"):
        parse_class_info(_wrap('A: { description: "ok" }'))
