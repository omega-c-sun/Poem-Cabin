"""Stage output schemas: parse LLM payloads into channel-ready structures."""
from __future__ import annotations

import json
import re

DIM_KEYS = ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth']



def _extract_json_blob(text):
    if not text:
        return None
    text = text.strip()
    m = re.search(r'```(?:json)?\s*([\s\S]+?)```', text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for pat in (r'(\{[\s\S]*\})',):
        m = re.search(pat, text)
        if not m:
            continue
        try:
            return json.loads(m.group(1))
        except Exception:
            continue
    return None


def _norm_dims(raw):
    out = {}
    if not isinstance(raw, dict):
        return {k: 50 for k in DIM_KEYS}
    for k in DIM_KEYS:
        v = raw.get(k)
        try:
            x = float(v)
            if x <= 1.0:
                x *= 100
            out[k] = int(round(min(100, max(0, x))))
        except Exception:
            out[k] = 50
    return out


def _clip_text(text, limit, prefer_lines=True):
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    if prefer_lines:
        cut = chunk.rfind('\n')
        if cut >= int(limit * 0.4):
            return chunk[:cut].rstrip()
    # avoid mid-word cut when possible
    cut = max(chunk.rfind(' '), chunk.rfind('—'), chunk.rfind('-'))
    if cut >= int(limit * 0.5):
        return chunk[:cut].rstrip()
    return chunk.rstrip()


def _norm_example(ex, idx):
    if not isinstance(ex, dict):
        return None
    eid = str(ex.get('id') or chr(65 + idx))
    poem = (ex.get('poem') or ex.get('诗正文') or '').strip()
    title = (ex.get('title') or ex.get('标题') or f'示例{eid}').strip()
    template = (ex.get('template') or ex.get('句式模板') or '').strip()
    rules = (ex.get('rules') or ex.get('主韵') or ex.get('禁忌') or '').strip()
    if ex.get('禁忌') and '禁忌' not in rules:
        rules = (rules + '；禁忌：' + str(ex.get('禁忌'))).strip('；')
    dims = _norm_dims(ex.get('dims') or ex.get('维度差异') or ex.get('维度') or {})
    return {
        'id': eid,
        'title': _clip_text(title, 80, prefer_lines=False),
        'dims': dims,
        'template': _clip_text(template, 240, prefer_lines=False),
        'rules': _clip_text(rules, 280, prefer_lines=False),
        # English sonnets need ~800–1400 chars; old 400 cut mid-word
        'poem': _clip_text(poem, 2000, prefer_lines=True),
    }


def _heuristic_from_prose(text):
    """Pull up to 3 cards from ### 示例 / 诗正文 blocks."""
    if not text:
        return None
    chunks = re.split(r'(?=###\s*示例|示例\s*[123ABC一二三])', text)
    examples = []
    for ch in chunks:
        if not re.search(r'示例|诗正文', ch):
            continue
        title_m = re.search(r'(?:###\s*)?示例\s*([123ABC一二三N\d]?)[：:\s]*([^\n]*)', ch)
        title = ''
        eid = chr(65 + len(examples))
        if title_m:
            raw_id = title_m.group(1) or ''
            title = (title_m.group(2) or '').strip()[:40]
            if raw_id in '1一':
                eid = 'A'
            elif raw_id in '2二':
                eid = 'B'
            elif raw_id in '3三':
                eid = 'C'
            elif raw_id in 'ABC':
                eid = raw_id
        poem_m = re.search(r'诗正文[:：]\s*\n([\s\S]+?)(?=\n###|\n示例|\n---|\n\*\*|维度差异|下一步|$)', ch)
        poem = ''
        if poem_m:
            poem = poem_m.group(1).strip()
            poem = re.split(r'\n#{1,3}\s|\n维度|\n检查', poem)[0].strip()
        if not poem:
            # lines that look like verse
            lines = []
            for ln in ch.splitlines():
                s = ln.strip()
                if not s or s.startswith('#') or s.startswith('**') or '：' in s[:12]:
                    continue
                if re.search(r'[\u4e00-\u9fff]{4,}', s) and len(s) < 40:
                    lines.append(s)
            poem = '\n'.join(lines[:4])
        if not poem:
            continue
        tmpl_m = re.search(r'句式模板[:：]\s*(.+)', ch)
        rules_m = re.search(r'(?:主韵|押韵规则|禁忌)[:：]\s*(.+)', ch)
        dims = {}
        dim_m = re.search(
            r'rhyme\s*[:=｜|]?\s*(\d+).*?rhythm\s*[:=｜|]?\s*(\d+).*?'
            r'tension\s*[:=｜|]?\s*(\d+).*?paradox\s*[:=｜|]?\s*(\d+).*?'
            r'metaphor\s*[:=｜|]?\s*(\d+).*?freshness\s*[:=｜|]?\s*(\d+)',
            ch, re.I | re.S)
        if dim_m:
            for i, k in enumerate(DIM_KEYS):
                dims[k] = int(dim_m.group(i + 1))
        examples.append(_norm_example({
            'id': eid,
            'title': title or f'示例{eid}',
            'poem': poem,
            'template': tmpl_m.group(1).strip() if tmpl_m else '',
            'rules': rules_m.group(1).strip() if rules_m else '',
            'dims': dims,
        }, len(examples)))
        if len(examples) >= 3:
            break
    if not examples:
        return None
    return {
        'summary': '三组风格对照已生成，请点选其一进入结构。',
        'examples': examples,
        'choices': default_choices(examples),
    }


def default_choices(examples, lang=None):
    en = (lang or 'zh').startswith('en')
    themes = ['insomnia', 'old key', 'echo'] if en else ['失眠', '旧钥匙', '回声']
    adjust = 'Adjust dimensions / bans' if en else '调整维度/禁忌'
    use_fmt = 'Use {id} for: {theme}' if en else '用{id}写：{theme}'
    choices = []
    for i, ex in enumerate(examples[:3]):
        theme = themes[i] if i < len(themes) else ('new theme' if en else '新主题')
        choices.append({
            'id': str(i + 1),
            'example_id': ex['id'],
            'label': use_fmt.format(id=ex['id'], theme=theme),
        })
    choices.append({'id': '4', 'example_id': None, 'label': adjust})
    return choices


def average_dims(examples):
    if not examples:
        return {k: 50 for k in DIM_KEYS}
    acc = {k: 0 for k in DIM_KEYS}
    n = 0
    for ex in examples:
        dims = ex.get('dims') or {}
        n += 1
        for k in DIM_KEYS:
            acc[k] += int(dims.get(k, 50))
    if not n:
        return acc
    return {k: int(round(acc[k] / n)) for k in DIM_KEYS}


def parse_examples_payload(text, lang=None):
    """
    Returns dict: summary, examples[3], choices
    Always returns something usable.
    """
    en = (lang or 'zh').startswith('en')
    data = _extract_json_blob(text)
    if isinstance(data, dict) and (data.get('examples') or data.get('示例')):
        raw_list = data.get('examples') or data.get('示例') or []
        examples = []
        for i, ex in enumerate(raw_list[:5]):
            ne = _norm_example(ex, i)
            if ne and ne.get('poem'):
                examples.append(ne)
        if len(examples) >= 1:
            choices = data.get('choices') or data.get('选项')
            if not isinstance(choices, list) or not choices:
                choices = default_choices(examples, lang=lang)
            else:
                norm_c = []
                for i, c in enumerate(choices):
                    if not isinstance(c, dict):
                        continue
                    norm_c.append({
                        'id': str(c.get('id') or i + 1),
                        'example_id': c.get('example_id') or c.get('example') or (
                            examples[i]['id'] if i < len(examples) else None),
                        'label': str(c.get('label') or c.get('text') or '')[:60],
                    })
                if not any(c.get('id') == '4' for c in norm_c):
                    norm_c.append({
                        'id': '4',
                        'example_id': None,
                        'label': 'Adjust dimensions / bans' if en else '调整维度/禁忌',
                    })
                choices = norm_c
            summary = (data.get('summary') or data.get('摘要') or (
                'Three contrasting styles — pick a card.'
                if en else '三组风格对照已生成，请点选卡片或下方编号。'))
            return {
                'summary': str(summary)[:120],
                'examples': examples[:3],
                'choices': choices,
            }

    heur = _heuristic_from_prose(text)
    if heur:
        heur['choices'] = default_choices(heur['examples'], lang=lang)
        if en:
            heur['summary'] = 'Parsed style cards — pick one to continue.'
        return heur

    # language-aware degraded fallback
    if en:
        return json.loads(FALLBACK_EXAMPLES_JSON_EN)
    return json.loads(FALLBACK_EXAMPLES_JSON)


def _base_dims(**kwargs):
    base = {k: 50 for k in DIM_KEYS}
    base.update(kwargs)
    return base


FALLBACK_EXAMPLES_JSON = json.dumps({
    'summary': '三组对照（本地降级）',
    'examples': [
        {
            'id': 'A',
            'title': '高句内韵·七言',
            'dims': _base_dims(rhyme=90, rhythm=20, tension=70, paradox=65, metaphor=30, freshness=40, depth=75),
            'template': '[二字抽象][单字动词][二字具象][韵脚]',
            'rules': '主韵 -ang；禁忌：无否定式',
            'poem': '残阳烫穿深渊膛\n血日裂开余烬量\n念头游走骨缝中\n碎镜反照虚空方',
        },
        {
            'id': 'B',
            'title': '长短交替·借代',
            'dims': _base_dims(rhyme=40, rhythm=80, tension=30, paradox=50, metaphor=80, freshness=65, depth=70),
            'template': '[实体][动词][抽象] / [主体][空位][韵脚]',
            'rules': '7-5交替；每行≤1单字动词',
            'poem': '锈锁咬碎月光链\n时间锈在铁轨间\n断弦弹破旧琴键\n站台空悬半张脸',
        },
        {
            'id': 'C',
            'title': '短句对立爆发',
            'dims': _base_dims(rhyme=30, rhythm=50, tension=85, paradox=90, metaphor=60, freshness=55, depth=80),
            'template': '[主体][动词][自身] / [向对立][动词][意象]',
            'rules': '四字短句；禁忌七言',
            'poem': '影子撕开自己\n向光刺出匕首\n影子贴在墙上\n等待光熄灭后',
        },
    ],
    'choices': [
        {'id': '1', 'example_id': 'A', 'label': '用A写：失眠'},
        {'id': '2', 'example_id': 'B', 'label': '用B写：旧钥匙'},
        {'id': '3', 'example_id': 'C', 'label': '用C写：回声'},
        {'id': '4', 'example_id': None, 'label': '调整维度/禁忌'},
    ],
}, ensure_ascii=False)

FALLBACK_EXAMPLES_JSON_EN = json.dumps({
    'summary': 'Three style cards (offline fallback)',
    'examples': [
        {
            'id': 'A',
            'title': 'Dense internal rhyme',
            'dims': _base_dims(rhyme=90, rhythm=20, tension=70, paradox=65, metaphor=30, freshness=40, depth=75),
            'template': '[abstract][verb][image][rhyme]',
            'rules': 'End-rhyme locked; no negation clichés',
            'poem': 'Ash sun burns the abyss open\nBlood light splits the ember measure\nA thought walks bone-seam corridors\nBroken glass returns the hollow face',
        },
        {
            'id': 'B',
            'title': 'Long-short breath + metonymy',
            'dims': _base_dims(rhyme=40, rhythm=80, tension=30, paradox=50, metaphor=80, freshness=65, depth=70),
            'template': '[object][verb][abstract] / [subject][gap][rhyme]',
            'rules': 'Alternating line length; ≤1 stark verb per line',
            'poem': 'Rust locks chew the moon-chain\nTime rusts between iron rails\nA snapped string cracks the old key\nThe platform holds half a face',
        },
        {
            'id': 'C',
            'title': 'Short-line paradox burst',
            'dims': _base_dims(rhyme=30, rhythm=50, tension=85, paradox=90, metaphor=60, freshness=55, depth=80),
            'template': '[subject][verb][self] / [toward opposite][verb][image]',
            'rules': 'Short lines; no padding adverbs',
            'poem': 'Shadow tears itself\nStabs a knife toward light\nShadow sticks to wall\nWaits for light to die',
        },
    ],
    'choices': [
        {'id': '1', 'example_id': 'A', 'label': 'Use A for: insomnia'},
        {'id': '2', 'example_id': 'B', 'label': 'Use B for: old key'},
        {'id': '3', 'example_id': 'C', 'label': 'Use C for: echo'},
        {'id': '4', 'example_id': None, 'label': 'Adjust dimensions / bans'},
    ],
}, ensure_ascii=False)
