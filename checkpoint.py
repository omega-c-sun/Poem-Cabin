"""Hardcoded checkpoint / soft-ask gate logic."""
from __future__ import annotations

import re

DIM_KEYS = ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth']


# Gray zone for max axis deviation vs target (on 0–100 scale)
GRAY_LOW = 8
GRAY_HIGH = 20

VAGUE_PATTERNS = [
    r'随便', r'都行', r'看看', r'不确定', r'不知道', r'或许', r'也许',
    r'maybe', r'not sure', r'whatever', r'idk',
]


def _norm_score(v):
    if v is None:
        return None
    try:
        x = float(v)
    except Exception:
        return None
    if x <= 1.0:
        x *= 100.0
    return x


def max_axis_deviation(scores, target):
    """Max |score - target| on shared dims. Target may be 0–1 or 0–100."""
    if not scores or not target:
        return None
    diffs = []
    for k in DIM_KEYS:
        if k not in scores or k not in target:
            continue
        s = _norm_score(scores.get(k))
        t = _norm_score(target.get(k))
        if s is None or t is None:
            continue
        diffs.append(abs(s - t))
    if not diffs:
        return None
    return max(diffs)


def user_message_vague(text):
    t = (text or '').strip().lower()
    if not t:
        return False
    return any(re.search(p, t, re.I) for p in VAGUE_PATTERNS)


def gate_decision(scores, target, *, recent_rejects=0, vague_user=False,
                  soft_ask_skips=0, filled_ratio=0.0, kind='structure'):
    """
    Returns dict:
      action: 'auto' | 'ask' | 'ask_off'
      reason: str
      deviation: float|None
    soft_ask_skips: consecutive continues after ask — lowers sensitivity.
    """
    # After two explicit continues, bias toward auto this stage
    gray_high = GRAY_HIGH + (soft_ask_skips * 6)
    gray_low = max(4, GRAY_LOW - soft_ask_skips)

    dev = max_axis_deviation(scores, target)

    if recent_rejects > 0 or vague_user:
        return {
            'action': 'ask',
            'reason': 'recent_reject_or_vague',
            'deviation': dev,
        }

    # While canvas is still largely empty, keep filling — don't stop to ask
    if kind in ('text', 'fill') and filled_ratio < 0.72:
        return {
            'action': 'auto',
            'reason': 'fill_incomplete_keep_going',
            'deviation': dev,
        }

    # Keyword salad / weak logic — keep revising, do not pause for confirm
    coh = None
    if scores:
        try:
            coh = float(scores.get('coherence')) if scores.get('coherence') is not None else None
        except Exception:
            coh = None
    if kind in ('text', 'fill') and coh is not None and coh < 58:
        return {
            'action': 'auto',
            'reason': 'coherence_too_low_keep_going',
            'deviation': dev,
        }

    if kind == 'structure' and filled_ratio < 0.05:
        # Empty skeleton: prefer clause-ready POS frames (coherence / ok_ratio)
        overall = _norm_score((scores or {}).get('overall')) or 50
        sk_coh = _norm_score((scores or {}).get('coherence'))
        ok_ratio = None
        try:
            if scores and scores.get('skeleton_ok_ratio') is not None:
                ok_ratio = float(scores.get('skeleton_ok_ratio'))
        except Exception:
            ok_ratio = None
        if ok_ratio is not None and ok_ratio < 0.55:
            return {'action': 'ask', 'reason': 'structure_clause_weak', 'deviation': dev}
        if sk_coh is not None and sk_coh < 50:
            return {'action': 'ask', 'reason': 'structure_logic_weak', 'deviation': dev}
        # First pass: always ask so user can confirm skeleton (strong participation)
        if soft_ask_skips == 0:
            return {'action': 'ask', 'reason': 'structure_confirm_participation', 'deviation': dev}
        if overall >= 72 and (sk_coh is None or sk_coh >= 65) and (
                ok_ratio is None or ok_ratio >= 0.7):
            return {'action': 'auto', 'reason': 'structure_clause_ready', 'deviation': dev}
        if overall >= 75 and soft_ask_skips >= 1:
            return {'action': 'auto', 'reason': 'structure_ok_after_continue', 'deviation': dev}
        if overall < 55:
            return {'action': 'ask', 'reason': 'structure_weak', 'deviation': dev}
        return {'action': 'ask', 'reason': 'structure_default_ask', 'deviation': dev}

    if dev is None:
        return {'action': 'ask', 'reason': 'no_target', 'deviation': None}

    if soft_ask_skips >= 2 and filled_ratio >= 0.7:
        return {'action': 'auto', 'reason': 'user_wants_pace', 'deviation': dev}

    if dev < gray_low:
        return {'action': 'auto', 'reason': 'in_range_confident', 'deviation': dev}
    if dev <= gray_high:
        # Near target with high fill → auto; otherwise ask
        if kind in ('text', 'fill') and filled_ratio >= 0.85 and soft_ask_skips >= 1:
            return {'action': 'auto', 'reason': 'near_target_well_filled', 'deviation': dev}
        return {'action': 'ask', 'reason': 'in_range_uncertain', 'deviation': dev}
    return {'action': 'ask', 'reason': 'off_target', 'deviation': dev}
