from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "strategy_v0.yaml"


class ConfigError(RuntimeError):
    """Raised when the strategy configuration is missing or unsafe."""


STRATEGY_NAME_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*$"
)


def get_strategy_name(
    config: Mapping[str, Any],
) -> str:
    """Return the validated strategy identifier."""

    strategy = config.get("strategy")

    if not isinstance(strategy, Mapping):
        raise ConfigError(
            "Configuration strategy section must be an object."
        )

    name = strategy.get("name")

    if not isinstance(name, str) or not name.strip():
        raise ConfigError(
            "strategy.name must be a non-blank string."
        )

    if (
        name != name.strip()
        or STRATEGY_NAME_PATTERN.fullmatch(name) is None
    ):
        raise ConfigError(
            "strategy.name must be a valid identifier containing only "
            "letters, numbers, underscores, or hyphens."
        )

    return name


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load and validate the YAML strategy configuration."""

    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ConfigError("Configuration must contain a YAML object.")

    required_sections = {
        "strategy",
        "universe",
        "signal",
        "exit",
        "risk",
        "execution",
    }

    missing_sections = required_sections - config.keys()
    if missing_sections:
        raise ConfigError(
            f"Missing configuration sections: {sorted(missing_sections)}"
        )

    get_strategy_name(config)

    symbols = config["universe"].get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ConfigError("universe.symbols must contain at least one symbol.")

    risk_per_trade = config["risk"].get("risk_per_trade_usd")
    maximum_position = config["risk"].get("maximum_position_usd")

    if not isinstance(risk_per_trade, (int, float)) or risk_per_trade <= 0:
        raise ConfigError("risk_per_trade_usd must be greater than zero.")

    if not isinstance(maximum_position, (int, float)) or maximum_position <= 0:
        raise ConfigError("maximum_position_usd must be greater than zero.")

    if config["execution"].get("fail_closed") is not True:
        raise ConfigError("execution.fail_closed must remain true.")

    if config["execution"].get("require_broker_stop_confirmation") is not True:
        raise ConfigError(
            "Broker-held protective-stop confirmation must remain enabled."
        )

    return config


if __name__ == "__main__":
    loaded_config = load_config()

    print("Configuration loaded successfully.")
    print(f"Strategy: {loaded_config['strategy']['name']}")
    print(f"Enabled: {loaded_config['strategy']['enabled']}")
    print(f"Symbols: {len(loaded_config['universe']['symbols'])}")