import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, jsonify, stream_with_context
from dotenv import load_dotenv
import db
import auth
import agents
import preferences
import i18n

load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret')
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2MB


@app.context_processor
def inject_i18n():
    return {
        't': i18n.t,
        'lang': i18n.get_lang(),
        'langs': i18n.LANGS,
        'ui': i18n.ui_pack(),
    }


def _warm_pool():
    import threading
    def run():
        try:
            try:
                db.init_db()
            except Exception:
                pass
            db.ping()
            app._db_ready = True
            app._db_error = None
        except Exception as e:
            app._db_ready = False
            app._db_error = str(e)
    threading.Thread(target=run, daemon=True).start()


_warm_pool()


@app.before_request
def ensure_db():
    if getattr(app, '_db_ready', False):
        return
    try:
        import refresh_db_url
        refresh_db_url.ensure_pg()
    except Exception:
        pass
    try:
        try:
            db.init_db()
        except Exception:
            pass
        db.ping()
        app._db_ready = True
        app._db_error = None
    except Exception as e:
        app._db_ready = False
        app._db_error = str(e)


@app.route('/lang', methods=['POST'])
def set_language():
    i18n.set_lang(request.form.get('lang') or request.args.get('lang') or 'zh')
    nxt = request.form.get('next') or request.referrer or url_for('home')
    return redirect(nxt)


@app.route('/')
def home():
    poems = []
    err = getattr(app, '_db_error', None)
    try:
        poems = agents.public_poems()
        app._db_error = None
    except Exception as e:
        err = str(e)
        app._db_ready = False
        app._db_error = err
    return render_template('home.html', poems=poems, error=err, user_id=auth.current_user_id())


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        user, err = auth.register_user(request.form.get('username', ''), request.form.get('password', ''))
        if err:
            flash(err)
            return render_template('register.html')
        session['user_id'] = str(user['id'])
        session['username'] = user['username']
        return redirect(url_for('onboarding'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user, err = auth.login_user(request.form.get('username', ''), request.form.get('password', ''))
        if err:
            flash(err)
            return render_template('login.html')
        session['user_id'] = str(user['id'])
        session['username'] = user['username']
        return redirect(url_for('chat'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


@app.route('/onboarding', methods=['GET', 'POST'])
@auth.login_required
def onboarding():
    if request.method == 'POST':
        preferences.apply_onboarding(auth.current_user_id(), {
            'mood': request.form.get('mood', 'calm'),
            'style': request.form.get('style', 'modern'),
            'control': request.form.get('control', 'guided'),
            'culture': request.form.get('culture', 'none'),
        })
        return redirect(url_for('chat'))
    return render_template('onboarding.html')


@app.route('/chat', methods=['GET', 'POST'])
@auth.login_required
def chat():
    uid = auth.current_user_id()
    session_id = request.args.get('session_id')
    if request.method == 'POST':
        agents.handle_user_message(
            uid,
            request.form.get('message', ''),
            action=request.form.get('action'),
            form=request.form)
        return redirect(url_for('chat'))
    try:
        bundle = agents.refresh_bundle(uid, session_id=session_id)
    except Exception as e:
        app._db_ready = False
        flash(f'数据库暂时不可用：{e}')
        return render_template('chat.html', bundle={}, chat_log=[], nodes=[], current=None,
                               current_scores={}, poem_text='', slider_vals={}, agency='balanced',
                               stage='chat', stage_label='', username=session.get('username'),
                               show_actions=False, canvas={}, sessions=[], session_id='',
                               run_status='idle', checkpoint_id=None, db_error=str(e),
                               examples_payload=None)
    current_scores = {}
    poem_text = ''
    if bundle['current']:
        current_scores = db.loads(bundle['current'].get('radar_scores'), {})
        poem_text = agents.extract_poem(bundle['current'].get('poem_content') or '')
    # Only show radar scores when past talk / there is draft content — not prefs defaults
    st = bundle.get('stage') or 'chat'
    if current_scores and st not in ('chat',):
        current_scores = agents.ensure_seven_dims(current_scores)
    elif poem_text or (bundle.get('canvas') or {}).get('lines'):
        current_scores = agents.ensure_seven_dims(current_scores or {
            k: int(float(v) * 100)
            for k, v in bundle['prefs']['dimension_weights'].items()
        })
    else:
        current_scores = {}
    import canvas as poem_canvas
    cv = bundle.get('canvas') or {}
    if cv.get('lines'):
        poem_text = poem_canvas.canvas_filled_text(cv) or poem_canvas.canvas_to_text(cv) or poem_text
    target = agents.ensure_seven_dims(db.loads(bundle['session']['target_dimensions'], {}))
    weights = bundle['prefs']['dimension_weights']
    slider_vals = {}
    for k in ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth']:
        if k in target and isinstance(target[k], (int, float)):
            slider_vals[k] = int(target[k]) if target[k] > 1 else int(float(target[k]) * 100)
        else:
            slider_vals[k] = int(float(weights.get(k, 0.5)) * 100)
    sessions = agents.list_sessions(uid)
    examples_payload = (bundle.get('stage_meta') or {}).get('examples_payload')
    return render_template(
        'chat.html',
        bundle=bundle,
        chat_log=bundle['chat_log'],
        nodes=bundle['nodes'],
        current=bundle['current'],
        current_scores=current_scores,
        poem_text=poem_text,
        slider_vals=slider_vals,
        agency=bundle['prefs']['agency'],
        stage=bundle['stage'],
        stage_label=bundle['stage_label'],
        username=session.get('username'),
        show_actions=bundle['stage'] != 'chat',
        canvas=cv,
        sessions=sessions,
        session_id=str(bundle['session']['id']),
        run_status=bundle.get('run_status') or 'idle',
        checkpoint_id=bundle.get('checkpoint_id'),
        db_error=getattr(app, '_db_error', None),
        examples_payload=examples_payload,
    )


def _parse_stream_payload():
    if request.content_type and 'multipart/form-data' in request.content_type:
        text = request.form.get('message') or ''
        action = request.form.get('action')
        session_id = request.form.get('session_id')
        resume_from = request.form.get('resume_from')
        form = {}
        for k in ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth', 'session_id']:
            if request.form.get(k) is not None:
                form[k] = request.form.get(k)
        attachments = []
        files = request.files.getlist('files') or []
        for f in files:
            name = (f.filename or '').lower()
            if not name:
                continue
            if any(name.endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')):
                continue
            if not any(name.endswith(ext) for ext in ('.txt', '.md', '.text', '.csv', '.json', '.log')):
                # allow extensionless text
                if '.' in name.rsplit('/', 1)[-1]:
                    continue
            try:
                raw = f.read()
                content = raw.decode('utf-8')
            except Exception:
                try:
                    content = raw.decode('gbk', errors='ignore')
                except Exception:
                    continue
            attachments.append({'name': f.filename, 'content': content[:50000]})
        return text, action, form, session_id, resume_from, attachments

    data = request.get_json(silent=True) or {}
    return (
        data.get('message') or '',
        data.get('action'),
        data.get('form') or {},
        data.get('session_id'),
        data.get('resume_from'),
        data.get('attachments') or [],
    )


@app.route('/api/stream', methods=['POST'])
@auth.login_required
def api_stream():
    uid = auth.current_user_id()
    text, action, form, session_id, resume_from, attachments = _parse_stream_payload()
    lang = i18n.get_lang()

    def generate():
        token = i18n.push_lang(lang)
        try:
            for chunk in agents.stream_user_turn(
                    uid, text, action=action, form=form, lang=lang,
                    session_id=session_id, attachments=attachments,
                    resume_from=resume_from):
                yield chunk
                # Heartbeat-friendly: chunks already have blank lines
        except GeneratorExit:
            try:
                agents.interrupt_session(uid, session_id)
            except Exception:
                pass
            raise
        except Exception as e:
            msg = str(e)
            if any(x in msg.lower() for x in ('connection', 'operational', 'timeout', 'closed')):
                app._db_ready = False
                app._db_error = msg
                yield agents.sse('error', {'message': f'数据库暂时不可用，已暂停创作：{msg}'})
            else:
                yield agents.sse('error', {'message': msg})
            try:
                yield agents.sse('done', agents.refresh_payload(uid, lang=lang, session_id=session_id))
            except Exception:
                yield agents.sse('done', {'stage': 'chat', 'show_actions': False})
        finally:
            i18n.reset_lang(token)

    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })


@app.route('/api/interrupt', methods=['POST'])
@auth.login_required
def api_interrupt():
    data = request.get_json(silent=True) or {}
    agents.interrupt_session(auth.current_user_id(), data.get('session_id'))
    return jsonify({'ok': True})


@app.route('/api/sessions')
@auth.login_required
def api_sessions():
    rows = agents.list_sessions(auth.current_user_id())
    return jsonify({
        'sessions': [
            {
                'id': str(s['id']),
                'title': s.get('title') or agents.default_session_title('untitled'),
                'stage': s.get('stage'),
                'updated_at': s['updated_at'].isoformat() if s.get('updated_at') else None,
            }
            for s in rows
        ]
    })


@app.route('/api/sessions/switch', methods=['POST'])
@auth.login_required
def api_sessions_switch():
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    row = agents.switch_session(auth.current_user_id(), sid)
    if not row:
        return jsonify({'ok': False}), 404
    return jsonify(agents.refresh_payload(auth.current_user_id(), session_id=str(row['id'])))


@app.route('/api/state')
@auth.login_required
def api_state():
    sid = request.args.get('session_id')
    return jsonify(agents.refresh_payload(auth.current_user_id(), session_id=sid))


@app.route('/information')
@auth.login_required
def information():
    uid = auth.current_user_id()
    prefs = preferences.get_preferences(uid)
    nodes = agents.user_history_nodes(uid)
    report = None
    weight_pct = {
        k: int(float(prefs['dimension_weights'].get(k, 0.5)) * 100)
        for k in ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth']
    }
    return render_template(
        'information.html',
        prefs=prefs,
        weight_pct=weight_pct,
        nodes=nodes,
        report=report,
        username=session.get('username'),
    )


@app.route('/api/status-report')
@auth.login_required
def api_status_report():
    return jsonify({'report': agents.status_report(auth.current_user_id())})


@app.route('/extend/<session_id>', methods=['POST'])
@auth.login_required
def extend(session_id):
    row = agents.extend_public(auth.current_user_id(), session_id)
    if not row:
        flash('无法衍生该作品')
        return redirect(url_for('home'))
    return redirect(url_for('chat', session_id=str(row['id'])))


@app.route('/checkout/<node_id>', methods=['POST'])
@auth.login_required
def checkout(node_id):
    uid = auth.current_user_id()
    node = db.fetchone('SELECT * FROM poem_nodes WHERE id = %s', (node_id,))
    if not node:
        return redirect(url_for('chat'))
    sess = db.fetchone('SELECT * FROM poem_sessions WHERE id = %s AND user_id = %s',
                       (str(node['session_id']), uid))
    if not sess:
        return redirect(url_for('chat'))
    canvas = db.loads(node.get('canvas_json'), {}) if node.get('canvas_json') is not None else {}
    if canvas.get('lines'):
        db.execute(
            '''UPDATE poem_sessions
               SET current_node_id = %s, stage = %s, canvas_json = %s::jsonb,
                   run_status = 'idle', checkpoint_id = NULL, updated_at = CURRENT_TIMESTAMP
               WHERE id = %s''',
            (node_id, node.get('stage') or sess['stage'], db.dumps(canvas), str(sess['id'])))
    else:
        db.execute(
            '''UPDATE poem_sessions
               SET current_node_id = %s, stage = %s, run_status = 'idle', checkpoint_id = NULL,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = %s''',
            (node_id, node.get('stage') or sess['stage'], str(sess['id'])))
    return redirect(url_for('chat', session_id=str(sess['id'])))


if __name__ == '__main__':
    try:
        if not db.ping():
            raise RuntimeError('db ping failed')
        db.init_db()
        app._db_ready = True
    except Exception:
        try:
            import refresh_db_url
            refresh_db_url.main()
            load_dotenv(override=True)
            db.set_database_url(os.getenv('DATABASE_URL'))
            db.init_db()
            app._db_ready = True
        except Exception as e:
            app._db_error = str(e)
            print('DB boot failed:', e)
    port = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True, use_reloader=False)
