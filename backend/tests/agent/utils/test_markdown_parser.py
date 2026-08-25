import pytest

from backend.agents.utils.markdown_parser import (
    detect_format,
    extract_fields,
    extract_list_items,
    extract_warnings,
    get_boolean_field,
    get_enum_field,
    get_number_field,
    get_string_field,
    parse_sr_level_line,
    parse_sr_levels,
    parse_warnings_line,
    split_sections,
)

# ── splitSections ────────────────────────────────────────────────────


def test_split_sections_splits_on_headers():
    # TS: splitSections 'splits on ## headers'
    raw = """## TECHNICAL
- Bias: bullish
- Confidence: 65

## WAVE
- Confirmation: partial
- Confidence: 55"""

    sections = split_sections(raw)

    assert len(sections) == 2
    assert "technical" in sections
    assert "wave" in sections
    assert "Bias: bullish" in sections["technical"]
    assert "Confirmation: partial" in sections["wave"]


def test_split_sections_normalizes_section_keys_to_lowercase_with_underscores():
    raw = """## Risk Assessment
- Risk Level: medium

## UNITED FRONT ANALYSIS
- Test: value"""

    sections = split_sections(raw)

    assert "risk_assessment" in sections
    assert "united_front_analysis" in sections

def test_split_sections_handles_hyphens_in_section_names():
    raw = """## SUPPORT LEVELS
- item1

## TRADE RECOMMENDATION
- item2"""

    sections = split_sections(raw)

    assert "support_levels" in sections
    assert "trade_recommendation" in sections

def test_split_sections_returns_root_fallback_when_no_headers():
    raw = """- Bias: bullish
- Confidence: 65"""

    sections = split_sections(raw)

    assert len(sections) == 1
    assert "root" in sections

def test_split_sections_returns_empty_map_for_empty_input():
    assert len(split_sections("")) == 0

def test_split_sections_handles_single_section():
    raw = """## TECHNICAL
- Bias: bearish"""

    sections = split_sections(raw)

    assert sections["technical"].strip() == "- Bias: bearish"

def test_split_sections_handles_5_sections_comprehensive_format():
    raw = """## TECHNICAL
- Bias: bullish

## WAVE
- Confirmation: confirmed

## CHANLUN
- Trend: up

## RISK
- Risk Level: medium

## ARBITRATION
- Final Direction: buy"""

    sections = split_sections(raw)

    assert len(sections) == 5
    assert "technical" in sections
    assert "wave" in sections
    assert "chanlun" in sections
    assert "risk" in sections
    assert "arbitration" in sections

# ── extractFields ────────────────────────────────────────────────────


def test_extract_fields_extracts_key_value_pairs():
    raw = """- Bias: bullish
- Confidence: 65
- Phase: trending"""

    fields = extract_fields(raw)

    assert fields.get("bias") == "bullish"
    assert fields.get("confidence") == "65"
    assert fields.get("phase") == "trending"

def test_extract_fields_normalizes_keys():
    raw = """- Risk Level: medium
- Final Direction: buy
- Max Position Size: 0.10"""

    fields = extract_fields(raw)

    assert fields.get("risk_level") == "medium"
    assert fields.get("final_direction") == "buy"
    assert fields.get("max_position_size") == "0.10"

def test_extract_fields_first_occurrence_wins_for_duplicate_keys():
    raw = """- Confidence: 65
- Confidence: 80"""

    fields = extract_fields(raw)

    assert fields.get("confidence") == "65"

def test_extract_fields_handles_values_with_colons():
    # TS: extractFields 'handles values with colons (e.g. rationale with time)'
    fields = extract_fields("- Rationale: H4/D1 uptrend, entry at 14:30 UTC")
    assert fields.get("rationale") == "H4/D1 uptrend, entry at 14:30 UTC"


def test_extract_fields_handles_chinese_text():
    # TS: extractFields 'handles Chinese text'
    fields = extract_fields("- Rationale: 多周期共振看多，H1回调结束 (Multi-TF bullish confluence, H1 pullback done)")
    assert "多周期共振看多" in fields.get("rationale", "")


def test_extract_fields_handles_empty_values():
    # TS: extractFields 'handles empty values'
    fields = extract_fields("- Primary Contradiction: ")
    assert fields.get("primary_contradiction") == ""


def test_extract_fields_ignores_non_kv_lines():
    raw = """Some random text
- Bias: bullish
More random text"""

    fields = extract_fields(raw)

    assert len(fields) == 1
    assert fields.get("bias") == "bullish"

def test_extract_fields_ignores_indented_list_items():
    section = """- Support Levels:
  - 4287.50 | support | strong | H1 | 3
- Bias: bullish"""

    fields = extract_fields(section)

    assert fields.get("bias") == "bullish"
    assert fields.get("4287.50") is None

# ── extractListItems ─────────────────────────────────────────────────


def test_extract_list_items_extracts_indented_list_items():
    raw = """- Support Levels:
  - 4287.50 | support | strong | H1 | 3
  - 4265.00 | support | moderate | H4 | 2"""

    items = extract_list_items(raw)

    assert len(items) == 2
    assert "4287.50" in items[0]
    assert "4265.00" in items[1]

def test_extract_list_items_excludes_kv_pairs_that_are_not_pipe_delimited():
    raw = """- Some Key: some value
  - Not a KV list item
  - Key-Value: excluded"""

    items = extract_list_items(raw)

    assert len(items) == 1
    assert items[0] == "Not a KV list item"

def test_extract_list_items_includes_pipe_delimited_lines_even_with_colons():
    # TS: extractListItems 'includes pipe-delimited lines even with colons'

    section = "  - 4287.50 | support | strong | H1 | 3"
    items = extract_list_items(section)

    assert len(items) == 1
    assert "4287.50" in items[0]

# ── getEnumField ─────────────────────────────────────────────────────


def test_get_enum_field_returns_exact_match():
    # TS: getEnumField 'returns exact match'

    fields = {"bias": "bullish"}

    assert get_enum_field(fields, "bias", ("bullish", "bearish", "neutral"), "neutral") == "bullish"

def test_get_enum_field_is_case_insensitive():
    # TS: getEnumField 'is case-insensitive'

    fields = {"bias": "BULLISH"}

    assert get_enum_field(fields, "bias", ("bullish", "bearish", "neutral"), "neutral") == "bullish"

def test_get_enum_field_returns_default_for_invalid_value():
    # TS: getEnumField 'returns default for invalid value'

    fields = {"bias": "dual"}

    assert get_enum_field(fields, "bias", ("bullish", "bearish", "neutral"), "neutral") == "neutral"

def test_get_enum_field_fuzzy_matches_with_hyphens_spaces():
    fields = {"phase": "mark up"}
    allowed = ("accumulation", "markup", "distribution", "markdown")

    assert get_enum_field(fields, "phase", allowed, "accumulation") == "markup"

def test_get_enum_field_returns_default_for_missing_key():
    # TS: getEnumField 'returns default for missing key'

    assert get_enum_field({}, "bias", ("bullish", "bearish", "neutral"), "neutral") == "neutral"

def test_get_enum_field_maps_common_llm_mistakes_buy_not_in_recommendation_enum():
    fields = {"recommendation": "buy"}
    allowed = ("hold", "close", "partial_close", "trail_stop", "none")

    assert get_enum_field(fields, "recommendation", allowed, "none") == "none"

# ── getNumberField ───────────────────────────────────────────────────


def test_get_number_field_extracts_a_plain_number():
    # TS: getNumberField 'extracts a plain number'

    assert get_number_field({"confidence": "65"}, "confidence", 0) == 65

def test_get_number_field_extracts_number_from_mixed_text():
    # TS: getNumberField 'extracts number from mixed text'

    assert get_number_field({"confidence": "65%"}, "confidence", 0) == 65

def test_get_number_field_extracts_decimal_number():
    # TS: getNumberField 'extracts decimal number'

    assert get_number_field({"entry_price": "4325.50"}, "entry_price", 0) == 4325.5

def test_get_number_field_extracts_negative_number():
    # TS: getNumberField 'extracts negative number'

    assert get_number_field({"value": "-12.5"}, "value", 0) == -12.5

def test_get_number_field_respects_min_constraint():
    assert get_number_field({"confidence": "-5"}, "confidence", 50, {"min": 0}) == 50

def test_get_number_field_respects_max_constraint():
    assert get_number_field({"confidence": "150"}, "confidence", 50, {"max": 100}) == 50

def test_get_number_field_returns_default_for_non_numeric_text():
    # TS: getNumberField 'returns default for non-numeric text'

    assert get_number_field({"confidence": "N/A"}, "confidence", 0) == 0

def test_get_number_field_returns_default_for_missing_key():
    # TS: getNumberField 'returns default for missing key'

    assert get_number_field({}, "confidence", 0) == 0

def test_get_number_field_extracts_number_from_wave_3():
    assert get_number_field({"extension_wave": "3"}, "extension_wave", 0, {"min": 1, "max": 5}) == 3

# ── getBooleanField ──────────────────────────────────────────────────


@pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "Yes"])
def test_get_boolean_field_parses_true_variations(val):
    assert get_boolean_field({"add_on": val}, "add_on", False) is True

@pytest.mark.parametrize("val", ["false", "False", "FALSE", "0", "no", "No"])
def test_get_boolean_field_parses_false_variations(val):
    assert get_boolean_field({"add_on": val}, "add_on", False) is False

def test_get_boolean_field_returns_default_for_invalid_text():
    # TS: getBooleanField 'returns default for invalid text'

    assert get_boolean_field({"add_on": "maybe"}, "add_on", False) is False

def test_get_boolean_field_returns_default_for_missing_key():
    assert get_boolean_field({}, "add_on", False) is False

# ── getStringField ───────────────────────────────────────────────────


def test_get_string_field_extracts_string_value():
    # TS: getStringField 'extracts string value'

    assert (
        get_string_field({"rationale": "Short-term bullish momentum"}, "rationale", "default")
        == "Short-term bullish momentum"
    )

def test_get_string_field_removes_html_tags():
    # TS: getStringField 'removes HTML tags'

    fields = {"rationale": "<script>alert(1)</script>Bullish<b> trend</b>"}

    assert get_string_field(fields, "rationale", "") == "alert(1)Bullish trend"

def test_get_string_field_truncates_to_max_length():
    # TS: getStringField 'truncates to maxLength'

    fields = {"rationale": "A" * 5000}

    assert len(get_string_field(fields, "rationale", "")) == 2000

def test_get_string_field_returns_default_for_missing_key():
    # TS: getStringField 'returns default for missing key'

    assert get_string_field({}, "rationale", "default") == "default"

def test_get_string_field_handles_chinese_text_with_special_characters():
    # TS: getStringField 'handles Chinese text with special characters'

    fields = {"rationale": "H4/D1上升趋势，H1回调 (H4/D1 uptrend, H1 pullback)"}

    assert "上升趋势" in get_string_field(fields, "rationale", "")

def test_get_string_field_returns_empty_string_for_empty_value():
    fields = {"primary_contradiction": ""}

    assert get_string_field(fields, "primary_contradiction", "N/A") == "N/A"

# ── parseSRLevelLine ─────────────────────────────────────────────────


def test_parse_sr_level_line_parses_valid_sr_level_line():
    result = parse_sr_level_line("4287.50 | support | strong | H1 | 3", "support")

    assert result is not None
    assert result["price"] == 4287.5
    assert result["type"] == "support"
    assert result["strength"] == "strong"
    assert result["timeframe"] == "H1"
    assert result["touches"] == 3

def test_parse_sr_level_line_defaults_strength_to_moderate_if_invalid():
    result = parse_sr_level_line("4287.50 | support | invalid | H1 | 3", "support")

    assert result is not None
    assert result["strength"] == "moderate"

def test_parse_sr_level_line_defaults_touches_to_1_if_missing():
    result = parse_sr_level_line("4287.50 | support | strong | H1", "support")

    assert result is not None
    assert result["touches"] == 1

def test_parse_sr_level_line_defaults_timeframe_to_h1_if_missing():
    result = parse_sr_level_line("4287.50 | support | strong", "support")

    assert result is not None
    assert result["timeframe"] == "H1"

def test_parse_sr_level_line_returns_none_for_non_numeric_price():
    assert parse_sr_level_line("abc | support | strong | H1 | 3", "support") is None

def test_parse_sr_level_line_returns_none_for_zero_price():
    assert parse_sr_level_line("0 | support | strong | H1 | 3", "support") is None

def test_parse_sr_level_line_returns_none_for_insufficient_parts():
    assert parse_sr_level_line("4287.50", "support") is None

def test_parse_sr_level_line_clamps_touches_to_0_20():
    result = parse_sr_level_line("4287.50 | support | strong | H1 | 999", "support")

    assert result is not None
    assert result["touches"] == 20

# ── parseSRLevels ────────────────────────────────────────────────────


def test_parse_sr_levels_parses_multiple_valid_lines():
    lines = [
        "4287.50 | support | strong | H1 | 3",
        "4265.00 | support | moderate | H4 | 2",
        "4250.00 | support | weak | M30 | 1",
    ]

    results = parse_sr_levels(lines, "support")

    assert len(results) == 3
    assert results[0]["price"] == 4287.50
    assert results[1]["price"] == 4265.00

def test_parse_sr_levels_filters_out_invalid_lines():
    lines = [
        "4287.50 | support | strong | H1 | 3",
        "invalid line",
        "4265.00 | support | moderate | H4 | 2",
    ]

    results = parse_sr_levels(lines, "support")

    assert len(results) == 2

def test_parse_sr_levels_limits_to_6_levels():
    lines = [f"{4200 + i * 10} | support | strong | H1 | 1" for i in range(10)]
    results = parse_sr_levels(lines, "support")

    assert len(results) == 6

# ── parseWarningsLine ────────────────────────────────────────────────


def test_parse_warnings_line_parses_semicolon_separated_warnings():
    # TS: parseWarningsLine 'parses semicolon-separated warnings'

    result = parse_warnings_line("Spread elevated; H4 resistance unbroken; RSI overbought")

    assert result == ["Spread elevated", "H4 resistance unbroken", "RSI overbought"]

def test_parse_warnings_line_handles_chinese_text():
    # TS: parseWarningsLine 'handles Chinese text'

    result = parse_warnings_line("点差偏高注意交易成本; H4阻力未突破 (H4 resistance unbroken)")

    assert len(result) == 2
    assert "点差偏高" in result[0]

def test_parse_warnings_line_removes_html_tags():
    # TS: parseWarningsLine 'removes HTML tags'

    result = parse_warnings_line("<script>alert(1)</script>; Normal warning")

    assert result == ["alert(1)", "Normal warning"]

def test_parse_warnings_line_limits_to_10_warnings():
    # TS: parseWarningsLine 'limits to 10 warnings'

    many = "; ".join(f"Warning {i}" for i in range(15))

    assert len(parse_warnings_line(many)) == 10

def test_parse_warnings_line_filters_empty_entries():
    # TS: parseWarningsLine 'filters empty entries'

    result = parse_warnings_line("Warning 1; ; Warning 2; ; ;")

    assert result == ["Warning 1", "Warning 2"]

def test_parse_warnings_line_returns_empty_array_for_empty_input():
    assert parse_warnings_line("") == []

def test_extract_warnings_falls_back_to_list_items():
    # TS: extractWarnings 'falls back to list items'

    list_items = ["Spread high", "RSI overbought"]

    assert extract_warnings({}, list_items) == ["Spread high", "RSI overbought"]

def test_extract_warnings_prefers_field_over_list_items():
    fields = {"warnings": "Field warning"}
    list_items = ["List warning 1", "List warning 2"]

    assert extract_warnings(fields, list_items) == ["Field warning"]

def test_extract_warnings_returns_empty_when_neither_available():
    assert extract_warnings({}, []) == []

# ── detectFormat ─────────────────────────────────────────────────────


def test_detect_format_detects_markdown():
    assert detect_format("## TECHNICAL\n- Bias: bullish") == "markdown"

def test_detect_format_detects_unknown():
    assert detect_format("Just some random text without structure") == "unknown"

def test_detect_format_empty_string_is_unknown():
    assert detect_format("") == "unknown"

COMPREHENSIVE_INPUT = """## TECHNICAL
- Bias: bullish
- Confidence: 65
- Phase: trending
- Indicators Summary: RSI中性偏强，MACD正值，短期多头排列 (RSI neutral-bullish, MACD positive)
- Recommendation: hold
- Rationale: 短期多头动能减弱，H4阻力明显 (Short-term momentum weakening)

## WAVE
- Confirmation: partial
- Extension Wave: 3
- Corrective Type: zigzag
- Trend Strength: moderate
- Target Level 1.618: 4380.50
- Target Level 2.0: 4412.00
- Confidence: 55
- Rationale: 第3浪延伸中 (Wave 3 extension in progress)

## CHANLUN
- Trend: up
- Strength: moderate
- Latest Signal: hold
- Hub State: active
- Confidence: 50
- Rationale: 中枢形成中 (Hub forming)

## RISK
- Risk Level: medium
- Max Position Size: 0.10
- Suggested SL: 4287.50
- Suggested TP: 4370.00
- Warnings: 点差偏高注意交易成本 (Spread elevated); H4阻力未突破 (H4 resistance unbroken)
- Add On: false

## ARBITRATION
- Final Direction: buy
- Confidence: 65
- Action: open
- Primary Contradiction:
- Phase: trending
- United Front Analysis: 道氏+波浪+缠论三方看多 (Dow+Wave+Chanlun all bullish)
- Reasoning: 多理论共振做多 (Multi-theory confluence)
- Dow Primary Trend: bullish
- Dow Primary Phase: markup
- Dow Secondary Trend: bullish
- Dow Short Term Trend: neutral
- Dow Multi TF Confirm: false
- Dow Rationale: H4/D1上升趋势 (H4/D1 uptrend)
- Wave Current Wave: Wave 3
- Wave Direction: impulse_up
- Wave Confidence: 60
- Wave Rationale: 第3浪延伸 (Wave 3 extension)
- Chanlun Trend: up
- Chanlun Bi Direction: up
- Chanlun Duan Direction: none
- Chanlun Zhongshu State: active
- Chanlun Buy Sell Point: buy_2
- Chanlun Confidence: 55
- Trade Direction: buy
- Trade Entry Price: 4325.00
- Trade Stop Loss: 4287.50
- Trade Take Profit 1: 4370.00
- Trade Take Profit 2: 4395.00
- Trade Risk Reward Ratio: 2.2
- Trade Position Size Lots: 0.05-0.1
- Trade Rationale: 多理论共振 (Multi-theory confluence)"""


def test_integration_splits_into_5_sections():
    # TS: integration 'splits into 5 sections'

    sections = split_sections(COMPREHENSIVE_INPUT)

    assert len(sections) == 5
    assert "technical" in sections
    assert "wave" in sections
    assert "chanlun" in sections
    assert "risk" in sections
    assert "arbitration" in sections

def test_integration_parses_technical_section():
    sections = split_sections(COMPREHENSIVE_INPUT)
    fields = extract_fields(sections["technical"])

    assert get_enum_field(fields, "bias", ("bullish", "bearish", "neutral"), "neutral") == "bullish"
    assert get_number_field(fields, "confidence", 0, {"min": 0, "max": 100}) == 65
    phase_allowed = ("trending", "ranging", "breakout", "reversal", "consolidation")
    assert get_enum_field(fields, "phase", phase_allowed, "consolidation") == "trending"
    rec_allowed = ("hold", "close", "partial_close", "trail_stop", "none")
    assert get_enum_field(fields, "recommendation", rec_allowed, "none") == "hold"

def test_integration_parses_wave_section():
    sections = split_sections(COMPREHENSIVE_INPUT)
    fields = extract_fields(sections["wave"])

    conf_allowed = ("confirmed", "partial", "rejected")
    assert get_enum_field(fields, "confirmation", conf_allowed, "rejected") == "partial"
    assert get_number_field(fields, "extension_wave", 0) == 3
    assert get_number_field(fields, "target_level_1.618", 0) == 4380.50
    assert get_number_field(fields, "confidence", 0, {"min": 0, "max": 100}) == 55

def test_integration_parses_chanlun_section():
    sections = split_sections(COMPREHENSIVE_INPUT)
    fields = extract_fields(sections["chanlun"])

    assert get_enum_field(fields, "trend", ("up", "down", "range"), "range") == "up"
    assert get_enum_field(fields, "strength", ("strong", "moderate", "weak"), "weak") == "moderate"
    assert get_enum_field(fields, "latest_signal", ("buy", "sell", "hold"), "hold") == "hold"
    assert get_enum_field(fields, "hub_state", ("forming", "active", "none"), "none") == "active"
    assert get_number_field(fields, "confidence", 0, {"min": 0, "max": 100}) == 50

def test_integration_parses_risk_section():
    sections = split_sections(COMPREHENSIVE_INPUT)
    fields = extract_fields(sections["risk"])

    assert get_enum_field(fields, "risk_level", ("low", "medium", "high", "extreme"), "medium") == "medium"
    assert get_number_field(fields, "max_position_size", 0) == pytest.approx(0.10)
    assert get_number_field(fields, "suggested_sl", 0) == 4287.50
    assert get_number_field(fields, "suggested_tp", 0) == 4370.00
    assert get_boolean_field(fields, "add_on", False) is False

    warnings = extract_warnings(fields, [])
    assert len(warnings) == 2
    assert "点差偏高" in warnings[0]

def test_integration_parses_arbitration_section():
    sections = split_sections(COMPREHENSIVE_INPUT)
    fields = extract_fields(sections["arbitration"])

    assert get_enum_field(fields, "final_direction", ("buy", "sell", "hold", "close"), "hold") == "buy"
    assert get_number_field(fields, "confidence", 0, {"min": 0, "max": 100}) == 65
    assert get_enum_field(fields, "action", ("open", "close", "modify", "hold"), "hold") == "open"

    # Nested Dow theory fields
    assert get_enum_field(fields, "dow_primary_trend", ("bullish", "bearish", "neutral"), "neutral") == "bullish"
    dow_phase = ("accumulation", "markup", "distribution", "markdown")
    assert get_enum_field(fields, "dow_primary_phase", dow_phase, "accumulation") == "markup"
    assert get_boolean_field(fields, "dow_multi_tf_confirm", False) is False

    # Trade recommendation fields
    assert get_enum_field(fields, "trade_direction", ("buy", "sell", "hold"), "hold") == "buy"
    assert get_number_field(fields, "trade_entry_price", 0) == 4325.00
    assert get_number_field(fields, "trade_stop_loss", 0) == 4287.50
    assert get_number_field(fields, "trade_take_profit_1", 0) == 4370.00
    assert get_number_field(fields, "trade_take_profit_2", 0) == 4395.00
    assert get_number_field(fields, "trade_risk_reward_ratio", 0) == pytest.approx(2.2)

# ── Edge cases: real LLM failure patterns ────────────────────────────


def test_edge_case_handles_chinese_quotes_in_rationale():
    # TS: edge cases 'handles Chinese quotes in rationale (JSON killer)'
    fields = extract_fields("- Rationale: 多周期指标分化：短线M15/M30看涨但超买，H4仍偏空，无明显趋势方向。")
    assert fields.get("rationale") is not None
    assert "多周期指标分化" in fields["rationale"]


def test_edge_case_handles_incomplete_output_truncation():
    # TS: edge cases 'handles incomplete output (truncation)'
    raw = """## TECHNICAL
- Bias: bullish
- Confidence: 65

## WAVE
- Confirmation: partial
- Confidence:"""

    sections = split_sections(raw)
    assert len(sections) == 2

    tech_fields = extract_fields(sections["technical"])
    assert get_enum_field(tech_fields, "bias", ("bullish", "bearish", "neutral"), "neutral") == "bullish"

    wave_fields = extract_fields(sections["wave"])
    assert get_number_field(wave_fields, "confidence", 50, {"min": 0, "max": 100}) == 50


def test_edge_case_handles_extra_whitespace_and_blank_lines():
    # TS: edge cases 'handles extra whitespace and blank lines'

    raw = """## TECHNICAL

-   Bias:    bullish

- Confidence: 65

"""

    sections = split_sections(raw)
    fields = extract_fields(sections["technical"])
    assert get_number_field(fields, "confidence", 0) == 65


def test_edge_case_handles_enum_value_outside_schema():
    # TS: edge cases 'handles enum value outside schema (dual → default)'
    allowed = ("buy", "sell", "hold", "close")
    fields = extract_fields("- Final Direction: dual")

    assert get_enum_field(fields, "final_direction", allowed, "hold") == "hold"


def test_edge_case_handles_confidence_greater_than_100():
    fields = extract_fields("- Confidence: 999")

    assert get_number_field(fields, "confidence", 50, {"min": 0, "max": 100}) == 50

def test_edge_case_handles_sr_levels_with_occasional_bad_lines():
    lines = [
        "4287.50 | support | strong | H1 | 3",
        "N/A  | invalid line",
        "4265.00 | support | moderate | H4 | 2",
        "",
        "0 | support | strong | H1 | 1",
    ]

    results = parse_sr_levels(lines, "support")

    assert len(results) == 2
    assert results[0]["price"] == 4287.50
    assert results[1]["price"] == 4265.00

def test_edge_case_handles_html_injection_in_rationale():
    fields = extract_fields("- Rationale: <img src=x onerror=alert(1)>Bullish trend")

    assert get_string_field(fields, "rationale") == "Bullish trend"

def test_edge_case_handles_very_long_text_truncation():
    fields = {"rationale": "A" * 5000}

    assert len(get_string_field(fields, "rationale", "", 2000)) == 2000

def test_edge_case_handles_position_size_with_range_format():
    fields = {"trade_position_size_lots": "0.05-0.1"}

    assert get_number_field(fields, "trade_position_size_lots", 0) == pytest.approx(0.05)

def test_edge_case_handles_wave_3_in_extension_wave():
    fields = extract_fields("- Extension Wave: Wave 3")

    assert get_number_field(fields, "extension_wave", 0, {"min": 1, "max": 5}) == 3
