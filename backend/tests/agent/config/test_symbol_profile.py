"""镜像 gold-bot `apps/app-agent/src/config/symbol-profile.test.ts`。"""
from backend.agents.agents import _support
from backend.agents.config.symbol_profile import detect_cross_instrument_price, get_symbol_profile


def test_keeps_standard_gold_at_the_default_minimum_lot_size():
    # TS: 'keeps standard gold at the default minimum lot size'
    assert get_symbol_profile("GOLD").maxLots == 0.5


def test_raises_the_minimum_lot_size_for_xm_style_micro_gold_symbols():
    # TS: 'raises the minimum lot size for XM-style micro gold symbols'
    assert get_symbol_profile("GOLDm#").maxLots == 0.5


def test_does_not_mutate_the_base_profile_after_resolving_a_micro_symbol():
    # TS: 'does not mutate the base profile after resolving a micro symbol'

    assert get_symbol_profile("GOLD").minLots == 0.01


def test_resolves_xm_style_micro_silver_symbols_to_the_xagusd_profile_with_micro_lot_bounds():
    # TS: 'resolves XM-style micro silver symbols to the XAGUSD profile with micro lot bounds'
    profile = get_symbol_profile("SILVERm#")

    assert profile.minLots == 0.1
    assert profile.assetClass == "metal"
    assert profile.priceRange == (15, 50)


def test_resolves_lowercase_xm_style_micro_silver_symbols_to_the_xagusd_profile():
    # TS: 'resolves lowercase XM-style micro silver symbols to the XAGUSD profile'
    profile = get_symbol_profile("silverm#")

    assert profile.minLots == 0.1
    assert profile.assetClass == "metal"
    assert profile.priceRange == (15, 50)


def test_support_symbol_profile_preserves_mixed_case_and_micro_suffix():
    us100 = _support.get_symbol_profile("US100Cash")
    assert us100["symbol"] == "US100CASH"
    assert us100["asset_class"] == "index"
    assert _support._has_micro_contract_suffix("XAUUSDm") is True
    assert _support._has_micro_contract_suffix("XAUUSD") is False


def test_detect_cross_instrument_price_skips_self_case_variants():
    assert detect_cross_instrument_price("GOLDM#", 3600.0, 3600.0, {"GOLDm#": 3600.0}) is None
    assert _support.detect_cross_instrument_price("GOLDM#", 3600.0, 3600.0, {"GOLDm#": 3600.0}) is None
