import db


DEFAULT_WEIGHTS = {
    'rhyme': 0.4,
    'rhythm': 0.4,
    'tension': 0.6,
    'paradox': 0.5,
    'metaphor': 0.55,
    'freshness': 0.7,
    'depth': 0.7,
}


def get_preferences(user_id):
    row = db.fetchone(
        'SELECT * FROM user_preferences WHERE user_id = %s',
        (user_id,))
    if not row:
        db.execute(
            'INSERT INTO user_preferences (user_id, dimension_weights) VALUES (%s, %s::jsonb)',
            (user_id, db.dumps(DEFAULT_WEIGHTS)))
        row = db.fetchone(
            'SELECT * FROM user_preferences WHERE user_id = %s',
            (user_id,))
    weights = db.loads(row['dimension_weights'], DEFAULT_WEIGHTS.copy())
    if not weights:
        weights = DEFAULT_WEIGHTS.copy()
    for k, v in DEFAULT_WEIGHTS.items():
        if k not in weights:
            weights[k] = v
    # persist migration so radar / targets see depth
    if 'depth' not in db.loads(row['dimension_weights'], {}):
        try:
            save_weights(user_id, weights)
        except Exception:
            pass
    return {
        'dimension_weights': weights,
        'negative_feedback_history': db.loads(row['negative_feedback_history'], []),
        'verb_preferences': db.loads(row['verb_preferences'], {}),
        'cultural_preferences': db.loads(row['cultural_preferences'], {}),
        'agency': row.get('agency') or 'balanced',
    }


def save_weights(user_id, weights, agency=None):
    if agency:
        db.execute(
            '''UPDATE user_preferences
               SET dimension_weights = %s::jsonb, agency = %s, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = %s''',
            (db.dumps(weights), agency, user_id))
    else:
        db.execute(
            '''UPDATE user_preferences
               SET dimension_weights = %s::jsonb, updated_at = CURRENT_TIMESTAMP
               WHERE user_id = %s''',
            (db.dumps(weights), user_id))


def apply_onboarding(user_id, answers):
    weights = DEFAULT_WEIGHTS.copy()
    agency = 'balanced'
    mood = answers.get('mood', 'calm')
    style = answers.get('style', 'modern')
    control = answers.get('control', 'guided')
    culture = answers.get('culture', 'none')
    if mood == 'intense':
        weights['tension'] = 0.85
        weights['paradox'] = 0.7
    elif mood == 'soft':
        weights['tension'] = 0.3
        weights['rhythm'] = 0.7
    if style == 'classical':
        weights['rhyme'] = 0.8
        weights['rhythm'] = 0.8
    elif style == 'free':
        weights['rhyme'] = 0.25
        weights['freshness'] = 0.85
    if control == 'active':
        agency = 'active'
    elif control == 'passive':
        agency = 'passive'
    cultural = {}
    if culture and culture != 'none':
        cultural[culture] = 0.9
    db.execute(
        '''UPDATE user_preferences
           SET dimension_weights = %s::jsonb,
               cultural_preferences = %s::jsonb,
               agency = %s,
               updated_at = CURRENT_TIMESTAMP
           WHERE user_id = %s''',
        (db.dumps(weights), db.dumps(cultural), agency, user_id))
    return weights, agency


def register_rejection(user_id, node_summary, scores):
    prefs = get_preferences(user_id)
    history = prefs['negative_feedback_history']
    history.append({'summary': (node_summary or '')[:200], 'scores': scores})
    history = history[-30:]
    weights = prefs['dimension_weights']
    if scores:
        top = max(scores.items(), key=lambda kv: kv[1] if isinstance(kv[1], (int, float)) and kv[0] != 'overall' else -1)
        dim = top[0]
        if dim in weights:
            weights[dim] = round(max(0.1, weights[dim] - 0.08), 2)
            probe = [d for d in weights if d != dim]
            if probe:
                weights[probe[0]] = round(min(1.0, weights[probe[0]] + 0.05), 2)
    db.execute(
        '''UPDATE user_preferences
           SET dimension_weights = %s::jsonb,
               negative_feedback_history = %s::jsonb,
               updated_at = CURRENT_TIMESTAMP
           WHERE user_id = %s''',
        (db.dumps(weights), db.dumps(history), user_id))
    return weights


def update_from_sliders(user_id, form):
    weights = get_preferences(user_id)['dimension_weights']
    for key in ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth']:
        raw = form.get(key)
        if raw is not None and raw != '':
            weights[key] = round(float(raw) / 100.0, 2)
    # migrate older prefs missing depth
    if 'depth' not in weights:
        weights['depth'] = DEFAULT_WEIGHTS['depth']
    save_weights(user_id, weights)
    return weights


def note_verbs(user_id, verbs):
    """Accumulate simple verb preference counts from filled V slots."""
    if not verbs:
        return
    prefs = get_preferences(user_id)
    vp = prefs.get('verb_preferences') or {}
    if not isinstance(vp, dict):
        vp = {}
    for v in verbs:
        v = (v or '').strip()
        if not v or len(v) > 8:
            continue
        vp[v] = int(vp.get(v) or 0) + 1
    # keep top 40
    items = sorted(vp.items(), key=lambda kv: -kv[1])[:40]
    vp = dict(items)
    db.execute(
        '''UPDATE user_preferences
           SET verb_preferences = %s::jsonb, updated_at = CURRENT_TIMESTAMP
           WHERE user_id = %s''',
        (db.dumps(vp), user_id))
    return vp
