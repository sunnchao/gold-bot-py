"""Publisher Agent(1:1 镜像 gold-bot apps/app-agent/src/agents/publisher.ts)。

发布分析结果:
- build_feishu_card(account_id, symbol, result):飞书 interactive 卡片构造
- PublisherService.postToGoldbot:金标 API 发布(通过注入的 goldbot_api stub 化)
- PublisherService.sendFeishuCard:进程内串行化飞书 webhook 推送
  (rate-limit 退避:2000/5000/10000ms),FEISHU_WEBHOOK_URL/SECRET 支持 env 注入
- PublisherService.publish:并发发布,全部失败才抛错

网络路径全部 stub 化:goldbot_api 与 fetch 均可注入,默认实现直接抛
NotImplementedError,避免任何真实外呼。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from backend.agents.agents._support import get_logger

__all__ = [
    "FEISHU_RATE_LIMIT_BACKOFF_MS",
    "GoldbotApi",
    "PublisherService",
    "build_feishu_card",
    "format_beijing_time",
    "tr_action",
    "tr_bias",
    "tr_direction",
    "tr_exit",
    "tr_risk",
]

JSONDict = dict[str, Any]

FEISHU_RATE_LIMIT_BACKOFF_MS = (2_000, 5_000, 10_000)

FetchResponse = Any
"""fetch 返回的响应占位;测试注入对象需提供 .ok 与 async text()。"""

FetchCallable = Callable[..., Awaitable[FetchResponse]]
"""fetch(url, method=..., headers=..., body=...) 异步回调。"""


def _json_compatible(value: Any) -> Any:
    """Recursively normalize dataclass / Pydantic / mapping values for JSON publishing."""
    if hasattr(value, "model_dump"):
        return _json_compatible(value.model_dump())
    if is_dataclass(value):
        return {field.name: _json_compatible(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return value


def _json_dict(value: Any) -> JSONDict:
    """Normalize signal values to a JSON-compatible dict for API + card publishing."""
    payload = _json_compatible(value)
    return payload if isinstance(payload, dict) else dict(payload)


async def _default_fetch(*args: Any, **kwargs: Any) -> FetchResponse:
    raise NotImplementedError(
        "Publisher 的 fetch 网络路径已 stub 化;生产接入需注入 async fetch 回调"
    )


class GoldbotApi:
    """金标 API 契约(postAIResult);默认 stub,测试注入 fake。"""

    async def post_ai_result(self, account_id: str, symbol: str, result: JSONDict) -> None:
        raise NotImplementedError(
            "Publisher 的 goldbot_api 网络路径已 stub 化;生产接入需注入 post_ai_result"
        )


# ─── Beijing time formatter(镜像 formatBeijingTime) ───────────────────────────


def format_beijing_time(date: datetime | None = None) -> str:
    """Asia/Shanghai 时区的 YYYY/MM/DD HH:MM:SS(近似 zh-CN toLocaleString)。"""
    moment = date if date is not None else datetime.now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        moment = moment.astimezone(ZoneInfo("Asia/Shanghai"))
    return moment.strftime("%Y/%m/%d %H:%M:%S")


# ─── Translation helpers ──────────────────────────────────────────────────────


def tr_bias(v: str | None = None) -> str:
    mapping = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性"}
    return mapping.get((v or "").lower(), v or "未知")


def tr_action(v: str | None = None) -> str:
    mapping = {
        "open": "开仓",
        "close": "平仓",
        "modify": "调整",
        "hold": "持有",
        "buy": "买入",
        "sell": "卖出",
    }
    return mapping.get((v or "").lower(), v or "N/A")


def tr_direction(v: str | None = None) -> str:
    mapping = {
        "long": "做多",
        "short": "做空",
        "buy": "买入",
        "sell": "卖出",
        "hold": "观望",
    }
    return mapping.get((v or "").lower(), v or "N/A")


def tr_exit(v: str | None = None) -> str:
    mapping = {
        "hold": "持有",
        "close": "平仓",
        "partial_close": "部分平仓",
        "trail_stop": "移动止损",
        "none": "无",
    }
    return mapping.get((v or "").lower(), v or "N/A")


def tr_risk(v: str | None = None) -> str:
    mapping = {"low": "🟢 低", "medium": "🟡 中", "high": "🟠 高", "extreme": "🔴 极高"}
    return mapping.get((v or "").lower(), v or "N/A")


def is_feishu_frequency_limited(data: JSONDict) -> bool:
    message = f"{data.get('msg') or ''} {data.get('message') or ''}".lower()
    return data.get("code") == 11232 or "frequency limited" in message


def feishu_message(data: JSONDict) -> str | None:
    return data.get("msg") or data.get("message")


# ─── Feishu Card Builder(镜像 buildFeishuCard) ───────────────────────────────


def build_feishu_card(account_id: str, symbol: str, result: JSONDict) -> JSONDict:
    """镜像 buildFeishuCard:交互式卡片(消息头由 action/bias 决定)。"""
    arbitration = result.get("arbitration")
    action = (arbitration or {}).get("action") or ""
    action = action.lower() if isinstance(action, str) else ""
    is_open_signal = action in ("buy", "sell", "open")
    header_title = "📈 开单信号" if is_open_signal else "📉 持仓调整"
    bias = result.get("bias")
    if action == "buy":
        header_color = "green"
    elif action == "sell":
        header_color = "red"
    elif bias == "bullish":
        header_color = "green"
    elif bias == "bearish":
        header_color = "red"
    else:
        header_color = "blue"

    sr_levels = result.get("sr_levels") or {}
    support_prices = (
        ", ".join(f"{price:.2f}" for price in [p for p in (sr_levels.get("support") or []) if p is not None])
        or "N/A"
    )
    resistance_prices = (
        ", ".join(f"{price:.2f}" for price in [p for p in (sr_levels.get("resistance") or []) if p is not None])
        or "N/A"
    )

    analysis_sections: list[JSONDict] = []

    analysis_sections.append(
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**账户:**\n{account_id}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**品种:**\n{symbol}"}},
            ],
        }
    )

    analysis_sections.append(
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**信号:**\n{tr_bias(bias)}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**置信度:**\n{result.get('confidence')}%"}},
            ],
        }
    )

    analysis_sections.append(
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**操作建议:**\n"
                            f"{tr_action((arbitration or {}).get('action')) or tr_exit(result.get('exit_suggestion'))}"
                        ),
                    },
                },
                {
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**方向:**\n{tr_direction((arbitration or {}).get('direction')) or tr_bias(bias)}",
                    },
                },
            ],
        }
    )

    risk_line = f"**风险等级:**\n{tr_risk(result.get('risk_level'))}" if result.get("risk_level") else ""
    phase_line = f"**市场阶段:**\n{(arbitration or {}).get('phase')}" if (arbitration or {}).get("phase") else ""
    if risk_line or phase_line:
        fields: list[JSONDict] = []
        if risk_line:
            fields.append({"is_short": True, "text": {"tag": "lark_md", "content": risk_line}})
        if phase_line:
            fields.append({"is_short": True, "text": {"tag": "lark_md", "content": phase_line}})
        analysis_sections.append({"tag": "div", "fields": fields})

    sl_tp_fields: list[JSONDict] = []
    if result.get("suggested_sl"):
        sl_tp_fields.append(
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**建议止损:**\n{result['suggested_sl']:.2f}"}}
        )
    if result.get("suggested_tp"):
        sl_tp_fields.append(
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**建议止盈:**\n{result['suggested_tp']:.2f}"}}
        )
    if len(sl_tp_fields) > 0:
        analysis_sections.append({"tag": "div", "fields": sl_tp_fields})

    if result.get("indicators_summary") and len(result["indicators_summary"]) > 5:
        analysis_sections.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**📊 技术指标摘要:**\n{result['indicators_summary']}"},
            }
        )

    analysis_sections.append(
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**🔍 分析摘要:**\n{(arbitration or {}).get('reasoning') or '暂无分析摘要'}",
            },
        }
    )

    if (arbitration or {}).get("contradiction"):
        analysis_sections.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**⚡ 主要矛盾:**\n{(arbitration or {}).get('contradiction')}",
                },
            }
        )

    analysis_sections.append(
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**支撑位:**\n{support_prices}"}},
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**阻力位:**\n{resistance_prices}"}},
            ],
        }
    )

    if result.get("risk_alert"):
        analysis_sections.append(
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"⚠️ 风险警报: {result.get('alert_reason') or '检测到高风险'}"}
                ],
            }
        )

    if result.get("dow_theory"):
        dt = result["dow_theory"]
        trend_map = {"bullish": "🟢 看涨", "bearish": "🔴 看跌", "neutral": "⚪ 中性"}
        phase_map = {"accumulation": "吸筹", "markup": "拉升", "distribution": "派发", "markdown": "下跌"}
        rationale = dt.get("rationale") or ""
        if _search(r"unavailable|不可用", rationale):
            phase = "不适用"
        else:
            phase = phase_map.get(dt.get("primary_phase"), dt.get("primary_phase"))
        analysis_sections.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(
                        [
                            "**📘 道氏理论分析:**",
                            f"主趋势: {trend_map.get(dt.get('primary_trend'), dt.get('primary_trend'))} "
                            f"| 阶段: {phase}",
                            f"次级趋势: {trend_map.get(dt.get('secondary_trend'), dt.get('secondary_trend'))} "
                            f"| 短期: {trend_map.get(dt.get('short_term_trend'), dt.get('short_term_trend'))}",
                            f"多周期确认: {'✅ 是' if dt.get('multi_tf_confirm') else '❌ 否'}",
                            f"{rationale}",
                        ]
                    ),
                },
            }
        )

    if result.get("wave_theory"):
        wt = result["wave_theory"]
        analysis_sections.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(
                        [
                            "**🌊 波浪理论分析:**",
                            f"当前波浪: {wt.get('current_wave')} | 方向: {wt.get('wave_direction')}",
                            f"波浪计数: {wt.get('wave_count')}",
                            f"下一目标: {wt.get('next_target')}",
                            f"置信度: {wt.get('confidence')}%",
                            f"{wt.get('rationale')}",
                        ]
                    ),
                },
            }
        )

    if result.get("chanlun_theory"):
        ct = result["chanlun_theory"]
        trend_map = {"up": "🟢 上涨", "down": "🔴 下跌", "range": "⚪ 盘整"}
        bi_map = {"up": "↑ 上笔", "down": "↓ 下笔", "none": "— 无"}
        zhongshu_map = {
            "forming": "构建中",
            "active": "活跃",
            "breaking_up": "向上突破",
            "breaking_down": "向下突破",
            "none": "无",
        }
        bsp_map = {
            "buy_1": "一买",
            "buy_2": "二买",
            "buy_3": "三买",
            "sell_1": "一卖",
            "sell_2": "二卖",
            "sell_3": "三卖",
            "none": "无",
        }
        analysis_sections.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(
                        [
                            "**📐 缠论分析:**",
                            f"走势: {trend_map.get(ct.get('trend'), ct.get('trend'))} "
                            f"| 笔: {bi_map.get(ct.get('bi_direction'), ct.get('bi_direction'))} "
                            f"| 段: {bi_map.get(ct.get('duan_direction'), ct.get('duan_direction'))}",
                            f"中枢: {zhongshu_map.get(ct.get('zhongshu_state'), ct.get('zhongshu_state'))} "
                            f"| 买卖点: {bsp_map.get(ct.get('buy_sell_point'), ct.get('buy_sell_point'))}",
                            f"置信度: {ct.get('confidence')}%",
                            f"{ct.get('rationale')}",
                        ]
                    ),
                },
            }
        )

    if result.get("harmonic_theory"):
        ht = result["harmonic_theory"]
        pattern_map = {
            "gartley": "Gartley 加特利",
            "bat": "Bat 蝙蝠",
            "butterfly": "Butterfly 蝴蝶",
            "crab": "Crab 螃蟹",
            "abcd": "AB=CD",
            "cypher": "Cypher 密码",
            "shark": "Shark 鲨鱼",
            "none": "无形态",
        }
        dir_map = {"bullish": "🟢 看多", "bearish": "🔴 看空", "neutral": "⚪ 中性"}
        analysis_sections.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "\n".join(
                        [
                            "**🔁 谐波理论分析:**",
                            f"形态: {pattern_map.get(ht.get('pattern'), ht.get('pattern'))} "
                            f"| 方向: {dir_map.get(ht.get('direction'), ht.get('direction'))}",
                            f"置信度: {ht.get('confidence')}%",
                            f"{ht.get('rationale')}",
                        ]
                    ),
                },
            }
        )

    if result.get("trade_recommendation"):
        tr = result["trade_recommendation"]
        if tr.get("direction") != "hold":
            dir_emoji = "🟢 做多" if tr.get("direction") == "buy" else "🔴 做空"
            lines = [
                "**🎯 交易操作建议:**",
                f"方向: {dir_emoji}",
                f"入场: {tr['entry_price']:.2f}",
                f"止损: {tr['stop_loss']:.2f}",
                f"止盈1: {tr['take_profit_1']:.2f}",
            ]
            if tr.get("take_profit_2"):
                lines.append(f"止盈2: {tr['take_profit_2']:.2f}")
            lines.append(f"盈亏比: 1:{tr['risk_reward_ratio']:.1f}")
            lines.append(f"仓位: {tr.get('position_size_lots')}")
            lines.append(f"{tr.get('rationale')}")
            analysis_sections.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        else:
            has_unavailable_reference = (
                tr.get("entry_price") == 0
                and tr.get("stop_loss") == 0
                and tr.get("take_profit_1") == 0
                and tr.get("risk_reward_ratio") == 0
            )
            if has_unavailable_reference:
                lines = [
                    "**🎯 交易建议:** ⏸️ 观望",
                    "参考入场: 暂无可靠入场价",
                    "参考止损: 暂无可靠止损",
                    "参考止盈1: 暂无可靠止盈",
                    "盈亏比: 盈亏比不可用",
                ]
            else:
                lines = [
                    "**🎯 交易建议:** ⏸️ 观望",
                    f"参考入场: {tr['entry_price']:.2f}",
                    f"参考止损: {tr['stop_loss']:.2f}",
                    f"参考止盈1: {tr['take_profit_1']:.2f}",
                ]
            if tr.get("take_profit_2") and not has_unavailable_reference:
                lines.append(f"参考止盈2: {tr['take_profit_2']:.2f}")
            if not has_unavailable_reference:
                lines.append(f"盈亏比: 1:{tr['risk_reward_ratio']:.1f}")
            lines.append(f"参考仓位: {tr.get('position_size_lots')}")
            if tr.get("rationale"):
                lines.append(tr["rationale"])
            analysis_sections.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    analysis_sections.append({"tag": "hr"})
    analysis_sections.append(
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"生成时间 {format_beijing_time()}"}],
        }
    )

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"{header_title} — {symbol}"},
                "template": header_color,
            },
            "elements": analysis_sections,
        },
    }


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


class PublisherService:
    """镜像 PublisherService:发布到金标 API + 飞书 webhook(进程内串行化)。"""

    def __init__(
        self,
        goldbot_api: GoldbotApi | None = None,
        *,
        webhook_url: str | None = None,
        secret: str | None = None,
        fetch: FetchCallable | None = None,
        backoff_ms: tuple[int, int, int] = FEISHU_RATE_LIMIT_BACKOFF_MS,
    ) -> None:
        self.goldbot_api = goldbot_api if goldbot_api is not None else GoldbotApi()
        self._webhook_url = webhook_url
        self._secret = secret
        self._fetch = fetch if fetch is not None else _default_fetch
        self._backoff_ms = backoff_ms
        self._feishu_queue: asyncio.Task[None] | None = None

    async def post_to_goldbot(self, account_id: str, symbol: str, result: JSONDict) -> None:
        """镜像 postToGoldbot:将结果以 AISignalResult 发布到金标 API。"""
        logger = get_logger()
        payload = _json_dict(result)
        logger.info({"accountId": account_id, "symbol": symbol}, "Publisher: posting to Goldbot API")
        await self.goldbot_api.post_ai_result(account_id, symbol, payload)
        logger.info({"accountId": account_id, "symbol": symbol}, "Publisher: Goldbot API post successful")

    def send_feishu_card(self, account_id: str, symbol: str, result: JSONDict) -> Awaitable[None]:
        """镜像 sendFeishuCard:经进程内队列串行化发送飞书卡片。"""

        async def _run() -> None:
            previous = self._feishu_queue
            if previous is not None:
                try:
                    await asyncio.shield(previous)
                except Exception:
                    # 队列吞掉前一任务的错误(镜像 send.catch(() => undefined))
                    pass
            task = asyncio.create_task(self._send_feishu_card_now(account_id, symbol, result))
            self._feishu_queue = task
            try:
                await task
            finally:
                if self._feishu_queue is task:
                    self._feishu_queue = None

        return _run()

    async def _send_feishu_card_now(self, account_id: str, symbol: str, result: JSONDict) -> None:
        logger = get_logger()
        payload = _json_dict(result)
        webhook_url = self._webhook_url if self._webhook_url is not None else os.environ.get("FEISHU_WEBHOOK_URL")
        if not webhook_url:
            logger.warn({}, "Publisher: FEISHU_WEBHOOK_URL not set, skipping Feishu notification")
            return

        card = build_feishu_card(account_id, symbol, payload)

        secret = self._secret if self._secret is not None else os.environ.get("FEISHU_WEBHOOK_SECRET")
        if secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{secret}"
            sign = base64.b64encode(
                hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
            ).decode("utf-8")
            card["timestamp"] = timestamp
            card["sign"] = sign

        logger.info(
            {"accountId": account_id, "symbol": symbol, "webhookUrl": webhook_url},
            "Publisher: sending Feishu card",
        )

        for attempt in range(len(self._backoff_ms) + 1):
            response = await self._fetch(
                webhook_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps(card, ensure_ascii=False),
            )
            body = await _response_text(response)
            data: JSONDict | None = None
            if body:
                try:
                    parsed = json.loads(body)
                    data = parsed if isinstance(parsed, dict) else None
                except (TypeError, ValueError):
                    data = None

            ok = bool(getattr(response, "ok", True))
            if (
                data is not None
                and is_feishu_frequency_limited(data)
                and attempt < len(self._backoff_ms)
            ):
                backoff_ms = self._backoff_ms[attempt]
                logger.warn(
                    {
                        "accountId": account_id,
                        "symbol": symbol,
                        "attempt": attempt + 1,
                        "backoffMs": backoff_ms,
                        "code": data.get("code"),
                        "msg": feishu_message(data),
                    },
                    "Publisher: Feishu frequency limited, retrying",
                )
                await asyncio.sleep(backoff_ms / 1000)
                continue

            if not ok:
                raise RuntimeError(f"Feishu webhook failed: {getattr(response, 'status', '?')} {body or 'no body'}")

            if data is None:
                raise RuntimeError(f"Feishu webhook returned invalid JSON: {body or 'empty body'}")

            if data.get("code") != 0:
                raise RuntimeError(f"Feishu webhook error: code={data.get('code')}, msg={feishu_message(data)}")

            logger.info({"accountId": account_id, "symbol": symbol}, "Publisher: Feishu card sent successfully")
            return

        raise RuntimeError("Feishu webhook error: exhausted rate-limit retries")

    async def publish(self, account_id: str, symbol: str, result: JSONDict, skip_feishu: bool = False) -> None:
        """镜像 publish:并发发布;全部目标失败时抛错。"""
        logger = get_logger()
        payload = _json_dict(result)
        logger.info(
            {"accountId": account_id, "symbol": symbol, "bias": payload.get("bias")},
            "Publisher: publishing result",
        )

        operations: list[Awaitable[None]] = [self.post_to_goldbot(account_id, symbol, payload)]
        if not skip_feishu:
            logger.info({"accountId": account_id, "symbol": symbol}, "Publisher: sending Feishu card")
            operations.append(self.send_feishu_card(account_id, symbol, payload))

        outcomes = await asyncio.gather(*operations, return_exceptions=True)

        goldbot_outcome = outcomes[0]
        feishu_outcome = outcomes[1] if len(outcomes) > 1 else None

        if isinstance(goldbot_outcome, Exception):
            logger.error(
                {"err": str(goldbot_outcome), "accountId": account_id, "symbol": symbol},
                "Publisher: Goldbot API post failed",
            )

        if isinstance(feishu_outcome, Exception):
            logger.error(
                {"err": str(feishu_outcome), "accountId": account_id, "symbol": symbol},
                "Publisher: Feishu card send failed",
            )

        if all(isinstance(outcome, Exception) for outcome in outcomes):
            feishu_reason = (
                str(feishu_outcome) if isinstance(feishu_outcome, Exception) else "skipped"
            )
            raise RuntimeError(
                "Publisher: all publish targets failed — "
                f"goldbot: {goldbot_outcome}, feishu: {feishu_reason}"
            )


async def _response_text(response: FetchResponse) -> str:
    text = getattr(response, "text", None)
    if callable(text):
        return await text()
    return str(response)
