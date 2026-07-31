import json
import os
import re
import math
import uuid
import db
import llm
import evaluate
import preferences
import canvas as poem_canvas
import checkpoint as ckpt
import stage_schema
import verse_form
from prompts import (
    STAGE_TASKS, DIM_MAP, ANTI_STIFF, ANALYZE_IMITATE_COMPACT,
    SUBJECT_FIT_COMPACT, STAGE_BOUNDARY, QUALITY_PASS, DUP_HARD_BAN, ASSOCIATION,
)

STAGES = ['chat', 'examples', 'structure', 'symbols', 'verbs', 'final']
CANVAS_STAGES = {'structure', 'symbols', 'verbs', 'final'}

# Active runs: session_id -> run_token (also stored in DB)
_active_runs = {}


def stage_label(stage, lang=None):
    import i18n
    key = {
        'chat': 'stage_chat',
        'examples': 'stage_examples',
        'structure': 'stage_structure',
        'symbols': 'stage_symbols',
        'verbs': 'stage_verbs',
        'final': 'stage_final',
    }.get(stage)
    return i18n.t(key, lang=lang) if key else (stage or '')


def sigmoid_temperature(delta, t_min=0.25, t_max=0.95, k=6.0, theta=0.35):
    return t_min + (t_max - t_min) / (1 + math.exp(-k * (delta - theta)))


def touch_session(session_id):
    try:
        db.execute(
            'UPDATE poem_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = %s',
            (session_id,))
    except Exception:
        pass


def get_session(user_id, session_id=None):
    if session_id:
        row = db.fetchone(
            'SELECT * FROM poem_sessions WHERE id = %s AND user_id = %s',
            (session_id, user_id))
        if row:
            return row
    return get_or_create_session(user_id)


def default_session_title(kind='untitled', lang=None):
    import i18n
    key = 'session_new_title' if kind == 'new' else 'session_untitled'
    return i18n.t(key, lang=lang)


def is_placeholder_title(title):
    placeholders = {
        '未命名诗稿', '新的诗稿', '未命名',
        'Untitled draft', 'New draft', 'Untitled',
        '無題の草稿', '新しい草稿', '無題',
    }
    return not title or title in placeholders


def get_or_create_session(user_id, lang=None):
    row = db.fetchone(
        '''SELECT * FROM poem_sessions
           WHERE user_id = %s
           ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 1''',
        (user_id,))
    if row:
        return row
    prefs = preferences.get_preferences(user_id)
    target = ensure_seven_dims(
        {k: int(float(v) * 100) for k, v in prefs['dimension_weights'].items()})
    return db.execute(
        '''INSERT INTO poem_sessions (user_id, title, target_dimensions, stage, chat_log)
           VALUES (%s, %s, %s::jsonb, 'chat', '[]'::jsonb)
           RETURNING *''',
        (user_id, default_session_title('untitled', lang=lang), db.dumps(target)),
        returning=True)


def list_sessions(user_id, limit=40):
    return db.fetchall(
        '''SELECT id, title, stage, created_at, updated_at
           FROM poem_sessions
           WHERE user_id = %s
           ORDER BY COALESCE(updated_at, created_at) DESC
           LIMIT %s''',
        (user_id, limit))


def create_session(user_id, title=None, source_id=None, seed_text=None, lang=None):
    import i18n
    prefs = preferences.get_preferences(user_id)
    chat_log = []
    if seed_text:
        chat_log.append({
            'role': 'system',
            'content': i18n.t('session_derived_seed', lang=lang).format(seed=seed_text),
        })
    if not title:
        title = default_session_title('untitled', lang=lang)
    target = ensure_seven_dims(
        {k: int(float(v) * 100) for k, v in prefs['dimension_weights'].items()})
    return db.execute(
        '''INSERT INTO poem_sessions
           (user_id, title, target_dimensions, stage, chat_log, source_session_id, run_status)
           VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, %s, 'idle')
           RETURNING *''',
        (user_id, title, db.dumps(target),
         'examples' if seed_text else 'chat',
         db.dumps(chat_log), source_id),
        returning=True)


def switch_session(user_id, session_id):
    row = db.fetchone(
        'SELECT * FROM poem_sessions WHERE id = %s AND user_id = %s',
        (session_id, user_id))
    if not row:
        return None
    touch_session(str(row['id']))
    return row


def interrupt_session(user_id, session_id=None):
    session = get_session(user_id, session_id)
    sid = str(session['id'])
    db.execute(
        '''UPDATE poem_sessions
           SET run_token = NULL, run_status = 'aborted', checkpoint_id = NULL
           WHERE id = %s''',
        (sid,))
    _active_runs.pop(sid, None)
    return True


def begin_run(session_id):
    token = uuid.uuid4().hex
    sid = str(session_id)
    db.execute(
        '''UPDATE poem_sessions
           SET run_token = %s, run_status = 'running', updated_at = CURRENT_TIMESTAMP
           WHERE id = %s''',
        (token, sid))
    _active_runs[sid] = token
    return token


def run_still_valid(session_id, token):
    sid = str(session_id)
    if _active_runs.get(sid) != token:
        return False
    row = db.fetchone('SELECT run_token, run_status FROM poem_sessions WHERE id = %s', (sid,))
    if not row:
        return False
    return row.get('run_token') == token and row.get('run_status') == 'running'


def set_awaiting(session_id, checkpoint_id, token):
    db.execute(
        '''UPDATE poem_sessions
           SET run_status = 'awaiting', checkpoint_id = %s, run_token = %s,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = %s''',
        (checkpoint_id, token, str(session_id)))
    _active_runs[str(session_id)] = token


def set_idle(session_id):
    sid = str(session_id)
    db.execute(
        '''UPDATE poem_sessions
           SET run_status = 'idle', run_token = NULL, checkpoint_id = NULL,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = %s''',
        (sid,))
    _active_runs.pop(sid, None)


def load_canvas(session):
    """Always prefer fresh canvas_json from DB to avoid stale in-memory session dicts."""
    sid = None
    if isinstance(session, dict):
        sid = session.get('id')
    elif session:
        sid = session
    raw = None
    if sid:
        try:
            row = db.fetchone('SELECT canvas_json FROM poem_sessions WHERE id = %s', (str(sid),))
            if row is not None:
                raw = row.get('canvas_json')
        except Exception:
            raw = None
    if raw is None and isinstance(session, dict):
        raw = session.get('canvas_json')
    data = db.loads(raw, {}) if raw is not None else {}
    if not data or not data.get('lines'):
        return poem_canvas.empty_canvas()
    return poem_canvas.normalize_canvas(data)


def save_canvas(session_id, canvas):
    canvas = poem_canvas.normalize_canvas(canvas)
    db.execute(
        '''UPDATE poem_sessions
           SET canvas_json = %s::jsonb, updated_at = CURRENT_TIMESTAMP
           WHERE id = %s''',
        (db.dumps(canvas), str(session_id)))
    # keep in-memory session dicts in sync if callers still hold them
    return canvas


def load_stage_meta(session):
    raw = session.get('stage_meta')
    data = db.loads(raw, {}) if raw is not None else {}
    return data if isinstance(data, dict) else {}


def save_stage_meta(session_id, meta):
    try:
        db.execute(
            '''UPDATE poem_sessions
               SET stage_meta = %s::jsonb, updated_at = CURRENT_TIMESTAMP
               WHERE id = %s''',
            (db.dumps(meta or {}), str(session_id)))
    except Exception:
        # column may not exist yet — try init
        try:
            db.init_db()
            db.execute(
                '''UPDATE poem_sessions
                   SET stage_meta = %s::jsonb, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s''',
                (db.dumps(meta or {}), str(session_id)))
        except Exception:
            pass


def append_chat(session_id, role, content):
    import i18n
    row = db.fetchone('SELECT chat_log, title FROM poem_sessions WHERE id = %s', (session_id,))
    log = db.loads(row['chat_log'], [])
    log.append({'role': role, 'content': content})
    title = row.get('title') or i18n.t('session_untitled')
    # Auto-title from first user message
    if role == 'user' and is_placeholder_title(title):
        title = (content or '')[:24].strip() or title
    db.execute(
        '''UPDATE poem_sessions
           SET chat_log = %s::jsonb, title = %s, updated_at = CURRENT_TIMESTAMP
           WHERE id = %s''',
        (db.dumps(log), title, session_id))
    return log


def session_nodes(session_id):
    return db.fetchall(
        '''SELECT * FROM poem_nodes
           WHERE session_id = %s
           ORDER BY created_at ASC''',
        (session_id,))


def _score_quality(scores, poem='', canvas=None):
    """Rank attempts: prefer coherence; disqualify heavy repetition."""
    if evaluate.has_heavy_repetition(poem or '', canvas):
        return -100.0
    s = ensure_seven_dims(db.loads(scores, {}) if not isinstance(scores, dict) else scores)
    try:
        overall = float(s.get('overall') or 0)
    except Exception:
        overall = 0.0
    try:
        coh = float(s.get('coherence') or 0)
    except Exception:
        coh = 0.0
    try:
        fit = float(s.get('fit') or 0)
    except Exception:
        fit = 0.0
    # Logic-heavy ranking for rollback
    q = 0.60 * coh + 0.25 * overall + 0.15 * fit
    if coh < 40:
        q -= 25
    return q


def add_node(session_id, parent_id, thought, content, scores, stage, executed=False, canvas=None,
             record=True):
    if canvas is None:
        try:
            canvas = load_canvas({'id': session_id})
        except Exception:
            canvas = {}
    try:
        node = db.execute(
            '''INSERT INTO poem_nodes
               (session_id, parent_id, ai_thought, poem_content, radar_scores, is_executed, stage, canvas_json)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
               RETURNING *''',
            (session_id, parent_id, thought, content, db.dumps(scores), executed, stage,
             db.dumps(canvas or {})),
            returning=True)
    except Exception:
        # Older DBs without canvas_json column
        node = db.execute(
            '''INSERT INTO poem_nodes
               (session_id, parent_id, ai_thought, poem_content, radar_scores, is_executed, stage)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
               RETURNING *''',
            (session_id, parent_id, thought, content, db.dumps(scores), executed, stage),
            returning=True)
    db.execute(
        'UPDATE poem_sessions SET current_node_id = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s',
        (str(node['id']), session_id))
    # Also keep a rolling best-attempt list for mid-stage rollback
    if record:
        try:
            record_attempt(session_id, canvas, scores, stage, thought=thought, poem=content)
        except Exception:
            pass
    return node


def record_attempt(session_id, canvas, scores, stage, thought='', poem='', force=False):
    """Snapshot a fill/stage state so Back can restore the best recent try."""
    # Never snapshot talk/examples cards — those poison Back-to-best into stage=examples
    if (stage or '') in ('chat', 'examples', ''):
        return
    session = db.fetchone('SELECT stage_meta FROM poem_sessions WHERE id = %s', (session_id,))
    meta = load_stage_meta(session or {})
    attempts = list(meta.get('attempts') or [])
    poem_text = (poem or '').strip() or poem_canvas.canvas_filled_text(canvas) or ''
    if not poem_text and not (canvas or {}).get('lines'):
        return
    # Never store heavy-repetition drafts as rollback candidates
    if evaluate.has_heavy_repetition(poem_text, canvas) and not force:
        return
    if evaluate.has_heavy_repetition(poem_text, canvas) and force:
        # forced snaps (checkpoints) still skip if repetition is severe
        if evaluate.repetition_penalty(poem_text) >= 28:
            return
    q = _score_quality(scores, poem=poem_text, canvas=canvas)
    if q < -50:
        return
    # Skip near-duplicates unless forced / clearly better
    if attempts and not force:
        last = attempts[-1]
        if abs(float(last.get('quality') or 0) - q) < 0.8:
            last_poem = (last.get('poem') or '').strip()
            if last_poem == poem_text.strip():
                return
        best_q = max(float(a.get('quality') or 0) for a in attempts)
        if q + 1.5 < best_q and len(attempts) >= 3:
            if q < best_q - 8:
                return
    attempts.append({
        'quality': round(q, 2),
        'stage': stage,
        'thought': (thought or '')[:80],
        'poem': poem_text[:4000],
        'scores': ensure_seven_dims(scores if isinstance(scores, dict) else db.loads(scores, {})),
        'canvas': canvas or {},
        'has_repeat': False,
    })
    # Drop any legacy examples/chat snaps still sitting in the list
    attempts = [a for a in attempts if (a.get('stage') or '') in CANVAS_STAGES]
    meta['attempts'] = attempts[-12:]
    save_stage_meta(session_id, meta)


def find_best_recent_attempt(session, current_poem=''):
    """
    Pick best recent attempt from stage_meta.attempts, else best poem_node.
    Skips drafts with heavy word repetition.
    Never restores examples/chat nodes (that strands the pipeline).
    """
    sid = str(session['id'])
    meta = load_stage_meta(session)
    attempts = list(meta.get('attempts') or [])
    cur = (current_poem or '').strip()

    def _ok_attempt(a):
        poem = (a.get('poem') or '').strip()
        if not poem or poem == cur:
            return False
        st = a.get('stage') or ''
        if st not in CANVAS_STAGES:
            return False
        cv = a.get('canvas') or {}
        # Prefer real drafts; bare POS skeletons are weak rollback targets
        if st == 'structure' and not poem_canvas.canvas_readable_text(cv):
            return False
        if evaluate.has_heavy_repetition(poem, cv):
            return False
        if _score_quality(a.get('scores'), poem=poem, canvas=cv) < 0:
            return False
        return True

    cand = [a for a in attempts if _ok_attempt(a)]
    if cand:
        best = max(
            cand,
            key=lambda a: _score_quality(a.get('scores'), poem=a.get('poem') or '', canvas=a.get('canvas')),
        )
        return {
            'source': 'attempt',
            'poem': best.get('poem') or '',
            'scores': best.get('scores') or {},
            'canvas': best.get('canvas') or {},
            'stage': best.get('stage') or session.get('stage'),
            'thought': best.get('thought') or '',
            'quality': float(best.get('quality') or 0),
        }

    nodes = session_nodes(sid)
    cur_id = str(session.get('current_node_id') or '')
    ncand = []
    for n in nodes:
        if str(n.get('id')) == cur_id:
            continue
        st = n.get('stage') or ''
        if st not in CANVAS_STAGES:
            continue
        poem = (n.get('poem_content') or '').strip()
        if not poem or poem == cur:
            continue
        cv = db.loads(n.get('canvas_json'), {}) if n.get('canvas_json') is not None else {}
        if st == 'structure' and not poem_canvas.canvas_readable_text(cv):
            continue
        if evaluate.has_heavy_repetition(poem, cv):
            continue
        if _score_quality(n.get('radar_scores'), poem=poem, canvas=cv) < 0:
            continue
        ncand.append(n)
    if not ncand:
        return None
    node = max(
        ncand,
        key=lambda n: _score_quality(
            n.get('radar_scores'),
            poem=n.get('poem_content') or '',
            canvas=db.loads(n.get('canvas_json'), {}) if n.get('canvas_json') is not None else {},
        ),
    )
    cv = db.loads(node.get('canvas_json'), {}) if node.get('canvas_json') is not None else {}
    return {
        'source': 'node',
        'node_id': str(node['id']),
        'poem': node.get('poem_content') or '',
        'scores': db.loads(node.get('radar_scores'), {}),
        'canvas': cv,
        'stage': node.get('stage') or session.get('stage'),
        'thought': node.get('ai_thought') or '',
        'quality': _score_quality(node.get('radar_scores'), poem=node.get('poem_content') or '', canvas=cv),
    }


def restore_attempt(session, attempt, lang=None):
    """Apply attempt onto session; yields nothing — caller emits SSE."""
    sid = str(session['id'])
    cv = attempt.get('canvas') or {}
    if not cv.get('lines') and attempt.get('poem'):
        cv = poem_canvas.skeleton_from_text(attempt['poem'])
        cv = poem_canvas.normalize_canvas(cv)
    if cv.get('lines'):
        save_canvas(sid, cv)
    stage = attempt.get('stage') or session.get('stage') or 'symbols'
    # Never land on talk/examples — that blocks Confirm (must pick a card)
    if stage not in CANVAS_STAGES:
        cur = session.get('stage') or 'symbols'
        stage = cur if cur in CANVAS_STAGES else 'symbols'
    node_id = attempt.get('node_id')
    if node_id:
        db.execute(
            '''UPDATE poem_sessions
               SET current_node_id = %s, stage = %s, run_status = 'idle', checkpoint_id = NULL,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = %s''',
            (node_id, stage, sid))
    else:
        # Create a restored node so history stays consistent
        parent = session.get('current_node_id')
        node = add_node(
            sid, parent,
            attempt.get('thought') or _tr(lang, '回退到较优尝试', 'Restored better attempt'),
            attempt.get('poem') or '',
            attempt.get('scores') or {},
            stage,
            canvas=cv,
            record=False,
        )
        # add_node already set current_node_id; also clear awaiting
        db.execute(
            '''UPDATE poem_sessions
               SET stage = %s, run_status = 'idle', checkpoint_id = NULL,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = %s''',
            (stage, sid))
        try:
            meta = load_stage_meta(db.fetchone('SELECT stage_meta FROM poem_sessions WHERE id = %s', (sid,)))
            meta['restored_from_quality'] = attempt.get('quality')
            # Purge legacy examples snaps so the next Back cannot bounce again
            meta['attempts'] = [
                a for a in (meta.get('attempts') or [])
                if (a.get('stage') or '') in CANVAS_STAGES
            ]
            save_stage_meta(sid, meta)
        except Exception:
            pass
    return cv, stage


def ensure_seven_dims(dims):
    """Fill missing axes (esp. depth) on targets/scores for 7-dim radar."""
    out = {}
    defaults = {
        'rhyme': 50, 'rhythm': 50, 'tension': 50, 'paradox': 50,
        'metaphor': 50, 'freshness': 50, 'depth': 70,
    }
    src = dims or {}
    for k, d in defaults.items():
        v = src.get(k, d)
        try:
            x = float(v)
            if x <= 1.0 and k in src:
                x = x * 100.0
            out[k] = x
        except Exception:
            out[k] = d
    # preserve composite analytics
    for k in ('coherence', 'fit', 'overall'):
        if k in src and src[k] is not None:
            try:
                out[k] = float(src[k])
            except Exception:
                pass
    return out


def score_poem(text, session_id=None, target=None):
    hist = history_texts(session_id) if session_id else None
    return ensure_seven_dims(evaluate.evaluate_poem(text, hist, target=target))


def score_brief(scores, lang=None):
    """Short process-rail line: logic + fit + overall."""
    scores = scores or {}
    try:
        c = float(scores.get('coherence') if scores.get('coherence') is not None else 0)
    except Exception:
        c = 0.0
    try:
        f = float(scores.get('fit') if scores.get('fit') is not None else 0)
    except Exception:
        f = 0.0
    try:
        o = float(scores.get('overall') if scores.get('overall') is not None else 0)
    except Exception:
        o = 0.0
    if _lang_en(lang):
        return f"Scores — logic {c:.0f} · fit {f:.0f} · overall {o:.0f}"
    return f"评分 — 逻辑 {c:.0f} · 拟合 {f:.0f} · 综合 {o:.0f}"


def willingness(text):
    t = (text or '').lower().strip()
    if not t:
        return False
    keys = [
        '诗', '写一首', '写诗', '作诗', '表达', '抒发', '帮我写', '创作', '想写', '来一首', '做一首',
        'verse', 'poem', 'poetry', 'sonnet', 'haiku', 'ballad', 'limerick', 'ode', 'stanza',
        'write a poem', 'write me a', 'write poetry', "let's write", 'lets write',
        'compose', 'draft a poem', 'i want to write', 'make me a poem', 'give me a poem',
        '詩', '書きたい', '作って', 'ソネット',
    ]
    if any(k in t for k in keys):
        return True
    if re.search(r'\b(write|compose|draft|create)\b.{0,40}\b(poem|poetry|sonnet|verse|haiku|ballad)\b', t):
        return True
    if re.search(r'\b(poem|sonnet|verse|haiku)\b.{0,20}\b(please|for me|about)\b', t):
        return True
    return False


def migrate_session_targets(session, prefs):
    """Ensure session target_dimensions has all 7 axes; persist if depth was missing."""
    sid = str(session['id'])
    raw = db.loads(session.get('target_dimensions'), {}) or {}
    weights = prefs.get('dimension_weights') or {}
    from_weights = {k: int(float(v) * 100) for k, v in weights.items()}
    merged = dict(from_weights)
    merged.update(raw)
    target = ensure_seven_dims(merged)
    if 'depth' not in raw or len(raw) < 7:
        try:
            db.execute(
                'UPDATE poem_sessions SET target_dimensions = %s::jsonb WHERE id = %s',
                (db.dumps(target), sid))
            session = dict(session)
            session['target_dimensions'] = db.dumps(target)
        except Exception:
            pass
    return target, session


def extract_poem(text):
    if not text:
        return ''
    m = re.search(r'(?:诗正文|Poem(?:\s*body)?|poem(?:\s*text)?)[:：]\s*\n(.+)', text, re.S | re.I)
    if m:
        body = m.group(1).strip()
        body = re.split(r'\n#{1,3}\s|\n维度|\n检查：|\n自评分|\nDimensions|\nCheck', body)[0].strip()
        return body
    lines = [ln.rstrip() for ln in text.splitlines()]
    poem_lines = []
    skip_prefixes = (
        '主体', '表达', '长度', '句式', '主韵', '禁忌', '维度', '体裁', '匹配', '测试', '问题',
        '自评分', '三层', '动词表', 'Subject', 'Template', 'Rhyme', 'Ban', 'Dimension', 'Length',
    )
    for ln in lines:
        s = ln.strip()
        if not s:
            if poem_lines:
                poem_lines.append('')
            continue
        if s.startswith('#') or s.startswith('###'):
            continue
        if any(s.startswith(p) for p in skip_prefixes):
            continue
        if ('：' in s or (':' in s and len(s) < 48)) and not re.search(r'[\u4e00-\u9fff]{8,}', s):
            # likely a label line in CJK or short EN meta
            if re.match(r'^[A-Za-z ]{2,20}:', s) and len(s) < 48:
                continue
            if '：' in s and len(s) < 40 and not re.search(r'[\u4e00-\u9fff]{8,}', s):
                continue
        poem_lines.append(ln)
    body = '\n'.join(poem_lines).strip()
    cjk = len(re.findall(r'[\u4e00-\u9fff]', body))
    latin = len(re.findall(r'[A-Za-z]', body))
    if cjk < 8 and latin < 24:
        return text.strip()
    return body


def _topic_tokens(text):
    """Lightweight topic tokens for similarity (ZH bigrams + content words)."""
    text = text or ''
    toks = set()
    for w in re.findall(r"[A-Za-z']{3,}", text.lower()):
        toks.add(w)
    chars = re.findall(r'[\u4e00-\u9fff]', text)
    for i in range(len(chars) - 1):
        toks.add(chars[i] + chars[i + 1])
    # single thematic chars that often mark subject
    for c in chars:
        if c in (
            '月', '星', '光', '暗', '雨', '海', '江', '河', '窗', '门', '梦',
            '乡', '家', '母', '父', '死', '生', '血', '雪', '霜', '夜', '晨',
            '城', '街', '车', '船', '山', '风', '花', '叶', '心', '影', '时',
            '钟', '信', '照', '骨', '身', '手', '泪', '雾', '烟', '灯', '火',
        ):
            toks.add(c)
    return toks


def topic_similarity(a, b):
    ta, tb = _topic_tokens(a), _topic_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == 0:
        return 0.0
    return inter / float(len(ta | tb))


def current_topic_query(session, extra_user=None):
    """Build a topic query blob from the active session for memory retrieval."""
    parts = []
    if session:
        parts.append(session.get('title') or '')
        for m in db.loads(session.get('chat_log'), [])[-10:]:
            if m.get('role') == 'user':
                parts.append((m.get('content') or '')[:400])
        try:
            meta = load_stage_meta(session)
            sel = meta.get('selected_example') or {}
            if isinstance(sel, dict):
                parts.append(sel.get('title') or '')
                parts.append((sel.get('poem') or '')[:200])
            if meta.get('verse_form'):
                parts.append(str(meta.get('verse_form')))
        except Exception:
            pass
        try:
            cv = load_canvas(session)
            parts.append(poem_canvas.canvas_readable_text(cv)[:200])
        except Exception:
            pass
    if extra_user:
        parts.append(extra_user)
    return '\n'.join(p for p in parts if p).strip()


def cross_session_memory(user_id, exclude_session_id=None, limit=5,
                         query_text=None, min_score=0.06):
    """
    Retrieve past drafts whose topic is similar to query_text.
    If query is empty or nothing is similar enough, return [] (do not dump unrelated history).
    """
    query_text = (query_text or '').strip()
    if not query_text:
        return []

    rows = db.fetchall(
        '''SELECT s.id, s.title, s.chat_log, n.poem_content, n.ai_thought
           FROM poem_sessions s
           LEFT JOIN poem_nodes n ON n.id = s.current_node_id
           WHERE s.user_id = %s
           ORDER BY COALESCE(s.updated_at, s.created_at) DESC
           LIMIT %s''',
        (user_id, max(24, limit * 5)))

    scored = []
    for r in rows:
        if exclude_session_id and str(r['id']) == str(exclude_session_id):
            continue
        title = r.get('title') or '无题'
        poem = extract_poem(r.get('poem_content') or '')
        # Also peek early user intents from that session
        past_users = []
        for m in db.loads(r.get('chat_log'), [])[:6]:
            if m.get('role') == 'user':
                past_users.append((m.get('content') or '')[:120])
        blob = '\n'.join([title, poem[:240], ' '.join(past_users)])
        score = topic_similarity(query_text, blob)
        if score < min_score:
            continue
        thought = (r.get('ai_thought') or '')[:40]
        snippet = extract_poem(poem)[:80]
        scored.append((score, {
            'title': title,
            'poem': snippet,
            'thought': thought,
            'score': round(score, 3),
        }))

    scored.sort(key=lambda x: -x[0])
    bits = []
    for score, item in scored[:limit]:
        bit = f'《{item["title"]}》'
        if item['poem']:
            bit += '｜' + item['poem']
        if item['thought']:
            bit += '｜念头:' + item['thought']
        bit += f'｜相近度:{item["score"]}'
        bits.append(bit)
    return bits


def build_context(session, prefs, stage=None, lang=None, user_id=None):
    log = db.loads(session['chat_log'], [])
    weights = prefs['dimension_weights']
    dim_line = ', '.join(f'{k}:{v}' for k, v in weights.items())
    messages = []
    for m in log[-12:]:
        role = m.get('role')
        if role in ('user', 'assistant'):
            messages.append({'role': role, 'content': m.get('content', '')})
    stage_task = STAGE_TASKS.get(stage or session.get('stage') or '', '')
    cultural = prefs.get('cultural_preferences') or {}
    neg = prefs.get('negative_feedback_history') or []
    en = (lang or 'zh').startswith('en')
    neg_hint = '；'.join((x.get('summary') or '')[:40] for x in neg[-3:]) if neg else (
        'none' if en else '无')
    mem = ''
    if user_id and not en:
        # Only inject topic-similar past drafts — never unrelated history
        q = current_topic_query(session)
        bits = cross_session_memory(
            user_id, exclude_session_id=str(session.get('id')), query_text=q)
        if bits:
            mem = (
                '题材相近的过往（仅相似题材，禁止硬套无关主题；可借鉴呼吸/意象场，勿抄原句）：'
                + ' || '.join(bits)
            )
        else:
            mem = '题材相近过往：无（勿假装记得无关作品）'
    import i18n
    lang_line = i18n.t('llm_lang', lang=lang)
    cv = load_canvas(session)
    cv_text = poem_canvas.canvas_to_text(cv, lang=lang)
    form = verse_form.detect_verse_form(session, extra_user=None)
    # Persist detected form for later stages
    try:
        meta = load_stage_meta(session)
        if form and form.get('id') and form.get('id') != 'free':
            if meta.get('verse_form') != form.get('id'):
                meta['verse_form'] = form.get('id')
                save_stage_meta(str(session.get('id')), meta)
        elif meta.get('verse_form'):
            form = verse_form.FORMS.get(meta['verse_form']) or form
    except Exception:
        pass
    form_line = verse_form.form_instruction(form, lang=lang)
    # Style cards + stage-scoped craft (participation clarity + anti-stiff)
    style_block = ''
    try:
        from style_corpus import pick_cards, format_injection
        cult = None
        if isinstance(cultural, dict) and cultural:
            cult = next(iter(cultural.keys()), None)
        cards = pick_cards(lang=lang, n=2, culture=cult)
        style_block = format_injection(cards, lang=lang)
    except Exception:
        style_block = ''
    stage_now = stage or session.get('stage') or ''
    # Selected style card — higher priority than corpus snippets
    selected_block = ''
    try:
        meta_sel = load_stage_meta(session)
        sel = meta_sel.get('selected_example') or {}
    except Exception:
        sel = {}
    if isinstance(sel, dict) and (sel.get('poem') or sel.get('template')) and stage_now in (
            'structure', 'symbols', 'verbs', 'final'):
        if en:
            selected_block = (
                'SELECTED STYLE CARD (primary draft — refine, do not abandon):\n'
                f'Title: {sel.get("title") or ""}\n'
                f'Template: {sel.get("template") or ""}\n'
                f'Rules: {sel.get("rules") or ""}\n'
                f'Poem:\n{sel.get("poem") or ""}\n'
                'Refine this breath and imagery unless the user explicitly asks to change theme. '
                'Do not replace with an unrelated new poem.\n'
            )
        else:
            selected_block = (
                '【已选样例卡·底稿优先】请在此基础上 refining，勿抛弃呼吸与核心意象：\n'
                f'标题：{sel.get("title") or ""}\n'
                f'模板：{sel.get("template") or ""}\n'
                f'规则：{sel.get("rules") or ""}\n'
                f'样例诗：\n{sel.get("poem") or ""}\n'
                '除非用户明确要求换主题，禁止另写一首无关新诗。\n'
            )
    protocol_bits = [STAGE_BOUNDARY, ANTI_STIFF, DUP_HARD_BAN, ASSOCIATION, QUALITY_PASS]
    if stage_now in ('examples', 'structure'):
        protocol_bits.append(ANALYZE_IMITATE_COMPACT)
    if stage_now in ('examples', 'symbols', 'verbs', 'final'):
        protocol_bits.append(SUBJECT_FIT_COMPACT)
    # Prefer compact reminder on fill rounds is already covered by ASSOCIATION full text once
    protocol_block = '\n'.join(protocol_bits)
    if en:
        messages.append({
            'role': 'user',
            'content': (
                f'{lang_line}\n{stage_task}\n{DIM_MAP}\n{protocol_block}\n'
                f'{selected_block}\n{style_block}\n'
                f'User dimension weights: {dim_line}\n'
                f'Cultural prefs: {cultural}\nRecent negative feedback: {neg_hint}\n'
                f'Verb prefs: {prefs.get("verb_preferences") or {}}\n'
                f'{form_line}\n'
                f'Current canvas:\n{cv_text or "(empty)"}\n'
                'Follow the protocol; no empty praise. Keep philosophical depth (depth) unless '
                'the user explicitly wants pure scenic description.\n'
                'LANGUAGE: poem titles + poem bodies + choice labels MUST be English. '
                'No Chinese characters in those fields.'
            )
        })
    else:
        messages.append({
            'role': 'user',
            'content': (
                f'{lang_line}\n{stage_task}\n{DIM_MAP}\n{protocol_block}\n'
                f'{selected_block}\n{style_block}\n'
                f'用户维度权重：{dim_line}\n'
                f'文化偏好：{cultural}\n近期负反馈摘要：{neg_hint}\n'
                f'动词偏好：{prefs.get("verb_preferences") or {}}\n'
                f'{form_line}\n'
                f'{mem}\n'
                f'当前画布：\n{cv_text or "（空）"}\n'
                '严格执行提示词协议；输出禁止无用空话；维度必须可追溯。'
                '默认保持哲学深度(depth)；仅当用户明确要求纯景白描时才允许压低 depth。'
            )
        })
    return messages


def history_texts(session_id):
    nodes = session_nodes(session_id)
    return [n['poem_content'] for n in nodes if n.get('poem_content')]


def refresh_bundle(user_id, lang=None, session_id=None):
    session = get_session(user_id, session_id)
    prefs = preferences.get_preferences(user_id)
    _, session = migrate_session_targets(session, prefs)
    # re-read after possible target migration
    session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (str(session['id']),)) or session
    nodes = session_nodes(str(session['id']))
    current = None
    if session.get('current_node_id'):
        current = db.fetchone(
            'SELECT * FROM poem_nodes WHERE id = %s',
            (session['current_node_id'],))
    st = session.get('stage') or 'chat'
    return {
        'session': session,
        'prefs': prefs,
        'nodes': nodes,
        'current': current,
        'chat_log': db.loads(session['chat_log'], []),
        'stage': st,
        'stage_label': stage_label(st, lang=lang),
        'canvas': load_canvas(session),
        'run_status': session.get('run_status') or 'idle',
        'checkpoint_id': session.get('checkpoint_id'),
        'sessions': list_sessions(user_id),
        'stage_meta': load_stage_meta(session),
    }


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def sse_process(line, *, waiting=False, kind='info'):
    return sse('process', {'line': line, 'waiting': waiting, 'kind': kind})


def _collect_stream(messages, role, temperature, run_check=None):
    """Yield SSE thought deltas; return (text, degraded)."""
    buf = []
    degraded = False
    for delta, meta in llm.stream_complete_meta(messages, role=role, temperature=temperature):
        if run_check and not run_check():
            break
        if meta.get('degraded'):
            degraded = True
        buf.append(delta)
        yield ('delta', delta, degraded)
    yield ('done', ''.join(buf), degraded)


def stream_user_turn(user_id, text, action=None, form=None, lang=None,
                     session_id=None, attachments=None, resume_from=None):
    try:
        session = get_session(user_id, session_id)
    except Exception as e:
        yield sse('error', {'message': f'数据库不可用：{e}'})
        yield sse('done', {'stage': 'chat', 'show_actions': False})
        return

    sid = str(session['id'])
    prefs = preferences.get_preferences(user_id)

    # Merge text attachments into message
    if attachments:
        bits = []
        for a in attachments:
            name = (a.get('name') or 'file')[:80]
            body = (a.get('content') or '')[:50000]
            bits.append(f'【附件:{name}】\n{body}')
        extra = '\n\n'.join(bits)
        text = (text or '') + ('\n\n' + extra if extra else '')

    if form and action in (None, '', 'save_dims', 'confirm', 'reject', 'continue', 'resume', 'pick_example'):
        if any(form.get(k) not in (None, '') for k in ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth']):
            preferences.update_from_sliders(user_id, form)
            prefs = preferences.get_preferences(user_id)
            target = {k: int(v * 100) for k, v in prefs['dimension_weights'].items()}
            db.execute(
                'UPDATE poem_sessions SET target_dimensions = %s::jsonb WHERE id = %s',
                (db.dumps(target), sid))

    stage = session['stage'] or 'chat'
    yield sse('stage', {'stage': stage, 'label': stage_label(stage, lang=lang)})
    yield sse('session', {'id': sid, 'title': session.get('title')})

    if action == 'interrupt':
        interrupt_session(user_id, sid)
        yield sse_process('已中断', kind='warn')
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if action == 'pick_example':
        yield from _handle_pick_example(session, prefs, form or {}, lang=lang, user_id=user_id)
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if action == 'save_dims':
        append_chat(sid, 'assistant', '维度已更新。')
        yield sse('message', {'role': 'assistant', 'content': '维度已更新。'})
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if action == 'new':
        create_session(user_id, title=default_session_title('new', lang=lang), lang=lang)
        yield sse('done', refresh_payload(user_id, lang=lang))
        return

    if action == 'switch' and form.get('session_id'):
        switched = switch_session(user_id, form.get('session_id'))
        if switched:
            yield sse('done', refresh_payload(user_id, lang=lang, session_id=str(switched['id'])))
        else:
            yield sse('error', {'message': '会话不存在'})
            yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if action == 'publish':
        session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,)) or session
        cv_pub = load_canvas(session)
        poem_pub = (
            poem_canvas.canvas_readable_text(cv_pub, lang=lang)
            or poem_canvas.canvas_filled_text(cv_pub, lang=lang)
            or ''
        )
        if session.get('current_node_id') and not poem_pub:
            node_p = db.fetchone(
                'SELECT poem_content FROM poem_nodes WHERE id = %s',
                (session['current_node_id'],))
            poem_pub = (node_p or {}).get('poem_content') or ''
        if evaluate.has_heavy_repetition(poem_pub, cv_pub):
            issues = '、'.join(evaluate.list_repetition_issues(poem_pub)[:6]) or '叠词'
            msg = _tr(
                lang,
                f'公开失败：稿中仍有叠词/复用（{issues}）。请先定稿去掉后再公开。',
                f'Publish blocked: repeated wording remains ({issues}). Fix before publishing.',
            )
            append_chat(sid, 'assistant', msg)
            yield sse('message', {'role': 'assistant', 'content': msg})
            yield sse_process(msg, kind='warn')
            yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
            return
        db.execute(
            'UPDATE poem_sessions SET is_public = TRUE WHERE id = %s AND user_id = %s',
            (sid, user_id))
        msg = _tr(lang, '已公开到首页。', 'Published to the home page.')
        append_chat(sid, 'assistant', msg)
        yield sse('message', {'role': 'assistant', 'content': msg})
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if action == 'back':
        # Interrupt any run, then restore the best recent attempt (not merely previous stage)
        try:
            interrupt_session(user_id, sid)
        except Exception:
            set_idle(sid)
        session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,)) or session
        cv_now = load_canvas(session)
        cur_poem = poem_canvas.canvas_filled_text(cv_now, lang=lang) or ''
        if session.get('current_node_id'):
            cur_node = db.fetchone('SELECT poem_content FROM poem_nodes WHERE id = %s',
                                   (session['current_node_id'],))
            if cur_node and cur_node.get('poem_content'):
                cur_poem = cur_node['poem_content'] or cur_poem
        attempt = find_best_recent_attempt(session, current_poem=cur_poem)
        if not attempt:
            # interrupt left status=aborted — always return to idle so Confirm still works
            set_idle(sid)
            msg = _tr(
                lang,
                '还没有可回退的较优尝试（样例卡不计入回退）。',
                'No better recent draft to restore (style cards are not rollback targets).',
            )
            append_chat(sid, 'assistant', msg)
            yield sse('message', {'role': 'assistant', 'content': msg})
            yield sse_process(msg, kind='warn')
            yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
            return
        cv, new_stage = restore_attempt(session, attempt, lang=lang)
        # Ready to continue from restored canvas stage
        token = begin_run(sid)
        set_awaiting(sid, 'stage_review', token)
        scores = ensure_seven_dims(attempt.get('scores') or {})
        poem = attempt.get('poem') or poem_canvas.canvas_filled_text(cv, lang=lang)
        yield sse('stage', {'stage': new_stage, 'label': stage_label(new_stage, lang=lang)})
        if cv.get('lines'):
            yield sse('canvas', cv)
        yield sse('poem', {'from': '', 'to': poem, 'full': poem})
        yield sse('radar', scores)
        q = attempt.get('quality')
        msg = _tr(
            lang,
            f'已回退到最近较优尝试（综合/逻辑加权 {q:.0f}）· 阶段 {stage_label(new_stage, lang=lang)}。'
            f'确认后继续下一阶段。',
            f'Restored best recent attempt (quality {q:.0f}) · stage {stage_label(new_stage, lang=lang)}. '
            f'Confirm to continue.',
        )
        append_chat(sid, 'assistant', msg)
        yield sse('message', {'role': 'assistant', 'content': msg})
        yield sse_process(msg, kind='ok')
        yield sse_process(score_brief(scores, lang=lang), kind='info')
        yield sse('checkpoint', {
            'id': 'stage_review',
            'message': _tr(lang, '已回退，确认继续？', 'Restored — confirm to continue?'),
        })
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    # continue / resume from checkpoint (skeleton_ready etc.)
    if action in ('continue', 'resume', 'confirm') and (
            resume_from or session.get('run_status') == 'awaiting' or action == 'confirm'):
        # examples stage: confirm without pick is not allowed
        if action == 'confirm' and stage == 'examples' and session.get('run_status') != 'awaiting':
            msg = '请先点选一张示例卡（或编号），再进入结构。'
            append_chat(sid, 'assistant', msg)
            yield sse('message', {'role': 'assistant', 'content': msg})
            meta = load_stage_meta(session)
            if meta.get('examples_payload'):
                yield sse('examples', meta['examples_payload'])
            yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
            return

        # confirm still advances stage when not awaiting canvas gate
        if action == 'confirm' and session.get('run_status') != 'awaiting' and not resume_from:
            if session.get('current_node_id'):
                db.execute('UPDATE poem_nodes SET is_executed = TRUE WHERE id = %s',
                           (session['current_node_id'],))
            idx = STAGES.index(stage) if stage in STAGES else 0
            new_stage = STAGES[idx + 1] if idx < len(STAGES) - 1 else 'final'
            db.execute(
                'UPDATE poem_sessions SET stage = %s, soft_ask_skips = COALESCE(soft_ask_skips,0) + 1 WHERE id = %s',
                       (new_stage, sid))
            session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
            yield sse('stage', {'stage': new_stage, 'label': stage_label(new_stage, lang=lang)})
            if new_stage != 'chat':
                yield from _stream_stage(session, prefs, new_stage, lang=lang, user_id=user_id)
            yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
            return

        # Resume fill after skeleton ask
        db.execute(
            'UPDATE poem_sessions SET soft_ask_skips = COALESCE(soft_ask_skips,0) + 1 WHERE id = %s',
            (sid,))
        session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
        cp = resume_from or session.get('checkpoint_id') or 'skeleton_ready'
        yield sse_process(f'继续：{cp}', kind='ok')
        if cp == 'skeleton_ready' or stage in CANVAS_STAGES:
            fill_stage = stage if stage in ('symbols', 'verbs', 'final') else 'symbols'
            if stage == 'structure':
                db.execute("UPDATE poem_sessions SET stage = 'symbols' WHERE id = %s", (sid,))
                fill_stage = 'symbols'
                session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
                yield sse('stage', {'stage': 'symbols', 'label': stage_label('symbols', lang=lang)})
            yield from _stream_canvas_loop(
                session, prefs, fill_stage, lang=lang, user_id=user_id, mode='fill')
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if action == 'reject':
        node = db.fetchone(
            'SELECT * FROM poem_nodes WHERE id = %s',
            (session['current_node_id'],)) if session.get('current_node_id') else None
        scores = db.loads(node['radar_scores'], {}) if node else {}
        preferences.register_rejection(
            user_id,
            node['poem_content'] if node else text,
            {k: v for k, v in scores.items() if k != 'overall'})
        prefs = preferences.get_preferences(user_id)
        append_chat(sid, 'user', text or '不满意，换一组')
        # Escape hatch: reject seeded structure → empty-slot pipeline
        if (session.get('stage') or stage) == 'structure':
            meta_r = load_stage_meta(session)
            meta_r['skip_example_seed'] = True
            save_stage_meta(sid, meta_r)
            save_canvas(sid, poem_canvas.empty_canvas())
        session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
        yield from _stream_stage(
            session, prefs, stage if stage != 'chat' else 'examples',
            temperature=0.85, lang=lang, user_id=user_id)
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    if text:
        append_chat(sid, 'user', text)
        yield sse('message', {'role': 'user', 'content': text})

    session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
    stage = session['stage'] or 'chat'

    if stage == 'chat':
        import i18n
        token = begin_run(sid)
        try:
            # Clear writing intent → jump into pipeline; do NOT let companion dump a finished poem
            if willingness(text):
                en = (lang or 'zh').startswith('en')
                # Lock verse form as early as the first writing intent
                form0 = verse_form.detect_verse_form(session, extra_user=text, text=text)
                if form0 and form0.get('id') != 'free':
                    meta0 = load_stage_meta(session)
                    meta0['verse_form'] = form0['id']
                    save_stage_meta(sid, meta0)
                    yield sse_process(
                        _tr(lang,
                            f'已记下体裁：{form0["id"]}',
                            f'Noted form: {form0["id"]}'),
                        kind='ok')
                ack = (
                    'Got it — starting the step-by-step draft with three style cards.'
                    if en else
                    '收到，开始逐步创作：先给出三组风格对照卡。'
                )
                append_chat(sid, 'assistant', ack)
                yield sse('message', {'role': 'assistant', 'content': ack})
                db.execute("UPDATE poem_sessions SET stage = 'examples' WHERE id = %s", (sid,))
                session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
                yield sse('stage', {'stage': 'examples', 'label': stage_label('examples', lang=lang)})
                yield sse_process('Entering examples' if en else '进入示例对照', kind='ok')
                if run_still_valid(sid, token):
                    yield from _stream_stage(session, prefs, 'examples', lang=lang, user_id=user_id)
            else:
                messages = []
                for m in db.loads(session['chat_log'], [])[-10:]:
                    if m.get('role') in ('user', 'assistant'):
                        messages.append({'role': m['role'], 'content': m['content']})
                bits = cross_session_memory(
                    user_id, exclude_session_id=sid, query_text=text or '')
                if bits:
                    messages.append({
                        'role': 'user',
                        'content': (
                            '（系统）仅注入与当前话题题材相近的过往创作；'
                            '可自然提及相似意象场，禁止硬套无关作品：'
                            + ' || '.join(bits[:3])
                        ),
                    })
                else:
                    messages.append({
                        'role': 'user',
                        'content': '（系统）无题材相近的过往可引用；不要假装记得无关诗稿。',
                    })
                messages.append({'role': 'user', 'content': i18n.t('llm_lang', lang=lang)})
                messages.append({
                    'role': 'user',
                    'content': (
                        'Hard rule: do NOT output a finished poem in this talk stage. '
                        'Ask at most one clarifying question, or invite the user to say “write a poem / 写一首诗” to start the pipeline.'
                    )
                })
                buf = []
                degraded = False
                yield sse('thought_start', {})
                for delta, meta in llm.stream_complete_meta(messages, role='companion', temperature=0.6):
                    if not run_still_valid(sid, token):
                        yield sse_process('已中断', kind='warn')
                        break
                    if meta.get('degraded'):
                        degraded = True
                    buf.append(delta)
                    yield sse('thought', {'delta': delta})
                if degraded:
                    yield sse('degraded', {'message': '模型不可用，已使用本地降级回复'})
                    yield sse_process('降级模式', kind='warn')
                reply = ''.join(buf)
                if reply and run_still_valid(sid, token):
                    append_chat(sid, 'assistant', reply)
                    yield sse('message', {'role': 'assistant', 'content': reply})
        finally:
            set_idle(sid)
        yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))
        return

    target, session = migrate_session_targets(session, prefs)
    current_node = db.fetchone(
        'SELECT * FROM poem_nodes WHERE id = %s',
        (session['current_node_id'],)) if session.get('current_node_id') else None
    current_scores = ensure_seven_dims(
        db.loads(current_node['radar_scores'], {}) if current_node else {})
    delta = 0.2
    if target and current_scores:
        diffs = []
        for k, tv in target.items():
            if k in current_scores and isinstance(tv, (int, float)):
                diffs.append(abs(float(tv) - float(current_scores[k])) / 100.0)
        if diffs:
            delta = sum(diffs) / len(diffs)
    temp = sigmoid_temperature(delta)
    yield sse('temp', {'t': round(temp, 2)})
    yield from _stream_stage(
        session, prefs, stage, temperature=temp, extra_user=text, lang=lang, user_id=user_id)
    yield sse('done', refresh_payload(user_id, lang=lang, session_id=sid))


def _handle_pick_example(session, prefs, form, lang=None, user_id=None):
    sid = str(session['id'])
    meta = load_stage_meta(session)
    payload = meta.get('examples_payload') or {}
    examples = payload.get('examples') or []
    example_id = form.get('example_id') or form.get('id')
    choice_id = form.get('choice_id')

    if choice_id == '4' or example_id in (None, '', 'null', '4'):
        msg = (
            'Tell me which dimensions, cultural preferences, or bans to adjust; then I will regenerate three cards.'
            if (lang or 'zh').startswith('en') else
            '请说明想调整的维度权重、文化偏好或禁忌；说完后我会按新约束重出三卡。'
        )
        append_chat(sid, 'assistant', msg)
        yield sse('message', {'role': 'assistant', 'content': msg})
        yield sse_process('Waiting for parameter tweaks' if (lang or 'zh').startswith('en') else '等待参数调整', waiting=True, kind='ask')
        return

    chosen = None
    for ex in examples:
        if str(ex.get('id')) == str(example_id):
            chosen = ex
            break
    if not chosen and examples:
        # choice 1/2/3 map to index
        try:
            idx = int(choice_id or example_id) - 1
            if 0 <= idx < len(examples):
                chosen = examples[idx]
        except Exception:
            pass
    if not chosen:
        msg = (
            'Could not find that example — pick a card again.'
            if (lang or 'zh').startswith('en') else
            '未找到该示例，请再点选一张卡片。'
        )
        append_chat(sid, 'assistant', msg)
        yield sse('message', {'role': 'assistant', 'content': msg})
        yield sse('examples', payload)
        return

    dims = chosen.get('dims') or {}
    target = {k: int(dims.get(k, 50)) for k in stage_schema.DIM_KEYS}
    meta['selected_example'] = chosen
    meta.pop('skip_example_seed', None)  # re-enable seeding on new pick
    # Keep / refresh verse form from chat + card text
    form = verse_form.detect_verse_form(session, text=f"{chosen.get('title') or ''}\n{chosen.get('template') or ''}\n{chosen.get('poem') or ''}")
    if form and form.get('id') != 'free':
        meta['verse_form'] = form['id']
    save_stage_meta(sid, meta)
    db.execute(
        '''UPDATE poem_sessions
           SET target_dimensions = %s::jsonb, stage = 'structure', updated_at = CURRENT_TIMESTAMP
           WHERE id = %s''',
        (db.dumps(target), sid))
    label = chosen.get('title') or chosen.get('id')
    if (lang or 'zh').startswith('en'):
        summary = f'Selected [{label}]. Template: {chosen.get("template") or "(see card)"}. Building slot skeleton.'
        user_pick = f'Selected example {chosen.get("id")}'
    else:
        summary = f'已选【{label}】。模板：{chosen.get("template") or "（见卡）"}。开始立槽位骨架。'
        user_pick = f'选择示例 {chosen.get("id")}'
    append_chat(sid, 'user', user_pick)
    append_chat(sid, 'assistant', summary)
    yield sse('message', {'role': 'user', 'content': user_pick})
    yield sse('message', {'role': 'assistant', 'content': summary})
    yield sse('poem', {'from': '', 'to': chosen.get('poem') or '', 'full': chosen.get('poem') or ''})
    yield sse('radar', ensure_seven_dims({**dims, 'overall': sum(dims.values()) / max(1, len(dims))}))
    yield sse_process(
        _tr(lang, f'已选 {chosen.get("id")} → 结构', f'Selected {chosen.get("id")} → structure'),
        kind='ok')
    session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
    yield sse('stage', {'stage': 'structure', 'label': stage_label('structure', lang=lang)})
    yield from _stream_stage(session, prefs, 'structure', lang=lang, user_id=user_id)


def _stream_stage(session, prefs, stage, temperature=0.7, extra_user=None, lang=None, user_id=None):
    if stage == 'examples':
        yield from _stream_examples_stage(
            session, prefs, temperature=temperature, extra_user=extra_user,
            lang=lang, user_id=user_id)
        return

    if stage in CANVAS_STAGES:
        mode = 'skeleton' if stage == 'structure' else 'fill'
        yield from _stream_canvas_loop(
            session, prefs, stage, temperature=temperature,
            extra_user=extra_user, lang=lang, user_id=user_id, mode=mode)
        return

    # legacy fallback
    yield from _stream_examples_stage(
        session, prefs, temperature=temperature, extra_user=extra_user,
        lang=lang, user_id=user_id)


def _examples_need_lang_retry(payload, lang):
    """True when EN UI but poem/title fields still contain Chinese."""
    if not (lang or 'zh').startswith('en'):
        return False
    for ex in (payload or {}).get('examples') or []:
        blob = f"{ex.get('title') or ''}\n{ex.get('poem') or ''}"
        if len(re.findall(r'[\u4e00-\u9fff]', blob)) >= 4:
            return True
    return False


def _stream_examples_stage(session, prefs, temperature=0.7, extra_user=None, lang=None, user_id=None):
    sid = str(session['id'])
    token = begin_run(sid)
    yield sse('stage', {'stage': 'examples', 'label': stage_label('examples', lang=lang)})
    en = (lang or 'zh').startswith('en')
    yield sse_process('Generating three style cards…' if en else '正在生成三组对照…')
    messages = build_context(session, prefs, stage='examples', lang=lang, user_id=user_id)
    if en:
        # Prepend hard lock so it outranks Chinese craft protocol / chat history
        form = verse_form.detect_verse_form(session, extra_user=extra_user)
        form_line = verse_form.form_instruction(form, lang=lang)
        messages.insert(0, {
            'role': 'user',
            'content': (
                'LANGUAGE LOCK (non-negotiable): The UI is English. '
                'Every example title and poem MUST be English. '
                f'{form_line} '
                'If the form is a sonnet, each sample poem MUST be a FULL 14-line English sonnet '
                '(Shakespearean 4+4+4+2 by default) — never a 2-quatrain+couplet stub. '
                'Chinese characters in title/poem = INVALID.'
            ),
        })
        if form and form.get('id') != 'free':
            meta = load_stage_meta(session)
            meta['verse_form'] = form['id']
            save_stage_meta(sid, meta)
    else:
        form = verse_form.detect_verse_form(session, extra_user=extra_user)
        if form and form.get('id') != 'free':
            meta = load_stage_meta(session)
            meta['verse_form'] = form['id']
            save_stage_meta(sid, meta)
            messages.append({
                'role': 'user',
                'content': verse_form.form_instruction(form, lang=lang),
            })
    if extra_user:
        messages.append({
            'role': 'user',
            'content': (
                f'User feedback: {extra_user}' if en else f'用户反馈：{extra_user}'
            ),
        })
    messages.append({
        'role': 'user',
        'content': (
            'Output ONLY the three-card JSON. '
            'title + poem + summary + choice labels: English only, zero Chinese characters. '
            'JSON keys stay English (summary/examples/choices/dims/...). '
            'Do not write long analysis prose.'
            if en else
            '只输出三卡对照JSON，不要Markdown长文。诗正文与选项文案用当前界面语言。'
        ),
    })

    buf = []
    degraded = False
    try:
        for delta, meta in llm.stream_complete_meta(
                messages, role='examples', temperature=temperature):
            if not run_still_valid(sid, token):
                yield sse_process('Interrupted' if en else '已中断', kind='warn')
                return
            if meta.get('degraded'):
                degraded = True
            buf.append(delta)
        if degraded:
            yield sse('degraded', {'message': 'Model unavailable — using local fallback cards' if en else '模型不可用，已使用本地降级三卡'})
            yield sse_process('Degraded mode' if en else '降级模式', kind='warn')

        content = ''.join(buf)
        payload = stage_schema.parse_examples_payload(content, lang=lang)

        if en and _examples_need_lang_retry(payload, lang) and run_still_valid(sid, token):
            yield sse_process('Cards were Chinese — regenerating in English', kind='warn')
            messages.append({
                'role': 'user',
                'content': (
                    'REJECTED: your poems/titles used Chinese. Rewrite the entire JSON now. '
                    'All three poems and titles must be English only. No 汉字.'
                ),
            })
            buf2 = []
            for delta, meta in llm.stream_complete_meta(
                    messages, role='examples', temperature=0.55):
                if not run_still_valid(sid, token):
                    return
                buf2.append(delta)
            payload2 = stage_schema.parse_examples_payload(''.join(buf2), lang=lang)
            if not _examples_need_lang_retry(payload2, lang):
                payload = payload2
            else:
                yield sse_process('Using English offline style cards', kind='warn')
                payload = stage_schema.parse_examples_payload(
                    stage_schema.FALLBACK_EXAMPLES_JSON_EN, lang='en')

        dims_avg = stage_schema.average_dims(payload.get('examples') or [])
        scores = ensure_seven_dims(
            {**dims_avg, 'overall': round(sum(dims_avg.values()) / max(1, len(dims_avg)), 1)})

        meta = load_stage_meta(session)
        meta['examples_payload'] = payload
        save_stage_meta(sid, meta)

        first_poem = ''
        if payload.get('examples'):
            first_poem = payload['examples'][0].get('poem') or ''

        summary = payload.get('summary') or (
            'Three style cards are ready — pick one.'
            if en else
            '三组对照已就绪，请点选卡片。'
        )
        chat_line = (
            f'{summary}\nPick a card or a numbered choice below.'
            if en else
            f'{summary}\n请点选下方示例卡或编号继续。'
        )
        append_chat(sid, 'assistant', chat_line)
        add_node(
            sid, session.get('current_node_id'),
            'Three style cards' if en else '三组风格对照',
            first_poem or chat_line, scores, 'examples')

        yield sse('intent', {'text': 'Cards ready — waiting for your pick' if en else '三组对照已生成，等待你点选'})
        yield sse_process(
            'Style cards ready — pick one' if en else '三组对照就绪 — 等待点选',
            waiting=True, kind='ask')
        yield sse('examples', payload)
        yield sse('radar', scores)
        if first_poem:
            yield sse('poem', {'from': '', 'to': first_poem, 'full': first_poem})
        yield sse('message', {'role': 'assistant', 'content': chat_line})
        yield sse('checkpoint', {
            'id': 'pick_example',
            'message': 'Pick a card to enter structure' if en else '请选择示例卡进入结构',
        })
    finally:
        set_idle(sid)


def _stream_canvas_loop(session, prefs, stage, temperature=0.7, extra_user=None,
                        lang=None, user_id=None, mode='fill', max_rounds=None):
    sid = str(session['id'])
    token = begin_run(sid)
    yield sse('stage', {'stage': stage, 'label': stage_label(stage, lang=lang)})
    yield sse_process(_tr(lang, f'画布微循环：{mode}', f'Canvas micro-loop: {mode}'))

    cv = load_canvas(session)
    target, session = migrate_session_targets(session, prefs)
    soft_skips = int(session.get('soft_ask_skips') or 0)
    neg = prefs.get('negative_feedback_history') or []
    recent_rejects = 1 if neg else 0

    try:
        if mode == 'skeleton' or not (cv.get('lines')):
            yield from _canvas_skeleton_round(
                session, prefs, sid, token, temperature, extra_user, lang, user_id,
                target, soft_skips, recent_rejects)
            # reload — may be awaiting
            session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
            if session.get('run_status') == 'awaiting':
                return
            cv = load_canvas(session)
            mode = 'fill'
            stage = session.get('stage') or 'symbols'
            # Refresh token if skeleton round ended idle
            if not run_still_valid(sid, token):
                token = begin_run(sid)

        # fill micro-loop — keep one in-memory canvas across rounds (persist each op)
        cv = load_canvas(session)
        form = verse_form.detect_verse_form(session, extra_user=extra_user)
        # Prefer locked form from canvas / meta if detect drifted to free
        if (not form or form.get('id') == 'free'):
            meta_f = load_stage_meta(session)
            fid = (cv.get('verse_form') or meta_f.get('verse_form') or '')
            if fid and fid in verse_form.FORMS:
                form = dict(verse_form.FORMS[fid])
        if form and form.get('lines'):
            cv['form_lock'] = True
            cv['verse_form'] = form.get('id')
            if form.get('chars_per_line'):
                cv['chars_per_line'] = int(form['chars_per_line'])
            ok_c, n_c, exp_c = verse_form.line_count_ok(cv, form)
            need_reset = (not ok_c) or (n_c != int(form['lines']))
            cpl = form.get('chars_per_line')
            if cpl and not need_reset:
                # Wrong slot density (e.g. 3 slots → 6 chars) must reset
                for ln in (cv.get('lines') or []):
                    nslot = len(ln.get('slots') or [])
                    if nslot != int(cpl):
                        need_reset = True
                        break
                    if verse_form.line_zh_char_count(ln) > int(cpl):
                        need_reset = True
                        break
            if need_reset:
                # Prefer re-seeding from selected card / readable draft over empty form skeleton
                meta_seed = load_stage_meta(session)
                sel_poem = ''
                if isinstance(meta_seed.get('selected_example'), dict):
                    sel_poem = (meta_seed['selected_example'].get('poem') or '').strip()
                readable = poem_canvas.canvas_readable_text(cv, lang=lang)
                seed_src = sel_poem or readable
                reseeds = None
                if seed_src and not meta_seed.get('skip_example_seed') and (
                        cv.get('seeded_from_example') or sel_poem):
                    reseeds = poem_canvas.seed_canvas_from_poem(seed_src, form=form, lang=lang)
                if reseeds and reseeds.get('lines'):
                    # Drop phantom empty DET/PREP inside already-filled lines
                    if reseeds.get('seeded_from_example'):
                        reseeds = poem_canvas.canvas_compact_empties_soft(reseeds)
                        reseeds['seeded_from_example'] = True
                        if form.get('id'):
                            reseeds['verse_form'] = form.get('id')
                            reseeds['form_lock'] = True
                    cv = reseeds
                    save_canvas(sid, cv)
                    yield sse_process(
                        _tr(lang,
                            f'已按体裁重排样例底稿（{len(cv.get("lines") or [])}行，保留诗句）',
                            f'Re-slotted style-card draft '
                            f'({len(cv.get("lines") or [])} lines, kept verse)'),
                        kind='ok')
                    yield sse('canvas', cv)
                else:
                    yield sse_process(
                        _tr(lang,
                            f'填词前体裁不符（{n_c}行/{exp_c}'
                            + (f'，每句{cpl}字' if cpl else '')
                            + '），重置锁定骨架',
                            f'Form mismatch before fill ({n_c}/{exp_c}'
                            + (f', {cpl} chars/line' if cpl else '')
                            + ') — reset locked skeleton'),
                        kind='warn')
                    cv = verse_form.skeleton_for_form(form, lang=lang)
                    save_canvas(sid, cv)
                    yield sse('canvas', cv)
            else:
                save_canvas(sid, cv)
        # Scrub phrase-dumped slots left by older runs (skip when seeded — text already OK)
        scrubbed = 0
        if not cv.get('seeded_from_example'):
            for ln in cv.get('lines') or []:
                for s in ln.get('slots') or []:
                    t = (s.get('text') or '').strip()
                    if not t:
                        continue
                    ok_t, _r = poem_canvas.fill_text_ok(t, s.get('pos'), lang=lang)
                    if not ok_t:
                        s['text'] = ''
                        s['status'] = 'empty'
                        scrubbed += 1
            if scrubbed:
                save_canvas(sid, cv)
                yield sse_process(
                    _tr(lang, f'已清空 {scrubbed} 个整句灌槽', f'Cleared {scrubbed} phrase-dumped slots'),
                    kind='warn')
                yield sse('canvas', cv)

        empty_n = sum(
            1 for ln in (cv.get('lines') or [])
            for s in (ln.get('slots') or [])
            if not (s.get('text') or '').strip()
        )
        seeded = bool(cv.get('seeded_from_example'))
        ratio_start = _fill_ratio(cv)
        light_revise = seeded or ratio_start >= 0.85
        # Seeded drafts: drop phantom empty DET chips before light-revise UI
        if seeded and any(
                not (s.get('text') or '').strip()
                for ln in (cv.get('lines') or [])
                for s in (ln.get('slots') or [])):
            cv = poem_canvas.canvas_compact_empties_soft(cv)
            cv['seeded_from_example'] = True
            save_canvas(sid, cv)
            yield sse('canvas', cv)
            empty_n = sum(
                1 for ln in (cv.get('lines') or [])
                for s in (ln.get('slots') or [])
                if not (s.get('text') or '').strip()
            )
            ratio_start = _fill_ratio(cv)
            light_revise = True
        # Aim to fill ~6–10 slots per round; more empty → more rounds before asking
        if max_rounds is None:
            # Batch eval can cap rounds via env to finish 100 runs in reasonable time
            env_cap = os.environ.get('BATCH_MAX_FILL_ROUNDS', '').strip()
            if env_cap.isdigit():
                rounds = max(3, min(24, int(env_cap)))
            elif light_revise:
                # Seeded / nearly full: few light-revise rounds, then advance
                rounds = 3 if stage != 'final' else 2
            else:
                rounds = min(24, max(10, (empty_n + 7) // 6))
        else:
            rounds = max_rounds if mode == 'fill' else 0
        last_intent = ''
        stagnant = 0
        for round_i in range(rounds):
            if not run_still_valid(sid, token):
                yield sse_process(_tr(lang, '已中断', 'Interrupted'), kind='warn')
                return
            ratio0 = _fill_ratio(cv)
            empties_now = _empty_slots(cv)
            # Still have holes: do not early-exit light revise until they are filled or dropped
            if light_revise and ratio0 >= 0.85 and stage != 'final' and round_i >= 1 and not empties_now:
                yield sse_process(
                    _tr(lang, '底稿已满，结束本阶段轻改', 'Seeded draft full — end light revise'),
                    kind='ok')
                break
            op_lo, op_hi = ((2, 5) if light_revise else ((6, 12) if ratio0 < 0.55 else (4, 8)))
            yield sse_process(
                _tr(lang,
                    f'{"轻改" if light_revise else "填词"}轮次 {round_i + 1}',
                    f'{"Light revise" if light_revise else "Fill"} round {round_i + 1}'))
            # refresh session row for chat_log etc., but canvas comes from `cv` / DB
            session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,)) or session
            messages = build_context(session, prefs, stage=stage, lang=lang, user_id=user_id)
            form_bit = verse_form.form_instruction(form, lang=lang) if form else ''
            empty_hint = ''
            if empties_now:
                bits = ', '.join(f'{eid or "?"}({pos})@L{li+1}' for eid, pos, li in empties_now[:12])
                empty_hint = (
                    f'EMPTY SLOTS (fill these slot_id exactly, do not invent new empties): {bits}\n'
                    if _lang_en(lang) else
                    f'空槽必须按 slot_id 填（禁止再制造空DET）：{bits}\n'
                )
            # Explicit current canvas text so model does not invent from stale memory
            if light_revise:
                fill_instr_en = (
                    f'Current stage {stage}. Fill ratio {ratio0:.0%}. {form_bit}\n'
                    f'Current canvas (LIGHT REVISE — keep the seeded draft):\n'
                    f'{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                    f'{empty_hint}'
                    f'HARD RULES:\n'
                    f'(1) If EMPTY SLOTS listed: fill/replace THOSE slot_ids first with matching POS words.\n'
                    f'(2) Prefer replace on EXISTING filled slots; NEVER revise_syntax with blank slots; '
                    f'never create new empty DET/PREP chips.\n'
                    f'(3) Do NOT abandon the selected style-card breath or core images.\n'
                    f'(4) ASSOCIATION hooks; STYLE BANS (no not…but / itself).\n'
                    f'(5) Output {op_lo}-{op_hi} replace/fill JSON ops only — no init/rebuild.\n'
                    f'(6) TOPIC FIT: stay on user topic.\n'
                    f'User feedback: {extra_user or "none"}'
                )
                fill_instr_zh = (
                    f'当前阶段 {stage}。完成度 {ratio0:.0%}。{form_bit}\n'
                    f'当前画布（轻改模式 — 保留样例底稿）：\n'
                    f'{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                    f'{empty_hint}'
                    f'硬性要求：\n'
                    f'(1) 若有空槽列表：必须先按 slot_id 填满，POS 匹配。\n'
                    f'(2) 优先 replace；禁止 revise_syntax 带空槽；禁止再造空 DET/PREP。\n'
                    f'(3) 禁止抛弃已选样例卡的呼吸与核心意象。\n'
                    f'(4) 强关联；禁不是…而是…/本身强调。\n'
                    f'(5) 输出{op_lo}-{op_hi}个 replace/fill JSON ops，禁止 init。\n'
                    f'(6) 主题贴合用户意图。\n'
                    f'用户反馈：{extra_user or "无"}'
                )
            else:
                fill_instr_en = (
                    f'Current stage {stage}. Fill ratio {ratio0:.0%}. {form_bit}\n'
                    f'Current canvas (edit only; do NOT init/rebuild/add_line/drop_line):\n'
                    f'{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                    f'HARD RULES:\n'
                    f'(1) ONE slot = ONE short word/phrase matching that slot POS — '
                    f'NEVER put a whole clause into one slot (no commas, no "then …").\n'
                    f'(2) Left-to-right: ASSOCIATION hooks (same-field / sense / cause / subject / contrast-formula). '
                    f'Grammar optional; cross-field salad = fail. Tag hook in intent '
                    f'(e.g. \"same-field:rain-street|emotion:cold-wet\"). '
                    f'Never end a line on the/a/an/of/in/from.\n'
                    f'(3) DUP REJECT: never reuse a content word in the poem; '
                    f'no glued doubles (presses presses / 绕出绕出). System rejects such ops.\n'
                    f'(4) Never duplicate an entire line.\n'
                    f'(5) Output {op_lo}-{op_hi} fill/replace on EMPTY slots only.\n'
                    f'(6) STYLE BANS: no "not … but …"; no itself/the very/本身/恰恰 emphasis crutches.\n'
                    f'(7) ANTI-STIFF: vary adjacent syntax; max one hard tension verb per line; '
                    f'link images with ASSOCIATION hooks.\n'
                    f'(8) TOPIC FIT: images must serve the user topic; do not default to '
                    f'station/rail/bronze/frost-needle stock props unless asked.\n'
                    f'(9) No empty slots or □.\n'
                    + (
                        f'(10) Regulated verse: each line MUST total exactly {form.get("chars_per_line")} '
                        f'Chinese characters (one char per slot; never 3×2-char=6).\n'
                        if form and form.get('chars_per_line') else ''
                    )
                    + f'User feedback: {extra_user or "none"}'
                )
                fill_instr_zh = (
                    f'当前阶段 {stage}。完成度 {ratio0:.0%}。{form_bit}\n'
                    f'当前画布（必须在此基础上改，禁止 init/add_line/drop_line）：\n'
                    f'{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                    f'硬性要求：\n'
                    f'(1) 一槽一词/短词组，必须匹配该槽 POS；禁止把整句塞进一个槽（禁逗号、禁罗列）。\n'
                    f'(2) 强关联：相邻意象须同场或同情绪公式（蒙太奇可以无语法粘合）；'
                    f'intent 标注钩子如「同场:雨夜街｜情绪:冷湿」；'
                    f'禁止跨场乱接；禁止行尾停在「的/了/着」。\n'
                    f'(3) 叠词硬驳回：全诗实词不得重复；禁止绕出绕出/渗进渗进；系统会直接拒绝。\n'
                    f'(4) 禁止整行复制。\n'
                    f'(5) 输出{op_lo}-{op_hi}个fill/replace填空槽。\n'
                    f'(6) 风格禁忌：禁不是…而是…/not…but…；禁本身/itself 堆强调。\n'
                    f'(7) 反死板：邻行句式须变奏；每行最多一个强张力动词。\n'
                    f'(8) 主题贴合：意象必须服务用户主题；非站台/铁路题材禁止默认「站牌/铁轨/铜钟/霜针/耳廓」套路。\n'
                    f'(9) 禁止空槽与「□」。\n'
                    + (
                        f'(10) 格律硬约束：每句汉字合计必须恰好 {form.get("chars_per_line")} 字'
                        f'（一槽一字；禁止三槽双字凑成六字行）。\n'
                        if form and form.get('chars_per_line') else ''
                    )
                    + f'用户反馈：{extra_user or "无"}'
                )
            messages.append({
                'role': 'user',
                'content': fill_instr_en if _lang_en(lang) else fill_instr_zh,
            })
            buf = []
            degraded = False
            for delta, meta in llm.stream_complete_meta(
                    messages, role='canvas', temperature=temperature):
                if not run_still_valid(sid, token):
                    return
                if meta.get('degraded'):
                    degraded = True
                buf.append(delta)
            if degraded:
                yield sse('degraded', {
                    'message': _tr(lang, '模型不可用，已使用本地降级', 'Model unavailable — local fallback')
                })
                yield sse_process(_tr(lang, '降级模式', 'Degraded mode'), kind='warn')

            parsed = poem_canvas.parse_ops_json(''.join(buf))
            if not parsed:
                # retry once with harder instruction
                yield sse_process(_tr(lang, 'JSON解析失败，重试', 'JSON parse failed — retry'), kind='warn')
                messages.append({
                    'role': 'user',
                    'content': (
                        'Previous output was not valid JSON. Output ONLY an ops-array JSON.'
                        if _lang_en(lang) else
                        '上一次不是合法JSON，只输出ops数组JSON。'
                    )
                })
                buf = []
                for delta, meta in llm.stream_complete_meta(
                        messages, role='canvas', temperature=0.4):
                    if not run_still_valid(sid, token):
                        return
                    buf.append(delta)
                parsed = poem_canvas.parse_ops_json(''.join(buf))

            if not parsed:
                # degrade destroys locked forms — skip
                if cv.get('form_lock') or (form and form.get('lines')):
                    stagnant += 1
                    yield sse_process(
                        _tr(lang, '解析失败且体裁已锁，跳过本轮', 'Parse failed with form lock — skip round'),
                        kind='warn')
                    if stagnant >= 3:
                        break
                    continue
                yield sse_process(_tr(lang, '降级为整段填词', 'Fallback: whole-draft fill'), kind='warn')
                yield from _canvas_degrade_fill(session, prefs, sid, token, stage, lang, user_id)
                return

            ops = poem_canvas.ensure_op_ids(parsed.get('ops') or [])
            applied_any = False
            for op in ops:
                if not run_still_valid(sid, token):
                    return
                # During fill, refuse init / line-count edits that break form
                kind = op.get('type') or op.get('op') or ''
                if kind in ('init', 'canvas_init') and (cv.get('lines') or []):
                    yield sse_process(
                        _tr(lang, '忽略会清空画布的 init', 'Ignored init that would clear the canvas'),
                        kind='warn')
                    continue
                if kind in ('add_line', 'drop_line') and (cv.get('form_lock') or (form and form.get('lines'))):
                    yield sse_process(
                        _tr(lang, '体裁已锁，忽略增删行', 'Form locked — ignored add/drop line'),
                        kind='warn')
                    continue
                if kind == 'revise_syntax' and (cv.get('form_lock') or (form and form.get('lines'))):
                    yield sse_process(
                        _tr(lang, '体裁已锁，忽略整行重写', 'Form locked — ignored line rewrite'),
                        kind='warn')
                    continue
                intent = op.get('intent') or op.get('念头') or ''
                cv, ok, msg = poem_canvas.apply_op(cv, op)
                if not ok:
                    # Hard-reject duplicate wording — surface clearly to the process log
                    dup_reasons = {
                        'adjacent_dup', 'line_dup', 'poem_dup', 'slot_internal_dup',
                    }
                    if msg in dup_reasons:
                        yield sse_process(
                            _tr(lang,
                                f'驳回叠词：{msg}（{op.get("text") or ""}）',
                                f'Rejected duplicate: {msg} ({op.get("text") or ""})'),
                            kind='warn')
                    else:
                        yield sse_process(
                            _tr(lang, f'跳过非法op：{msg}', f'Skipped invalid op: {msg}'),
                            kind='warn')
                    if intent:
                        yield sse_process(
                            _tr(lang, f'念头未落地：{intent}', f'Intent not applied: {intent}'),
                            kind='warn')
                    continue
                last_intent = intent or last_intent
                save_canvas(sid, cv)
                applied_any = True
                changed = op.get('text') or op.get('value') or kind
                if intent:
                    yield sse('intent', {'text': intent})
                    yield sse_process(
                        _tr(lang, f'念头：{intent} → {changed}', f'Intent: {intent} → {changed}'),
                        kind='ok')
                else:
                    yield sse_process(
                        _tr(lang, f'改动：{kind} {changed}', f'Change: {kind} {changed}'),
                        kind='ok')
                yield sse('op', {'op': op, 'canvas': cv})
                yield sse('canvas', cv)
                # learn verbs
                if user_id and (op.get('type') in ('fill', 'replace')):
                    text_v = (op.get('text') or '').strip()
                    found = poem_canvas.find_slot(cv, op.get('slot_id') or op.get('id'))
                    pos = (found[2].get('pos') if found else op.get('pos')) or ''
                    if text_v and str(pos).upper() == 'V':
                        try:
                            preferences.note_verbs(user_id, [text_v])
                        except Exception:
                            pass
                poem_text = poem_canvas.canvas_filled_text(cv)
                scores = score_poem(poem_text, sid, target=target)
                yield sse('radar', scores)
                yield sse('poem', {'from': '', 'to': poem_text, 'full': poem_text})

            if not applied_any:
                stagnant += 1
                yield sse_process(
                    _tr(lang, '本轮无有效改动，保留当前诗面', 'No valid edits this round — keeping draft'),
                    kind='warn')
                # re-push current canvas so UI stays in sync
                yield sse('canvas', cv)
                yield sse('poem', {
                    'from': '',
                    'to': poem_canvas.canvas_filled_text(cv),
                    'full': poem_canvas.canvas_filled_text(cv),
                })
                if stagnant >= 2 and _fill_ratio(cv) >= 0.55 and not _empty_slots(cv):
                    break
            else:
                stagnant = 0

            # Always strip mid-line empty DET/PREP chips so UI matches readable verse
            if _empty_slots(cv):
                before_e = len(_empty_slots(cv))
                cv = _drop_midline_empties(cv)
                after_e = len(_empty_slots(cv))
                if after_e < before_e:
                    save_canvas(sid, cv)
                    yield sse('canvas', cv)
                    yield sse_process(
                        _tr(lang,
                            f'已去掉行内空槽 {before_e - after_e} 个',
                            f'Dropped {before_e - after_e} mid-line empty slots'),
                        kind='ok')
                    poem_text = poem_canvas.canvas_readable_text(cv, lang=lang)
                    yield sse('poem', {'from': '', 'to': poem_text, 'full': poem_text})

            # Global 成句 + structure check every 2 rounds
            if (round_i + 1) % 2 == 0:
                issues = poem_canvas.canvas_structure_issues(cv, form=form, lang=lang)
                complete = poem_canvas.canvas_complete_lines_text(cv, lang=lang)
                if complete and complete.count('\n') + 1 >= 1:
                    g_scores = score_poem(complete, sid, target=target)
                    coh_g = float(g_scores.get('coherence') or 0)
                    yield sse_process(
                        _tr(lang,
                            f'全局成句检查 — 逻辑 {coh_g:.0f}'
                            + (f' · 结构问题 {len(issues)}' if issues else ''),
                            f'Global sentence check — logic {coh_g:.0f}'
                            + (f' · {len(issues)} structure issues' if issues else '')),
                        kind='info')
                    yield sse('radar', g_scores)
                    need_fix = coh_g < 52 or any(
                        x.startswith(('hang_line', 'dup_line', 'bad_slot', 'line_count'))
                        for x in issues
                    )
                    if need_fix and run_still_valid(sid, token):
                        yield sse_process(
                            _tr(lang, '强关联/结构偏弱，本轮强制修补',
                                'Weak association/structure — forced repair this round'),
                            kind='warn')
                        repair_msgs = build_context(
                            session, prefs, stage=stage, lang=lang, user_id=user_id)
                        issue_s = ', '.join(issues[:8]) if issues else 'coherence'
                        repair_msgs.append({
                            'role': 'user',
                            'content': (
                                f'ASSOCIATION / STRUCTURE REPAIR ({issue_s}). '
                                'replace/fill ONLY — one short token per slot matching POS. '
                                'Link neighboring images (space/sense/cause/subject/time); '
                                'fragments OK if linked. DUP REJECT any repeated content word. '
                                'No add_line/drop_line/init. '
                                f'Complete lines now:\n{complete}\n'
                                f'Full canvas:\n{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                                'Output 4-10 replace/fill JSON ops only.'
                                if _lang_en(lang) else
                                f'全局强关联/结构修复（{issue_s}）。只允许 replace/fill：一槽一词，匹配 POS。'
                                '相邻意象必须有空间/感官/因果/主体/时间钩子；允许短行；'
                                '叠词必须换掉。禁止增删行/init。\n'
                                f'已满行：\n{complete}\n'
                                f'整幅画布：\n{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                                '只输出4-10个 replace/fill JSON ops。'
                            ),
                        })
                        rbuf = []
                        for delta, meta in llm.stream_complete_meta(
                                repair_msgs, role='canvas', temperature=0.4):
                            if not run_still_valid(sid, token):
                                break
                            rbuf.append(delta)
                        rparsed = poem_canvas.parse_ops_json(''.join(rbuf))
                        if rparsed:
                            for op in poem_canvas.ensure_op_ids(rparsed.get('ops') or []):
                                kind = op.get('type') or op.get('op') or ''
                                if kind in ('init', 'canvas_init', 'add_line', 'drop_line'):
                                    continue
                                cv, ok, _ = poem_canvas.apply_op(cv, op)
                                if ok:
                                    save_canvas(sid, cv)
                                    yield sse('op', {'op': op, 'canvas': cv})
                                    yield sse('canvas', cv)
                            poem_text = poem_canvas.canvas_filled_text(cv, lang=lang)
                            scores = score_poem(poem_text, sid, target=target)
                            yield sse('radar', scores)
                            yield sse_process(score_brief(scores, lang=lang), kind='info')

            # Soft gate only after several rounds AND when mostly filled + coherent enough to judge
            if (round_i + 1) % 4 == 0:
                ratio = _fill_ratio(cv)
                poem_text = poem_canvas.canvas_filled_text(cv, lang=lang)
                scores = score_poem(poem_text, sid, target=target)
                try:
                    record_attempt(sid, cv, scores, stage, thought=last_intent, poem=poem_text)
                except Exception:
                    pass
                yield sse_process(score_brief(scores, lang=lang), kind='info')
                if ratio < 0.72 or float(scores.get('coherence') or 0) < 52:
                    yield sse_process(
                        _tr(lang,
                            f'完成度 {ratio:.0%} / 逻辑 {scores.get("coherence", 0):.0f}，继续改',
                            f'Fill {ratio:.0%} / logic {scores.get("coherence", 0):.0f} — keep going'),
                        kind='ok')
                else:
                    decision = ckpt.gate_decision(
                        scores, target,
                        recent_rejects=0,
                        vague_user=ckpt.user_message_vague(extra_user),
                        soft_ask_skips=soft_skips,
                        filled_ratio=ratio,
                        kind='fill',
                    )
                    if decision['action'] == 'ask':
                        try:
                            record_attempt(
                                sid, cv, scores, stage,
                                thought=last_intent, poem=poem_text, force=True)
                        except Exception:
                            pass
                        set_awaiting(sid, 'stage_review', token)
                        ask = _tr(
                            lang,
                            '当前稿接近目标，但不确定是否合你意——继续改，还是确认？',
                            'Close to target, but unsure it fits you — keep revising, or confirm?',
                        )
                        yield sse('checkpoint', {
                            'id': 'stage_review',
                            'message': ask,
                            'reason': decision['reason'],
                            'deviation': decision.get('deviation'),
                        })
                        yield sse_process(ask, waiting=True, kind='ask')
                        append_chat(sid, 'assistant', ask)
                        yield sse('message', {'role': 'assistant', 'content': ask})
                        return

            session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
            if _empty_slots(cv):
                cv = _drop_midline_empties(cv)
                save_canvas(sid, cv)
                yield sse('canvas', cv)
            if not _empty_slots(cv) and _fill_ratio(cv) >= 0.92:
                yield sse_process(
                    _tr(lang, '槽位已基本填满', 'Slots mostly filled'),
                    kind='ok')
                break

        # finalize node
        cv = load_canvas(session)
        poem_text = poem_canvas.canvas_filled_text(cv)
        scores = score_poem(poem_text, sid, target=target)
        ratio = _fill_ratio(cv)
        # Keyword salad → force logic-repair rounds before stopping
        if float(scores.get('coherence') or 0) < 55 and run_still_valid(sid, token):
            yield sse_process(
                _tr(lang, '逻辑偏弱，强制连贯修订', 'Logic weak — forced coherence revise'),
                kind='warn')
            yield sse_process(score_brief(scores, lang=lang), kind='info')
            for _fix in range(3):
                if not run_still_valid(sid, token):
                    break
                messages = build_context(session, prefs, stage=stage, lang=lang, user_id=user_id)
                messages.append({
                    'role': 'user',
                    'content': (
                        'ASSOCIATION REPAIR: images are unlinked (keyword salad). '
                        'Replace/fill so neighboring images share space/sense/cause/subject/time. '
                        'Fragments OK if linked. HARD REJECT any duplicate content word. '
                        f'Current canvas:\n{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                        'Output 4-10 replace/fill JSON ops only.'
                        if _lang_en(lang) else
                        '强关联修复：当前像无钩子词表。请 replace/fill，'
                        '让相邻意象有空间/感官/因果/主体/时间关系；'
                        '允许短行，禁止无关联堆砌；叠词一律换掉。\n'
                        f'当前画布：\n{poem_canvas.canvas_to_text(cv, lang=lang)}\n'
                        '只输出4-10个 replace/fill JSON ops。'
                    ),
                })
                buf = []
                for delta, meta in llm.stream_complete_meta(
                        messages, role='canvas', temperature=0.45):
                    if not run_still_valid(sid, token):
                        break
                    buf.append(delta)
                parsed = poem_canvas.parse_ops_json(''.join(buf))
                if not parsed:
                    continue
                for op in poem_canvas.ensure_op_ids(parsed.get('ops') or []):
                    kind = op.get('type') or op.get('op') or ''
                    if kind in ('init', 'canvas_init'):
                        continue
                    cv, ok, _ = poem_canvas.apply_op(cv, op)
                    if ok:
                        save_canvas(sid, cv)
                        yield sse('op', {'op': op, 'canvas': cv})
                        yield sse('canvas', cv)
                poem_text = poem_canvas.canvas_filled_text(cv)
                scores = score_poem(poem_text, sid, target=target)
                yield sse('radar', scores)
                yield sse_process(score_brief(scores, lang=lang), kind='info')
                if float(scores.get('coherence') or 0) >= 55:
                    break

        # Final stage: whole-poem polish locked to selected style card
        if stage == 'final' and run_still_valid(sid, token):
            yield from _final_whole_poem_polish(
                session, prefs, sid, token, cv, form, lang, user_id, target)
            cv = load_canvas(session)
            poem_text = poem_canvas.canvas_readable_text(cv, lang=lang) or poem_text
            scores = score_poem(poem_text, sid, target=target)
            light_revise = light_revise or bool(cv.get('seeded_from_example'))

        # Drop unfilled slots so □ never reaches the user-facing poem
        before_empty = sum(
            1 for ln in (cv.get('lines') or [])
            for s in (ln.get('slots') or [])
            if not (s.get('text') or '').strip()
        )
        if before_empty:
            # Always strip mid-line ghosts; soft keeps form line-count placeholders only
            cv = _drop_midline_empties(cv)
            still = sum(
                1 for ln in (cv.get('lines') or [])
                for s in (ln.get('slots') or [])
                if not (s.get('text') or '').strip()
            )
            if still and not (cv.get('form_lock') or cv.get('seeded_from_example')):
                cv = poem_canvas.canvas_compact_empties(cv)
                still = 0
            save_canvas(sid, cv)
            yield sse('canvas', cv)
            yield sse_process(
                _tr(lang,
                    f'已清理空槽（原 {before_empty}）',
                    f'Cleared empty slots (was {before_empty})'),
                kind='ok')
            poem_text = poem_canvas.canvas_readable_text(cv, lang=lang)
            scores = score_poem(poem_text, sid, target=target)
        else:
            poem_text = poem_canvas.canvas_readable_text(cv, lang=lang) or poem_text

        thought = last_intent or _tr(lang, '画布本轮修订', 'Canvas revise this round')
        add_node(sid, session.get('current_node_id'), thought, poem_text, scores, stage)
        summary = _tr(
            lang,
            f'本阶段已更新诗稿（{stage_label(stage, lang=lang)}）。'
            f'逻辑{scores.get("coherence", 0):.0f}/拟合{scores.get("fit", 0):.0f}/综合{scores.get("overall", 0):.0f}。{thought}',
            f'Stage updated ({stage_label(stage, lang=lang)}). '
            f'Logic {scores.get("coherence", 0):.0f} / fit {scores.get("fit", 0):.0f} / '
            f'overall {scores.get("overall", 0):.0f}. {thought}',
        )
        append_chat(sid, 'assistant', summary)
        yield sse('message', {'role': 'assistant', 'content': summary})
        yield sse('radar', scores)
        yield sse_process(score_brief(scores, lang=lang), kind='ok')
        yield sse_process(_tr(lang, '本阶段画布完成', 'Stage canvas done'), kind='ok')

        coh = float(scores.get('coherence') or 0)
        decision = ckpt.gate_decision(
            scores, target,
            recent_rejects=recent_rejects,
            vague_user=ckpt.user_message_vague(extra_user),
            soft_ask_skips=soft_skips,
            filled_ratio=ratio,
            kind='fill',
        )
        # Prefer auto-advance: don't stall the pipeline unless truly uncertain & coherent enough to judge
        should_auto = (
            ratio >= 0.65 and coh >= 52 and (
                decision['action'] == 'auto'
                or soft_skips >= 1
                or float(scores.get('fit') or 0) >= 62
                or coh >= 65
            )
        )
        # Seeded / light-revise path: never chain symbols→verbs→final in one turn
        if light_revise or seeded or cv.get('seeded_from_example'):
            should_auto = False
        if should_auto:
            nxt = {'structure': 'symbols', 'symbols': 'verbs', 'verbs': 'final'}.get(stage)
            if nxt:
                db.execute(
                    'UPDATE poem_sessions SET stage = %s, run_status = %s, checkpoint_id = NULL, '
                    'updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                    (nxt, 'idle', sid))
                yield sse('stage', {'stage': nxt, 'label': stage_label(nxt, lang=lang)})
                yield sse_process(
                    _tr(lang, f'自动进入：{stage_label(nxt, lang=lang)}',
                        f'Auto-advance: {stage_label(nxt, lang=lang)}'),
                    kind='ok')
                session = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,))
                # Keep going in the same turn so the pipeline does not stall
                yield from _stream_canvas_loop(
                    session, prefs, nxt, temperature=temperature,
                    extra_user=extra_user, lang=lang, user_id=user_id, mode='fill')
                return
            set_idle(sid)
            return

        # Seeded: advance stage pointer but wait for user before next fill
        if (light_revise or seeded or cv.get('seeded_from_example')) and stage in (
                'symbols', 'verbs'):
            nxt = {'symbols': 'verbs', 'verbs': 'final'}.get(stage)
            if nxt:
                db.execute(
                    'UPDATE poem_sessions SET stage = %s, run_status = %s, checkpoint_id = NULL, '
                    'updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                    (nxt, 'idle', sid))
                yield sse('stage', {'stage': nxt, 'label': stage_label(nxt, lang=lang)})

        set_awaiting(sid, 'stage_review', token)
        ask = _tr(
            lang,
            f'综合{scores.get("overall", 0):.0f}（逻辑{coh:.0f}·拟合{scores.get("fit", 0):.0f}）。确认下一步，或继续微调？',
            f'Overall {scores.get("overall", 0):.0f} (logic {coh:.0f} · fit {scores.get("fit", 0):.0f}). '
            f'Confirm next step, or keep tweaking?',
        )
        yield sse('checkpoint', {'id': 'stage_review', 'message': ask})
        yield sse_process(ask, waiting=True, kind='ask')
    except GeneratorExit:
        interrupt_session(user_id, sid) if user_id else set_idle(sid)
        raise
    except Exception as e:
        yield sse('error', {'message': str(e)})
        set_idle(sid)


def _empty_slots(cv):
    """Return list of (slot_id, pos, line_index) for unfilled slots."""
    out = []
    for li, ln in enumerate((cv or {}).get('lines') or []):
        for s in (ln.get('slots') or []):
            if not (s.get('text') or '').strip():
                out.append((s.get('id') or '', normalize_pos_safe(s.get('pos')), li))
    return out


def normalize_pos_safe(pos):
    try:
        return poem_canvas.normalize_pos(pos)
    except Exception:
        return str(pos or 'X')


def _drop_midline_empties(cv):
    """Remove empty chips inside non-empty lines; keep seeded/form flags."""
    seeded = bool((cv or {}).get('seeded_from_example'))
    form_lock = bool((cv or {}).get('form_lock'))
    vf = (cv or {}).get('verse_form')
    cpl = (cv or {}).get('chars_per_line')
    cv2 = poem_canvas.canvas_compact_empties_soft(cv)
    if seeded:
        cv2['seeded_from_example'] = True
    if form_lock:
        cv2['form_lock'] = True
    if vf:
        cv2['verse_form'] = vf
    if cpl:
        cv2['chars_per_line'] = cpl
    return cv2


def _fill_ratio(cv):
    slots = [s for ln in (cv.get('lines') or []) for s in (ln.get('slots') or [])]
    if not slots:
        return 0.0
    filled = sum(1 for s in slots if (s.get('text') or '').strip())
    return filled / len(slots)


def _lang_en(lang):
    return (lang or 'zh').startswith('en')


def _tr(lang, zh, en):
    return en if _lang_en(lang) else zh


def _apply_skeleton_init(parsed, form=None, lang=None):
    """Apply first init op from parsed JSON; fallback to form-aware local template."""
    cv = poem_canvas.empty_canvas()
    if parsed:
        ops = poem_canvas.ensure_op_ids(parsed.get('ops') or [])
        for op in ops:
            if op.get('type') in ('init', 'canvas_init') or op.get('lines'):
                if 'type' not in op:
                    op['type'] = 'init'
                cv, ok, _ = poem_canvas.apply_op(cv, op)
                break
    if not cv.get('lines'):
        if form and form.get('lines'):
            cv = verse_form.skeleton_for_form(form, lang=lang)
        else:
            cv = poem_canvas.skeleton_from_text('')
    return cv


def _canvas_skeleton_round(session, prefs, sid, token, temperature, extra_user, lang, user_id,
                           target, soft_skips, recent_rejects):
    en = _lang_en(lang)
    # Reload from DB so locked form survives stale in-memory session dicts
    row = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s', (sid,)) or session
    form = verse_form.detect_verse_form(row, extra_user=extra_user)
    meta = load_stage_meta(row)
    if form and form.get('id') != 'free':
        meta['verse_form'] = form['id']
        save_stage_meta(sid, meta)
        yield sse_process(
            _tr(lang,
                f'体裁锁定：{form["id"]}（目标 {form.get("lines") or str(form.get("line_min"))+"–"+str(form.get("line_max"))} 行）',
                f'Form locked: {form["id"]} '
                f'(target {form.get("lines") or str(form.get("line_min"))+"–"+str(form.get("line_max"))} lines)'),
            kind='ok')
    form_line = verse_form.form_instruction(form, lang=lang)

    # Prefer selected example poem as prefilled canvas (keep card quality)
    sel = meta.get('selected_example') or {}
    seed_poem = ''
    if isinstance(sel, dict) and not meta.get('skip_example_seed'):
        seed_poem = (sel.get('poem') or '').strip()
    if seed_poem:
        cv = poem_canvas.seed_canvas_from_poem(seed_poem, form=form, lang=lang)
        if cv and (cv.get('lines') or []):
            # Remove mid-line empty DET/PREP ghosts so chips match readable verse
            cv = poem_canvas.canvas_compact_empties_soft(cv)
            cv['seeded_from_example'] = True
            if form and form.get('lines'):
                cv['form_lock'] = True
                cv['verse_form'] = form.get('id')
            logic = poem_canvas.skeleton_logic_report(cv)
            n_lines = len(cv.get('lines') or [])
            intent = _tr(
                lang,
                f'自样例卡导入底稿（{n_lines}行）',
                f'Seeded from style card ({n_lines} lines)',
            )
            yield sse_process(
                _tr(lang,
                    f'已用选中样例卡作底稿 · {n_lines} 行'
                    + (f' · 体裁 {form.get("id")}' if form and form.get('id') != 'free' else ''),
                    f'Seeded canvas from selected card · {n_lines} lines'
                    + (f' · form {form.get("id")}' if form and form.get('id') != 'free' else '')),
                kind='ok')
            save_canvas(sid, cv)
            yield sse('intent', {'text': intent})
            yield sse_process(_tr(lang, f'念头：{intent}', f'Intent: {intent}'))
            yield sse('canvas_init', cv)
            yield sse('canvas', cv)
            yield sse('op', {'op': {'type': 'init'}, 'canvas': cv})
            poem_text = (
                poem_canvas.canvas_readable_text(cv, lang=lang)
                or poem_canvas.canvas_to_text(cv, lang=lang)
            )
            yield sse('poem', {'from': '', 'to': poem_text, 'full': poem_text})
            score_prefs = dict(prefs or {})
            score_prefs['_verse_form'] = form
            scores = poem_canvas.structure_score(cv, score_prefs)
            scores = ensure_seven_dims({
                **scores,
                'overall': max(float(scores.get('overall') or 0), 72),
                'coherence': max(float(scores.get('coherence') or 0), 70),
            })
            yield sse('radar', scores)
            yield sse_process(
                _tr(lang,
                    f'底稿检查 — {n_lines} 行 · 成句 {logic["ok_n"]}/{logic["n"]}'
                    + (f' · 体裁 {form.get("id")}' if form and form.get('id') != 'free' else ''),
                    f'Seed check — {n_lines} lines · clause {logic["ok_n"]}/{logic["n"]}'
                    + (f' · form {form.get("id")}' if form and form.get('id') != 'free' else '')),
                kind='info')
            add_node(sid, session.get('current_node_id'), intent, poem_text, scores, 'structure')
            yield sse_process(
                _tr(lang, '样例底稿已导入，继续轻改', 'Card seed imported — continuing to light revise'),
                kind='ok')
            db.execute("UPDATE poem_sessions SET stage = 'symbols' WHERE id = %s", (sid,))
            return

    # Fixed forms (sonnet=14, haiku=3, …): never trust LLM for line count
    if form and form.get('lines'):
        cv = verse_form.skeleton_for_form(form, lang=lang)
        cv['form_lock'] = True
        cv['verse_form'] = form.get('id')
        logic = poem_canvas.skeleton_logic_report(cv)
        intent = _tr(
            lang,
            f'立{form.get("id")}骨架（{len(cv.get("lines") or [])}行）',
            f'Set {form.get("id")} skeleton ({len(cv.get("lines") or [])} lines)',
        )
        yield sse_process(
            _tr(lang,
                f'使用锁定体裁骨架：{form.get("id")} · {len(cv.get("lines") or [])} 行（模型不得改行数）',
                f'Using locked form skeleton: {form.get("id")} · '
                f'{len(cv.get("lines") or [])} lines (model cannot change line count)'),
            kind='ok')
        save_canvas(sid, cv)
        yield sse('intent', {'text': intent})
        yield sse_process(_tr(lang, f'念头：{intent}', f'Intent: {intent}'))
        yield sse('canvas_init', cv)
        yield sse('canvas', cv)
        yield sse('op', {'op': {'type': 'init'}, 'canvas': cv})
        poem_text = poem_canvas.canvas_to_text(cv, lang=lang)
        yield sse('poem', {'from': '', 'to': poem_text, 'full': poem_text})
        score_prefs = dict(prefs or {})
        score_prefs['_verse_form'] = form
        scores = poem_canvas.structure_score(cv, score_prefs)
        yield sse('radar', scores)
        yield sse_process(
            _tr(lang,
                f'骨架检查 — {len(cv.get("lines") or [])} 行 · 成句 {logic["ok_n"]}/{logic["n"]} · 体裁 {form.get("id")}',
                f'Skeleton check — {len(cv.get("lines") or [])} lines · clause '
                f'{logic["ok_n"]}/{logic["n"]} · form {form.get("id")}'),
            kind='info')
        add_node(sid, session.get('current_node_id'), intent, poem_text, scores, 'structure')
        yield sse_process(
            _tr(lang, '骨架可接受，自动继续填词', 'Skeleton looks fine — continuing to fill'),
            kind='ok')
        db.execute("UPDATE poem_sessions SET stage = 'symbols' WHERE id = %s", (sid,))
        return

    messages = build_context(row, prefs, stage='structure', lang=lang, user_id=user_id)
    skeleton_rules = (
        'HARD: each line POS sequence must be a clause frame — at least one V; '
        'no noun piles (N-N-N / A-N-N-N). English prefer DET/P; Chinese prefer V + PART/P. '
        'Bad skeletons = fail.'
        if en else
        '硬性：每行POS必须是成句骨架——至少含一个V；禁止名词堆砌（N-N-N等）。'
        '中文行优先含V与虚词槽；乱句骨架视为失败。'
    )
    messages.append({
        'role': 'user',
        'content': (
            'Output ONLY skeleton init JSON: multiple lines of slots, pos required, text empty. '
            f'{form_line} {skeleton_rules} '
            'Write every intent field in English (≤30 words). '
            f'User feedback: {extra_user or "none"}'
            if en else
            '请只输出骨架 init JSON：多行 slots，pos 必填，text 留空。'
            f'{form_line}{skeleton_rules}'
            f'用户反馈：{extra_user or "无"}'
        )
    })
    yield sse_process(_tr(lang, '生成槽位骨架', 'Building slot skeleton'))
    buf = []
    degraded = False
    for delta, meta_llm in llm.stream_complete_meta(messages, role='canvas', temperature=temperature):
        if not run_still_valid(sid, token):
            yield sse_process(_tr(lang, '已中断', 'Interrupted'), kind='warn')
            return
        if meta_llm.get('degraded'):
            degraded = True
        buf.append(delta)
    if degraded:
        yield sse('degraded', {
            'message': _tr(lang, '模型不可用，已使用本地降级骨架',
                           'Model unavailable — using local skeleton fallback')
        })
        yield sse_process(_tr(lang, '降级模式', 'Degraded mode'), kind='warn')
        if form and form.get('lines'):
            cv = verse_form.skeleton_for_form(form, lang=lang)
            parsed = {'ops': [{'type': 'init', 'intent': form.get('id')}]}
            logic = poem_canvas.skeleton_logic_report(cv)
        else:
            parsed = poem_canvas.parse_ops_json(''.join(buf))
            cv = _apply_skeleton_init(parsed, form=form, lang=lang)
            logic = poem_canvas.skeleton_logic_report(cv)
    else:
        parsed = poem_canvas.parse_ops_json(''.join(buf))
        cv = _apply_skeleton_init(parsed, form=form, lang=lang)
        logic = poem_canvas.skeleton_logic_report(cv)

    # Form line-count must match (sonnet = 14, etc.)
    ok_n, n_lines, expect = verse_form.line_count_ok(cv, form)
    if not ok_n:
        yield sse_process(
            _tr(lang,
                f'体裁行数不符：现有 {n_lines} 行，需要 {expect} — 重搭',
                f'Form line count mismatch: got {n_lines}, need {expect} — rebuilding'),
            kind='warn')
        repair_msgs = list(messages)
        repair_msgs.append({
            'role': 'user',
            'content': (
                f'REJECTED: skeleton has {n_lines} lines; form requires {expect}. '
                f'{form_line} Re-output a FULL init with exactly the required line count. '
                'For a sonnet that means 14 lines as 4+4+4+2 — never 8+2 or 4+4+2.'
                if en else
                f'驳回：现有 {n_lines} 行，体裁要求 {expect}。{form_line}'
                '请重新输出完整 init，行数必须正好符合。'
                '十四行必须是 4+4+4+2，禁止 8+2 或 4+4+2。'
            ),
        })
        buf2 = []
        for delta, _m in llm.stream_complete_meta(
                repair_msgs, role='canvas', temperature=0.35):
            if not run_still_valid(sid, token):
                return
            buf2.append(delta)
        parsed2 = poem_canvas.parse_ops_json(''.join(buf2))
        cv2 = _apply_skeleton_init(parsed2, form=form, lang=lang)
        ok2, n2, _ = verse_form.line_count_ok(cv2, form)
        if ok2:
            cv, parsed = cv2, parsed2
            logic = poem_canvas.skeleton_logic_report(cv)
            yield sse_process(
                _tr(lang, f'已按体裁重搭为 {n2} 行', f'Rebuilt to {n2} lines for form'),
                kind='ok')
        else:
            # Deterministic fallback so sonnet is never left at 10 lines
            cv = verse_form.skeleton_for_form(form, lang=lang)
            parsed = {'ops': [{'type': 'init', 'intent': form.get('id') or 'form'}]}
            logic = poem_canvas.skeleton_logic_report(cv)
            yield sse_process(
                _tr(lang,
                    f'模型仍不符，已用本地 {form.get("id")} 模板（{len(cv.get("lines") or [])} 行）',
                    f'Model still wrong — local {form.get("id")} template '
                    f'({len(cv.get("lines") or [])} lines)'),
                kind='warn')

    # Validate POS clause-readiness; repair once if messy
    logic = poem_canvas.skeleton_logic_report(cv)
    if logic['ok_ratio'] < 0.65 or logic['score'] < 55:
        yield sse_process(
            _tr(lang,
                f'骨架逻辑偏弱（成句行 {logic["ok_n"]}/{logic["n"]}），重搭',
                f'Skeleton logic weak ({logic["ok_n"]}/{logic["n"]} clause lines) — rebuilding'),
            kind='warn')
        bad = ', '.join(logic.get('bad') or []) or 'multiple lines'
        repair_msgs = list(messages)
        repair_msgs.append({
            'role': 'user',
            'content': (
                f'Previous skeleton failed logic check ({bad}). '
                f'{form_line} '
                'Re-output a FULL init JSON with the REQUIRED line count. '
                'Every line MUST include V; ban N/A stacks; '
                'English lines include DET or P where natural.'
                if en else
                f'上一版骨架逻辑失败（{bad}）。{form_line}'
                '请重新输出完整 init JSON（行数必须符合体裁）。'
                '每行必须含V，禁止N/A堆砌；可加PART/P使行可成句。'
            ),
        })
        buf2 = []
        for delta, _m in llm.stream_complete_meta(
                repair_msgs, role='canvas', temperature=0.45):
            if not run_still_valid(sid, token):
                return
            buf2.append(delta)
        parsed2 = poem_canvas.parse_ops_json(''.join(buf2))
        if parsed2:
            cv2 = _apply_skeleton_init(parsed2, form=form, lang=lang)
            ok2, _, _ = verse_form.line_count_ok(cv2, form)
            logic2 = poem_canvas.skeleton_logic_report(cv2)
            if ok2 and logic2['score'] >= logic['score']:
                cv, parsed, logic = cv2, parsed2, logic2
                yield sse_process(
                    _tr(lang, '骨架已按成句规则重搭', 'Skeleton rebuilt for clause frames'),
                    kind='ok')
            elif form and form.get('lines') and not ok2:
                cv = verse_form.skeleton_for_form(form, lang=lang)
                logic = poem_canvas.skeleton_logic_report(cv)
                yield sse_process(
                    _tr(lang, '逻辑重搭后行数仍错，改用本地体裁模板',
                        'Logic rebuild broke line count — local form template'),
                    kind='warn')

    # Fixed forms (sonnet=14, etc.): if still wrong, or sonnet family, prefer locked local frame
    if form and form.get('lines'):
        ok_f, n_f, _ = verse_form.line_count_ok(cv, form)
        if (not ok_f) or (
                'sonnet' in (form.get('id') or '') and n_f != int(form['lines'])):
            cv = verse_form.skeleton_for_form(form, lang=lang)
            logic = poem_canvas.skeleton_logic_report(cv)
            parsed = {'ops': [{'type': 'init', 'intent': form.get('id') or 'form'}]}
            yield sse_process(
                _tr(lang,
                    f'已锁定本地体裁骨架：{form.get("id")} · {len(cv.get("lines") or [])} 行',
                    f'Locked local form skeleton: {form.get("id")} · '
                    f'{len(cv.get("lines") or [])} lines'),
                kind='ok')
        cv['form_lock'] = True
        cv['verse_form'] = form.get('id')

    save_canvas(sid, cv)
    intent = _tr(lang, '先立槽位骨架', 'Set up the slot skeleton first')
    if parsed and parsed.get('ops'):
        intent = parsed['ops'][0].get('intent') or intent
    yield sse('intent', {'text': intent})
    yield sse_process(_tr(lang, f'念头：{intent}', f'Intent: {intent}'))
    yield sse('canvas_init', cv)
    yield sse('canvas', cv)
    yield sse('op', {'op': {'type': 'init'}, 'canvas': cv})
    poem_text = poem_canvas.canvas_to_text(cv)
    yield sse('poem', {'from': '', 'to': poem_text, 'full': poem_text})

    score_prefs = dict(prefs or {})
    score_prefs['_verse_form'] = form
    scores = poem_canvas.structure_score(cv, score_prefs)
    yield sse('radar', scores)
    n_final = len(cv.get('lines') or [])
    yield sse_process(
        _tr(lang,
            f'骨架检查 — {n_final} 行 · 成句 {logic["ok_n"]}/{logic["n"]} · 逻辑 {scores.get("coherence", 0):.0f}'
            + (f' · 体裁 {form.get("id")}' if form and form.get('id') != 'free' else ''),
            f'Skeleton check — {n_final} lines · clause {logic["ok_n"]}/{logic["n"]} · '
            f'logic {scores.get("coherence", 0):.0f}'
            + (f' · form {form.get("id")}' if form and form.get('id') != 'free' else '')),
        kind='info')
    add_node(sid, session.get('current_node_id'), intent, poem_text, scores, 'structure')

    decision = ckpt.gate_decision(
        scores, target,
        recent_rejects=recent_rejects,
        vague_user=ckpt.user_message_vague(extra_user),
        soft_ask_skips=soft_skips,
        filled_ratio=_fill_ratio(cv),
        kind='structure',
    )
    # Strong clause-ready skeleton → continue without stalling
    form_ok, _, _ = verse_form.line_count_ok(cv, form)
    if form_ok and ((logic['ok_ratio'] >= 0.75 and float(scores.get('coherence') or 0) >= 68) or (
            logic['ok_ratio'] >= 0.8 and float(scores.get('overall') or 0) >= 72)):
        decision = {
            'action': 'auto',
            'reason': 'skeleton_clause_ok',
            'deviation': decision.get('deviation'),
        }
    if not form_ok:
        decision = {
            'action': 'ask',
            'reason': 'structure_form_mismatch',
            'deviation': decision.get('deviation'),
        }

    if decision['action'] == 'auto':
        yield sse_process(
            _tr(lang, '骨架可接受，自动继续填词', 'Skeleton looks fine — continuing to fill'),
            kind='ok')
        db.execute("UPDATE poem_sessions SET stage = 'symbols' WHERE id = %s", (sid,))
        # Keep same run token valid for subsequent fill rounds in caller
        return

    set_awaiting(sid, 'skeleton_ready', token)
    ask = _tr(
        lang,
        '骨架已立（槽位+词性，已做成句/体裁检查）。是否顺眼？回复继续，或说明想改的行/疏密。',
        'Skeleton is up (slots + POS, clause/form-checked). Look good? Reply to continue, or say which lines/density to change.',
    )
    yield sse('checkpoint', {
        'id': 'skeleton_ready',
        'message': ask,
        'reason': decision['reason'],
        'deviation': decision.get('deviation'),
    })
    yield sse_process(ask, waiting=True, kind='ask')
    append_chat(sid, 'assistant', ask)
    yield sse('message', {'role': 'assistant', 'content': ask})


def _final_whole_poem_polish(session, prefs, sid, token, cv, form, lang, user_id, target):
    """One whole-poem polish pass locked to selected style card; rewrite canvas from result."""
    en = _lang_en(lang)
    yield sse_process(
        _tr(lang, '定稿整首润色（锁定样例风格）', 'Final whole-poem polish (style-card lock)'),
        kind='ok')
    current = poem_canvas.canvas_readable_text(cv, lang=lang) or poem_canvas.canvas_to_text(cv, lang=lang)
    meta = load_stage_meta(session)
    sel = meta.get('selected_example') or {}
    seed_poem = (sel.get('poem') or '') if isinstance(sel, dict) else ''
    form_bit = verse_form.form_instruction(form, lang=lang) if form else ''
    cpl = (form or {}).get('chars_per_line')

    def _polish_once(extra_ban=''):
        messages = build_context(session, prefs, stage='final', lang=lang, user_id=user_id)
        if en:
            messages.append({
                'role': 'user',
                'content': (
                    f'{form_bit}\n'
                    'WHOLE-POEM POLISH: Output ONLY the polished poem body (no JSON, no commentary). '
                    'Quality must match or exceed the selected style card. '
                    'Keep the same breath, core images, and approximate line count; '
                    'fix associations; HARD BAN duplicate content words and reduplication '
                    '(no "word word", no 窗窗 / 挑尽挑尽). '
                    f'{extra_ban}\n'
                    f'Selected card poem:\n{seed_poem or "(none)"}\n\n'
                    f'Current draft:\n{current}\n'
                ),
            })
        else:
            messages.append({
                'role': 'user',
                'content': (
                    f'{form_bit}\n'
                    '【整首润色】只输出润色后的诗正文（不要JSON、不要点评）。'
                    '完成度须不低于所选样例卡；保持呼吸、核心意象与大致行数；'
                    '补强关联；硬禁叠字叠词（禁窗窗/凉凉/挑尽挑尽/同行复用同一实字）。'
                    f'{extra_ban}\n'
                    f'样例卡诗：\n{seed_poem or "（无）"}\n\n'
                    f'当前稿：\n{current}\n'
                ),
            })
        buf = []
        for delta, meta_llm in llm.stream_complete_meta(
                messages, role='logic', temperature=0.45):
            if not run_still_valid(sid, token):
                return None
            buf.append(delta)
        content = ''.join(buf).strip()
        poem = extract_poem(content) or content
        poem = re.sub(r'^```(?:\w+)?\s*', '', poem)
        poem = re.sub(r'\s*```$', '', poem).strip()
        return poem if poem and len(poem) >= 8 else None

    poem = _polish_once()
    if poem is None:
        yield sse_process(
            _tr(lang, '润色无有效输出，保留当前稿', 'Polish empty — keep current draft'),
            kind='warn')
        return

    def _poem_bad(p):
        if evaluate.has_heavy_repetition(p):
            return True
        if cpl and not en:
            for ln in [x.strip() for x in p.splitlines() if x.strip()]:
                n = len(re.findall(r'[\u4e00-\u9fff]', ln))
                if n != int(cpl):
                    return True
        return False

    if _poem_bad(poem):
        issues = '、'.join(evaluate.list_repetition_issues(poem)[:5]) or '叠词/字数'
        yield sse_process(
            _tr(lang, f'润色含叠词或字数不符（{issues}），重试',
                f'Polish has repeats / bad meter ({issues}) — retry'),
            kind='warn')
        ban = (
            f'REJECTED issues: {issues}. Rewrite with ZERO reduplication; '
            f'each content character at most once; '
            + (f'each line exactly {cpl} Chinese characters. ' if cpl else '')
            if en else
            f'驳回问题：{issues}。重写：零叠字叠词；实字全诗最多一次；'
            + (f'每句恰好{cpl}字。' if cpl else '')
        )
        poem2 = _polish_once(ban)
        if poem2 and not _poem_bad(poem2):
            poem = poem2
        else:
            yield sse_process(
                _tr(lang, '润色仍有叠词，保留润色前稿',
                    'Polish still repetitive — keeping pre-polish draft'),
                kind='warn')
            return

    # Prefer form-aligned seed so line count stays locked
    polished_cv = poem_canvas.seed_canvas_from_poem(poem, form=form, lang=lang)
    if not polished_cv or not polished_cv.get('lines'):
        polished_cv = poem_canvas.skeleton_from_text(poem)
        polished_cv['seeded_from_example'] = True
    if cv.get('form_lock') or (form and form.get('lines')):
        polished_cv['form_lock'] = True
        if form and form.get('id'):
            polished_cv['verse_form'] = form.get('id')
        if cpl:
            polished_cv['chars_per_line'] = int(cpl)
    if cv.get('seeded_from_example'):
        polished_cv['seeded_from_example'] = True
    readable = poem_canvas.canvas_readable_text(polished_cv, lang=lang)
    if evaluate.has_heavy_repetition(readable, polished_cv):
        yield sse_process(
            _tr(lang, '写回画布前仍检出叠词，保留原稿',
                'Canvas still shows repeats — keeping prior draft'),
            kind='warn')
        return
    save_canvas(sid, polished_cv)
    scores = score_poem(readable or poem, sid, target=target)
    yield sse('canvas', polished_cv)
    yield sse('poem', {'from': current, 'to': readable or poem, 'full': readable or poem})
    yield sse('radar', scores)
    yield sse_process(
        _tr(lang, '整首润色已写回画布', 'Whole-poem polish written to canvas'),
        kind='ok')


def _canvas_degrade_fill(session, prefs, sid, token, stage, lang, user_id):
    messages = build_context(session, prefs, stage=stage, lang=lang, user_id=user_id)
    role = {'symbols': 'symbols', 'verbs': 'verb', 'final': 'logic'}.get(stage, 'verb')
    buf = []
    for delta, meta in llm.stream_complete_meta(messages, role=role, temperature=0.7):
        if not run_still_valid(sid, token):
            return
        buf.append(delta)
        yield sse('thought', {'delta': delta})
    content = ''.join(buf)
    poem = extract_poem(content)
    cv = poem_canvas.skeleton_from_text(poem)
    save_canvas(sid, cv)
    scores = evaluate.evaluate_poem(poem, history_texts(sid))
    yield sse('canvas', cv)
    yield sse('radar', scores)
    yield sse('poem', {'from': '', 'to': poem, 'full': content})
    add_node(sid, session.get('current_node_id'), '降级整段填词', content, scores, stage)
    append_chat(sid, 'assistant', '本阶段已用降级整段填词更新诗面。')
    yield sse('message', {'role': 'assistant', 'content': '本阶段已用降级整段填词更新诗面。'})
    set_idle(sid)


def refresh_payload(user_id, lang=None, session_id=None):
    b = refresh_bundle(user_id, lang=lang, session_id=session_id)
    current = b['current']
    scores = db.loads(current['radar_scores'], {}) if current else {}
    st = b['stage'] or 'chat'
    cv = b.get('canvas') or {}
    # Prefer empty scores in talk stage so UI does not force-open radar
    if scores:
        scores = ensure_seven_dims(scores)
    elif st not in ('chat', 'examples') or cv.get('lines'):
        scores = ensure_seven_dims({
            k: int(float(v) * 100) for k, v in b['prefs']['dimension_weights'].items()
        })
    else:
        scores = {}
    poem = ''
    if current:
        poem = extract_poem(current['poem_content']) if current else ''
    if cv.get('lines'):
        poem = poem_canvas.canvas_filled_text(cv) or poem_canvas.canvas_to_text(cv) or poem
    sessions = [
        {
            'id': str(s['id']),
            'title': s.get('title') or default_session_title('untitled', lang=lang),
            'stage': s.get('stage'),
            'updated_at': s['updated_at'].isoformat() if s.get('updated_at') else None,
        }
        for s in (b.get('sessions') or [])
    ]
    return {
        'stage': b['stage'],
        'stage_label': b['stage_label'],
        'scores': scores,
        'poem': poem,
        'thought': current['ai_thought'] if current else '',
        'agency': b['prefs']['agency'],
        'show_actions': b['stage'] != 'chat',
        'canvas': cv,
        'run_status': b.get('run_status'),
        'checkpoint_id': b.get('checkpoint_id'),
        'session_id': str(b['session']['id']),
        'sessions': sessions,
        'nodes': [
            {
                'id': str(n['id']),
                'stage': n.get('stage'),
                'thought': (n.get('ai_thought') or '')[:80],
                'created_at': n['created_at'].isoformat() if n.get('created_at') else None,
            }
            for n in (b.get('nodes') or [])[-20:]
        ],
        'examples': (b.get('stage_meta') or {}).get('examples_payload'),
        'selected_example': (b.get('stage_meta') or {}).get('selected_example'),
    }


def handle_user_message(user_id, text, action=None, form=None):
    for _ in stream_user_turn(user_id, text, action=action, form=form):
        pass
    return refresh_bundle(user_id)


def public_poems(limit=30):
    return db.fetchall(
        '''SELECT s.id, s.title, s.created_at, u.username,
                  n.poem_content, n.radar_scores
           FROM poem_sessions s
           JOIN users u ON u.id = s.user_id
           LEFT JOIN poem_nodes n ON n.id = s.current_node_id
           WHERE s.is_public = TRUE
           ORDER BY s.created_at DESC
           LIMIT %s''',
        (limit,))


def extend_public(user_id, session_id, lang=None):
    import i18n
    src = db.fetchone(
        '''SELECT s.*, n.poem_content
           FROM poem_sessions s
           LEFT JOIN poem_nodes n ON n.id = s.current_node_id
           WHERE s.id = %s AND s.is_public = TRUE''',
        (session_id,))
    if not src:
        return None
    seed = src.get('poem_content') or ''
    base = src.get('title') or i18n.t('poem_fallback', lang=lang)
    return create_session(
        user_id,
        title=i18n.t('session_derived', lang=lang).format(title=base),
        source_id=session_id,
        seed_text=seed,
        lang=lang,
    )


def user_history_nodes(user_id):
    return db.fetchall(
        '''SELECT n.*, s.title AS session_title
           FROM poem_nodes n
           JOIN poem_sessions s ON s.id = n.session_id
           WHERE s.user_id = %s
           ORDER BY n.created_at DESC
           LIMIT 100''',
        (user_id,))


def status_report(user_id):
    session = get_or_create_session(user_id)
    log = db.loads(session['chat_log'], [])
    summary = '\n'.join(
        f"{m.get('role')}: {m.get('content', '')[:120]}" for m in log[-8:])
    q = current_topic_query(session)
    bits = cross_session_memory(user_id, exclude_session_id=str(session.get('id')), query_text=q)
    if bits:
        summary += '\n题材相近过往：' + '；'.join(bits[:3])
    else:
        summary += '\n题材相近过往：无'
    return llm.chat_complete(
        [{'role': 'user', 'content': f'根据以下摘要写状态报告：\n{summary}'}],
        role='status', temperature=0.5)
