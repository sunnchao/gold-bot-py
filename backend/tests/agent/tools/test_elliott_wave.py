"""镜像 gold-bot `apps/app-agent/src/tools/elliott-wave.test.ts`。"""
from backend.agents.tools.elliott_wave import (
    detect_swing_points,
    label_corrective_waves,
    label_impulse_waves,
    validate_wave_rules,
)
from backend.agents.types.analysis import ElliottWaveSegment, ElliottWaveSwingPoint


def test_detects_alternating_swing_highs_and_lows_with_zigzag_filtering():
    # TS: detectSwingPoints 'detects alternating swing highs and lows with zigzag filtering'
    prices = [100, 105, 102, 110, 106, 114, 109, 118, 112]

    swings = detect_swing_points(prices, 0.02)

    assert [swing.type for swing in swings] == [
        "low",
        "high",
        "low",
        "high",
        "low",
        "high",
        "low",
        "high",
        "low",
    ]
    assert swings[1].price == 105
    assert swings[2].price == 102


def test_labels_a_bullish_five_wave_impulse_from_alternating_swing_points():
    # TS: labelImpulseWaves 'labels a bullish five-wave impulse from alternating swing points'
    swings = [
        ElliottWaveSwingPoint(index=0, price=100, type="low"),
        ElliottWaveSwingPoint(index=1, price=110, type="high"),
        ElliottWaveSwingPoint(index=2, price=104, type="low"),
        ElliottWaveSwingPoint(index=3, price=124, type="high"),
        ElliottWaveSwingPoint(index=4, price=116, type="low"),
        ElliottWaveSwingPoint(index=5, price=136, type="high"),
    ]

    waves = label_impulse_waves(swings, "bullish")

    assert len(waves) == 5
    assert [wave.wave for wave in waves] == [1, 2, 3, 4, 5]
    assert [wave.direction for wave in waves] == ["up", "down", "up", "down", "up"]
    assert waves[2].length > waves[0].length


def test_rejects_an_impulse_when_wave_3_is_the_shortest_motive_wave():
    # TS: validateWaveRules 'rejects an impulse when wave 3 is the shortest motive wave'
    impulse = [
        ElliottWaveSegment(wave=1, startIndex=0, endIndex=1, startPrice=100, endPrice=112, direction="up", length=12),
        ElliottWaveSegment(wave=2, startIndex=1, endIndex=2, startPrice=112, endPrice=106, direction="down", length=6),
        ElliottWaveSegment(wave=3, startIndex=2, endIndex=3, startPrice=106, endPrice=114, direction="up", length=8),
        ElliottWaveSegment(wave=4, startIndex=3, endIndex=4, startPrice=114, endPrice=113, direction="down", length=1),
        ElliottWaveSegment(wave=5, startIndex=4, endIndex=5, startPrice=113, endPrice=130, direction="up", length=17),
    ]

    validation = validate_wave_rules(impulse, "bullish")

    assert validation.isValid is False
    assert "Wave 3 cannot be the shortest motive wave." in validation.violations


def test_labels_an_abc_correction_after_a_bullish_impulse():
    # TS: labelCorrectiveWaves 'labels an ABC correction after a bullish impulse'
    swings = [
        ElliottWaveSwingPoint(index=0, price=100, type="low"),
        ElliottWaveSwingPoint(index=1, price=110, type="high"),
        ElliottWaveSwingPoint(index=2, price=104, type="low"),
        ElliottWaveSwingPoint(index=3, price=124, type="high"),
        ElliottWaveSwingPoint(index=4, price=116, type="low"),
        ElliottWaveSwingPoint(index=5, price=136, type="high"),
        ElliottWaveSwingPoint(index=6, price=126, type="low"),
        ElliottWaveSwingPoint(index=7, price=131, type="high"),
        ElliottWaveSwingPoint(index=8, price=120, type="low"),
    ]

    waves = label_corrective_waves(swings, "bullish", 5)

    assert len(waves) == 3
    assert [wave.wave for wave in waves] == ["A", "B", "C"]
    assert [wave.direction for wave in waves] == ["down", "up", "down"]
