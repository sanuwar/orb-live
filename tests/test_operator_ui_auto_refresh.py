from pathlib import Path
def test_refresh_script_is_bounded_local_and_read_only():
    text=(Path(__file__).parents[1]/"static/operator_refresh.js").read_text(encoding="utf-8")
    assert "minimum = 15" in text and "maximum = 300" in text and 'method: "GET"' in text
    assert "innerHTML" not in text and "eval(" not in text and "ws://" not in text and "wss://" not in text
