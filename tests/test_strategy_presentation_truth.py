from pathlib import Path

from src.config import load_config


ROOT = Path(__file__).resolve().parent.parent


def test_enabled_spy_vwap_presentation_matches_canonical_config_and_runtime_gate():
    config = load_config()
    presentation = (ROOT / "site" / "src" / "data" / "strategyFacts.js").read_text(encoding="utf-8")
    engine = (ROOT / "src" / "signal_engine.py").read_text(encoding="utf-8")
    assert config["signal"]["require_spy_above_vwap"] is True
    assert "spyVwapEnabled: true" in presentation
    assert 'signal.get("require_spy_above_vwap") is True' in engine
    assert "SPY is not above its session VWAP." in engine


def test_canonical_presentation_uses_completed_bar_breakout_not_unproven_one_minute_shorthand():
    presentation = (ROOT / "site" / "src" / "data" / "strategyFacts.js").read_text(encoding="utf-8")
    funnel = (ROOT / "site" / "src" / "components" / "diagrams" / "DecisionFunnel.jsx").read_text(encoding="utf-8")
    assert "Completed-bar close > opening-range high" in presentation
    assert "1m" not in funnel