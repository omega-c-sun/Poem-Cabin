"""PoemCanvas: slot-based poem draft with applyable ops."""
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy


POS_ALIASES = {
    'NOUN': 'N', 'N.': 'N',
    'VERB': 'V', 'V.': 'V',
    'ADJ': 'A', 'ADJECTIVE': 'A', 'ADJECTIVAL': 'A',
    'ADVERB': 'ADV', 'ADV.': 'ADV',
    'PREP': 'P', 'PREPOSITION': 'P', 'ADP': 'P',
    'CONJUNCTION': 'CONJ', 'CC': 'CONJ', 'CCONJ': 'CONJ', 'SCONJ': 'CONJ',
    'PRONOUN': 'PRON', 'PRO': 'PRON',
    'PARTICLE': 'PART', 'PRT': 'PART',
    'NUMBER': 'NUM', 'NUMERAL': 'NUM', 'CARD': 'NUM',
    'DETERMINER': 'DET', 'ARTICLE': 'DET', 'ART': 'DET', 'DT': 'DET',
    'OTHER': 'X', 'UNK': 'X', 'UNKNOWN': 'X',
}

POS_LABELS_ZH = {
    'N': '名', 'V': '动', 'A': '形', 'ADV': '副', 'P': '介',
    'PART': '助', 'NUM': '数', 'PRON': '代', 'CONJ': '连', 'DET': '限', 'X': '其它',
}

POS_LABELS_EN = {
    'N': 'N', 'V': 'V', 'A': 'ADJ', 'ADV': 'ADV', 'P': 'PREP',
    'PART': 'PART', 'NUM': 'NUM', 'PRON': 'PRON', 'CONJ': 'CONJ', 'DET': 'DET', 'X': 'OTHER',
}

# backward-compat default (Chinese)
POS_LABELS = POS_LABELS_ZH


def normalize_pos(pos):
    raw = str(pos or 'X').strip().upper()
    if not raw:
        return 'X'
    return POS_ALIASES.get(raw, raw)


def pos_label(pos, lang=None):
    code = normalize_pos(pos)
    en = (lang or 'zh').startswith('en')
    table = POS_LABELS_EN if en else POS_LABELS_ZH
    return table.get(code, code)


def empty_canvas():
    return {'lines': [], 'ops_applied': [], 'version': 1}


def new_op_id():
    return uuid.uuid4().hex[:12]


def canvas_to_text(canvas, lang=None):
    if not canvas:
        return ''
    en = _canvas_is_en(canvas, lang)
    sep = ' ' if en else ''
    lines_out = []
    for line in canvas.get('lines') or []:
        parts = []
        for slot in line.get('slots') or []:
            t = (slot.get('text') or '').strip()
            if t:
                parts.append(t)
            else:
                parts.append(f'〔{pos_label(slot.get("pos"), lang=lang)}〕')
        lines_out.append(sep.join(parts) if parts else '')
    return '\n'.join(lines_out).strip()


def canvas_filled_text(canvas, lang=None):
    """Only filled slots; empty slots become □."""
    if not canvas:
        return ''
    en = _canvas_is_en(canvas, lang)
    sep = ' ' if en else ''
    lines_out = []
    for line in canvas.get('lines') or []:
        parts = []
        for slot in line.get('slots') or []:
            t = (slot.get('text') or '').strip()
            parts.append(t if t else '□')
        lines_out.append(sep.join(parts))
    return '\n'.join(lines_out).strip()


def canvas_compact_empties(canvas):
    """Drop empty slots and blank lines so □ cannot leak into the poem.

    Regulated Chinese meter seats must never be dropped.
    """
    from copy import deepcopy
    cv = deepcopy(canvas or empty_canvas())
    if _regulated_meter(cv):
        return ensure_regulated_width(normalize_canvas(cv))
    new_lines = []
    for li, line in enumerate(cv.get('lines') or []):
        slots = []
        for s in line.get('slots') or []:
            t = (s.get('text') or '').strip()
            if not t:
                continue
            slots.append({**s, 'text': t, 'status': 'filled'})
        if not slots:
            continue
        # re-id for cleanliness
        for j, s in enumerate(slots):
            s['id'] = f'L{len(new_lines)}S{j}'
        new_lines.append({'slots': slots})
    cv['lines'] = new_lines
    return cv


_REGULATED_FORM_IDS = frozenset({
    'wuyan_lushi', 'qiyan_lushi', 'wuyan_jueju', 'qiyan_jueju',
})


def _regulated_meter(canvas):
    cv = canvas or {}
    if cv.get('chars_per_line'):
        return True
    return (cv.get('verse_form') or '') in _REGULATED_FORM_IDS


def ensure_regulated_width(canvas):
    """Pad / trim each line to chars_per_line one-char seats (never collapse to 1 slot)."""
    from copy import deepcopy
    cv = deepcopy(canvas or empty_canvas())
    cpl = cv.get('chars_per_line')
    fid = cv.get('verse_form') or ''
    if not cpl and fid in _REGULATED_FORM_IDS:
        cpl = 5 if 'wuyan' in fid else 7
        cv['chars_per_line'] = int(cpl)
    if not cpl:
        return normalize_canvas(cv)
    cpl = int(cpl)
    patterns_5 = ['N', 'V', 'N', 'V', 'N']
    patterns_7 = ['N', 'V', 'N', 'V', 'N', 'V', 'N']
    pat0 = patterns_5 if cpl == 5 else patterns_7
    # Target line count from form id when known
    target_lines = None
    if fid == 'wuyan_lushi' or fid == 'qiyan_lushi':
        target_lines = 8
    elif fid in ('wuyan_jueju', 'qiyan_jueju'):
        target_lines = 4
    lines_in = list(cv.get('lines') or [])
    if target_lines and len(lines_in) < target_lines:
        while len(lines_in) < target_lines:
            lines_in.append({'slots': []})
    new_lines = []
    for li, line in enumerate(lines_in):
        old = line.get('slots') or []
        # Keep filled chars in order (one char each for regulated)
        chars = []
        for s in old:
            t = (s.get('text') or '').strip()
            if not t:
                continue
            for ch in re.findall(r'[\u4e00-\u9fff]', t) or ([t] if t else []):
                chars.append((ch, normalize_pos(s.get('pos') or 'N')))
                if len(chars) >= cpl:
                    break
            if len(chars) >= cpl:
                break
        slots = []
        for j in range(cpl):
            if j < len(chars):
                ch, pos = chars[j]
                slots.append({
                    'id': f'L{li}S{j}',
                    'pos': pos or normalize_pos(pat0[j % len(pat0)]),
                    'text': ch,
                    'status': 'filled',
                })
            else:
                slots.append({
                    'id': f'L{li}S{j}',
                    'pos': normalize_pos(pat0[j % len(pat0)]),
                    'text': '',
                    'status': 'empty',
                })
        new_lines.append({'slots': _ensure_line_has_v(slots)})
    cv['lines'] = new_lines
    return normalize_canvas(cv)


def canvas_compact_empties_soft(canvas):
    """Drop empty slots inside non-empty lines; keep line count when form_lock / seeded.

    Regulated Chinese (chars_per_line / 五七言): NEVER drop empty slots — they are meter seats.
    """
    from copy import deepcopy
    cv = deepcopy(canvas or empty_canvas())
    if _regulated_meter(cv):
        return ensure_regulated_width(cv)
    keep_empty_lines = bool(cv.get('form_lock') or cv.get('seeded_from_example'))
    new_lines = []
    for line in (cv.get('lines') or []):
        filled = []
        for s in line.get('slots') or []:
            t = (s.get('text') or '').strip()
            if t:
                filled.append({**s, 'text': t, 'status': 'filled'})
        if filled:
            for j, s in enumerate(filled):
                s['id'] = f'L{len(new_lines)}S{j}'
            new_lines.append({'slots': filled})
        elif keep_empty_lines:
            # Preserve breath / form line count with a single empty placeholder
            new_lines.append({
                'slots': [{'id': f'L{len(new_lines)}S0', 'pos': 'V', 'text': '', 'status': 'empty'}]
            })
    cv['lines'] = new_lines
    return cv


def normalize_canvas(canvas):
    """Sync status↔text so UI never shows empty DET while status says filled (or vice versa)."""
    cv = deepcopy(canvas or empty_canvas())
    # Multi-char POS tags sometimes dumped into text by the model — never real verse words
    pos_as_text_ban = frozenset({
        'DET', 'PREP', 'ADV', 'NOUN', 'VERB', 'ADJ', 'CONJ', 'PRON', 'PART', 'NUM',
        'OTHER', 'UNK', 'UNKNOWN', 'ADP', '限', '名', '动', '形', '副', '介', '助', '数', '代', '连', '其它',
    })
    for li, line in enumerate(cv.get('lines') or []):
        slots = line.get('slots') or []
        for si, s in enumerate(slots):
            t = (s.get('text') or '').strip()
            if t.upper() in pos_as_text_ban or t in pos_as_text_ban:
                t = ''
            s['text'] = t
            s['status'] = 'filled' if t else 'empty'
            s['pos'] = normalize_pos(s.get('pos') or 'X')
            s['id'] = s.get('id') or f'L{li}S{si}'
    return cv


_EN_DET = frozenset('a an the'.split())
_EN_P = frozenset(
    'of in on at to for from with by as into onto over under through between among '
    'without within upon against beside besides beyond during except '
    'around across after before along amid amidst'.split()
)
_EN_CONJ = frozenset('and or but yet so nor if when while though although because since unless until'.split())
_EN_PRON = frozenset(
    'i you he she it we they me him her us them my your his its our their mine yours '
    'hers ours theirs this that these those who whom whose which what'.split()
)
_EN_ADV = frozenset(
    'not never still now then often always already also even once again here there '
    'away back forth up down out off too very quite rather almost only just'.split()
)
_ZH_PART = frozenset('的了着过吗呢吧啊呀嘛')
_ZH_P = frozenset('在向从自于对把被给和与跟同比沿顺')


def _guess_pos_en(word, prev_pos=None):
    w = (word or '').strip()
    low = w.lower()
    if low in _EN_DET:
        return 'DET'
    if low in _EN_P:
        return 'P'
    if low in _EN_CONJ:
        return 'CONJ'
    if low in _EN_PRON:
        return 'PRON'
    if low in _EN_ADV or (low.endswith('ly') and len(low) > 3):
        return 'ADV'
    if low.endswith(('ing', 'ed')) and len(low) > 4:
        return 'V'
    if prev_pos in ('DET', 'A', 'P') and low not in _EN_DET:
        # DET/A/P + word → often noun
        if prev_pos == 'DET' and low.endswith('ly'):
            return 'ADV'
        return 'N'
    if prev_pos in ('N', 'PRON', 'ADV') or prev_pos is None:
        # subject-ish then verb; otherwise noun default with later V ensure
        if prev_pos in ('N', 'PRON'):
            return 'V'
    return 'N'


def _guess_pos_zh(ch, prev_pos=None):
    c = (ch or '').strip()
    if not c:
        return 'X'
    if c in _ZH_PART:
        return 'PART'
    if c in _ZH_P:
        return 'P'
    # crude: after noun-ish prefer verb, else noun
    if prev_pos in ('N', 'A', 'PRON', None):
        return 'V' if prev_pos in ('N', 'A', 'PRON') else 'N'
    if prev_pos == 'V':
        return 'N'
    return 'N'


def _ensure_line_has_v(slots):
    if any(normalize_pos(s.get('pos')) == 'V' for s in slots):
        return slots
    # Flip a mid content slot to V
    for i, s in enumerate(slots):
        code = normalize_pos(s.get('pos'))
        if code in ('N', 'A', 'X') and (s.get('text') or '').strip():
            s['pos'] = 'V'
            return slots
    if slots:
        mid = len(slots) // 2
        slots[mid]['pos'] = 'V'
    return slots


def seed_canvas_from_poem(poem_text, form=None, lang=None):
    """
    Build a pre-filled canvas from a selected example poem.
    Aligns line count / chars_per_line when a fixed form is given.
    Marks seeded_from_example=True for downstream light-revise / soft-compact.
    """
    import verse_form as _vf

    sample_raw = (poem_text or '').strip()
    if not sample_raw:
        return None

    en = (lang or 'zh').startswith('en')
    if not en:
        # Detect English poem even under zh UI
        if re.search(r'[A-Za-z]', sample_raw) and not re.search(r'[\u4e00-\u9fff]', sample_raw):
            en = True

    # Classical Chinese cards often store 4 lines as one string with ，。
    if en:
        raw_lines = [ln.strip() for ln in sample_raw.splitlines() if ln.strip()]
    else:
        raw_lines = _vf.split_zh_verse_lines(sample_raw)
        if not raw_lines:
            raw_lines = [ln.strip() for ln in sample_raw.splitlines() if ln.strip()]
    if not raw_lines:
        return None

    target_n = None
    cpl = None
    fid = None
    if form:
        if form.get('lines') is not None:
            target_n = int(form['lines'])
        cpl = form.get('chars_per_line')
        fid = form.get('id')

    # If form missing but poem is clearly 4×7 / 8×5, adopt that form
    if not cpl or not target_n:
        inferred = _vf.infer_form_from_poem(sample_raw)
        if inferred:
            if not form or (form.get('id') in (None, 'free', 'quatrain', 'couplet')):
                form = inferred
                target_n = int(inferred.get('lines') or target_n or 0) or None
                cpl = inferred.get('chars_per_line') or cpl
                fid = inferred.get('id') or fid

    lines = list(raw_lines)
    if target_n:
        if len(lines) > target_n:
            lines = lines[:target_n]
        while len(lines) < target_n:
            lines.append('')

    canvas = empty_canvas()
    canvas['seeded_from_example'] = True
    if target_n:
        canvas['form_lock'] = True
    if fid:
        canvas['verse_form'] = fid
    if cpl:
        canvas['chars_per_line'] = int(cpl)

    # Regulated Chinese: one slot = one character
    if cpl and not en:
        patterns_5 = [
            ['N', 'V', 'N', 'V', 'N'],
            ['A', 'N', 'V', 'A', 'N'],
            ['N', 'N', 'V', 'N', 'N'],
            ['ADV', 'V', 'N', 'V', 'N'],
        ]
        patterns_7 = [
            ['N', 'V', 'N', 'V', 'N', 'V', 'N'],
            ['A', 'N', 'V', 'A', 'N', 'V', 'N'],
            ['N', 'N', 'V', 'N', 'N', 'V', 'N'],
            ['ADV', 'V', 'N', 'V', 'N', 'V', 'N'],
        ]
        patterns = patterns_5 if int(cpl) == 5 else patterns_7
        # Regulated Chinese: one slot = one character — keep card text; only collapse AABB/叠字
        for li, ln in enumerate(lines):
            zh = re.findall(r'[\u4e00-\u9fff]', ln)
            # Collapse immediate 叠字/叠词 before slotting (挑尽挑尽→挑尽, 窗窗→窗)
            collapsed = []
            i = 0
            while i < len(zh):
                # 2-char phrase repeat
                if i + 3 < len(zh) and zh[i] == zh[i + 2] and zh[i + 1] == zh[i + 3]:
                    collapsed.extend([zh[i], zh[i + 1]])
                    i += 4
                    continue
                # single-char redup
                if i + 1 < len(zh) and zh[i] == zh[i + 1]:
                    collapsed.append(zh[i])
                    i += 2
                    continue
                collapsed.append(zh[i])
                i += 1
            zh = collapsed
            if len(zh) > int(cpl):
                zh = zh[:int(cpl)]
            while len(zh) < int(cpl):
                zh.append('')
            pat = patterns[li % len(patterns)]
            slots = []
            prev_ch = ''
            for j in range(int(cpl)):
                ch = zh[j] if j < len(zh) else ''
                # Only strip immediate adjacent 叠字 on this line — do NOT blank
                # poem-wide unique chars (律诗样例卡必须整句保留，否则后面被压成一字行)
                if ch and ch == prev_ch and ch not in _FUNCTION_ZH_DUP:
                    ch = ''
                if ch:
                    prev_ch = ch
                else:
                    prev_ch = ''
                pos = pat[j] if j < len(pat) else 'N'
                slots.append({
                    'id': f'L{li}S{j}',
                    'pos': normalize_pos(pos),
                    'text': ch,
                    'status': 'filled' if ch else 'empty',
                })
            canvas['lines'].append({'slots': _ensure_line_has_v(slots)})
        return canvas

    if en:
        for li, ln in enumerate(lines):
            words = re.findall(r"[A-Za-z']+|[0-9]+", ln)
            if not words and not ln:
                canvas['lines'].append({
                    'slots': [
                        {'id': f'L{li}S0', 'pos': 'DET', 'text': '', 'status': 'empty'},
                        {'id': f'L{li}S1', 'pos': 'N', 'text': '', 'status': 'empty'},
                        {'id': f'L{li}S2', 'pos': 'V', 'text': '', 'status': 'empty'},
                    ]
                })
                continue
            if not words:
                words = re.findall(r'\S+', ln) or ['…']
            slots = []
            prev = None
            for j, w in enumerate(words[:14]):
                pos = _guess_pos_en(w, prev)
                # fill_text_ok: max 2 words for N/V/A; keep single word
                slots.append({
                    'id': f'L{li}S{j}',
                    'pos': normalize_pos(pos),
                    'text': w,
                    'status': 'filled',
                })
                prev = pos
            canvas['lines'].append({'slots': _ensure_line_has_v(slots)})
        return canvas

    # Chinese free verse: char/word chunks with POS hints
    for li, ln in enumerate(lines):
        chars = re.findall(r'[\u4e00-\u9fff]+|[A-Za-z]+|\S', ln)
        if not chars:
            canvas['lines'].append({
                'slots': [
                    {'id': f'L{li}S0', 'pos': 'N', 'text': '', 'status': 'empty'},
                    {'id': f'L{li}S1', 'pos': 'V', 'text': '', 'status': 'empty'},
                    {'id': f'L{li}S2', 'pos': 'N', 'text': '', 'status': 'empty'},
                ]
            })
            continue
        # Prefer finer slots: 1–2 chars for CJK runs
        pieces = []
        for tok in chars:
            if re.fullmatch(r'[\u4e00-\u9fff]+', tok) and len(tok) > 2:
                i = 0
                while i < len(tok):
                    step = 2 if i + 1 < len(tok) else 1
                    pieces.append(tok[i:i + step])
                    i += step
            else:
                pieces.append(tok)
        pieces = pieces[:8] or ['□']
        slots = []
        prev = None
        for j, piece in enumerate(pieces):
            if piece == '□':
                slots.append({
                    'id': f'L{li}S{j}', 'pos': 'N', 'text': '', 'status': 'empty'})
                continue
            if re.fullmatch(r'[\u4e00-\u9fff]+', piece):
                if len(piece) == 1:
                    pos = _guess_pos_zh(piece, prev)
                else:
                    pos = 'N' if prev == 'V' else 'V' if prev in ('N', 'A', None) else 'N'
            elif re.fullmatch(r'[A-Za-z]+', piece):
                pos = _guess_pos_en(piece, prev)
            else:
                pos = 'X'
            slots.append({
                'id': f'L{li}S{j}',
                'pos': normalize_pos(pos),
                'text': piece,
                'status': 'filled',
            })
            prev = pos
        canvas['lines'].append({'slots': _ensure_line_has_v(slots)})
    return canvas


def canvas_readable_text(canvas, lang=None):
    """Join only filled slots (no □). Prefer after canvas_compact_empties."""
    if not canvas:
        return ''
    en = _canvas_is_en(canvas, lang)
    sep = ' ' if en else ''
    lines_out = []
    for line in canvas.get('lines') or []:
        parts = [(s.get('text') or '').strip() for s in line.get('slots') or []]
        parts = [p for p in parts if p]
        if parts:
            lines_out.append(sep.join(parts))
    return '\n'.join(lines_out).strip()


def _canvas_is_en(canvas, lang=None):
    if lang and str(lang).startswith('en'):
        return True
    if lang and str(lang).startswith('zh'):
        return False
    blob = []
    for line in (canvas or {}).get('lines') or []:
        for s in line.get('slots') or []:
            blob.append(s.get('text') or '')
            blob.append(s.get('pos') or '')
    text = ' '.join(blob)
    return len(re.findall(r'[A-Za-z]', text)) >= max(2, len(re.findall(r'[\u4e00-\u9fff]', text)))


_FUNCTION_EN_DUP = {
    'the', 'a', 'an', 'of', 'to', 'in', 'on', 'at', 'for', 'from', 'with', 'by',
    'and', 'or', 'but', 'as', 'is', 'are', 'was', 'were', 'be',
}

_FUNCTION_ZH_DUP = {
    '的', '了', '着', '过', '在', '是', '和', '与', '或', '也', '都', '就', '才',
    '又', '而', '但', '却', '被', '把', '让', '给', '从', '向', '对', '于',
    '之', '其', '这', '那', '有', '无', '不', '没', '吗', '呢', '吧', '啊',
}


def _content_key(text):
    """Normalize a slot text to a content key for poem-wide dedupe."""
    t = (text or '').strip()
    if not t:
        return None
    en = re.findall(r"[A-Za-z']+", t.lower())
    if en:
        words = [w for w in en if w not in _FUNCTION_EN_DUP and len(w) > 1]
        return words[0] if words else None
    zh = re.findall(r'[\u4e00-\u9fff]+', t)
    if not zh:
        return None
    tok = zh[0]
    if len(tok) == 1 and tok in _FUNCTION_ZH_DUP:
        return None
    return tok


def fill_text_ok(text, pos=None, lang=None):
    """
    One slot = one lexical unit matching POS — reject clause dumps and internal dups.
    Returns (ok, reason).
    """
    t = (text or '').strip()
    if not t:
        return False, 'empty'
    # Clause / list dumps into a single slot
    if re.search(r'[,;:，；：。.!?…]', t):
        return False, 'clause_dump'
    if re.search(r'\bnot\b.{0,20}\bbut\b', t, re.I):
        return False, 'clause_dump'
    if '...' in t or '…' in t:
        return False, 'clause_dump'
    # Internal glued duplicate: 绕出绕出 / 窗窗 / presses presses
    if re.search(r'([\u4e00-\u9fff])\1', t):
        return False, 'slot_internal_dup'
    if re.search(r'([\u4e00-\u9fff]{2,6})\1', t):
        return False, 'slot_internal_dup'
    en_words = re.findall(r"[A-Za-z']+", t)
    if len(en_words) >= 2:
        lows = [w.lower() for w in en_words]
        for a, b in zip(lows, lows[1:]):
            if a == b and a not in _FUNCTION_EN_DUP:
                return False, 'slot_internal_dup'

    zh_chars = re.findall(r'[\u4e00-\u9fff]', t)
    if len(en_words) > 1 and re.search(r'\bthen\b', t, re.I):
        return False, 'clause_dump'
    code = normalize_pos(pos)
    en = (lang or '').startswith('en') or (len(en_words) >= max(1, len(zh_chars) // 2) and en_words)

    if en and en_words:
        max_w = 1
        if code in ('N', 'V', 'A', 'ADV', 'NUM'):
            max_w = 2  # allow "sleeping nerve" / "drinks in"
        if code in ('DET', 'P', 'CONJ', 'PART', 'PRON'):
            max_w = 1
        if len(en_words) > max_w:
            return False, 'too_many_words'
        # DET slot must be determiner-like
        if code == 'DET' and en_words[0].lower() not in (
                'the', 'a', 'an', 'this', 'that', 'these', 'those', 'my', 'your', 'our', 'its', 'his', 'her', 'their', 'no', 'each', 'every', 'some', 'any'):
            return False, 'det_mismatch'
    elif zh_chars:
        # Regulated 五/七言: prefer 1 char per slot (canvas may set max_slot_chars)
        max_c = 4 if code in ('N', 'V', 'A', 'ADV') else 2
        if lang and str(lang).startswith('zh-reg'):
            max_c = 1
        if len(zh_chars) > max_c:
            return False, 'too_many_chars'
    return True, 'ok'


_HANG_EN = {
    'the', 'a', 'an', 'of', 'to', 'in', 'on', 'at', 'for', 'from', 'with', 'by',
    'and', 'or', 'but', 'into', 'onto', 'upon', 'as', 'than',
}


def line_incomplete(line, lang=None):
    """True if a filled line hangs on a determiner/preposition."""
    slots = (line or {}).get('slots') or []
    texts = [(s.get('text') or '').strip() for s in slots]
    filled = [t for t in texts if t]
    if not filled:
        return False
    # any empty in the middle while later filled? messy
    if any(not t for t in texts) and any(texts):
        # trailing empties ok only if we consider incomplete when last filled is hang word
        pass
    last = filled[-1]
    en_words = re.findall(r"[A-Za-z']+", last)
    if en_words:
        return en_words[-1].lower() in _HANG_EN
    # Chinese: ending on 的/了/着/与/和 alone-ish
    if re.search(r'[的了着与和及被把向在]$', last) and len(re.findall(r'[\u4e00-\u9fff]', last)) <= 1:
        return True
    return False


def canvas_structure_issues(canvas, form=None, lang=None):
    """List structural problems for prompts / gates."""
    issues = []
    lines = (canvas or {}).get('lines') or []
    n = len(lines)
    if form:
        ok, got, expect = True, n, 'any'
        try:
            import verse_form
            ok, got, expect = verse_form.line_count_ok(canvas, form)
        except Exception:
            pass
        if not ok:
            issues.append(f'line_count:{got}!={expect}')
        try:
            import verse_form
            cok, cbad = verse_form.line_chars_ok(canvas, form)
            if not cok:
                issues.append('chars:' + ','.join(cbad[:6]))
        except Exception:
            pass

    # duplicate full lines
    seen = {}
    for i, ln in enumerate(lines):
        key = ' '.join((s.get('text') or '').strip().lower() for s in (ln.get('slots') or []) if (s.get('text') or '').strip())
        if key and len(key) > 8:
            if key in seen:
                issues.append(f'dup_line:L{seen[key]}~L{i}')
            else:
                seen[key] = i
        if line_incomplete(ln, lang=lang):
            issues.append(f'hang_line:L{i}')
        # phrase-stuffed slots
        for s in ln.get('slots') or []:
            t = (s.get('text') or '').strip()
            if not t:
                continue
            ok, reason = fill_text_ok(t, s.get('pos'), lang=lang)
            if not ok:
                issues.append(f'bad_slot:{s.get("id")}:{reason}')
                break
    return issues[:20]


def canvas_complete_lines_text(canvas, lang=None):
    """Join only fully filled lines (for global 成句 checks)."""
    if not canvas:
        return ''
    en = (lang or 'zh').startswith('en')
    sep = ' ' if en else ''
    out = []
    for line in canvas.get('lines') or []:
        slots = line.get('slots') or []
        if not slots:
            continue
        texts = [(s.get('text') or '').strip() for s in slots]
        if texts and all(texts):
            out.append(sep.join(texts) if en else ''.join(texts))
    return '\n'.join(out).strip()


def line_pos_ok(positions):
    """
    Whether a POS sequence can host a grammatical clause (not noun salad).
    Returns (ok: bool, reason: str).
    """
    pos = [normalize_pos(p) for p in (positions or [])]
    if len(pos) < 2:
        return False, 'too_short'
    if 'V' not in pos:
        return False, 'no_verb'
    # Bare noun/adj stacks with a lone verb still feel like keyword frames
    # e.g. N-N-N-V-N-N — allow N-V-A-N / DET-N-V-N
    nouns = sum(1 for p in pos if p == 'N')
    glue_extra = sum(1 for p in pos if p in ('P', 'DET', 'CONJ', 'ADV', 'PART', 'PRON'))
    if nouns >= 4 and glue_extra == 0 and len(pos) >= 5:
        return False, 'noun_pile'
    # Adjacent identical content POS too many (N N N)
    run = 1
    max_run = 1
    for a, b in zip(pos, pos[1:]):
        if a == b and a in ('N', 'A'):
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1
    if max_run >= 3:
        return False, 'pos_run'
    return True, 'ok'


def skeleton_logic_report(canvas):
    """Score skeleton POS patterns for clause-readiness. Returns dict."""
    lines = canvas.get('lines') or []
    if not lines:
        return {'ok_ratio': 0.0, 'score': 15.0, 'bad': ['empty'], 'n': 0}
    bad = []
    ok_n = 0
    for i, ln in enumerate(lines):
        pos = [s.get('pos') for s in (ln.get('slots') or [])]
        ok, reason = line_pos_ok(pos)
        if ok:
            ok_n += 1
        else:
            bad.append(f'L{i}:{reason}')
    ratio = ok_n / max(1, len(lines))
    score = round(20 + ratio * 75, 1)
    # Bonus if most lines have V + at least one glue/DET/P
    return {
        'ok_ratio': round(ratio, 3),
        'score': score,
        'bad': bad[:8],
        'n': len(lines),
        'ok_n': ok_n,
    }


def structure_score(canvas, prefs=None):
    """Heuristic for skeleton gate (not literary radar). 0–100 axes-ish dict."""
    lines = canvas.get('lines') or []
    n_lines = len(lines)
    n_slots = sum(len(ln.get('slots') or []) for ln in lines)
    filled = sum(
        1 for ln in lines for s in (ln.get('slots') or [])
        if (s.get('text') or '').strip()
    )
    fill_ratio = filled / max(1, n_slots)

    # Prefer 3–12 lines for free verse; form-aware callers may override via verse_form
    form = (prefs or {}).get('_verse_form') if isinstance(prefs, dict) else None
    if form and form.get('lines') is not None:
        exact = int(form['lines'])
        line_score = 92 if n_lines == exact else (35 if abs(n_lines - exact) <= 2 else 20)
    else:
        line_score = 70
        if 3 <= n_lines <= 12:
            line_score = 88
        elif n_lines < 2 or n_lines > 16:
            line_score = 40

    dens = n_slots / max(1, n_lines)
    # English sonnets need denser lines
    if form and 'sonnet' in (form.get('id') or ''):
        dens_score = 85 if 6 <= dens <= 14 else (55 if dens < 4 else 60)
    else:
        dens_score = 80 if 2 <= dens <= 6 else (55 if dens < 2 else 50)

    pos_set = set()
    for ln in lines:
        for s in ln.get('slots') or []:
            pos_set.add(normalize_pos(s.get('pos') or 'X'))
    variety = min(100, 40 + len(pos_set) * 12)

    logic = skeleton_logic_report(canvas)
    logic_score = logic['score']

    overall = round(
        0.25 * line_score + 0.20 * dens_score + 0.20 * variety + 0.35 * logic_score,
        1,
    )
    return {
        'rhyme': 50.0,
        'rhythm': float(line_score),
        'tension': 45.0 + (10 if 'V' in pos_set else 0),
        'paradox': 40.0,
        'metaphor': float(variety) * 0.7,
        'freshness': 60.0 + (10 if n_lines >= 4 else 0),
        'depth': 55.0 + (8 if 'N' in pos_set and 'V' in pos_set else 0),
        'coherence': float(logic_score),
        'fit': 55.0,
        'overall': overall,
        'structure_lines': n_lines,
        'structure_slots': n_slots,
        'fill_ratio': round(fill_ratio, 3),
        'skeleton_ok_ratio': logic['ok_ratio'],
        '_kind': 'structure',
    }


def find_slot(canvas, slot_id):
    for li, line in enumerate(canvas.get('lines') or []):
        for si, slot in enumerate(line.get('slots') or []):
            if slot.get('id') == slot_id:
                return li, si, slot
    return None


def apply_op(canvas, op):
    """Apply one op; returns (new_canvas, ok, message). Idempotent on op_id."""
    canvas = deepcopy(canvas or empty_canvas())
    op = op or {}
    op_id = op.get('op_id') or new_op_id()
    applied = canvas.setdefault('ops_applied', [])
    if op_id in applied:
        return canvas, True, 'duplicate'
    kind = op.get('type') or op.get('op') or ''

    if kind == 'init' or kind == 'canvas_init':
        lines = op.get('lines') or []
        normalized = []
        for li, line in enumerate(lines):
            slots_in = line.get('slots') if isinstance(line, dict) else line
            slots = []
            for si, s in enumerate(slots_in or []):
                if isinstance(s, str):
                    s = {'pos': 'X', 'text': s}
                sid = s.get('id') or f'L{li}S{si}'
                slots.append({
                    'id': sid,
                    'pos': normalize_pos(s.get('pos') or 'X'),
                    'text': s.get('text') or '',
                    'status': 'filled' if (s.get('text') or '').strip() else 'empty',
                })
            normalized.append({'slots': slots})
        canvas['lines'] = normalized
        applied.append(op_id)
        return canvas, True, 'init'

    if kind == 'fill' or kind == 'replace':
        sid = op.get('slot_id') or op.get('id')
        text = op.get('text')
        if text is None:
            text = op.get('value') or ''
        found = find_slot(canvas, sid)
        if not found:
            return canvas, False, f'unknown slot {sid}'
        li, si, slot = found
        pos = op.get('pos') or slot.get('pos')
        ok_t, reason = fill_text_ok(
            text, pos,
            lang='zh-reg' if canvas.get('chars_per_line') else None,
        )
        if not ok_t:
            return canvas, False, reason
        # Reject adjacent duplicate slot text (沉入|沉入)
        line_slots = (canvas.get('lines') or [])[li].get('slots') or []
        new_t = str(text).strip()
        code = normalize_pos(pos)
        cpl = canvas.get('chars_per_line')
        if cpl:
            # Project line length after this fill
            total = 0
            for j, s in enumerate(line_slots):
                piece = new_t if j == si else (s.get('text') or '')
                total += len(re.findall(r'[\u4e00-\u9fff]', piece))
            if total > int(cpl):
                return canvas, False, f'line_chars>{cpl}'
        for nbr in (si - 1, si + 1):
            if 0 <= nbr < len(line_slots):
                other = (line_slots[nbr].get('text') or '').strip()
                if other and other == new_t:
                    return canvas, False, 'adjacent_dup'
        # Same content word already on this line (function-word slots exempt)
        new_key = _content_key(new_t)
        if new_key and code not in ('DET', 'P', 'CONJ', 'PART', 'PRON'):
            for j, s in enumerate(line_slots):
                if j == si:
                    continue
                other_key = _content_key(s.get('text') or '')
                if other_key and other_key == new_key:
                    return canvas, False, 'line_dup'
        # Poem-wide: each content word at most once (determiners/preps exempt)
        if new_key and code not in ('DET', 'P', 'CONJ', 'PART', 'PRON'):
            for oli, line in enumerate(canvas.get('lines') or []):
                for oj, s in enumerate(line.get('slots') or []):
                    if oli == li and oj == si:
                        continue
                    other_key = _content_key(s.get('text') or '')
                    if other_key and other_key == new_key:
                        return canvas, False, 'poem_dup'
        slot['text'] = new_t
        slot['status'] = 'filled' if new_t else 'empty'
        if op.get('pos'):
            slot['pos'] = normalize_pos(op['pos'])
        applied.append(op_id)
        return canvas, True, kind

    if kind == 'clear':
        sid = op.get('slot_id') or op.get('id')
        found = find_slot(canvas, sid)
        if not found:
            return canvas, False, f'unknown slot {sid}'
        _, _, slot = found
        slot['text'] = ''
        slot['status'] = 'empty'
        applied.append(op_id)
        return canvas, True, 'clear'

    if kind == 'reorder':
        order = op.get('order')  # list of line indices
        lines = canvas.get('lines') or []
        if not order or len(order) != len(lines):
            return canvas, False, 'bad reorder'
        try:
            canvas['lines'] = [lines[i] for i in order]
        except Exception:
            return canvas, False, 'bad reorder index'
        applied.append(op_id)
        return canvas, True, 'reorder'

    if kind == 'drop_line':
        if canvas.get('form_lock'):
            return canvas, False, 'form_locked'
        idx = op.get('line_index')
        lines = canvas.get('lines') or []
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(lines):
            return canvas, False, 'bad line_index'
        lines.pop(idx)
        # re-id optional; keep ids
        canvas['lines'] = lines
        applied.append(op_id)
        return canvas, True, 'drop_line'

    if kind == 'add_line':
        if canvas.get('form_lock'):
            return canvas, False, 'form_locked'
        slots_in = op.get('slots') or [{'pos': 'N'}, {'pos': 'V'}, {'pos': 'N'}]
        li = len(canvas.get('lines') or [])
        slots = []
        for si, s in enumerate(slots_in):
            if isinstance(s, str):
                s = {'pos': 'X', 'text': s}
            slots.append({
                'id': s.get('id') or f'L{li}S{si}',
                'pos': normalize_pos(s.get('pos') or 'X'),
                'text': s.get('text') or '',
                'status': 'filled' if (s.get('text') or '').strip() else 'empty',
            })
        at = op.get('at')
        if at is None or at >= li:
            canvas.setdefault('lines', []).append({'slots': slots})
        else:
            canvas['lines'].insert(int(at), {'slots': slots})
        applied.append(op_id)
        return canvas, True, 'add_line'

    if kind == 'revise_syntax':
        if canvas.get('form_lock') or _regulated_meter(canvas):
            # Locked / 格律: never rewrite whole lines (collapses 5/7 seats to 1)
            return canvas, False, 'form_locked'
        # Replace whole line slots
        idx = op.get('line_index', 0)
        lines = canvas.get('lines') or []
        if idx < 0 or idx >= len(lines):
            return canvas, False, 'bad line_index'
        slots_in = op.get('slots') or []
        slots = []
        for si, s in enumerate(slots_in):
            if isinstance(s, str):
                s = {'pos': 'X', 'text': s}
            text = str(s.get('text') or '').strip()
            pos = normalize_pos(s.get('pos') or 'X')
            # Never introduce empty DET/PREP ghosts — skip blank slots
            if not text:
                continue
            ok_t, reason = fill_text_ok(text, pos)
            if not ok_t:
                return canvas, False, f'revise_syntax:{reason}'
            slots.append({
                'id': s.get('id') or f'L{idx}S{len(slots)}',
                'pos': pos,
                'text': text,
                'status': 'filled',
            })
        if not slots:
            return canvas, False, 'revise_syntax:empty_line'
        if not any(normalize_pos(s.get('pos')) == 'V' for s in slots):
            slots[min(1, len(slots) - 1)]['pos'] = 'V'
        lines[idx] = {'slots': slots}
        applied.append(op_id)
        return canvas, True, 'revise_syntax'

    return canvas, False, f'unknown op {kind}'


def parse_ops_json(text):
    """Extract JSON ops array or object from model output."""
    if not text:
        return None
    text = text.strip()
    # fenced
    m = re.search(r'```(?:json)?\s*([\s\S]+?)```', text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if 'ops' in data:
                return data
            if 'lines' in data:
                return {'ops': [{'type': 'init', 'op_id': new_op_id(), 'lines': data['lines'],
                                 'intent': data.get('intent') or data.get('念头') or ''}]}
            if 'type' in data or 'op' in data:
                return {'ops': [data]}
        if isinstance(data, list):
            return {'ops': data}
    except Exception:
        pass
    # find first [ ... ] or { ... }
    for pat in (r'(\[[\s\S]*\])', r'(\{[\s\S]*\})'):
        m = re.search(pat, text)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return {'ops': data}
            if isinstance(data, dict):
                if 'ops' in data:
                    return data
                if 'lines' in data:
                    return {'ops': [{'type': 'init', 'op_id': new_op_id(), 'lines': data['lines'],
                                     'intent': data.get('intent', '')}]}
                return {'ops': [data]}
        except Exception:
            continue
    return None


def ensure_op_ids(ops):
    out = []
    for op in ops or []:
        o = dict(op)
        if not o.get('op_id'):
            o['op_id'] = new_op_id()
        if 'type' not in o and 'op' in o:
            o['type'] = o['op']
        out.append(o)
    return out


def skeleton_from_text(poem_text, slots_per_line=None):
    """Fallback: build empty-ish canvas from free poem lines."""
    lines = [ln.strip() for ln in (poem_text or '').splitlines() if ln.strip()]
    if not lines:
        # default 4-line skeleton
        return {
            'lines': [
                {'slots': [
                    {'id': f'L{i}S{j}', 'pos': p, 'text': '', 'status': 'empty'}
                    for j, p in enumerate(pattern)
                ]}
                for i, pattern in enumerate([
                    ['DET', 'N', 'V', 'DET', 'N'],
                    ['ADV', 'V', 'P', 'N'],
                    ['N', 'V', 'A', 'N'],
                    ['PRON', 'V', 'N', 'P', 'N'],
                ])
            ],
            'ops_applied': [],
            'version': 1,
        }
    canvas = empty_canvas()
    for li, ln in enumerate(lines):
        chars = re.findall(r'[\u4e00-\u9fff]+|[A-Za-z]+|\S', ln)
        if not chars:
            chars = ['□', '□', '□']
        # group into ~2-4 slots
        n = min(6, max(2, len(chars)))
        chunk = max(1, len(chars) // n)
        slots = []
        idx = 0
        si = 0
        while idx < len(chars) and si < 8:
            piece = ''.join(chars[idx:idx + chunk])
            idx += chunk
            slots.append({
                'id': f'L{li}S{si}',
                'pos': 'N' if si % 2 == 0 else 'V',
                'text': piece if piece != '□' else '',
                'status': 'filled' if piece and piece != '□' else 'empty',
            })
            si += 1
        canvas['lines'].append({'slots': slots})
    return canvas
