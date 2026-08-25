"""镜像 packages/trading-core/src/harmonic/detector.spec.ts(逐 it() 用例镜像)。"""

from __future__ import annotations

from backend.trading_core.harmonic import build_context, detect_patterns

VALID_TYPES = {"gartley", "bat", "butterfly", "crab", "abcd", "deep_crab"}


def make_bar(high: float, low: float, close: float, open_: float) -> dict:
    return {"high": high, "low": low, "close": close, "open": open_}


class TestDetectPatterns:
    def test_returns_empty_for_insufficient_bars(self) -> None:
        assert detect_patterns([], "H4") == []
        assert detect_patterns([make_bar(100, 95, 98, 97)], "H4") == []

    def test_detects_patterns_in_a_zigzag_series(self) -> None:
        # 构造带清晰摆动、可能形成类 Gartley 形态的序列
        bars = [
            make_bar(100, 98, 99, 98.5),  # X - high
            make_bar(96, 94, 95, 95.5),  # A - low
            make_bar(98, 96, 97, 97.5),  # B - high(XA 的 ~0.618 回撤)
            make_bar(95, 93, 94, 94.5),  # C - low
            make_bar(97, 95, 96, 96.5),  # D - potential PRZ
        ]
        patterns = detect_patterns(bars, "H4")
        # 是否找到形态取决于比率精度
        assert isinstance(patterns, list)

    def test_sets_correct_fields_on_detected_patterns(self) -> None:
        # 用更真实、摆动交替的序列
        prices = [100, 80, 92, 75, 88, 70, 85, 72, 90, 68, 82, 74, 86, 71, 83]
        bars = [make_bar(p + 2, p - 2, p, p + 1) for p in prices]
        patterns = detect_patterns(bars, "H1")
        for p in patterns:
            assert p["type"] in VALID_TYPES
            assert p["direction"] in ("bullish", "bearish")
            assert p["timeframe"] == "H1"
            assert p["score"] >= 0
            assert p["score"] <= 100


class TestBuildContext:
    def test_builds_context_from_multi_timeframe_bars(self) -> None:
        prices = [100, 80, 92, 75, 88, 70, 85, 72, 90, 68, 82, 74, 86, 71, 83, 78, 89, 73, 84, 77]

        def make_swing_bars() -> list[dict]:
            return [make_bar(p + 2, p - 2, p, p + 1) for p in prices]

        ctx = build_context(make_swing_bars(), make_swing_bars(), make_swing_bars())
        assert isinstance(ctx["h4Patterns"], list)
        assert isinstance(ctx["h1Patterns"], list)
        assert isinstance(ctx["m30Patterns"], list)
        assert isinstance(ctx["directionBias"], str)
        assert isinstance(ctx["score"], (int, float))
        assert isinstance(ctx["summary"], str)

    def test_returns_neutral_context_for_empty_bars(self) -> None:
        ctx = build_context([], [], [])
        assert ctx["directionBias"] == "neutral"
        assert ctx["activePattern"] is None
