import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List

from config import (
    V2_TRIGGER_TFS, V2_HTF_TFS, V2_HTF_WEIGHTS, V2_HTF_MIN_SCORE, V2_RR,
    V2_HTF_TOUCH_LOOKBACK, ATR_BUFFER_V2, V2_MIN_QUALITY_SCORE,
    V2_MIN_VOLUME_SCORE, V2_MIN_VOLUME_IMBALANCE, V2_REQUIRE_DIRECTIONAL_VOLUME,
    V2_HTF_OBSTACLE_FILTER_ENABLED, V2_HTF_OBSTACLE_TFS, V2_HTF_OBSTACLE_ATR_BUFFER,
    V2_ENTRY_MODE, V2_MIN_TOUCH_DEPTH, V2_MIN_FVG_TIER,
    V2_RETEST_ENABLED, V2_RETEST_MIN_DEPTH, V2_RETEST_MAX_DEPTH, V2_RETEST_MIN_SCORE,
    V2_NORMAL_VOLUME_SCORE, V2_NORMAL_VOLUME_IMBALANCE, V2_NORMAL_MAIN_STRENGTH,
    V2_STRONG_VOLUME_SCORE, V2_STRONG_VOLUME_IMBALANCE, V2_STRONG_MAIN_STRENGTH,
)
from fvg_engine import FVGZone, detect_fvg, atr as compute_atr

logger = logging.getLogger(__name__)


@dataclass
class V2Signal:
    symbol: str
    direction: int                    # 1 long, -1 short
    trigger_tf: str                   # "15m"
    zone_top: float
    zone_bottom: float
    zone_born_time: int
    entry: float
    sl: float
    tp: float                         # entry ± R × V2_RR (display + initial executor TP2)
    atr: float
    confluence_score: int             # 1-4 (flat weights)
    htf_touches: Dict[str, bool]      # {"30m": bool, "1h": bool, "2h": bool, "4h": bool}
    fvg_buy_volume: float = 0.0       # taker buy volume of the 3-bar FVG formation
    fvg_sell_volume: float = 0.0
    indicators: Dict[str, float] = field(default_factory=dict)

    @property
    def direction_str(self) -> str:
        return "long" if self.direction == 1 else "short"


@dataclass(frozen=True)
class HTFObstacleDecision:
    blocked: bool
    reason: str = "clear"
    blocking_tf: str = ""
    blocking_direction: int = 0
    zone_top: float = 0.0
    zone_bottom: float = 0.0


@dataclass(frozen=True)
class RetestDecision:
    valid: bool
    reason: str = "valid"
    touch_depth: float = 0.0
    retest_score: float = 0.0
    rejection_ratio: float = 0.0
    body_ratio: float = 0.0
    confirmation_close: float = 0.0
    confirmation_time: int = 0


def _htf_active_and_touched(
    zone: Optional[FVGZone],
    bars: List,
    lookback: int = 1,
) -> bool:
    """Pine-parity touch: bull → low <= zone.top, bear → high >= zone.bottom.
    Single-edge probe over last `lookback` closed bars."""
    if zone is None:
        return False
    if zone.mitigation >= 1.0:
        return False
    if not bars:
        return False
    n = min(lookback, len(bars))
    recent = bars[-n:]
    if zone.direction == 1:
        for b in recent:
            if b.low <= zone.top:
                return True
    else:
        for b in recent:
            if b.high >= zone.bottom:
                return True
    return False


_TF_SECONDS = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}
_TIER_RANK = {"weak": 0, "normal": 1, "strong": 2}


def _expanded_zone_bounds(zone: FVGZone, atr_buffer_mult: float) -> tuple[float, float]:
    atr_val = float(getattr(zone, "atr", 0.0) or 0.0)
    buffer = max(0.0, atr_val * max(0.0, float(atr_buffer_mult)))
    return float(zone.bottom) - buffer, float(zone.top) + buffer


def _price_inside_zone(price: float, zone: FVGZone, atr_buffer_mult: float = 0.0) -> bool:
    bottom, top = _expanded_zone_bounds(zone, atr_buffer_mult)
    return bottom <= float(price) <= top


def _path_intersects_zone(entry: float, tp: float, zone: FVGZone, atr_buffer_mult: float = 0.0) -> bool:
    path_low = min(float(entry), float(tp))
    path_high = max(float(entry), float(tp))
    zone_bottom, zone_top = _expanded_zone_bounds(zone, atr_buffer_mult)
    return max(path_low, zone_bottom) <= min(path_high, zone_top)


def _htf_obstacle_decision(
    *,
    symbol: str,
    direction: int,
    entry: float,
    tp: float,
    zones: Dict[str, FVGZone],
    obstacle_tfs: List[str],
    atr_buffer_mult: float,
) -> HTFObstacleDecision:
    """Return whether an opposite 1h/2h/4h FVG blocks this trade."""
    opposite_direction = -1 if direction == 1 else 1
    for zone in zones.values():
        if zone.symbol != symbol:
            continue
        if zone.tf not in obstacle_tfs:
            continue
        if zone.direction != opposite_direction:
            continue
        if zone.mitigation >= 1.0:
            continue
        if _price_inside_zone(entry, zone, atr_buffer_mult):
            return HTFObstacleDecision(
                blocked=True,
                reason="entry_inside_opposite_htf_fvg",
                blocking_tf=zone.tf,
                blocking_direction=zone.direction,
                zone_top=float(zone.top),
                zone_bottom=float(zone.bottom),
            )
        if _path_intersects_zone(entry, tp, zone, atr_buffer_mult):
            return HTFObstacleDecision(
                blocked=True,
                reason="tp_path_blocked_by_opposite_htf_fvg",
                blocking_tf=zone.tf,
                blocking_direction=zone.direction,
                zone_top=float(zone.top),
                zone_bottom=float(zone.bottom),
            )
    return HTFObstacleDecision(blocked=False)


def _tier_allowed(tier: str, minimum: str) -> bool:
    return _TIER_RANK.get(tier, 0) >= _TIER_RANK.get(minimum, 1)


def _zeiierman_quality(z: FVGZone) -> float:
    """Zeiierman-style quality: absolute FVG gap normalized by ATR."""
    atr_val = float(getattr(z, "atr", 0.0) or 0.0)
    if atr_val <= 0:
        return 0.0
    return abs(float(getattr(z, "size", 0.0) or 0.0)) / atr_val



def _zone_live_quality(z: FVGZone, now_ms: int) -> float:
    """Live visible-zone ranking: Zeiierman gap quality minus mitigation/age decay."""
    tf_sec = _TF_SECONDS.get(z.tf, 900)
    age_bars = max(0, (now_ms - z.born_time) // (tf_sec * 1000))
    return _zeiierman_quality(z) - z.mitigation * 0.5 - age_bars * 0.001


# Top-N zones the chart actually displays. Pine default maxZones=10.
_VISIBLE_TOP_N = 10


def _visible_top_zones_all(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
) -> List[FVGZone]:
    """Top-N zones the chart displays — Pine indicator default maxZones=10.
    Returns ALL visible zones (both directions), sorted by live quality_score.
    Used for touch detection before direction filtering."""
    import time
    now_ms = int(time.time() * 1000)
    candidates = [
        z for z in zones.values()
        if z.symbol == symbol
        and z.tf == tf
        and z.mitigation < 1.0
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda z: _zone_live_quality(z, now_ms), reverse=True)
    return candidates[:_VISIBLE_TOP_N]


def _visible_top_zones(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
    direction: int,
) -> List[FVGZone]:
    """Top-N zones the chart displays — Pine indicator default maxZones=10.
    Sorted by live quality_score descending."""
    import time
    now_ms = int(time.time() * 1000)
    candidates = [
        z for z in zones.values()
        if z.symbol == symbol
        and z.tf == tf
        and z.direction == direction
        and z.mitigation < 1.0
    ]
    if not candidates:
        return []
    candidates.sort(key=lambda z: _zone_live_quality(z, now_ms), reverse=True)
    return candidates[:_VISIBLE_TOP_N]


def _latest_active_zone(
    zones: Dict[str, FVGZone],
    symbol: str,
    tf: str,
    direction: int,
) -> Optional[FVGZone]:
    """Pine alert behaviour: of the top-N visible zones (the ones the chart
    actually shows), return the one most recently born. This preserves v1
    'fresh signal' behaviour while filtering out micro low-quality zones the
    user can't see on their chart."""
    visible = _visible_top_zones(zones, symbol, tf, direction)
    if not visible:
        return None
    return max(visible, key=lambda z: z.born_time)


def _compute_htf_confluence(
    zones: Dict[str, FVGZone],
    symbol: str,
    direction: int,
    bars_by_tf: Dict[str, List],
) -> tuple:
    """Returns (score, touches_dict). Score sums weights for HTFs that have an
    active same-direction zone touched within V2_HTF_TOUCH_LOOKBACK."""
    touches: Dict[str, bool] = {}
    score = 0
    for tf in V2_HTF_TFS:
        zone = _latest_active_zone(zones, symbol, tf, direction)
        bars = bars_by_tf.get(tf, [])
        touched = _htf_active_and_touched(zone, bars, lookback=V2_HTF_TOUCH_LOOKBACK)
        touches[tf] = touched
        if touched:
            score += V2_HTF_WEIGHTS[tf]
    return score, touches


_TRIGGER_TOUCH_LOOKBACK = 1  # Pine alert fires on current bar only — match parity


def _trigger_zone_touched(zone: Optional[FVGZone], bars: List) -> Optional[FVGZone]:
    """Pine-parity touch on last `_TRIGGER_TOUCH_LOOKBACK` closed bars.
    Bull: low <= zone.top. Bear: high >= zone.bottom."""
    if zone is None or zone.mitigation >= 1.0:
        return None
    if not bars:
        return None
    n = min(_TRIGGER_TOUCH_LOOKBACK, len(bars))
    recent = bars[-n:]
    if zone.direction == 1:
        for b in recent:
            if b.low <= zone.top:
                return zone
    else:
        for b in recent:
            if b.high >= zone.bottom:
                return zone
    return None


def _compute_sl(zone: FVGZone, atr_val: float) -> float:
    """SL = zone edge ± ATR*buffer. Below FVG bottom for long, above top for short."""
    buf = atr_val * ATR_BUFFER_V2
    if zone.direction == 1:
        return zone.bottom - buf
    return zone.top + buf


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _zone_touch_depth(zone: FVGZone, touch_price: float) -> float:
    size = abs(float(zone.top) - float(zone.bottom))
    if size <= 0:
        return 0.0
    price = float(touch_price)
    if zone.direction == 1:
        return _clamp01((float(zone.top) - price) / size)
    return _clamp01((price - float(zone.bottom)) / size)


def _touch_depth_ok(zone: FVGZone, *, touch_price: float, min_depth: float) -> bool:
    return _zone_touch_depth(zone, touch_price) >= _clamp01(min_depth)


def _live_touch_qualifies(zone: FVGZone, *, live_price: float, min_depth: float) -> bool:
    price = float(live_price)
    if zone.direction == 1:
        if price > float(zone.top):
            return False
        return _touch_depth_ok(zone, touch_price=price, min_depth=min_depth)
    if price < float(zone.bottom):
        return False
    return _touch_depth_ok(zone, touch_price=price, min_depth=min_depth)


def _fvg_strength_tier(zone: FVGZone) -> tuple[str, dict]:
    """Classify FVG formation volume as weak/normal/strong.

    This keeps the strategy FVG-first, but stops weak churn zones from passing
    merely because price touched them. Strong requires aligned directional volume,
    higher imbalance, and higher existing Zeiierman-style main_strength.
    """
    buy = float(getattr(zone, "fvg_buy_volume", 0.0) or 0.0)
    sell = float(getattr(zone, "fvg_sell_volume", 0.0) or 0.0)
    total = buy + sell
    imbalance = abs(buy - sell) / total if total > 0 else 0.0
    volume_score = float(getattr(zone, "volume_score", 0.0) or 0.0)
    main_strength = int(getattr(zone, "main_strength", 0) or 0)
    aligned = (buy > sell) if zone.direction == 1 else (sell > buy)

    tier = "weak"
    if aligned or not V2_REQUIRE_DIRECTIONAL_VOLUME:
        if (
            volume_score >= V2_STRONG_VOLUME_SCORE
            and imbalance >= V2_STRONG_VOLUME_IMBALANCE
            and main_strength >= V2_STRONG_MAIN_STRENGTH
        ):
            tier = "strong"
        elif (
            volume_score >= V2_NORMAL_VOLUME_SCORE
            and imbalance >= V2_NORMAL_VOLUME_IMBALANCE
            and main_strength >= V2_NORMAL_MAIN_STRENGTH
        ):
            tier = "normal"
        elif volume_score >= V2_MIN_VOLUME_SCORE and imbalance >= V2_MIN_VOLUME_IMBALANCE:
            tier = "normal"

    return tier, {
        "fvg_total_volume": total,
        "fvg_volume_imbalance": imbalance,
        "fvg_volume_aligned": aligned,
        "volume_score": volume_score,
        "main_strength": main_strength,
        "fvg_strength_tier": tier,
    }


def _volume_confirmation(zone: FVGZone) -> tuple[bool, dict]:
    """Return whether the FVG formation volume confirms the zone direction."""
    tier, metrics = _fvg_strength_tier(zone)
    passed = _tier_allowed(tier, V2_MIN_FVG_TIER)
    return passed, metrics


def _score_depth(depth: float, min_depth: float, max_depth: float) -> float:
    if depth < min_depth or depth > max_depth:
        return 0.0
    ideal = (min_depth + max_depth) / 2
    span = max(ideal - min_depth, max_depth - ideal, 1e-9)
    return max(0.0, 1.0 - abs(depth - ideal) / span)


def _fvg_retest_decision(
    zone: FVGZone,
    bars: List,
    *,
    min_depth: float = V2_RETEST_MIN_DEPTH,
    max_depth: float = V2_RETEST_MAX_DEPTH,
    min_score: float = V2_RETEST_MIN_SCORE,
) -> RetestDecision:
    """Confirm an FVG retest on the latest closed candle."""
    if not bars:
        return RetestDecision(valid=False, reason="no_bars")

    b = bars[-1]
    candle_range = max(float(b.high) - float(b.low), abs(float(b.close)) * 1e-6, 1e-9)
    body_ratio = abs(float(b.close) - float(b.open)) / candle_range

    if zone.direction == 1:
        if float(b.low) > float(zone.top):
            return RetestDecision(valid=False, reason="no_retest_touch")
        depth = _zone_touch_depth(zone, float(b.low))
        if depth < min_depth:
            return RetestDecision(valid=False, reason="retest_too_shallow", touch_depth=depth)
        if depth > max_depth or float(b.low) < float(zone.bottom):
            return RetestDecision(valid=False, reason="retest_too_deep", touch_depth=depth)
        if float(b.close) <= float(zone.top):
            return RetestDecision(valid=False, reason="no_bullish_reclaim", touch_depth=depth)
        if float(b.close) <= float(b.open):
            return RetestDecision(valid=False, reason="no_bullish_rejection", touch_depth=depth)
        rejection_ratio = _clamp01((float(b.close) - float(b.low)) / candle_range)
        reclaim_ratio = _clamp01((float(b.close) - float(zone.top)) / max(float(zone.size), 1e-9))
    else:
        if float(b.high) < float(zone.bottom):
            return RetestDecision(valid=False, reason="no_retest_touch")
        depth = _zone_touch_depth(zone, float(b.high))
        if depth < min_depth:
            return RetestDecision(valid=False, reason="retest_too_shallow", touch_depth=depth)
        if depth > max_depth or float(b.high) > float(zone.top):
            return RetestDecision(valid=False, reason="retest_too_deep", touch_depth=depth)
        if float(b.close) >= float(zone.bottom):
            return RetestDecision(valid=False, reason="no_bearish_reject", touch_depth=depth)
        if float(b.close) >= float(b.open):
            return RetestDecision(valid=False, reason="no_bearish_rejection", touch_depth=depth)
        rejection_ratio = _clamp01((float(b.high) - float(b.close)) / candle_range)
        reclaim_ratio = _clamp01((float(zone.bottom) - float(b.close)) / max(float(zone.size), 1e-9))

    depth_score = _score_depth(depth, min_depth, max_depth)
    retest_score = (
        depth_score * 35.0
        + rejection_ratio * 30.0
        + min(body_ratio, 1.0) * 20.0
        + reclaim_ratio * 15.0
    )
    return RetestDecision(
        valid=retest_score >= min_score,
        reason="valid" if retest_score >= min_score else "retest_score_low",
        touch_depth=depth,
        retest_score=retest_score,
        rejection_ratio=rejection_ratio,
        body_ratio=body_ratio,
        confirmation_close=float(b.close),
        confirmation_time=int(getattr(b, "open_time", 0) or 0),
    )


def evaluate_v2_signal(
    symbol: str,
    zones: Dict[str, FVGZone],
    bars_by_tf: Dict[str, List],
) -> Optional[V2Signal]:
    """Multi-TF FVG touch confluence detector.

    Returns V2Signal if:
      1. 15m FVG (any strength, any direction) is touched on latest bar.
      2. HTF confluence score ≥ V2_HTF_MIN_SCORE across {30m, 1h, 2h, 4h}.

    CRITICAL: Signal direction MUST match zone.direction to avoid inverted signals.
    """
    for trigger_tf in V2_TRIGGER_TFS:
        # Get ALL visible zones (both bullish and bearish), then filter by touch
        visible = _visible_top_zones_all(zones, symbol, trigger_tf)
        if not visible:
            continue
        visible.sort(key=lambda z: z.born_time, reverse=True)

        bars = bars_by_tf.get(trigger_tf, [])
        if not bars:
            continue

        for triggered in visible:
            hit = _trigger_zone_touched(triggered, bars)
            if hit is None:
                continue

            # CRITICAL FIX: Use zone.direction, NOT loop direction!
            # This prevents inverted signals (bullish zone → SHORT signal)
            direction = triggered.direction

            if triggered.quality_score < V2_MIN_QUALITY_SCORE:
                logger.info(
                    "v2 skip %s %s %s | quality=%.1f < %.1f",
                    symbol, trigger_tf, "long" if direction == 1 else "short",
                    triggered.quality_score, V2_MIN_QUALITY_SCORE,
                )
                continue

            last_bar = bars[-1]
            touch_price = float(last_bar.low) if direction == 1 else float(last_bar.high)
            touch_depth = _zone_touch_depth(triggered, touch_price)

            retest = _fvg_retest_decision(triggered, bars)
            if V2_RETEST_ENABLED and not retest.valid:
                logger.info(
                    "v2 skip %s %s %s | retest=%s depth=%.3f score=%.1f",
                    symbol, trigger_tf, "long" if direction == 1 else "short",
                    retest.reason, retest.touch_depth, retest.retest_score,
                )
                continue

            volume_ok, volume_metrics = _volume_confirmation(triggered)
            if not volume_ok:
                logger.info(
                    "v2 skip %s %s %s | fvg_tier=%s volume_score=%.2f imbalance=%.3f aligned=%s min_tier=%s",
                    symbol, trigger_tf, "long" if direction == 1 else "short",
                    volume_metrics["fvg_strength_tier"], volume_metrics["volume_score"],
                    volume_metrics["fvg_volume_imbalance"], volume_metrics["fvg_volume_aligned"],
                    V2_MIN_FVG_TIER,
                )
                continue

            score, touches = _compute_htf_confluence(zones, symbol, direction, bars_by_tf)
            if score < V2_HTF_MIN_SCORE:
                continue

            atr_val = float(triggered.atr) if triggered.atr else 0.0
            if atr_val <= 0:
                if len(bars) >= 15:
                    highs = [b.high for b in bars]
                    lows = [b.low for b in bars]
                    closes = [b.close for b in bars]
                    atr_val = compute_atr(highs, lows, closes, 14) or triggered.size
                else:
                    atr_val = triggered.size

            sl = _compute_sl(triggered, atr_val)
            # In retest mode the signal is confirmed on candle close, and the
            # executor enters with a market order after this decision is stored.
            entry = retest.confirmation_close if V2_RETEST_ENABLED else (
                float(triggered.top) if direction == 1 else float(triggered.bottom)
            )
            r = abs(entry - sl)
            tp = entry + r * V2_RR if direction == 1 else entry - r * V2_RR

            obstacle = _htf_obstacle_decision(
                symbol=symbol,
                direction=direction,
                entry=entry,
                tp=tp,
                zones=zones,
                obstacle_tfs=V2_HTF_OBSTACLE_TFS,
                atr_buffer_mult=V2_HTF_OBSTACLE_ATR_BUFFER,
            )
            if V2_HTF_OBSTACLE_FILTER_ENABLED and obstacle.blocked:
                logger.info(
                    "v2 skip %s %s %s | htf_obstacle=%s tf=%s zone=[%g,%g]",
                    symbol, trigger_tf, "long" if direction == 1 else "short",
                    obstacle.reason, obstacle.blocking_tf, obstacle.zone_bottom, obstacle.zone_top,
                )
                continue

            return V2Signal(
                symbol=symbol,
                direction=direction,
                trigger_tf=trigger_tf,
                zone_top=triggered.top,
                zone_bottom=triggered.bottom,
                zone_born_time=triggered.born_time,
                entry=entry,
                sl=sl,
                tp=tp,
                atr=atr_val,
                confluence_score=score,
                htf_touches=touches,
                fvg_buy_volume=getattr(triggered, "fvg_buy_volume", 0.0),
                fvg_sell_volume=getattr(triggered, "fvg_sell_volume", 0.0),
                indicators={
                    "rsi": getattr(triggered, "rsi", 50.0),
                    "volume_score": getattr(triggered, "volume_score", 0.0),
                    "trend_score": getattr(triggered, "trend_score", 0.0),
                    "quality_score": _zeiierman_quality(triggered),
                    "quality_score_formula_live": "zeiierman_gap_atr",
                    "main_strength": getattr(triggered, "main_strength", 0),
                    "bull_strength": getattr(triggered, "bull_strength", 0),
                    "bear_strength": getattr(triggered, "bear_strength", 0),
                    "fvg_total_volume": volume_metrics["fvg_total_volume"],
                    "fvg_volume_imbalance": volume_metrics["fvg_volume_imbalance"],
                    "fvg_volume_aligned": volume_metrics["fvg_volume_aligned"],
                    "fvg_strength_tier": volume_metrics["fvg_strength_tier"],
                    "touch_depth": touch_depth,
                    "entry_mode": V2_ENTRY_MODE,
                    "min_touch_depth": V2_MIN_TOUCH_DEPTH,
                    "retest_enabled": float(V2_RETEST_ENABLED),
                    "retest_score": retest.retest_score,
                    "retest_reason": retest.reason,
                    "retest_touch_depth": retest.touch_depth,
                    "retest_rejection_ratio": retest.rejection_ratio,
                    "retest_body_ratio": retest.body_ratio,
                    "retest_confirmation_time": retest.confirmation_time,
                    "htf_obstacle_blocked": float(obstacle.blocked),
                    "htf_obstacle_reason": obstacle.reason,
                    "htf_obstacle_tf": obstacle.blocking_tf,
                },
            )
    return None
