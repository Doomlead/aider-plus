from __future__ import annotations

from aider.coders.base_coder import Coder
from aider.coders.udiff_coder import do_replace


def test_udiff_do_replace_inserts_newline_when_appending_to_existing_file(tmp_path):
    fname = tmp_path / "notes.txt"
    fname.write_text("hello")

    hunk = ["+world\n"]
    updated = do_replace(fname, fname.read_text(), hunk)

    assert updated == "hello\nworld\n"


def test_udiff_do_replace_new_file_from_empty_context(tmp_path):
    fname = tmp_path / "new.txt"

    hunk = ["+alpha\n", "+beta\n"]
    updated = do_replace(fname, "", hunk)

    assert updated == "alpha\nbeta\n"


def test_normalize_done_message_pair_keeps_multimodal_content_list():
    coder = Coder.__new__(Coder)
    content = [{"type": "text", "text": "Image file: chart.png"}]

    pair = coder._normalize_done_message_pair(content)

    assert pair[0]["role"] == "user"
    assert pair[0]["content"] == content
    assert pair[1] == {"role": "assistant", "content": "Ok."}
