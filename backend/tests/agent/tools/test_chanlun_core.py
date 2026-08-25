"""镜像 gold-bot `apps/app-agent/src/tools/chanlun-core.test.ts`。"""
from backend.agents.tools.chanlun_core import (
    analyze_chanlun,
    build_hubs,
    build_strokes,
    detect_fractals,
    process_containment,
)
from backend.agents.types.analysis import ChanlunBar, ChanlunFractal, ChanlunStroke


def test_handles_containment_in_up_direction_by_keeping_higher_high_and_higher_low():
    # TS: processContainment 'handles containment in an up direction by keeping higher high and higher low'
    bars = [
        ChanlunBar(index=0, open=10, high=10, low=8, close=9),
        ChanlunBar(index=1, open=9, high=12, low=9, close=11),
        ChanlunBar(index=2, open=11, high=11, low=10, close=10.5),
        ChanlunBar(index=3, open=10.5, high=13, low=11, close=12),
    ]

    processed = process_containment(bars)

    assert len(processed) == 3
    assert processed[1].index == 1
    assert processed[1].high == 12
    assert processed[1].low == 10


def test_detects_a_top_fractal_from_three_processed_bars():
    # TS: detectFractals 'detects a top fractal from three processed bars'
    bars = [
        ChanlunBar(index=0, open=10, high=11, low=9, close=10),
        ChanlunBar(index=1, open=10, high=14, low=10, close=13),
        ChanlunBar(index=2, open=13, high=12, low=8, close=9),
    ]

    fractals = detect_fractals(bars)

    assert fractals == [ChanlunFractal(type="top", index=1, price=14, confirmed=True)]


def test_detects_a_bottom_fractal_from_three_processed_bars():
    # TS: detectFractals 'detects a bottom fractal from three processed bars'
    bars = [
        ChanlunBar(index=0, open=12, high=14, low=10, close=13),
        ChanlunBar(index=1, open=13, high=12, low=7, close=8),
        ChanlunBar(index=2, open=8, high=13, low=9, close=12),
    ]

    fractals = detect_fractals(bars)

    assert fractals == [ChanlunFractal(type="bottom", index=1, price=7, confirmed=True)]


def test_confirms_a_stroke_when_alternating_fractals_are_separated_by_at_least_one_independent_bar():
    # TS: buildStrokes 'confirms a stroke when alternating fractals are separated by at least one independent bar'
    fractals = [
        ChanlunFractal(type="top", index=1, price=14, confirmed=True),
        ChanlunFractal(type="bottom", index=3, price=8, confirmed=True),
    ]

    strokes = build_strokes(fractals)

    assert strokes == [
        ChanlunStroke(
            startIndex=1,
            endIndex=3,
            startPrice=14,
            endPrice=8,
            direction="down",
            high=14,
            low=8,
        )
    ]


def test_detects_a_hub_when_three_consecutive_strokes_overlap():
    # TS: buildHubs 'detects a hub when three consecutive strokes overlap'
    strokes = [
        ChanlunStroke(startIndex=1, endIndex=3, startPrice=14, endPrice=9, direction="down", high=14, low=9),
        ChanlunStroke(startIndex=3, endIndex=5, startPrice=9, endPrice=13, direction="up", high=13, low=9),
        ChanlunStroke(startIndex=5, endIndex=7, startPrice=13, endPrice=10, direction="down", high=13, low=10),
    ]

    hubs = build_hubs(strokes)

    assert len(hubs) == 1
    assert hubs[0].startIndex == 1
    assert hubs[0].endIndex == 7
    assert hubs[0].high == 13
    assert hubs[0].low == 10
    assert hubs[0].strokeIndices == (0, 1, 2)


def test_aggregates_processed_bars_fractals_strokes_and_hubs():
    # TS: analyzeChanlun 'aggregates processed bars, fractals, strokes, and hubs'
    bars = [
        ChanlunBar(index=0, open=10, high=11, low=9, close=10),
        ChanlunBar(index=1, open=10, high=14, low=10, close=13),
        ChanlunBar(index=2, open=13, high=12, low=9, close=10),
        ChanlunBar(index=3, open=10, high=11, low=8, close=9),
        ChanlunBar(index=4, open=9, high=12, low=9, close=11),
        ChanlunBar(index=5, open=11, high=13, low=10, close=12),
        ChanlunBar(index=6, open=12, high=12, low=9, close=10),
        ChanlunBar(index=7, open=10, high=11, low=7, close=8),
        ChanlunBar(index=8, open=8, high=11, low=8, close=10),
        ChanlunBar(index=9, open=10, high=12, low=9, close=11),
        ChanlunBar(index=10, open=11, high=11, low=8, close=9),
        ChanlunBar(index=11, open=9, high=10, low=6, close=7),
        ChanlunBar(index=12, open=7, high=11, low=7, close=10),
    ]

    analysis = analyze_chanlun(bars)

    assert len(analysis.processedBars) > 0
    assert [fractal.type for fractal in analysis.fractals] == [
        "top",
        "bottom",
        "top",
        "bottom",
        "top",
        "bottom",
    ]
    assert len(analysis.strokes) == 5
    assert len(analysis.hubs) > 0
    assert analysis.hubs[0].high == 13
    assert analysis.hubs[0].low == 8
