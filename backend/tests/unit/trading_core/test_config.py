"""镜像 packages/trading-core/src/engine/config.spec.ts。"""

from __future__ import annotations

import pytest

from backend.trading_core.engine import (
    default_strategy_config,
    default_trend_config,
    eurusd_strategy_config,
    gbpjpy_strategy_config,
    gbpusd_strategy_config,
    get_strategy_config_by_symbol,
    gold_strategy_config,
    jpy_cross_strategy_config,
    oil_strategy_config,
    silver_strategy_config,
    us100_cash_strategy_config,
    usdcad_strategy_config,
)


class TestDefaultStrategyConfig:
    def test_returns_valid_config_with_all_fields(self) -> None:
        cfg = default_strategy_config()
        assert cfg["pullbackMinADX"] == pytest.approx(25.0)
        assert cfg["minScore"] == 5
        assert cfg["scaleInEnabled"] is True
        assert cfg["momentumScalpMinADX"] == pytest.approx(20.0)
        assert cfg["fibExtension"]["enabled"] is False
        assert cfg["pullbackFib"]["retracementEnabled"] is False
        assert cfg["trend"]["enabled"] is True


class TestDefaultTrendConfig:
    def test_returns_valid_trend_config(self) -> None:
        tc = default_trend_config()
        assert tc["d1Weight"] == pytest.approx(0.05)
        assert tc["h4Weight"] == pytest.approx(0.25)
        assert tc["h1Weight"] == pytest.approx(0.35)
        assert tc["m30Weight"] == pytest.approx(0.35)
        assert tc["enabled"] is True


class TestPerSymbolConfigs:
    def test_gold_config_has_correct_overrides(self) -> None:
        cfg = gold_strategy_config()
        assert cfg["momentumScalpMinADX"] == pytest.approx(18.0)
        assert cfg["pullbackFib"]["retracementEnabled"] is True

    def test_silver_config_has_wider_sl_tp(self) -> None:
        cfg = silver_strategy_config()
        assert cfg["pullbackSLATR"] == pytest.approx(2.0)
        assert cfg["pullbackTP1ATR"] == pytest.approx(3.0)
        assert cfg["h4ADXThreshold"] == 22

    def test_gbpjpy_config_has_lower_adx_thresholds(self) -> None:
        cfg = gbpjpy_strategy_config()
        assert cfg["h4ADXThreshold"] == pytest.approx(22.0)
        assert cfg["h4RequireConsecutive"] == 2
        assert cfg["pullbackSLATR"] == pytest.approx(1.8)
        assert cfg["momentumScalpSLATR"] == pytest.approx(0.8)

    def test_jpy_cross_inherits_gbpjpy_config(self) -> None:
        jpy = jpy_cross_strategy_config()
        gbpjpy = gbpjpy_strategy_config()
        assert jpy["h4ADXThreshold"] == pytest.approx(gbpjpy["h4ADXThreshold"])
        assert jpy["pullbackSLATR"] == pytest.approx(gbpjpy["pullbackSLATR"])

    def test_eurusd_config_has_tighter_sl(self) -> None:
        cfg = eurusd_strategy_config()
        assert cfg["h4ADXThreshold"] == pytest.approx(20.0)
        assert cfg["pullbackSLATR"] == pytest.approx(1.0)

    def test_gbpusd_config_is_between_eurusd_and_gbpjpy(self) -> None:
        cfg = gbpusd_strategy_config()
        assert cfg["h4ADXThreshold"] == pytest.approx(22.0)
        assert cfg["pullbackSLATR"] == pytest.approx(1.3)

    def test_usdcad_config_has_moderate_parameters(self) -> None:
        cfg = usdcad_strategy_config()
        assert cfg["h4ADXThreshold"] == pytest.approx(25.0)
        assert cfg["pullbackSLATR"] == pytest.approx(1.2)

    def test_us100cash_config_has_index_specific_tuning(self) -> None:
        cfg = us100_cash_strategy_config()
        assert cfg["h4RequireConsecutive"] == 3
        assert cfg["pullbackSLATR"] == pytest.approx(1.0)
        assert cfg["momentumScalpMaxHoldingMin"] == 60
        assert cfg["trend"]["h4Weight"] == pytest.approx(0.35)

    def test_oil_config_has_wide_sl_tp(self) -> None:
        cfg = oil_strategy_config()
        assert cfg["pullbackSLATR"] == pytest.approx(2.0)
        assert cfg["pullbackTP1ATR"] == pytest.approx(2.5)
        assert cfg["pullbackFib"]["retracementEnabled"] is True


class TestGetStrategyConfigBySymbol:
    def test_returns_correct_config_for_known_symbols(self) -> None:
        assert get_strategy_config_by_symbol("XAUUSD")["pullbackFib"]["retracementEnabled"] is True
        assert get_strategy_config_by_symbol("GOLD")["pullbackFib"]["retracementEnabled"] is True
        assert get_strategy_config_by_symbol("XAGUSD")["pullbackSLATR"] == pytest.approx(2.0)
        assert get_strategy_config_by_symbol("SILVER")["pullbackSLATR"] == pytest.approx(2.0)
        assert get_strategy_config_by_symbol("GBPJPY")["h4ADXThreshold"] == pytest.approx(22.0)
        assert get_strategy_config_by_symbol("EURJPY")["h4ADXThreshold"] == pytest.approx(22.0)
        assert get_strategy_config_by_symbol("USDJPY")["h4ADXThreshold"] == pytest.approx(22.0)
        assert get_strategy_config_by_symbol("EURUSD")["pullbackSLATR"] == pytest.approx(1.0)
        assert get_strategy_config_by_symbol("GBPUSD")["pullbackSLATR"] == pytest.approx(1.3)
        assert get_strategy_config_by_symbol("USDCAD")["pullbackSLATR"] == pytest.approx(1.2)
        assert get_strategy_config_by_symbol("US100CASH")["h4RequireConsecutive"] == 3
        assert get_strategy_config_by_symbol("USOILCASH")["pullbackSLATR"] == pytest.approx(2.0)
        assert get_strategy_config_by_symbol("UKOILCASH")["pullbackSLATR"] == pytest.approx(2.0)

    def test_returns_default_config_for_unknown_symbols(self) -> None:
        cfg = get_strategy_config_by_symbol("UNKNOWN")
        def_cfg = default_strategy_config()
        assert cfg["pullbackMinADX"] == pytest.approx(def_cfg["pullbackMinADX"])
        assert cfg["minScore"] == def_cfg["minScore"]
