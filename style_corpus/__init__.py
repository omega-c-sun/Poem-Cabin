"""Load style cards + sparse historical palettes for prompt injection."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_CARDS_PATH = _ROOT / 'cards.json'
_USER_POEMS_PATH = _ROOT / 'user_poems.json'
_cache = None
_poems_cache = None

# Card register: classical ≈ 近体/文言余味；modern ≈ 自由诗/当代；mixed = either
_REGISTER_BY_ID = {
    'user_ref_cluster': 'modern',
    'misty_montage': 'modern',
    'fairy_clear': 'modern',
    'ritual_land': 'mixed',
    'tang_breath': 'classical',
    'en_imagist': 'modern',
    'en_breath_confessional': 'modern',
}

# Sparse classical image/verb seeds for regulated verse (偶用，非整库倾泻)
_CLASSICAL_PALETTE = {
    '景物': ['月', '江', '雁', '霜', '灯', '竹', '窗', '云', '山', '渡', '桥', '笛', '钟', '帆', '雨', '烟'],
    '时令': ['暮', '宵', '晓', '秋', '春', '寒', '晚', '夜'],
    '人情': ['愁', '归', '别', '影', '梦', '泪', '心', '思'],
    '动词': ['照', '敲', '断', '凝', '卷', '落', '拂', '映', '渡', '寄', '闻', '望'],
}

# Modern free-verse habits that must not leak into 格律填字
_MODERN_BAN_ZH = (
    '心脏', '脉搏', '人儿', '人儿呦', '妄想', '彷徨', '纠结', '泄气',
    '本身', '不是而是', '问号', '站牌', '铁轨', '耳廓', '霜针',
)


def _load():
    global _cache
    if _cache is None:
        _cache = json.loads(_CARDS_PATH.read_text(encoding='utf-8'))
    return _cache


def _load_poems():
    global _poems_cache
    if _poems_cache is None:
        if _USER_POEMS_PATH.exists():
            _poems_cache = json.loads(_USER_POEMS_PATH.read_text(encoding='utf-8'))
        else:
            _poems_cache = {'poems': []}
    return _poems_cache


def all_cards():
    cards = list((_load().get('cards') or []))
    for c in cards:
        if 'register' not in c:
            c['register'] = _REGISTER_BY_ID.get(c.get('id'), 'mixed')
    return cards


def is_regulated_form(form) -> bool:
    if not form:
        return False
    if form.get('chars_per_line'):
        return True
    fid = (form.get('id') or '')
    return fid in (
        'wuyan_lushi', 'qiyan_lushi', 'wuyan_jueju', 'qiyan_jueju',
    )


def card_register(card) -> str:
    return (card or {}).get('register') or _REGISTER_BY_ID.get((card or {}).get('id'), 'mixed')


def inject_budget(stage=None, form=None, rng=None) -> int:
    """
    How many style cards to inject this round.
    Lower frequency overall; regulated verse prefers 0–1 classical, not 2 modern.
    """
    rng = rng or random
    regulated = is_regulated_form(form)
    st = (stage or '') or ''
    # examples / structure: slightly more guidance; fill: rarer
    if regulated:
        # ~35% none, ~50% one classical, ~15% two
        roll = rng.random()
        if roll < 0.35:
            return 0
        if roll < 0.85:
            return 1
        return 1 if st in ('symbols', 'verbs', 'link', 'final') else 2
    # free / English: still don't always dump 2 cards
    if st in ('symbols', 'verbs'):
        return 1 if rng.random() < 0.55 else 0
    if st in ('link', 'final'):
        return 1 if rng.random() < 0.4 else 0
    return 2 if rng.random() < 0.7 else 1


def pick_cards(lang='zh', n=2, culture=None, form=None, topic=None, stage=None, rng=None):
    """
    Pick up to n style cards matching language + verse register.
    Regulated Chinese → prefer classical (tang_breath); deprioritize modern user_ref.
    """
    rng = rng or random
    if n is None:
        n = inject_budget(stage=stage, form=form, rng=rng)
    if n <= 0:
        return []

    en = (lang or 'zh').startswith('en')
    cards = all_cards()
    if en:
        pool = [c for c in cards if (c.get('lang') or '').startswith('en')]
        if len(pool) < n:
            pool = pool + [c for c in cards if c not in pool]
    else:
        pool = [c for c in cards if not (c.get('lang') or '').startswith('en')]

    regulated = is_regulated_form(form)
    if regulated and not en:
        classical = [c for c in pool if card_register(c) == 'classical']
        mixed = [c for c in pool if card_register(c) == 'mixed']
        modern = [c for c in pool if card_register(c) == 'modern']
        # Prefer classical; allow mixed; modern only as last resort and never first
        pool = classical + mixed + modern
    else:
        # Free verse: soft prefer user_ref, but do not always force it first
        pool.sort(key=lambda c: (
            0 if str(c.get('id', '')).startswith('user_') and rng.random() < 0.45 else 1,
            0 if card_register(c) == 'modern' else 1,
        ))

    if culture:
        keyed = [c for c in pool if culture in json.dumps(c, ensure_ascii=False)]
        if keyed:
            pool = keyed + [c for c in pool if c not in keyed]

    if topic and not en:
        # Soft boost cards whose image_clusters overlap topic chars
        tchars = set(re.findall(r'[\u4e00-\u9fff]', topic or ''))
        def _overlap(c):
            blob = ' '.join(c.get('image_clusters') or []) + (c.get('prompt_snippet') or '')
            return len(tchars & set(re.findall(r'[\u4e00-\u9fff]', blob)))
        pool = sorted(pool, key=lambda c: -_overlap(c))

    if not pool:
        return []
    if n >= len(pool):
        chosen = pool[:n]
    else:
        # Sample without always locking card[0]
        if regulated and not en:
            preferred = [c for c in pool if card_register(c) in ('classical', 'mixed')] or pool
            chosen = []
            rest = list(preferred)
            rng.shuffle(rest)
            chosen.extend(rest[:n])
        else:
            chosen = []
            rest = list(pool)
            # 40% chance skip forcing first card
            if rest and rng.random() < 0.6:
                chosen.append(rest.pop(0))
            rng.shuffle(rest)
            chosen.extend(rest[: max(0, n - len(chosen))])

    # Hard filter: regulated must not get only-modern set if classical exists
    if regulated and not en and chosen:
        if all(card_register(c) == 'modern' for c in chosen):
            classical = [c for c in all_cards() if card_register(c) == 'classical'
                         and not (c.get('lang') or '').startswith('en')]
            if classical:
                chosen = [classical[0]] + [c for c in chosen if card_register(c) != 'modern'][: n - 1]
    return chosen[:n]


def pick_historical_palette(topic=None, form=None, n=6, rng=None) -> list:
    """
    Sparse historical/classical words for card-fill — not whole poems.
    Lower density: few tokens, topic-filtered when possible.
    """
    rng = rng or random
    regulated = is_regulated_form(form)
    # Free verse: even rarer / smaller
    if not regulated:
        if rng.random() > 0.35:
            return []
        n = min(n, 3)

    pool = []
    for words in _CLASSICAL_PALETTE.values():
        pool.extend(words)

    # Light touch from user poems: only 1–2 char classical-ish tokens overlapping topic
    topic_chars = set(re.findall(r'[\u4e00-\u9fff]', topic or ''))
    for poem in (_load_poems().get('poems') or [])[:12]:
        text = poem.get('text') or ''
        # Skip heavy modern free-verse dumps for regulated
        if regulated and any(b in text for b in ('人儿呦', '心脏', '妄想着')):
            # still allow shared classical chars (月/星/窗)
            pass
        for m in re.findall(r'[\u4e00-\u9fff]{1,2}', text):
            if regulated and m in _MODERN_BAN_ZH:
                continue
            if topic_chars and not (set(m) & topic_chars):
                # keep some non-topic classical for breath
                if rng.random() > 0.15:
                    continue
            if m not in pool and len(m) <= 2:
                pool.append(m)

    # Prefer topic overlap
    if topic_chars:
        preferred = [w for w in pool if set(w) & topic_chars]
        other = [w for w in pool if w not in preferred]
        rng.shuffle(preferred)
        rng.shuffle(other)
        ordered = preferred + other
    else:
        ordered = list(pool)
        rng.shuffle(ordered)

    out = []
    seen = set()
    for w in ordered:
        if w in seen or w in _MODERN_BAN_ZH:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= n:
            break
    return out


def format_injection(cards, lang='zh', form=None, palette=None) -> str:
    if not cards and not palette:
        return ''
    en = (lang or 'zh').startswith('en')
    regulated = is_regulated_form(form)
    bits = []
    for c in cards or []:
        snip = c.get('prompt_snippet_en') if en else c.get('prompt_snippet')
        snip = snip or c.get('prompt_snippet') or ''
        reg = card_register(c)
        bits.append(f"- [{c.get('id')}|{reg}] {snip}")
    if palette:
        joined = '、'.join(palette) if not en else ', '.join(palette)
        if en:
            bits.append(
                f'- [palette] Occasional craft seeds (use ≤2 if they fit; do NOT dump all): {joined}'
            )
        else:
            bits.append(
                f'- [历史意象·偶用] 仅当与当前主题/体裁契合时选用其中≤2个，禁止整表倾泻：{joined}'
            )
    if en:
        bits.append(
            '- [assoc] Montage OK without grammar IF same field or one emotion-formula; '
            'ban cross-field salad; tag hook type in intent.'
        )
    else:
        bits.append(
            '- [assoc] 并置可无语法粘合，但须同场或同情绪公式；禁跨场乱接；intent标明钩子类型。'
        )
        if regulated:
            bits.append(
                '- [语体] 当前为格律/近体：仿古典顿挫与单字锤炼；'
                '禁止把现代自由诗的短行独白、心理分析长定语、当代口语灌进五/七言。'
            )
    if en:
        header = (
            'STYLE CARDS (imitate breath/structure only; NEVER copy themes or lines; '
            'use sparsely):\n'
        )
    else:
        header = (
            '【风格卡——只仿呼吸与结构，禁止抄主题与原句；低频参考，勿整段套用】\n'
        )
    return header + '\n'.join(bits)


def modern_dictation_bans(form=None, lang='zh') -> str:
    """Extra ban line when filling regulated Chinese verse."""
    if not is_regulated_form(form):
        return ''
    if (lang or 'zh').startswith('en'):
        return ''
    bans = '、'.join(_MODERN_BAN_ZH[:10])
    return (
        '【格律语体硬约束】用近体/文言意象与单字动词；'
        f'禁止现代自由诗套语（如 {bans} 等）；'
        '禁止白话长定语（的）、散文句、口语独白；每槽一字，景→情，勿心理说明书。'
    )
