from app.utils.chunking import chunk_text


def test_chunk_text_empty_input():
    assert chunk_text("") == []


def test_chunk_text_overlaps_long_input():
    text = "a" * 2000
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    assert len(chunks) == 3
    assert len(chunks[0]) == 1000
    assert chunks[0][-100:] == chunks[1][:100]
