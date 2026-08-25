"""逐 symbol 的策略配置(镜像 packages/trading-core/src/engine/config.ts)。

TS 源头部注明 Ported from internal/strategy/engine/config.go,
此处沿用它各字段的取值范围注释;dict 键保持 TS 的 camelCase 原样。
"""

from __future__ import annotations

from typing import Any

FibExtensionTPConfig = dict[str, Any]
"""镜像 FibExtensionTPConfig:enabled / minADX / swingWindow / useH4Preference。"""

PullbackFibConfig = dict[str, Any]
"""镜像 PullbackFibConfig:retracementEnabled / goldenPocketBufferATR / requireRSIConfirm /
rsiConfirmBullThreshold / rsiConfirmBearThreshold / stopLossOuterATR / usePendingOrder /
pendingOrderLevel / maxFibSLDistATR / fibMinRR。"""

TrendConfig = dict[str, Any]
"""镜像 TrendConfig:d1Weight 0.05 / h4Weight 0.25 / h1Weight 0.35 / m30Weight 0.35 /
softThreshold 0.30 / mediumThreshold 0.15 / weakADXThreshold 20 / strongADXThreshold 30 /
enabled true。"""

StrategyConfig = dict[str, Any]
"""镜像 StrategyConfig:各 strategy 的参数字段 + fibExtension / pullbackFib / trend 子对象。"""

__all__ = [
    "FibExtensionTPConfig",
    "PullbackFibConfig",
    "StrategyConfig",
    "TrendConfig",
    "default_strategy_config",
    "default_trend_config",
    "eurusd_strategy_config",
    "gbpjpy_strategy_config",
    "gbpusd_strategy_config",
    "get_strategy_config_by_symbol",
    "gold_strategy_config",
    "jpy_cross_strategy_config",
    "oil_strategy_config",
    "silver_strategy_config",
    "us100_cash_strategy_config",
    "usdcad_strategy_config",
]

# ---------------------------------------------------------------- Default Config

def default_trend_config() -> TrendConfig:
    return {
        "d1Weight": 0.05,
        "h4Weight": 0.25,
        "h1Weight": 0.35,
        "m30Weight": 0.35,
        "softThreshold": 0.30,
        "mediumThreshold": 0.15,
        "weakADXThreshold": 20,
        "strongADXThreshold": 30,
        "enabled": True,
    }

def default_strategy_config() -> StrategyConfig:
    return {
        "pullbackMinADX": 25.0,
        "pullbackRSIOversold": 30.0,
        "pullbackRSIOverbought": 70.0,
        "pullbackDistATR": 0.5,
        "pullbackADXBonus": 30.0,
        "pullbackSLATR": 1.5,
        "pullbackTP1ATR": 1.5,
        "pullbackTP2ATR": 3.0,

        "breakoutRetestLookback": 50,
        "breakoutRetestConfirmWindow": 3,
        "breakoutRetestDistATR": 0.5,
        "breakoutRetestSLATR": 1.5,
        "breakoutRetestTP1ATR": 2.0,
        "breakoutRetestTP2ATR": 4.0,

        "divergenceWindowRecent": 15,
        "divergenceWindowPrev": 15,
        "divergenceRSIBullThresh": 40.0,
        "divergenceRSIBearThresh": 60.0,
        "divergenceSLATR": 1.0,
        "divergenceTP1ATR": 2.0,
        "divergenceTP2ATR": 4.0,

        "breakoutPyramidMinADX": 30.0,
        "breakoutPyramidSLATR": 1.5,
        "breakoutPyramidMinSpacingATR": 2.0,

        "scaleInEnabled": True,
        "scaleInMinADX": 25.0,
        "scaleInMinDistATR": 1.5,
        "scaleInMinFloatLossATR": 0.5,
        "scaleInMaxAddCount": 2,
        "scaleInLotDecay": 0.6,
        "scaleInSLATR": 1.2,
        "scaleInTP1ATR": 1.5,
        "scaleInTP2ATR": 3.0,
        "scaleInMinIntervalMin": 30,
        "scaleInMaxFloatLossPct": 5.0,

        "srBufferATR": 0.5,
        "srMaxDistATR": 3.0,
        "srMinDistATR": 0.3,

        "h4ADXThreshold": 30.0,
        "h4RequireConsecutive": 3,

        "m15ConfirmRSIThreshold": 40.0,

        "minScore": 5,
        "minRR": 1.25,

        "momentumScalpMinADX": 20.0,
        "momentumScalpEMAPeriod1": 5,
        "momentumScalpEMAPeriod2": 8,
        "momentumScalpEMAPeriod3": 12,
        "momentumScalpRSIBullThresh": 45.0,
        "momentumScalpRSIBearThresh": 55.0,
        "momentumScalpRSICrossoverBull": 48.0,
        "momentumScalpRSICrossoverBear": 52.0,
        "momentumScalpSLATR": 0.4,
        "momentumScalpTP1ATR": 0.5,
        "momentumScalpTP2ATR": 0.8,
        "momentumScalpVolConfirm": 1.05,
        "momentumScalpMinScore": 7,
        "momentumScalpMaxHoldingMin": 20,

        "fibExtension": {
            "enabled": False,
            "minADX": 25.0,
            "swingWindow": 50,
            "useH4Preference": True,
        },
        "pullbackFib": {
            "retracementEnabled": False,
            "goldenPocketBufferATR": 0.5,
            "requireRSIConfirm": False,
            "rsiConfirmBullThreshold": 40,
            "rsiConfirmBearThreshold": 60,
            "stopLossOuterATR": 0.5,
            "usePendingOrder": False,
            "pendingOrderLevel": "618",
            "maxFibSLDistATR": 1.5,
            "fibMinRR": 1.25,
        },
        "trend": default_trend_config(),
    }


# ---------------------------------------------------------------- Per-Symbol Configs

def gold_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["pullbackMinADX"] = 25.0
    cfg["pullbackSLATR"] = 1.5
    cfg["pullbackTP1ATR"] = 1.5
    cfg["pullbackTP2ATR"] = 3.0
    cfg["momentumScalpMinADX"] = 18.0
    cfg["momentumScalpVolConfirm"] = 1.05
    cfg["momentumScalpMinScore"] = 6
    cfg["fibExtension"]["minADX"] = 25.0
    cfg["pullbackFib"]["retracementEnabled"] = True
    return cfg

def silver_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["pullbackSLATR"] = 2.0
    cfg["pullbackTP1ATR"] = 3.0
    cfg["pullbackTP2ATR"] = 5.0
    cfg["minScore"] = 6
    cfg["h4ADXThreshold"] = 22
    cfg["momentumScalpMinADX"] = 15.0
    cfg["momentumScalpSLATR"] = 0.6
    cfg["momentumScalpTP1ATR"] = 0.8
    cfg["momentumScalpTP2ATR"] = 1.2
    cfg["momentumScalpMinScore"] = 7
    return cfg

def gbpjpy_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()

    cfg["h4ADXThreshold"] = 22.0
    cfg["h4RequireConsecutive"] = 2

    cfg["pullbackMinADX"] = 20.0
    cfg["pullbackRSIOversold"] = 35.0
    cfg["pullbackRSIOverbought"] = 65.0
    cfg["pullbackDistATR"] = 0.6
    cfg["pullbackADXBonus"] = 25.0
    cfg["pullbackSLATR"] = 1.8
    cfg["pullbackTP1ATR"] = 2.0
    cfg["pullbackTP2ATR"] = 3.5

    cfg["breakoutRetestLookback"] = 40
    cfg["breakoutRetestConfirmWindow"] = 2
    cfg["breakoutRetestDistATR"] = 0.7
    cfg["breakoutRetestSLATR"] = 2.0
    cfg["breakoutRetestTP1ATR"] = 2.5
    cfg["breakoutRetestTP2ATR"] = 4.5

    cfg["divergenceWindowRecent"] = 12
    cfg["divergenceWindowPrev"] = 12
    cfg["divergenceRSIBullThresh"] = 45.0
    cfg["divergenceRSIBearThresh"] = 55.0
    cfg["divergenceSLATR"] = 1.5
    cfg["divergenceTP1ATR"] = 2.5
    cfg["divergenceTP2ATR"] = 4.5

    cfg["breakoutPyramidMinADX"] = 25.0
    cfg["breakoutPyramidSLATR"] = 2.0
    cfg["breakoutPyramidMinSpacingATR"] = 2.5

    cfg["scaleInMinADX"] = 20.0
    cfg["scaleInMinDistATR"] = 1.8
    cfg["scaleInSLATR"] = 1.8
    cfg["scaleInTP1ATR"] = 2.0
    cfg["scaleInTP2ATR"] = 3.5

    cfg["momentumScalpMinADX"] = 18.0
    cfg["momentumScalpSLATR"] = 0.8
    cfg["momentumScalpTP1ATR"] = 1.0
    cfg["momentumScalpTP2ATR"] = 1.5
    cfg["momentumScalpMinScore"] = 7
    cfg["momentumScalpMaxHoldingMin"] = 45
    cfg["momentumScalpRSIBullThresh"] = 42.0
    cfg["momentumScalpRSIBearThresh"] = 58.0
    cfg["momentumScalpRSICrossoverBull"] = 46.0
    cfg["momentumScalpRSICrossoverBear"] = 54.0
    cfg["momentumScalpVolConfirm"] = 1.02

    cfg["m15ConfirmRSIThreshold"] = 45.0
    cfg["minScore"] = 5
    cfg["fibExtension"]["minADX"] = 28.0
    cfg["pullbackFib"]["retracementEnabled"] = True
    cfg["pullbackFib"]["goldenPocketBufferATR"] = 0.3

    return cfg

def jpy_cross_strategy_config() -> StrategyConfig:
    return gbpjpy_strategy_config()


def eurusd_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["h4ADXThreshold"] = 20.0
    cfg["h4RequireConsecutive"] = 2
    cfg["pullbackMinADX"] = 20.0
    cfg["pullbackSLATR"] = 1.0
    cfg["pullbackTP1ATR"] = 1.5
    cfg["pullbackTP2ATR"] = 2.5
    cfg["pullbackDistATR"] = 0.4
    cfg["breakoutRetestSLATR"] = 1.2
    cfg["breakoutRetestTP1ATR"] = 1.8
    cfg["breakoutRetestTP2ATR"] = 3.5
    cfg["divergenceSLATR"] = 0.8
    cfg["divergenceTP1ATR"] = 1.5
    cfg["divergenceTP2ATR"] = 3.0
    cfg["breakoutPyramidMinADX"] = 25.0
    cfg["scaleInSLATR"] = 1.0
    cfg["scaleInTP1ATR"] = 1.5
    cfg["scaleInTP2ATR"] = 2.5
    cfg["momentumScalpMinADX"] = 15.0
    cfg["momentumScalpSLATR"] = 0.3
    cfg["momentumScalpTP1ATR"] = 0.5
    cfg["momentumScalpTP2ATR"] = 0.8
    cfg["momentumScalpMinScore"] = 6
    cfg["momentumScalpMaxHoldingMin"] = 25
    cfg["m15ConfirmRSIThreshold"] = 40.0
    cfg["minScore"] = 5
    return cfg

def gbpusd_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["h4ADXThreshold"] = 22.0
    cfg["h4RequireConsecutive"] = 2
    cfg["pullbackMinADX"] = 20.0
    cfg["pullbackSLATR"] = 1.3
    cfg["pullbackTP1ATR"] = 1.8
    cfg["pullbackTP2ATR"] = 3.0
    cfg["pullbackDistATR"] = 0.5
    cfg["breakoutRetestSLATR"] = 1.5
    cfg["breakoutRetestTP1ATR"] = 2.0
    cfg["breakoutRetestTP2ATR"] = 4.0
    cfg["divergenceSLATR"] = 1.0
    cfg["divergenceTP1ATR"] = 2.0
    cfg["divergenceTP2ATR"] = 3.5
    cfg["breakoutPyramidMinADX"] = 28.0
    cfg["breakoutPyramidSLATR"] = 1.5
    cfg["scaleInSLATR"] = 1.3
    cfg["scaleInTP1ATR"] = 1.8
    cfg["scaleInTP2ATR"] = 3.0
    cfg["momentumScalpMinADX"] = 16.0
    cfg["momentumScalpSLATR"] = 0.5
    cfg["momentumScalpTP1ATR"] = 0.7
    cfg["momentumScalpTP2ATR"] = 1.0
    cfg["momentumScalpMinScore"] = 6
    cfg["momentumScalpMaxHoldingMin"] = 30
    cfg["m15ConfirmRSIThreshold"] = 42.0
    cfg["minScore"] = 5
    return cfg

def usdcad_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["h4ADXThreshold"] = 25.0
    cfg["h4RequireConsecutive"] = 2
    cfg["pullbackMinADX"] = 22.0
    cfg["pullbackSLATR"] = 1.2
    cfg["pullbackTP1ATR"] = 1.5
    cfg["pullbackTP2ATR"] = 3.0
    cfg["pullbackDistATR"] = 0.5
    cfg["breakoutRetestSLATR"] = 1.3
    cfg["breakoutRetestTP1ATR"] = 2.0
    cfg["breakoutRetestTP2ATR"] = 3.5
    cfg["divergenceSLATR"] = 0.8
    cfg["divergenceTP1ATR"] = 1.8
    cfg["divergenceTP2ATR"] = 3.0
    cfg["breakoutPyramidMinADX"] = 28.0
    cfg["breakoutPyramidSLATR"] = 1.5
    cfg["scaleInSLATR"] = 1.2
    cfg["scaleInTP1ATR"] = 1.5
    cfg["scaleInTP2ATR"] = 3.0
    cfg["momentumScalpMinADX"] = 16.0
    cfg["momentumScalpSLATR"] = 0.4
    cfg["momentumScalpTP1ATR"] = 0.6
    cfg["momentumScalpTP2ATR"] = 0.9
    cfg["momentumScalpMinScore"] = 6
    cfg["momentumScalpMaxHoldingMin"] = 25
    cfg["m15ConfirmRSIThreshold"] = 40.0
    cfg["minScore"] = 5
    return cfg

def us100_cash_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["h4ADXThreshold"] = 25.0
    cfg["h4RequireConsecutive"] = 3
    cfg["pullbackMinADX"] = 22.0
    cfg["pullbackDistATR"] = 0.6
    cfg["pullbackSLATR"] = 1.0
    cfg["pullbackTP1ATR"] = 2.0
    cfg["pullbackTP2ATR"] = 3.5
    cfg["breakoutRetestLookback"] = 45
    cfg["breakoutRetestDistATR"] = 0.6
    cfg["breakoutRetestSLATR"] = 1.0
    cfg["breakoutRetestTP1ATR"] = 2.5
    cfg["breakoutRetestTP2ATR"] = 5.0
    cfg["divergenceSLATR"] = 0.6
    cfg["divergenceTP1ATR"] = 2.0
    cfg["divergenceTP2ATR"] = 4.0
    cfg["breakoutPyramidMinADX"] = 25.0
    cfg["breakoutPyramidSLATR"] = 1.0
    cfg["scaleInSLATR"] = 1.0
    cfg["scaleInTP1ATR"] = 2.0
    cfg["scaleInTP2ATR"] = 3.5
    cfg["momentumScalpMinADX"] = 16.0
    cfg["momentumScalpSLATR"] = 0.5
    cfg["momentumScalpTP1ATR"] = 0.8
    cfg["momentumScalpTP2ATR"] = 1.2
    cfg["momentumScalpMaxHoldingMin"] = 60
    cfg["momentumScalpMinScore"] = 6
    cfg["trend"]["h4Weight"] = 0.35
    cfg["trend"]["h1Weight"] = 0.35
    cfg["trend"]["m30Weight"] = 0.25
    cfg["trend"]["d1Weight"] = 0.05
    cfg["minScore"] = 5
    cfg["fibExtension"]["minADX"] = 25.0
    return cfg

def oil_strategy_config() -> StrategyConfig:
    cfg = default_strategy_config()
    cfg["h4ADXThreshold"] = 22.0
    cfg["h4RequireConsecutive"] = 2
    cfg["pullbackMinADX"] = 20.0
    cfg["pullbackDistATR"] = 0.8
    cfg["pullbackSLATR"] = 2.0
    cfg["pullbackTP1ATR"] = 2.5
    cfg["pullbackTP2ATR"] = 4.0
    cfg["breakoutRetestLookback"] = 45
    cfg["breakoutRetestDistATR"] = 0.7
    cfg["breakoutRetestSLATR"] = 2.0
    cfg["breakoutRetestTP1ATR"] = 2.5
    cfg["breakoutRetestTP2ATR"] = 4.5
    cfg["divergenceSLATR"] = 1.5
    cfg["divergenceTP1ATR"] = 2.5
    cfg["divergenceTP2ATR"] = 4.5
    cfg["breakoutPyramidMinADX"] = 28.0
    cfg["breakoutPyramidSLATR"] = 2.0
    cfg["breakoutPyramidMinSpacingATR"] = 2.5
    cfg["scaleInMinADX"] = 22.0
    cfg["scaleInSLATR"] = 1.8
    cfg["scaleInTP1ATR"] = 2.0
    cfg["scaleInTP2ATR"] = 3.5
    cfg["momentumScalpMinADX"] = 15.0
    cfg["momentumScalpSLATR"] = 0.6
    cfg["momentumScalpTP1ATR"] = 0.8
    cfg["momentumScalpTP2ATR"] = 1.2
    cfg["momentumScalpMinScore"] = 7
    cfg["momentumScalpMaxHoldingMin"] = 45
    cfg["m15ConfirmRSIThreshold"] = 42.0
    cfg["minScore"] = 5
    cfg["fibExtension"]["minADX"] = 28.0
    cfg["pullbackFib"]["retracementEnabled"] = True
    cfg["pullbackFib"]["goldenPocketBufferATR"] = 0.4
    return cfg


# ---------------------------------------------------------------- Symbol Lookup


def get_strategy_config_by_symbol(base_symbol: str) -> StrategyConfig:
    """镜像 getStrategyConfigBySymbol:未知 symbol 返回默认配置。"""
    factory = _SYMBOL_CONFIG_FACTORIES.get(base_symbol, default_strategy_config)
    return factory()


_SYMBOL_CONFIG_FACTORIES = {
    "XAUUSD": gold_strategy_config,
    "GOLD": gold_strategy_config,
    "XAGUSD": silver_strategy_config,
    "SILVER": silver_strategy_config,
    "GBPJPY": gbpjpy_strategy_config,
    "EURJPY": jpy_cross_strategy_config,
    "USDJPY": jpy_cross_strategy_config,
    "EURUSD": eurusd_strategy_config,
    "GBPUSD": gbpusd_strategy_config,
    "USDCAD": usdcad_strategy_config,
    "US100CASH": us100_cash_strategy_config,
    "USOILCASH": oil_strategy_config,
    "UKOILCASH": oil_strategy_config,
}
