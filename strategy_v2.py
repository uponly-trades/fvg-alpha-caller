import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List

from config import (
    V2_TRIGGER_TFS, V2_HTF_TFS, V2_HTF_WEIGHTS, V2_RR,
    V2_HTF_TOUCH_LOOKBACK, ATR_BUFFER_V2,
    V2_SWING_LOOKBACK, V2_SWING_FRACTAL, V2_TP_MIN_DIST_R, V2_RR_CAP,
    V2_MIN_VOLUME_SCORE, V2_MIN_VOLUME_IMBALANCE, V2_REQUIRE_DIRECTIONAL_VOLUME,
    V2_HTF_OBSTACLE_FILTER_ENABLED, V2_HTF_OBSTACLE_TFS, V2_HTF_OBSTACLE_ATR_BUFFER,
    V2_ENTRY_MODE, V2_RETEST_ENABLED, V2_RETEST_MAX_DEPTH,
    V2_ENTRY_TRIGGER, V2_REQUIRE_PRIOR_TOUCH,
    V2_REQUIRE_SUPERTREND_FILTER, V2_SUPERTREND_ATR_LENGTH,
    V2_SUPERTREND_MULTIPLIER, V2_SUPERTREND_ALPHA_PCT,
    V2_SUPERTREND_THRESHOLD_ATR,
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


@dataclass(frozen=True)
class SuperTrendState:
    trend: int = 1
    band: float = 0.0
    switch_price: float = 0.0


def _supertrend_recovery_state(
    bars: List,
    *,
    atr_length: int = V2_SUPERTREND_ATR_LENGTH,
    multiplier: float = V2_SUPERTREND_MULTIPLIER,
    alpha_pct: float = V2_SUPERTREND_ALPHA_PCT,
    threshold_atr: float = V2_SUPERTREND_THRESHOLD_ATR,
) -> SuperTrendState:
    """LuxAlgo-style SuperTrend Recovery filter from fvg retest.txt."""
    if not bars:
        return SuperTrendState()
    alpha = float(alpha_pct) / 100.0
    trend = 1
    band = 0.0
    switch_price = float(bars[0].close)
    highs, lows, closes = [], [], []
    for i, b in enumerate(bars):
        highs.append(float(b.high))
        lows.append(float(b.low))
        closes.append(float(b.close))
        atr_val = compute_atr(highs, lows, closes, atr_length) or max(float(b.high) - float(b.low), 1e-9)
        src = (float(b.high) + float(b.low)) / 2.0
        upper_base = src + float(multiplier) * atr_val
        lower_base = src - float(multiplier) * atr_val
        deviation = float(threshold_atr) * atr_val
        at_loss = (trend == 1 and (switch_price - float(b.close)) > deviation) or (
            trend == -1 and (float(b.close) - switch_price) > deviation
        )
        prev_band = band if i > 0 else (lower_base if trend == 1 else upper_base)
        if trend == 1:
            target_band = alpha * float(b.close) + (1.0 - alpha) * prev_band if at_loss else lower_base
            band = max(target_band, prev_band)
            if float(b.close) < band:
                trend = -1
                band = upper_base
                switch_price = float(b.close)
        else:
            target_band = alpha * float(b.close) + (1.0 - alpha) * prev_band if at_loss else upper_base
            band = min(target_band, prev_band)
            if float(b.close) > band:
                trend = 1
                band = lower_base
                switch_price = float(b.close)
        if i == 0:
            switch_price = float(b.close)
    return SuperTrendState(trend=trend, band=band, switch_price=switch_price)


def _step_supertrend(
    prev: SuperTrendState,
    bar,
    atr_val: float,
    *,
    multiplier: float = V2_SUPERTREND_MULTIPLIER,
    alpha_pct: float = V2_SUPERTREND_ALPHA_PCT,
    threshold_atr: float = V2_SUPERTREND_THRESHOLD_ATR,
) -> SuperTrendState:
    """Advance SuperTrend Recovery by exactly one bar.

    Pine's stBand/stTrend/stSwitchPrice are `var` series. Recomputing the whole
    state from a short buffer drifts away from the chart (see EIGENUSDT 15m,
    2026-05-20: chart Bias=ST Bearish but bot sampled trend=1 from a 100-bar
    window). The caller is expected to seed `prev` once from a long history
    and then step every bar close through here.
    """
    alpha = float(alpha_pct) / 100.0
    close = float(bar.close)
    src = (float(bar.high) + float(bar.low)) / 2.0
    upper_base = src + float(multiplier) * float(atr_val)
    lower_base = src - float(multiplier) * float(atr_val)
    deviation = float(threshold_atr) * float(atr_val)
    trend = int(prev.trend) or 1
    switch_price = float(prev.switch_price) if prev.switch_price else close
    prev_band = float(prev.band) if prev.band else (lower_base if trend == 1 else upper_base)
    at_loss = (trend == 1 and (switch_price - close) > deviation) or (
        trend == -1 and (close - switch_price) > deviation
    )
    if trend == 1:
        target_band = alpha * close + (1.0 - alpha) * prev_band if at_loss else lower_base
        band = max(target_band, prev_band)
        if close < band:
            trend = -1
            band = upper_base
            switch_price = close
    else:
        target_band = alpha * close + (1.0 - alpha) * prev_band if at_loss else upper_base
        band = min(target_band, prev_band)
        if close > band:
            trend = 1
            band = lower_base
            switch_price = close
    return SuperTrendState(trend=trend, band=band, switch_price=switch_price)


def _supertrend_aligned(
    direction: int,
    bars: List,
    *,
    prev_state: Optional[SuperTrendState] = None,
) -> tuple[bool, SuperTrendState]:
    """Return (aligned, state). When `prev_state` is given, the caller already
    holds a seeded ST state and we should not recompute from `bars`."""
    if prev_state is not None:
        st = prev_state
    else:
        st = _supertrend_recovery_state(bars)
    if not V2_REQUIRE_SUPERTREND_FILTER:
        return True, st
    return ((direction == 1 and st.trend == 1) or (direction == -1 and st.trend == -1)), st


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
    """Stored telemetry: absolute FVG gap normalized by ATR."""
    atr_val = float(getattr(z, "atr", 0.0) or 0.0)
    if atr_val <= 0:
        return 0.0
    return abs(float(getattr(z, "size", 0.0) or 0.0)) / atr_val


def _pine_zone_quality(z: FVGZone, now_ms: int) -> float:
    """Pine fvg retest.txt qualityScore used for visible-zone ranking."""
    tf_sec = _TF_SECONDS.get(z.tf, 900)
    age_bars = max(0, (now_ms - z.born_time) // (tf_sec * 1000))
    return (
        float(getattr(z, "size", 0.0) or 0.0) * 100.0
        + float(getattr(z, "volume_score", 0.0) or 0.0) * 10.0
        + float(getattr(z, "trend_score", 0.0) or 0.0) * 20.0
        - float(getattr(z, "mitigation", 0.0) or 0.0) * 50.0
        - float(age_bars) * 0.1
    )


def _zone_live_quality(z: FVGZone, now_ms: int) -> float:
    """Live visible-zone ranking: match Pine's qualityScore sort."""
    return _pine_zone_quality(z, now_ms)


# Top-N zones the chart actually displays. Pine default maxZones=8.
_VISIBLE_TOP_N = 8


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

# ---------------------------------------------------------------------------
# Dynamic SL/TP helpers — spec: .specify/specs/dynamic-sltp.md
# ---------------------------------------------------------------------------

def _swings(bars, kind: str, lookback: int = 60, fractal: int = 2):
    """Fractal swing detector. Returns list of (index, price).

    A swing high at index i requires bars[i].high strictly greater than the
    `fractal` neighbors on each side. The last `fractal` bars cannot confirm
    a swing because they lack right-side neighbors.
    """
    if not bars or len(bars) < (2 * fractal + 1):
        return []
    n = len(bars)
    start = max(fractal, n - lookback)
    out = []
    for i in range(start, n - fractal):
        if kind == "high":
            pivot = float(bars[i].high)
            ok = all(float(bars[i - k].high) < pivot for k in range(1, fractal + 1)) and \
                 all(float(bars[i + k].high) < pivot for k in range(1, fractal + 1))
            if ok:
                out.append((i, pivot))
        else:
            pivot = float(bars[i].low)
            ok = all(float(bars[i - k].low) > pivot for k in range(1, fractal + 1)) and \
                 all(float(bars[i + k].low) > pivot for k in range(1, fractal + 1))
            if ok:
                out.append((i, pivot))
    return out


def _structural_sl(
    zone: FVGZone,
    bars,
    atr_val: float,
    side: str,
    lookback: int = V2_SWING_LOOKBACK,
    fractal: int = V2_SWING_FRACTAL,
    buffer_atr: float = 0.25,
) -> float:
    """Anchor SL behind the worst-case of {zone far edge, latest swing extreme}.

    Long: SL = min(zone.bottom, last swing low) - buffer*ATR
    Short: SL = max(zone.top, last swing high) + buffer*ATR
    Falls back to zone-edge baseline if no qualifying swing exists.
    """
    buf = max(0.0, float(atr_val)) * float(buffer_atr)
    if side == "long":
        baseline = float(zone.bottom)
        lows = _swings(bars, "low", lookback=lookback, fractal=fractal)
        # only consider swings strictly below baseline (otherwise zone edge wins)
        candidates = [p for _, p in lows if p < baseline]
        anchor = min(candidates) if candidates else baseline
        return anchor - buf
    else:
        baseline = float(zone.top)
        highs = _swings(bars, "high", lookback=lookback, fractal=fractal)
        candidates = [p for _, p in highs if p > baseline]
        anchor = max(candidates) if candidates else baseline
        return anchor + buf


def _tp_magnets(
    *,
    entry: float,
    side: str,
    risk: float,
    bars_15m,
    htf_zones: dict,
    min_dist_r: float = V2_TP_MIN_DIST_R,
    rr_cap: float = V2_RR_CAP,
    lookback: int = V2_SWING_LOOKBACK,
    fractal: int = V2_SWING_FRACTAL,
):
    """Find TP1 and TP2 magnets.

    Magnet candidates (long): swing highs above entry on 15m, plus the near
    edge (bottom) of opposite-direction (bear) FVG zones on 1h/4h that sit
    above entry. Mirrored for short.

    Returns (tp1, tp1_kind, tp2, tp2_kind). tp1 may be None if no qualifying
    magnet exists; in that case tp2_kind is "none" too. tp2 falls back to
    `entry ± risk * rr_cap` capped distance when only one magnet exists.
    """
    if risk <= 0:
        return None, "none", None, "none"
    min_dist = float(risk) * float(min_dist_r)
    cap_distance = float(risk) * float(rr_cap)

    candidates = []  # list of (price, distance, kind)
    if side == "long":
        for _, p in _swings(bars_15m, "high", lookback=lookback, fractal=fractal):
            if p > entry:
                candidates.append((p, p - entry, "swing"))
        for tf in ("1h", "4h"):
            for z in htf_zones.get(tf, []) or []:
                if int(getattr(z, "direction", 0)) != -1:
                    continue
                if float(getattr(z, "mitigation", 0.0) or 0.0) >= 1.0:
                    continue
                near_edge = float(z.bottom)
                if near_edge > entry:
                    candidates.append((near_edge, near_edge - entry, f"fvg_{tf}"))
    else:
        for _, p in _swings(bars_15m, "low", lookback=lookback, fractal=fractal):
            if p < entry:
                candidates.append((p, entry - p, "swing"))
        for tf in ("1h", "4h"):
            for z in htf_zones.get(tf, []) or []:
                if int(getattr(z, "direction", 0)) != 1:
                    continue
                if float(getattr(z, "mitigation", 0.0) or 0.0) >= 1.0:
                    continue
                near_edge = float(z.top)
                if near_edge < entry:
                    candidates.append((near_edge, entry - near_edge, f"fvg_{tf}"))

    # drop too-close magnets and cap-far magnets
    candidates = [c for c in candidates if c[1] >= min_dist and c[1] <= cap_distance]
    candidates.sort(key=lambda c: c[1])

    if not candidates:
        return None, "none", None, "none"

    tp1_price, _, tp1_kind = candidates[0]
    if len(candidates) >= 2:
        tp2_price, _, tp2_kind = candidates[1]
    else:
        if side == "long":
            tp2_price = float(entry) + cap_distance
        else:
            tp2_price = float(entry) - cap_distance
        tp2_kind = "cap"
    return tp1_price, tp1_kind, tp2_price, tp2_kind


def _structural_rr(*, entry: float, sl: float, tp1, min_rr: float):
    """Return (rr, ok). rr=0 when tp1 missing or sl distance is zero."""
    if tp1 is None:
        return 0.0, False
    risk = abs(float(entry) - float(sl))
    if risk <= 0:
        return 0.0, False
    rr = abs(float(tp1) - float(entry)) / risk
    return rr, rr >= float(min_rr)



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
    """Return telemetry for FVG formation volume; never gate retest entry."""
    tier, metrics = _fvg_strength_tier(zone)
    return tier != "weak", metrics


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
    min_depth: float = 0.0,
    max_depth: float = V2_RETEST_MAX_DEPTH,
    min_score: float = 0.0,
) -> RetestDecision:
    """Confirm Pine-style FVG retest on the latest closed candle.

    Parity with fvg retest.txt: retest requires a prior touch. The latest candle
    must reject from inside the still-active FVG: bull low <= top, low > bottom,
    close > top; bear high >= bottom, high < top, close < bottom.
    """
    if not bars:
        return RetestDecision(valid=False, reason="no_bars")
    if V2_REQUIRE_PRIOR_TOUCH and len(bars) < 2:
        return RetestDecision(valid=False, reason="no_prior_touch")

    relevant_bars = list(bars)
    if zone.born_time:
        born_idx = next(
            (i for i, b in enumerate(relevant_bars) if int(getattr(b, "open_time", 0) or 0) >= int(zone.born_time)),
            None,
        )
        if born_idx is None:
            return RetestDecision(valid=False, reason="zone_not_in_bars")
        relevant_bars = relevant_bars[born_idx:]
        if len(relevant_bars) < 2:
            return RetestDecision(valid=False, reason="no_prior_touch")

    if V2_REQUIRE_PRIOR_TOUCH:
        prior_touched = any(
            float(b.high) >= float(zone.bottom) and float(b.low) <= float(zone.top)
            for b in relevant_bars[:-1]
        )
        if not prior_touched:
            return RetestDecision(valid=False, reason="no_prior_touch")

    b = relevant_bars[-1]
    candle_range = max(float(b.high) - float(b.low), abs(float(b.close)) * 1e-6, 1e-9)
    body_ratio = abs(float(b.close) - float(b.open)) / candle_range
    candle_touches_zone = float(b.high) >= float(zone.bottom) and float(b.low) <= float(zone.top)
    if not candle_touches_zone:
        return RetestDecision(valid=False, reason="no_retest_touch")

    if zone.direction == 1:
        depth = _zone_touch_depth(zone, float(b.low))
        if depth > max_depth or float(b.low) <= float(zone.bottom):
            return RetestDecision(valid=False, reason="retest_too_deep", touch_depth=depth)
        if float(b.close) <= float(zone.top):
            return RetestDecision(valid=False, reason="no_bullish_reclaim", touch_depth=depth)
        rejection_ratio = _clamp01((float(b.close) - float(b.low)) / candle_range)
        reclaim_ratio = _clamp01((float(b.close) - float(zone.top)) / max(float(zone.size), 1e-9))
    else:
        depth = _zone_touch_depth(zone, float(b.high))
        if depth > max_depth or float(b.high) >= float(zone.top):
            return RetestDecision(valid=False, reason="retest_too_deep", touch_depth=depth)
        if float(b.close) >= float(zone.bottom):
            return RetestDecision(valid=False, reason="no_bearish_reject", touch_depth=depth)
        rejection_ratio = _clamp01((float(b.high) - float(b.close)) / candle_range)
        reclaim_ratio = _clamp01((float(zone.bottom) - float(b.close)) / max(float(zone.size), 1e-9))

    depth_score = _score_depth(depth, min_depth, max_depth)
    score = (depth_score * 35.0) + (rejection_ratio * 45.0) + (body_ratio * 10.0) + (reclaim_ratio * 10.0)
    return RetestDecision(
        valid=True,
        touch_depth=depth,
        retest_score=score,
        rejection_ratio=rejection_ratio,
        body_ratio=body_ratio,
        confirmation_close=float(b.close),
        confirmation_time=int(getattr(b, "open_time", 0) or 0),
    )


def evaluate_v2_signal(
    symbol: str,
    zones: Dict[str, FVGZone],
    bars_by_tf: Dict[str, List],
    *,
    supertrend_state: Optional["SuperTrendState"] = None,
) -> Optional[V2Signal]:
    """15m FVG retest trigger with SuperTrend direction filter.

    Entry parity target: fvg retest.txt. HTF, volume, and quality metrics are
    telemetry only; fixed TP magnets are not used for this retest path.

    `supertrend_state` is the seeded, bar-by-bar advanced ST Recovery state
    maintained by the caller (see main._st_state_step). When omitted we fall
    back to recomputing from `bars` for tests/legacy callers; production must
    pass the seeded state to stay in parity with the Pine indicator.
    """
    for trigger_tf in V2_TRIGGER_TFS:
        # Get ALL visible zones (both bullish and bearish), then filter by touch
        visible = _visible_top_zones_all(zones, symbol, trigger_tf)
        if not visible:
            continue

        bars = bars_by_tf.get(trigger_tf, [])
        if not bars:
            continue

        for triggered in visible:
            direction = triggered.direction

            last_bar = bars[-1]
            touch_price = float(last_bar.low) if direction == 1 else float(last_bar.high)
            touch_depth = _zone_touch_depth(triggered, touch_price)

            retest = _fvg_retest_decision(triggered, bars)
            if V2_ENTRY_TRIGGER in {"retest", "retest_only"} and not retest.valid:
                logger.info(
                    "v2 skip %s %s %s | retest=%s depth=%.3f score=%.1f",
                    symbol, trigger_tf, "long" if direction == 1 else "short",
                    retest.reason, retest.touch_depth, retest.retest_score,
                )
                continue

            st_aligned, st_state = _supertrend_aligned(
                direction, bars, prev_state=supertrend_state,
            )
            if not st_aligned:
                logger.info(
                    "v2 skip %s %s %s | supertrend_mismatch trend=%s band=%g",
                    symbol, trigger_tf, "long" if direction == 1 else "short",
                    st_state.trend, st_state.band,
                )
                continue

            # Pine fvg retest.txt does not gate retest signals by volume tier,
            # quality threshold, or HTF confluence. Keep FVG volume metrics as
            # telemetry only so alerts can still explain zone context.
            _, volume_metrics = _volume_confirmation(triggered)

            score = 0
            touches = {tf: False for tf in V2_HTF_TFS}

            atr_val = float(triggered.atr) if triggered.atr else 0.0
            if atr_val <= 0:
                if len(bars) >= 15:
                    highs = [b.high for b in bars]
                    lows = [b.low for b in bars]
                    closes = [b.close for b in bars]
                    atr_val = compute_atr(highs, lows, closes, 14) or triggered.size
                else:
                    atr_val = triggered.size

            # In retest mode the signal is confirmed on candle close, and the
            # executor enters with a market order after this decision is stored.
            entry = retest.confirmation_close if V2_RETEST_ENABLED else (
                float(triggered.top) if direction == 1 else float(triggered.bottom)
            )

            # Pine parity: trade plan exits on the SuperTrend Recovery band.
            # fvg retest.txt sets slPrice = stBand on entry and refreshes it
            # every bar while the virtual trade remains open.
            side_str = "long" if direction == 1 else "short"
            sl = float(st_state.band)
            sl_mode_used = "supertrend_band"

            r = abs(entry - sl)
            if r <= 0:
                logger.info(
                    "v2 skip %s %s %s | sl_distance_zero entry=%g sl=%g",
                    symbol, trigger_tf, side_str, entry, sl,
                )
                continue

            tp_mode_used = "supertrend_exit"
            tp = entry
            tp1 = entry

            obstacle = HTFObstacleDecision(False)

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
                    "entry_trigger": "retest",
                    "retest_enabled": float(V2_RETEST_ENABLED),
                    "supertrend_trend": st_state.trend,
                    "supertrend_band": st_state.band,
                    "supertrend_switch_price": st_state.switch_price,
                    "retest_score": retest.retest_score,
                    "retest_reason": retest.reason,
                    "retest_touch_depth": retest.touch_depth,
                    "retest_rejection_ratio": retest.rejection_ratio,
                    "retest_body_ratio": retest.body_ratio,
                    "retest_confirmation_time": retest.confirmation_time,
                    "htf_obstacle_blocked": float(obstacle.blocked),
                    "htf_obstacle_reason": obstacle.reason,
                    "htf_obstacle_tf": obstacle.blocking_tf,
                    "sl_mode": sl_mode_used,
                    "tp_mode": tp_mode_used,
                    "tp1": float(tp1),
                },
            )
    return None
