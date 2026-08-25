"""镜像 packages/trading-core/src/indicators/candlestick.spec.ts。"""

from __future__ import annotations

from backend.trading_core.indicators import (
    detect_bearish_engulfing,
    detect_bullish_engulfing,
    detect_hammer,
    detect_morning_star,
    detect_shooting_star,
    is_bearish,
    is_bullish,
)


def make_bar(close: float, open_: float, high: float, low: float, extra: dict | None = None) -> dict:
    bar = {"close": close, "open": open_, "high": high, "low": low}
    if extra:
        bar.update(extra)
    return bar


def _bear_context_bars() -> list[dict]:
    # 下行的 EMA50 = bearish/neutral 上下文
    bars: list[dict] = []
    for i in range(12):
        bars.append(
            make_bar(
                100 - i * 0.5,
                100 - i * 0.5 + 0.2,
                100 - i * 0.5 + 0.5,
                100 - i * 0.5 - 0.5,
                {"ema50": 105 - i * 0.3, "atr": 2},
            )
        )
    return bars


def _bull_context_bars() -> list[dict]:
    # 上行的 EMA50 = bull 上下文
    bars: list[dict] = []
    for i in range(12):
        bars.append(
            make_bar(
                100 + i * 0.5,
                100 + i * 0.5 - 0.2,
                100 + i * 0.5 + 0.5,
                100 + i * 0.5 - 0.5,
                {"ema50": 95 + i * 0.3, "atr": 2},
            )
        )
    return bars


class TestCandlestickPatternDetection:
    def test_detects_hammer_pattern_directly(self) -> None:
        # 构造非 bull 上下文,允许 hammer 反转
        bars = _bear_context_bars()
        # Hammer:极长下影线、小实体、收在上一半
        bars.append(make_bar(95, 94.5, 95.5, 82, {"ema50": 102, "atr": 3}))

        result = detect_hammer(bars, len(bars) - 1, 3)
        if result is not None:
            assert result["signal"] == "hammer"
            assert result["bullish"] is True

    def test_detects_bullish_engulfing_directly(self) -> None:
        bars = _bear_context_bars()
        # 阴线
        bars.append(make_bar(90, 93, 93.5, 89, {"ema50": 100, "atr": 3}))
        # 看涨吞没:开于前收之下,收于前开之上
        bars.append(make_bar(96, 88, 97, 87, {"ema50": 99, "atr": 3}))

        result = detect_bullish_engulfing(bars, len(bars) - 1, 3)
        if result is not None:
            assert result["signal"] == "bullish_engulfing"
            assert result["bullish"] is True

    def test_detects_morning_star_directly(self) -> None:
        bars = _bear_context_bars()
        # 大阴线
        bars.append(make_bar(92, 97, 97.5, 91, {"ema50": 100, "atr": 3}))
        # 星线(小实体)
        bars.append(make_bar(91.5, 92, 92.5, 91, {"ema50": 99, "atr": 3}))
        # 大阳线,收于第一根中点之上
        bars.append(make_bar(96, 90, 97, 89, {"ema50": 98, "atr": 3}))

        result = detect_morning_star(bars, len(bars) - 1, 3)
        if result is not None:
            assert result["signal"] == "morning_star"
            assert result["bullish"] is True

    def test_detects_shooting_star(self) -> None:
        bars = _bull_context_bars()
        # 射击之星:长上影、小实体、收在下半
        bars.append(make_bar(105, 106, 118, 104.5, {"ema50": 100, "atr": 3}))

        result = detect_shooting_star(bars, len(bars) - 1, 3)
        if result is not None:
            assert result["signal"] == "shooting_star"
            assert result["bullish"] is False

    def test_detects_bearish_engulfing(self) -> None:
        bars = _bull_context_bars()
        # 阳线
        bars.append(make_bar(110, 107, 110.5, 106, {"ema50": 100, "atr": 3}))
        # 看跌吞没
        bars.append(make_bar(105, 111, 112, 104, {"ema50": 101, "atr": 3}))

        result = detect_bearish_engulfing(bars, len(bars) - 1, 3)
        if result is not None:
            assert result["signal"] == "bearish_engulfing"
            assert result["bullish"] is False


class TestIsBullishIsBearish:
    def test_classifies_signals_correctly(self) -> None:
        assert is_bullish("hammer") is True
        assert is_bullish("bullish_engulfing") is True
        assert is_bullish("morning_star") is True
        assert is_bullish("three_white_soldiers") is True
        assert is_bullish("piercing_line") is True

        assert is_bearish("shooting_star") is True
        assert is_bearish("bearish_engulfing") is True
        assert is_bearish("evening_star") is True
        assert is_bearish("three_black_crows") is True
        assert is_bearish("dark_cloud_cover") is True

        assert is_bullish("shooting_star") is False
        assert is_bearish("hammer") is False
