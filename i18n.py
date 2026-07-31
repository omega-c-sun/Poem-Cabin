from contextvars import ContextVar

_lang_var = ContextVar('poem_lang', default=None)

LANGS = {
    'zh': '中文',
    'en': 'English',
}

DEFAULT_LANG = 'zh'

TEXTS = {
    'zh': {
        'brand': '诗协作舱',
        'nav_home': '首页',
        'nav_chat': '创作',
        'nav_info': '信息',
        'nav_logout': '退出',
        'nav_login': '登录',
        'nav_register': '注册',
        'home_title': '首页 · 诗协作舱',
        'home_lede': '把情绪写成可回溯的诗。',
        'home_start': '开始创作',
        'home_begin': '开始',
        'home_public': '公开作品',
        'home_untitled': '无题',
        'home_derive': '衍生',
        'home_empty': '暂无公开作品',
        'lang_label': '语言',
        'login_title': '登录 · 诗协作舱',
        'login_h1': '登录',
        'login_user': '用户名',
        'login_pass': '密码',
        'login_submit': '进入',
        'login_to_register': '没有账号？注册',
        'register_title': '注册 · 诗协作舱',
        'register_h1': '注册',
        'register_submit': '创建账号',
        'onboard_title': '冷启动 · 诗协作舱',
        'onboard_h1': '一点琐碎偏好',
        'onboard_lede': '可跳过式回答，用来初始化维度权重。',
        'onboard_mood': '此刻情绪底色',
        'onboard_mood_calm': '平静',
        'onboard_mood_intense': '强烈',
        'onboard_mood_soft': '柔软',
        'onboard_style': '诗体倾向',
        'onboard_style_modern': '现代自由',
        'onboard_style_classical': '偏古典齐整',
        'onboard_style_free': '极自由',
        'onboard_control': '协作方式',
        'onboard_control_guided': '希望引导',
        'onboard_control_active': '我要手调维度',
        'onboard_control_passive': '你多做主',
        'onboard_culture': '文化意象',
        'onboard_culture_none': '无偏好',
        'onboard_submit': '进入创作',
        'chat_title': '创作 · 诗协作舱',
        'chat_hello': '今天想写点什么？',
        'chat_placeholder': '有想法，直接说',
        'chat_confirm': '确认本步',
        'chat_continue': '继续',
        'chat_reject': '换一组',
        'chat_back': '回退最优',
        'chat_publish': '公开',
        'chat_new': '新会话',
        'session_untitled': '未命名诗稿',
        'session_new_title': '新的诗稿',
        'session_derived': '衍生：{title}',
        'session_derived_seed': '衍生自公开作品：\n{seed}',
        'session_fallback': '未命名',
        'poem_fallback': '诗稿',
        'chat_dims': '维度',
        'chat_save_dims': '保存',
        'stage_chat': '倾诉',
        'stage_examples': '选风格',
        'stage_structure': '认骨架',
        'stage_symbols': '长意象',
        'stage_verbs': '活句子',
        'stage_final': '定稿',
        'dim_rhyme': '韵脚',
        'dim_rhythm': '节律',
        'dim_tension': '张力',
        'dim_paradox': '悖论',
        'dim_metaphor': '隐喻',
        'dim_freshness': '新鲜',
        'dim_depth': '哲深',
        'ex_pick': '选用此卡',
        'ex_template': '模板 / 规则',
        'ex_summary_default': '请选择一组风格',
        'ex_generating': '正在生成三组对照…',
        'proc_stopped': '已停止',
        'proc_degraded': '降级模式',
        'proc_error': '错误',
        'info_title': '信息 · 诗协作舱',
        'info_h1': '的协作画像',
        'info_agency': '协作倾向',
        'info_report': 'AI 状态报告',
        'info_history': '历史节点',
        'info_no_history': '暂无历史',
        'info_neg': '负反馈摘要',
        'info_none': '尚无',
        'llm_lang': '请用简体中文回复。诗正文、标题、选项标签均须中文。',
    },
    'en': {
        'brand': 'Poem Cabin',
        'nav_home': 'Home',
        'nav_chat': 'Create',
        'nav_info': 'Profile',
        'nav_logout': 'Log out',
        'nav_login': 'Log in',
        'nav_register': 'Sign up',
        'home_title': 'Home · Poem Cabin',
        'home_lede': 'Turn feeling into poems you can rewind.',
        'home_start': 'Start writing',
        'home_begin': 'Get started',
        'home_public': 'Public poems',
        'home_untitled': 'Untitled',
        'home_derive': 'Derive',
        'home_empty': 'No public poems yet',
        'lang_label': 'Language',
        'login_title': 'Log in · Poem Cabin',
        'login_h1': 'Log in',
        'login_user': 'Username',
        'login_pass': 'Password',
        'login_submit': 'Enter',
        'login_to_register': 'No account? Sign up',
        'register_title': 'Sign up · Poem Cabin',
        'register_h1': 'Sign up',
        'register_submit': 'Create account',
        'onboard_title': 'Onboarding · Poem Cabin',
        'onboard_h1': 'A few preferences',
        'onboard_lede': 'Optional answers to seed your dimension weights.',
        'onboard_mood': 'Mood baseline',
        'onboard_mood_calm': 'Calm',
        'onboard_mood_intense': 'Intense',
        'onboard_mood_soft': 'Soft',
        'onboard_style': 'Form preference',
        'onboard_style_modern': 'Modern free verse',
        'onboard_style_classical': 'More classical meter',
        'onboard_style_free': 'Very free',
        'onboard_control': 'Collaboration style',
        'onboard_control_guided': 'Guide me',
        'onboard_control_active': 'I want full controls',
        'onboard_control_passive': 'You lead',
        'onboard_culture': 'Cultural imagery',
        'onboard_culture_none': 'No preference',
        'onboard_submit': 'Enter studio',
        'chat_title': 'Create · Poem Cabin',
        'chat_hello': 'What would you like to write today?',
        'chat_placeholder': 'Ask anything',
        'chat_confirm': 'Confirm step',
        'chat_continue': 'Continue',
        'chat_reject': 'Try another',
        'chat_back': 'Back to best',
        'chat_publish': 'Publish',
        'chat_new': 'New session',
        'session_untitled': 'Untitled draft',
        'session_new_title': 'New draft',
        'session_derived': 'Derived: {title}',
        'session_derived_seed': 'Derived from a public piece:\n{seed}',
        'session_fallback': 'Untitled',
        'poem_fallback': 'poem',
        'chat_dims': 'Dimensions',
        'chat_save_dims': 'Save',
        'stage_chat': 'Talk',
        'stage_examples': 'Pick style',
        'stage_structure': 'Confirm skeleton',
        'stage_symbols': 'Grow images',
        'stage_verbs': 'Activate verbs',
        'stage_final': 'Finalize',
        'dim_rhyme': 'Rhyme',
        'dim_rhythm': 'Rhythm',
        'dim_tension': 'Tension',
        'dim_paradox': 'Paradox',
        'dim_metaphor': 'Metaphor',
        'dim_freshness': 'Fresh',
        'dim_depth': 'Depth',
        'ex_pick': 'Use this card',
        'ex_template': 'Template / rules',
        'ex_summary_default': 'Pick a style card',
        'ex_generating': 'Generating three style cards…',
        'proc_stopped': 'Stopped',
        'proc_degraded': 'Degraded mode',
        'proc_error': 'Error',
        'llm_lang': (
            'CRITICAL LANGUAGE LOCK: Write ALL user-facing text in English only — '
            'including poem titles, poem lines, summaries, choice labels, and intents. '
            'Do NOT use Chinese characters in poem or title fields. '
            'Sonnet/verse must be English verse matching the requested form.'
        ),
        'info_title': 'Profile · Poem Cabin',
        'info_h1': "'s profile",
        'info_agency': 'Agency',
        'info_report': 'AI status report',
        'info_history': 'History nodes',
        'info_no_history': 'No history yet',
        'info_neg': 'Negative feedback',
        'info_none': 'None',
    },
}


def get_lang():
    lang = _lang_var.get()
    if not lang:
        try:
            from flask import has_request_context, session as flask_session
            if has_request_context():
                lang = flask_session.get('lang') or DEFAULT_LANG
            else:
                lang = DEFAULT_LANG
        except Exception:
            lang = DEFAULT_LANG
    # Japanese temporarily disabled
    if lang == 'ja':
        lang = DEFAULT_LANG
    if lang not in TEXTS:
        return DEFAULT_LANG
    return lang


def set_lang(lang):
    if lang == 'ja':
        lang = DEFAULT_LANG
    if lang in TEXTS:
        _lang_var.set(lang)
        try:
            from flask import has_request_context, session as flask_session
            if has_request_context():
                flask_session['lang'] = lang
        except Exception:
            pass
    return get_lang()


def push_lang(lang):
    if lang == 'ja' or lang not in TEXTS:
        lang = DEFAULT_LANG
    return _lang_var.set(lang)


def reset_lang(token):
    try:
        _lang_var.reset(token)
    except Exception:
        pass


def t(key, lang=None):
    lang = lang if lang else get_lang()
    pack = TEXTS.get(lang) or TEXTS[DEFAULT_LANG]
    if key in pack:
        return pack[key]
    return TEXTS[DEFAULT_LANG].get(key, key)


def ui_pack(lang=None):
    lang = lang if lang else get_lang()
    return {
        'lang': lang,
        'langs': LANGS,
        'radar': [
            t('dim_rhyme', lang),
            t('dim_rhythm', lang),
            t('dim_tension', lang),
            t('dim_paradox', lang),
            t('dim_metaphor', lang),
            t('dim_freshness', lang),
            t('dim_depth', lang),
        ],
        'stages': {
            'chat': t('stage_chat', lang),
            'examples': t('stage_examples', lang),
            'structure': t('stage_structure', lang),
            'symbols': t('stage_symbols', lang),
            'verbs': t('stage_verbs', lang),
            'final': t('stage_final', lang),
        },
        'llm_lang': t('llm_lang', lang),
        'ex_pick': t('ex_pick', lang),
        'ex_template': t('ex_template', lang),
        'ex_summary_default': t('ex_summary_default', lang),
        'ex_generating': t('ex_generating', lang),
        'proc_stopped': t('proc_stopped', lang),
        'proc_degraded': t('proc_degraded', lang),
        'proc_error': t('proc_error', lang),
        'session_fallback': t('session_fallback', lang),
        'pos': (
            {
                'N': 'N', 'V': 'V', 'A': 'ADJ', 'ADV': 'ADV', 'P': 'PREP',
                'PART': 'PART', 'NUM': 'NUM', 'PRON': 'PRON', 'CONJ': 'CONJ',
                'DET': 'DET', 'X': 'OTHER',
            }
            if (lang or '').startswith('en') else
            {
                'N': '名', 'V': '动', 'A': '形', 'ADV': '副', 'P': '介',
                'PART': '助', 'NUM': '数', 'PRON': '代', 'CONJ': '连',
                'DET': '限', 'X': '其它',
            }
        ),
    }
