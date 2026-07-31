(function () {
  var POS_ALIASES = {
    NOUN: 'N', VERB: 'V', ADJ: 'A', ADJECTIVE: 'A', ADVERB: 'ADV',
    PREP: 'P', PREPOSITION: 'P', ADP: 'P', CONJUNCTION: 'CONJ', CC: 'CONJ',
    PRONOUN: 'PRON', PRO: 'PRON', PARTICLE: 'PART', PRT: 'PART',
    NUMBER: 'NUM', NUMERAL: 'NUM', DETERMINER: 'DET', ARTICLE: 'DET',
    ART: 'DET', DT: 'DET', OTHER: 'X', UNK: 'X', UNKNOWN: 'X'
  };

  var POS_ZH = {
    N: '名', V: '动', A: '形', ADV: '副', P: '介', PART: '助',
    NUM: '数', PRON: '代', CONJ: '连', DET: '限', X: '其它'
  };

  var POS_EN = {
    N: 'N', V: 'V', A: 'ADJ', ADV: 'ADV', P: 'PREP', PART: 'PART',
    NUM: 'NUM', PRON: 'PRON', CONJ: 'CONJ', DET: 'DET', X: 'OTHER'
  };

  // normPos -> 词性别名归一成短码
  function normPos(pos) {
    var raw = String(pos || 'X').trim().toUpperCase();
    if (!raw) return 'X';
    return POS_ALIASES[raw] || raw;
  }

  // posTbl -> 按 UI 语言取词性标签表
  function posTbl() {
    var lang = (window.UI_I18N && window.UI_I18N.lang) || 'zh';
    if (window.UI_I18N && window.UI_I18N.pos) return window.UI_I18N.pos;
    return String(lang).indexOf('en') === 0 ? POS_EN : POS_ZH;
  }

  // posLab -> 词性码转显示标签
  function posLab(pos) {
    var code = normPos(pos);
    var tbl = posTbl();
    return tbl[code] || code;
  }

  // stBrk -> 按体裁算节间空行位置
  function stBrk(nLn, formId) {
    var id = String(formId || '');
    // 商籁：4/4/4/2；彼特拉克：8/6
    if (id.indexOf('sonnet') >= 0 && nLn === 14) {
      return { 3: 1, 7: 1, 11: 1 };
    }
    if (id.indexOf('petrarchan') >= 0 && nLn === 14) {
      return { 7: 1 };
    }
    return {};
  }

  // renCv -> 把槽位画布渲到 DOM
  function renCv(el, cv) {
    if (!el) return;
    var lns = (cv && cv.lines) || [];
    if (!lns.length) {
      el.innerHTML = '';
      return;
    }
    el.innerHTML = '';
    var formId = (cv && (cv.verse_form || cv.form_id)) || '';
    var brks = stBrk(lns.length, formId);
    if (formId || cv.form_lock) {
      var meta = document.createElement('div');
      meta.className = 'canvas-meta';
      meta.textContent = (formId ? formId + ' · ' : '') + lns.length + ' lines'
        + (cv.form_lock ? ' · locked' : '');
      el.appendChild(meta);
    }
    lns.forEach(function (ln, li) {
      var row = document.createElement('div');
      row.className = 'canvas-line';
      var num = document.createElement('span');
      num.className = 'canvas-line-num';
      num.textContent = String(li + 1);
      row.appendChild(num);
      (ln.slots || []).forEach(function (slot) {
        var sp = document.createElement('span');
        var filled = !!(slot.text && String(slot.text).trim());
        sp.className = 'canvas-slot ' + (filled ? 'filled' : 'empty');
        sp.setAttribute('data-id', slot.id || '');
        if (filled) {
          sp.textContent = slot.text;
        } else {
          sp.innerHTML = '<em>' + posLab(slot.pos) + '</em>';
        }
        row.appendChild(sp);
      });
      el.appendChild(row);
      if (brks[li]) {
        var br = document.createElement('div');
        br.className = 'canvas-stanza-break';
        el.appendChild(br);
      }
    });
  }

  // CV -> 画布渲染与 op 高亮
  window.CV = {
    render: renCv,
    applyOpHighlight: function (el, op) {
      if (!el || !op) return;
      var sid = op.slot_id || op.id;
      if (!sid) return;
      var node = el.querySelector('[data-id="' + CSS.escape(sid) + '"]');
      if (!node) {
        node = el.querySelector('[data-id="' + sid + '"]');
      }
      if (!node) return;
      node.classList.add('flash');
      setTimeout(function () { node.classList.remove('flash'); }, 700);
    }
  };
})();
