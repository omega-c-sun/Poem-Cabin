"""Detect requested verse forms and their structural constraints."""
from __future__ import annotations

import re


# Canonical forms: exact or range line counts + stanza hints for the skeleton agent
FORMS = {
    'shakespearean_sonnet': {
        'id': 'shakespearean_sonnet',
        'aliases': ('shakespearean sonnet', 'english sonnet', '莎士比亚商籁', '莎体十四行'),
        'lines': 14,
        'line_min': 14,
        'line_max': 14,
        'stanzas': '3 quatrains (4+4+4) + couplet (2) = 14 lines',
        'stanzas_zh': '三节四行（4+4+4）+ 对句（2）= 14 行',
        'rhyme': 'ABAB CDCD EFEF GG (approximate OK at skeleton)',
        'slots_hint': 'English: ~8–12 slots/line (iambic pentameter-ish); keep DET/N/V/P.',
    },
    'petrarchan_sonnet': {
        'id': 'petrarchan_sonnet',
        'aliases': ('petrarchan sonnet', 'italian sonnet', '彼特拉克', '意体十四行'),
        'lines': 14,
        'line_min': 14,
        'line_max': 14,
        'stanzas': 'octave (8) + sestet (6) = 14 lines',
        'stanzas_zh': '八行组（8）+ 六行组（6）= 14 行',
        'rhyme': 'ABBAABBA + CDECDE/CDCDCD',
        'slots_hint': 'English: ~8–12 slots/line; keep DET/N/V/P.',
    },
    'sonnet': {
        'id': 'sonnet',
        'aliases': ('sonnet', '十四行', '商籁', 'ソネット'),
        # Default English sonnet = Shakespearean layout
        'lines': 14,
        'line_min': 14,
        'line_max': 14,
        'stanzas': 'DEFAULT Shakespearean: 3 quatrains + couplet (4+4+4+2). NOT 2 quatrains + couplet.',
        'stanzas_zh': '默认莎体：三节四行 + 对句（4+4+4+2）。禁止两节四行+对句（那不是十四行）。',
        'rhyme': 'ABAB CDCD EFEF GG',
        'slots_hint': 'Exactly 14 lines. English prefer DET/N/V glue; ~8–12 slots/line.',
    },
    'haiku': {
        'id': 'haiku',
        'aliases': ('haiku', '俳句', '俳諧'),
        'lines': 3,
        'line_min': 3,
        'line_max': 3,
        'stanzas': '3 lines (5-7-5 mora / short-long-short)',
        'stanzas_zh': '三行（5-7-5 音或短-长-短）',
        'rhyme': 'none required',
        'slots_hint': '3 lines only; sparse slots.',
    },
    'limerick': {
        'id': 'limerick',
        'aliases': ('limerick', '五行打油'),
        'lines': 5,
        'line_min': 5,
        'line_max': 5,
        'stanzas': '5 lines AABBA',
        'stanzas_zh': '五行 AABBA',
        'rhyme': 'AABBA',
        'slots_hint': '5 lines; lines 3–4 shorter.',
    },
    'couplet': {
        'id': 'couplet',
        'aliases': ('heroic couplet', '对句', 'couplet poem'),
        'lines': 2,
        'line_min': 2,
        'line_max': 2,
        'stanzas': '2 rhyming lines',
        'stanzas_zh': '两行押韵对句',
        'rhyme': 'AA',
        'slots_hint': 'Exactly 2 lines.',
    },
    'quatrain': {
        'id': 'quatrain',
        'aliases': ('quatrain', '四行诗'),
        'lines': 4,
        'line_min': 4,
        'line_max': 4,
        'stanzas': '1 quatrain (4 lines)',
        'stanzas_zh': '一节四行',
        'rhyme': 'ABAB or AABB',
        'slots_hint': 'Exactly 4 lines.',
    },
    # Chinese regulated verse — chars_per_line is hard
    'wuyan_lushi': {
        'id': 'wuyan_lushi',
        'aliases': (
            '五言律诗', '五律', '八句五言', '五言八句',
            'wuyan lushi', '5-char regulated',
        ),
        'lines': 8,
        'line_min': 8,
        'line_max': 8,
        'chars_per_line': 5,
        'stanzas': '8 lines × 5 characters (wuyan lüshi)',
        'stanzas_zh': '五言律诗：恰好 8 句，每句恰好 5 字（禁止六字行）',
        'rhyme': '平起/仄起均可；二四六八句押韵（骨架阶段可粗略）',
        'slots_hint': '8行×每行5个单字槽（或槽位汉字合计=5）。禁止双字×3=六字。',
    },
    'qiyan_lushi': {
        'id': 'qiyan_lushi',
        'aliases': ('七言律诗', '七律', '八句七言', '七言八句', 'qiyan lushi'),
        'lines': 8,
        'line_min': 8,
        'line_max': 8,
        'chars_per_line': 7,
        'stanzas': '8 lines × 7 characters',
        'stanzas_zh': '七言律诗：恰好 8 句，每句恰好 7 字',
        'rhyme': '二四六八句押韵',
        'slots_hint': '8行×每行汉字合计=7。',
    },
    'wuyan_jueju': {
        'id': 'wuyan_jueju',
        'aliases': ('五言绝句', '五绝', '四句五言', 'wuyan jueju'),
        'lines': 4,
        'line_min': 4,
        'line_max': 4,
        'chars_per_line': 5,
        'stanzas': '4 lines × 5 characters',
        'stanzas_zh': '五言绝句：恰好 4 句，每句恰好 5 字',
        'rhyme': '二四句押韵',
        'slots_hint': '4行×每行5字。',
    },
    'qiyan_jueju': {
        'id': 'qiyan_jueju',
        'aliases': ('七言绝句', '七绝', '四句七言', 'qiyan jueju'),
        'lines': 4,
        'line_min': 4,
        'line_max': 4,
        'chars_per_line': 7,
        'stanzas': '4 lines × 7 characters',
        'stanzas_zh': '七言绝句：恰好 4 句，每句恰好 7 字',
        'rhyme': '二四句押韵',
        'slots_hint': '4行×每行7字。',
    },
    'free': {
        'id': 'free',
        'aliases': ('free verse', '自由诗'),
        'lines': None,
        'line_min': 3,
        'line_max': 12,
        'stanzas': 'free verse: typically 3–12 lines unless user says otherwise',
        'stanzas_zh': '自由诗：默认 3–12 行（除非用户另有说明）',
        'rhyme': 'optional',
        'slots_hint': '3–12 lines, 2–6 slots/line unless form needs denser English meter.',
    },
}


def _blob_from_session(session, extra_user=None):
    bits = []
    if extra_user:
        bits.append(str(extra_user))
    meta = {}
    raw = session.get('stage_meta') if session else None
    if isinstance(raw, dict):
        meta = raw
    elif raw is not None:
        try:
            import db as _db
            meta = _db.loads(raw, {}) or {}
        except Exception:
            try:
                import json
                meta = json.loads(raw) if isinstance(raw, str) else {}
            except Exception:
                meta = {}
    if meta.get('verse_form'):
        bits.append(str(meta.get('verse_form')))
    sel = meta.get('selected_example') or {}
    for k in ('title', 'template', 'rules', 'poem', 'label'):
        if sel.get(k):
            bits.append(str(sel.get(k)))
    log = session.get('chat_log') if session else None
    if log is not None and not isinstance(log, list):
        try:
            import db as _db
            log = _db.loads(log, [])
        except Exception:
            try:
                import json
                log = json.loads(log) if isinstance(log, str) else []
            except Exception:
                log = []
    for m in (log or [])[-24:]:
        if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and m.get('content'):
            bits.append(str(m['content'])[:800])
    # session title sometimes carries form hints
    if session and session.get('title'):
        bits.append(str(session.get('title')))
    return '\n'.join(bits).lower()


def split_zh_verse_lines(text):
    """
    Split classical Chinese verse into lines.
    Handles both newlines and ，。； punctuation on a single line.
    """
    out = []
    for ln in re.split(r'[\r\n]+', text or ''):
        ln = (ln or '').strip()
        if not ln:
            continue
        parts = re.split(r'[，。；、！？；;!?\u3002\uff0c]+', ln)
        for p in parts:
            p = p.strip()
            if re.search(r'[\u4e00-\u9fffA-Za-z]', p):
                out.append(p)
    return out


def infer_form_from_poem(poem_text):
    """Infer regulated / fixed form from poem line count × chars."""
    lines = split_zh_verse_lines(poem_text)
    zh_lines = [ln for ln in lines if re.search(r'[\u4e00-\u9fff]', ln)]
    if len(zh_lines) >= 4:
        lens = [len(re.findall(r'[\u4e00-\u9fff]', ln)) for ln in zh_lines]
        n = len(zh_lines)
        if n == 8 and all(x == 5 for x in lens):
            return dict(FORMS['wuyan_lushi'])
        if n == 8 and all(x == 7 for x in lens):
            return dict(FORMS['qiyan_lushi'])
        if n == 4 and all(x == 5 for x in lens):
            return dict(FORMS['wuyan_jueju'])
        if n == 4 and all(x == 7 for x in lens):
            return dict(FORMS['qiyan_jueju'])
        # near-miss: 4 lines mostly 7 chars → 七绝
        if n == 4 and sum(1 for x in lens if x == 7) >= 3:
            return dict(FORMS['qiyan_jueju'])
        if n == 4 and sum(1 for x in lens if x == 5) >= 3:
            return dict(FORMS['wuyan_jueju'])
        if n >= 7 and sum(1 for x in lens[:8] if x == 5) >= 6:
            return dict(FORMS['wuyan_lushi'])
        if n >= 7 and sum(1 for x in lens[:8] if x == 7) >= 6:
            return dict(FORMS['qiyan_lushi'])
    # English sonnet-ish
    en_lines = [ln for ln in (poem_text or '').splitlines() if re.search(r'[A-Za-z]', ln)]
    if len(en_lines) == 14:
        return dict(FORMS['sonnet'])
    return None


def detect_explicit_form(blob):
    """
    Detect form from explicit user/card wording only (no lock, no weak aliases).
    Priority: 七绝/五绝/七律/五律/sonnet… over bare 格律诗 / quatrain.
    """
    b = (blob or '').lower()
    if not b.strip():
        return None
    # Chinese regulated — most specific first
    if re.search(r'七言绝句|七绝|四句七言|每句\s*7\s*字|每句七字|7字绝句', b):
        return dict(FORMS['qiyan_jueju'])
    if re.search(r'五言绝句|五绝|四句五言|每句\s*5\s*字|每句五字|5字绝句', b):
        return dict(FORMS['wuyan_jueju'])
    if re.search(r'七言律诗|七律|八句七言', b):
        return dict(FORMS['qiyan_lushi'])
    if re.search(r'五言律诗|五律|八句五言|五言八句', b):
        return dict(FORMS['wuyan_lushi'])
    if re.search(r'绝句', b):
        if '七言' in b or '七字' in b:
            return dict(FORMS['qiyan_jueju'])
        return dict(FORMS['wuyan_jueju'])
    if re.search(r'格律诗|律诗', b):
        if '七言' in b or '七字' in b:
            return dict(FORMS['qiyan_lushi'])
        return dict(FORMS['wuyan_lushi'])
    if re.search(r'shakespearean\s+sonnet|莎体|英语十四行', b):
        return dict(FORMS['shakespearean_sonnet'])
    if re.search(r'petrarchan|彼特拉克|意体十四行', b):
        return dict(FORMS['petrarchan_sonnet'])
    if re.search(r'\bsonnet\b|十四行|商籁', b):
        return dict(FORMS['sonnet'])
    if re.search(r'\bhaiku\b|俳句', b):
        return dict(FORMS['haiku'])
    if re.search(r'\blimerick\b|五行打油', b):
        return dict(FORMS['limerick'])
    return None


_WEAK_LOCKS = frozenset({'quatrain', 'couplet', 'free'})


def detect_verse_form(session=None, extra_user=None, text=None):
    """
    Return a form dict (from FORMS). Explicit user/card names beat a weak prior lock.
    Falls back to free.
    """
    meta = {}
    if session is not None:
        raw = session.get('stage_meta')
        if isinstance(raw, dict):
            meta = raw
        elif raw is not None:
            try:
                import db as _db
                meta = _db.loads(raw, {}) or {}
            except Exception:
                meta = {}

    blob = (text or '')
    if session is not None or extra_user:
        blob = (_blob_from_session(session, extra_user) + '\n' + blob)
    blob_l = blob.lower()

    # 1) Explicit wording always wins (七言绝句 etc.) — even over wrong quatrain lock
    explicit = detect_explicit_form(blob_l)
    if explicit:
        return explicit

    # 2) Poem-shape from selected card / text
    sel = meta.get('selected_example') if isinstance(meta, dict) else None
    shape = None
    if isinstance(sel, dict) and sel.get('poem'):
        shape = infer_form_from_poem(sel.get('poem'))
    if not shape and text:
        shape = infer_form_from_poem(text)
    if shape:
        return shape

    # 3) Honor lock unless it is a weak English alias that fights 格律 hints
    locked = meta.get('verse_form') if isinstance(meta, dict) else None
    if locked and locked in FORMS and locked != 'free':
        if locked in _WEAK_LOCKS and re.search(r'格律|绝句|律诗|五言|七言', blob_l):
            pass  # fall through — do not keep quatrain over 格律
        else:
            return dict(FORMS[locked])

    # Heuristics for 格律诗 / 五言 from example cards
    if re.search(r'八句五言|五言八句|〔五言〕.*〔五言〕', blob_l) or (
            blob_l.count('〔五言〕') >= 4 or blob_l.count('[五言]') >= 4):
        return dict(FORMS['wuyan_lushi'])
    if re.search(r'八句七言|七言八句|〔七言〕', blob_l):
        return dict(FORMS['qiyan_lushi'])
    if re.search(r'四句五言', blob_l):
        return dict(FORMS['wuyan_jueju'])
    if re.search(r'四句七言', blob_l):
        return dict(FORMS['qiyan_jueju'])
    if '七言律诗' in blob_l or '七律' in blob_l or '八句七言' in blob_l:
        return dict(FORMS['qiyan_lushi'])
    if '五言律诗' in blob_l or '五律' in blob_l or '八句五言' in blob_l:
        return dict(FORMS['wuyan_lushi'])
    if '格律诗' in blob_l or '律诗' in blob_l:
        if '七言' in blob_l:
            return dict(FORMS['qiyan_lushi'])
        return dict(FORMS['wuyan_lushi'])
    if '七言绝句' in blob_l or '七绝' in blob_l:
        return dict(FORMS['qiyan_jueju'])
    if '五言绝句' in blob_l or '五绝' in blob_l or '绝句' in blob_l:
        if '七言' in blob_l:
            return dict(FORMS['qiyan_jueju'])
        return dict(FORMS['wuyan_jueju'])

    # Order: more specific first — skip bare quatrain when 格律 context
    order = (
        'shakespearean_sonnet',
        'petrarchan_sonnet',
        'sonnet',
        'wuyan_lushi',
        'qiyan_lushi',
        'wuyan_jueju',
        'qiyan_jueju',
        'haiku',
        'limerick',
        'couplet',
        'quatrain',
        'free',
    )
    for fid in order:
        if fid == 'quatrain' and re.search(r'格律|绝句|律诗|五言|七言', blob_l):
            continue
        spec = FORMS[fid]
        for alias in spec['aliases']:
            if alias.lower() in blob_l:
                return dict(spec)
        if fid in ('sonnet', 'haiku', 'limerick', 'couplet', 'quatrain'):
            if re.search(rf'\b{re.escape(fid)}\b', blob_l):
                return dict(spec)

    shape2 = infer_form_from_poem(blob)
    if shape2:
        return shape2

    return dict(FORMS['free'])


def lock_verse_form(session_id, form):
    """Persist form id into stage_meta.verse_form."""
    if not form or not form.get('id') or form.get('id') == 'free':
        return
    try:
        import db as _db
        row = _db.fetchone('SELECT stage_meta FROM poem_sessions WHERE id = %s', (str(session_id),))
        meta = _db.loads((row or {}).get('stage_meta'), {}) or {}
        meta['verse_form'] = form['id']
        _db.execute(
            'UPDATE poem_sessions SET stage_meta = %s::jsonb, updated_at = CURRENT_TIMESTAMP WHERE id = %s',
            (_db.dumps(meta), str(session_id)))
    except Exception:
        pass


def form_instruction(form, lang=None):
    """Human/LLM-facing constraint block."""
    en = (lang or 'zh').startswith('en')
    if not form or form.get('id') == 'free':
        return (
            'FORM: free verse unless the user named a fixed form. Default 3–12 lines.'
            if en else
            '体裁：自由诗（除非用户点名固定体裁）。默认 3–12 行。'
        )
    st = form.get('stanzas') if en else form.get('stanzas_zh') or form.get('stanzas')
    lines = form.get('lines')
    line_bit = (
        f'EXACTLY {lines} lines'
        if lines is not None else
        f'{form.get("line_min")}–{form.get("line_max")} lines'
    )
    chars = form.get('chars_per_line')
    char_bit = f'；每句恰好 {chars} 个汉字' if chars else ''
    char_bit_en = f'; each line EXACTLY {chars} Chinese characters' if chars else ''
    if en:
        return (
            f'FORM HARD CONSTRAINT — {form.get("id")}: {line_bit}{char_bit_en}. '
            f'Stanza layout: {st}. Rhyme hint: {form.get("rhyme")}. '
            f'{form.get("slots_hint")} '
            'Do NOT invent a shorter substitute (e.g. 2 quatrains + couplet is NOT a sonnet; '
            '3×2-char slots = 6 chars is NOT wuyan).'
        )
    return (
        f'体裁硬约束 — {form.get("id")}：必须 {line_bit}{char_bit}。'
        f'节结构：{st}。韵式提示：{form.get("rhyme")}。'
        f'{form.get("slots_hint")} '
        '禁止擅自缩短；禁止五言写成六字行（如三槽各填双字）。'
    )


def register_instruction(form, lang=None):
    """Diction / register: classical regulated vs modern free verse."""
    en = (lang or 'zh').startswith('en')
    cpl = (form or {}).get('chars_per_line')
    fid = (form or {}).get('id') or 'free'
    if not cpl and fid not in (
            'wuyan_lushi', 'qiyan_lushi', 'wuyan_jueju', 'qiyan_jueju'):
        if en:
            return 'REGISTER: match the named form; modern free-verse diction OK unless classical requested.'
        return '语体：随体裁；未点名格律时可用现代自由诗语汇，但须贴合主题。'
    if en:
        return (
            'REGISTER — classical regulated Chinese: monosyllabic diction, scenic→lyric; '
            'FORBIDDEN to fill like modern free verse (short-line monologue, psych jargon, '
            '白话长定语). Each slot = one character.'
        )
    return (
        '【语体——近体/格律】用古典景物与单字动词，景→情；'
        '严禁写成现代自由诗：禁止短行独白拼贴、当代心理词（心脏/人儿呦/妄想…）、'
        '白话「的」字长定语、散文句。一槽一字，句句满字。'
    )


def line_count_ok(canvas, form):
    """(ok, n_lines, expected_desc)."""
    n = len((canvas or {}).get('lines') or [])
    if not form:
        return True, n, 'any'
    lo = form.get('line_min')
    hi = form.get('line_max')
    exact = form.get('lines')
    if exact is not None:
        return n == exact, n, str(exact)
    if lo is not None and hi is not None:
        return lo <= n <= hi, n, f'{lo}-{hi}'
    return True, n, 'any'


def sonnet_stanza_ok(canvas, form):
    """
    Extra check for sonnet-family: 14 lines.
    Optionally warn if blank-line grouping exists — we don't store stanzas, so only line count.
    """
    if not form or 'sonnet' not in (form.get('id') or ''):
        return True, ''
    n = len((canvas or {}).get('lines') or [])
    if n != 14:
        return False, f'sonnet needs 14 lines, got {n}'
    return True, ''


def line_zh_char_count(line):
    n = 0
    for s in (line or {}).get('slots') or []:
        n += len(re.findall(r'[\u4e00-\u9fff]', s.get('text') or ''))
    return n


def line_chars_ok(canvas, form):
    """For regulated Chinese forms: every fully-filled line must hit chars_per_line."""
    cpl = (form or {}).get('chars_per_line')
    if not cpl:
        return True, []
    bad = []
    for i, ln in enumerate((canvas or {}).get('lines') or []):
        slots = ln.get('slots') or []
        texts = [(s.get('text') or '').strip() for s in slots]
        if not texts or not all(texts):
            # also flag over-length even if incomplete
            got = line_zh_char_count(ln)
            if got > cpl:
                bad.append(f'L{i}:{got}>{cpl}')
            continue
        got = line_zh_char_count(ln)
        if got != cpl:
            bad.append(f'L{i}:{got}!={cpl}')
    return (len(bad) == 0), bad


def skeleton_for_form(form, lang=None):
    """Deterministic empty skeleton matching the form (fallback / repair)."""
    from canvas import normalize_pos

    en = (lang or 'zh').startswith('en')
    fid = (form or {}).get('id') or 'free'
    n = (form or {}).get('lines')
    if n is None:
        n = 4
    cpl = (form or {}).get('chars_per_line')

    if cpl and not en:
        # One slot = one character for 五/七言 so totals cannot drift to 6
        if cpl == 5:
            patterns = [
                ['N', 'V', 'N', 'V', 'N'],
                ['A', 'N', 'V', 'A', 'N'],
                ['N', 'N', 'V', 'N', 'N'],
                ['ADV', 'V', 'N', 'V', 'N'],
                ['N', 'V', 'A', 'N', 'N'],
                ['PRON', 'V', 'N', 'P', 'N'],
                ['N', 'ADV', 'V', 'N', 'N'],
                ['A', 'N', 'V', 'N', 'V'],
            ]
        else:
            patterns = [
                ['N', 'V', 'N', 'V', 'N', 'V', 'N'],
                ['A', 'N', 'V', 'A', 'N', 'V', 'N'],
                ['N', 'N', 'V', 'N', 'N', 'V', 'N'],
                ['ADV', 'V', 'N', 'V', 'N', 'V', 'N'],
            ]
    elif en:
        patterns = [
            ['DET', 'A', 'N', 'V', 'DET', 'N', 'P', 'DET', 'N'],
            ['PRON', 'V', 'DET', 'N', 'P', 'DET', 'A', 'N'],
            ['ADV', 'DET', 'N', 'V', 'P', 'DET', 'N'],
            ['DET', 'N', 'V', 'A', 'P', 'DET', 'N'],
            ['DET', 'N', 'P', 'DET', 'N', 'V', 'ADV'],
            ['CONJ', 'DET', 'N', 'V', 'DET', 'N'],
        ]
    else:
        patterns = [
            ['N', 'V', 'A', 'N'],
            ['ADV', 'V', 'N', 'PART'],
            ['N', 'V', 'P', 'N'],
            ['PRON', 'V', 'N'],
            ['N', 'ADV', 'V', 'N'],
            ['A', 'N', 'V', 'N'],
        ]

    lines = []
    for i in range(n):
        pat = patterns[i % len(patterns)]
        lines.append({
            'slots': [
                {
                    'id': f'L{i}S{j}',
                    'pos': normalize_pos(p),
                    'text': '',
                    'status': 'empty',
                }
                for j, p in enumerate(pat)
            ]
        })
    out = {
        'lines': lines,
        'ops_applied': [],
        'version': 1,
        'verse_form': fid,
        'form_lock': bool((form or {}).get('lines')),
    }
    if cpl:
        out['chars_per_line'] = int(cpl)
    return out
