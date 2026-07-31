"""Load style cards for prompt injection."""
from __future__ import annotations

import json
import random
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_CARDS_PATH = _ROOT / 'cards.json'
_cache = None


def _load():
    global _cache
    if _cache is None:
        _cache = json.loads(_CARDS_PATH.read_text(encoding='utf-8'))
    return _cache


def all_cards():
    return list((_load().get('cards') or []))


def pick_cards(lang='zh', n=2, culture=None, rng=None):
    """Pick up to n style cards matching language (zh cards ok for zh; en prefers en)."""
    rng = rng or random
    en = (lang or 'zh').startswith('en')
    cards = all_cards()
    if en:
        pool = [c for c in cards if (c.get('lang') or '').startswith('en')]
        if len(pool) < n:
            pool = pool + [c for c in cards if c not in pool]
    else:
        pool = [c for c in cards if not (c.get('lang') or '').startswith('en')]
        # prefer user_ref first
        pool.sort(key=lambda c: 0 if str(c.get('id', '')).startswith('user_') else 1)
    if culture:
        # soft prefer cards mentioning culture keywords in clusters/source
        keyed = [c for c in pool if culture in json.dumps(c, ensure_ascii=False)]
        if keyed:
            pool = keyed + [c for c in pool if c not in keyed]
    if not pool:
        return []
    if n >= len(pool):
        return pool[:n]
    # always include first preferred if available
    chosen = [pool[0]]
    rest = pool[1:]
    rng.shuffle(rest)
    chosen.extend(rest[: max(0, n - 1)])
    return chosen[:n]


def format_injection(cards, lang='zh') -> str:
    if not cards:
        return ''
    en = (lang or 'zh').startswith('en')
    bits = []
    for c in cards:
        snip = c.get('prompt_snippet_en') if en else c.get('prompt_snippet')
        snip = snip or c.get('prompt_snippet') or ''
        bits.append(f"- [{c.get('id')}] {snip}")
    # Always append one association teaching line
    if en:
        bits.append(
            '- [assoc] Montage OK without grammar IF same field or one emotion-formula; '
            'ban cross-field salad; tag hook type in intent.'
        )
    else:
        bits.append(
            '- [assoc] 并置可无语法粘合，但须同场或同情绪公式；禁跨场乱接；intent标明钩子类型。'
        )
    header = (
        'STYLE CARDS (imitate breath/structure only; NEVER copy themes or lines):\n'
        if en else
        '【风格卡——只仿呼吸与结构，禁止抄主题与原句】\n'
    )
    return header + '\n'.join(bits)
