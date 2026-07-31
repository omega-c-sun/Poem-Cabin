(function () {
  var sh = document.getElementById('gpt-shell');
  if (!sh) return;

  var strm = document.getElementById('chat-stream');
  var empH = document.getElementById('empty-hero');
  var liveP = document.getElementById('live-panel');
  var intLn = document.getElementById('intent-line');
  var poemEl = document.getElementById('poem-live');
  var stRail = document.getElementById('stage-rail');
  var actMenu = document.getElementById('action-menu');
  var dimSh = document.getElementById('dims-sheet');
  var frm = document.getElementById('composer');
  var inp = document.getElementById('message-input');
  var sendB = document.getElementById('send-btn');
  var stopB = document.getElementById('stop-btn');
  var plusB = document.getElementById('plus-btn');
  var attB = document.getElementById('attach-btn');
  var fileIn = document.getElementById('file-input');
  var chips = document.getElementById('attach-chips');
  var procR = document.getElementById('process-rail');
  var rDock = document.getElementById('radar-dock');
  var sessLs = document.getElementById('session-list');
  var exPanel = document.getElementById('examples-panel');
  var busy = false;
  var abCtrl = null;
  var sid = sh.getAttribute('data-session') || '';
  var atts = [];
  var procLs = [];
  var noThot = false;

  var initPoem = sh.getAttribute('data-poem') || '';
  var initCv = {};
  try { initCv = JSON.parse(sh.getAttribute('data-canvas') || '{}'); } catch (e) {}

  // 有槽位画布优先 CV，否则 PM 瞬时展示
  if (initCv && initCv.lines && initCv.lines.length && window.CV) {
    CV.render(poemEl, initCv);
  } else if (poemEl) {
    PM.setInstant(poemEl, initPoem);
  }

  // hh -> 从隐藏 JSON 回放历史消息
  function hh() {
    var el = document.getElementById('chat-log-data');
    if (!el || !strm) return;
    var log = [];
    try { log = JSON.parse(el.textContent || '[]'); } catch (e) { return; }
    log.forEach(function (m) {
      if (!m || (m.role !== 'user' && m.role !== 'assistant')) return;
      addMsg(m.role, m.content || '', false);
    });
  }

  // ui -> 取 i18n 文案
  function ui(key, fb) {
    var pack = window.UI_I18N || {};
    return (pack[key] != null && pack[key] !== '') ? pack[key] : fb;
  }

  // isEn -> 当前是否英文 UI
  function isEn() {
    return String((window.UI_I18N && window.UI_I18N.lang) || '').indexOf('en') === 0;
  }

  // needRdr -> 起草阶段才显示雷达
  function needRdr(st) {
    return st === 'structure' || st === 'symbols' || st === 'verbs'
      || st === 'link' || st === 'final';
  }

  // dBar -> 示例卡七维短条
  function dBar(dims) {
    var ks = ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth'];
    var labs = (window.UI_I18N && window.UI_I18N.radar) || ks;
    return '<div class="ex-dims">' + ks.map(function (k, i) {
      var v = (dims && dims[k] != null) ? dims[k] : 0;
      var lab = labs[i] || k;
      var short = lab.length <= 3 ? lab : lab.slice(0, 2);
      return '<span title="' + esc(lab) + '">' +
        esc(short) + ' ' + v + '</span>';
    }).join('') + '</div>';
  }

  // renEx -> 渲染示例卡片列表
  function renEx(p) {
    if (!exPanel || !p || !p.examples) {
      if (exPanel) exPanel.classList.add('is-hidden');
      return;
    }
    exPanel.classList.remove('is-hidden');
    showLv();
    // 仅 examples 阶段收起雷达，避免挡选卡
    var st = sh.getAttribute('data-stage') || '';
    if (st === 'examples' || !st) closeRd();
    var pickLab = ui('ex_pick', '选用此卡');
    var tmplLab = ui('ex_template', '模板 / 规则');
    var cards = (p.examples || []).map(function (ex) {
      return '<article class="ex-card" data-example-id="' + esc(ex.id) + '">' +
        '<header><strong>' + esc(ex.id) + '</strong> ' + esc(ex.title || '') + '</header>' +
        '<pre class="ex-poem">' + esc(ex.poem || '') + '</pre>' +
        dBar(ex.dims) +
        '<details><summary>' + esc(tmplLab) + '</summary><p>' + esc(ex.template || '') +
        '</p><p>' + esc(ex.rules || '') + '</p></details>' +
        '<button type="button" class="ex-pick" data-example-id="' + esc(ex.id) + '">' +
        esc(pickLab) + '</button>' +
        '</article>';
    }).join('');
    var chs = (p.choices || []).map(function (c) {
      return '<button type="button" class="ex-choice" data-choice-id="' + esc(c.id) +
        '" data-example-id="' + esc(c.example_id == null ? '' : c.example_id) + '">' +
        esc(c.label || c.id) + '</button>';
    }).join('');
    exPanel.innerHTML =
      '<div class="ex-summary">' + esc(p.summary || ui('ex_summary_default', '请选择一组风格')) + '</div>' +
      '<div class="ex-cards">' + cards + '</div>' +
      '<div class="ex-choices">' + chs + '</div>';
  }

  // pickEx -> 选用某张示例卡
  function pickEx(exId, chId) {
    var f = colDims();
    f.example_id = exId || '';
    f.choice_id = chId || '';
    runStr({ action: 'pick_example', form: f });
  }

  if (exPanel) {
    exPanel.addEventListener('click', function (e) {
      var pick = e.target.closest('.ex-pick');
      if (pick) {
        pickEx(pick.getAttribute('data-example-id'), '');
        return;
      }
      var ch = e.target.closest('.ex-choice');
      if (ch) {
        pickEx(ch.getAttribute('data-example-id'), ch.getAttribute('data-choice-id'));
      }
    });
  }

  // hideEmp -> 隐藏空态英雄区
  function hideEmp() {
    if (empH) empH.classList.add('is-hidden');
    if (empH) empH.style.display = 'none';
  }

  // autoSz -> 输入框按内容增高（上限约 100 行）
  function autoSz() {
    if (!inp) return;
    inp.style.height = 'auto';
    var lineH = parseFloat(getComputedStyle(inp).lineHeight) || 22;
    var max = lineH * 100;
    var h = Math.min(inp.scrollHeight, max);
    inp.style.height = h + 'px';
    inp.style.overflowY = inp.scrollHeight > max ? 'auto' : 'hidden';
  }

  // addMsg -> 往聊天流追加一条消息
  function addMsg(role, txt, streaming) {
    hideEmp();
    var row = document.createElement('div');
    row.className = 'msg ' + role;
    var body = document.createElement('div');
    body.className = 'msg-body latex-ready';
    if (role === 'assistant' && window.renderMessage) {
      window.renderMessage(body, txt || '', !!streaming);
    } else {
      body.textContent = txt || '';
    }
    row.appendChild(body);
    strm.appendChild(row);
    strm.parentElement.scrollTop = strm.parentElement.scrollHeight;
    return body;
  }

  // setSt -> 同步关卡状态到壳与轨道
  function setSt(st) {
    sh.setAttribute('data-stage', st || '');
    if (stRail) {
      stRail.classList.toggle('is-idle', st === 'chat');
      Array.prototype.forEach.call(stRail.querySelectorAll('.stage-dot'), function (dot) {
        dot.classList.toggle('on', dot.getAttribute('data-stage') === st);
      });
    }
    if (actMenu) {
      actMenu.classList.toggle('is-hidden', st === 'chat');
    }
  }

  // showLv -> 显示实时面板
  function showLv() {
    if (liveP) liveP.classList.remove('is-hidden');
  }

  // openRd -> 打开右侧雷达 dock
  function openRd() {
    sh.classList.add('layout-split');
    if (rDock) rDock.classList.add('is-open');
    setTimeout(function () {
      if (window.RL) RL.resize();
    }, 350);
  }

  // closeRd -> 关闭雷达 dock
  function closeRd() {
    sh.classList.remove('layout-split');
    if (rDock) rDock.classList.remove('is-open');
  }

  // pushProc -> 往过程条追加一行
  function pushProc(line, waiting, kind) {
    if (!procR || !line) return;
    procLs.push({ line: line, waiting: !!waiting, kind: kind || 'info' });
    if (procLs.length > 40) procLs = procLs.slice(-40);
    var html = procLs.slice(-8).map(function (p) {
      return '<div class="proc-line ' + (p.waiting ? 'waiting' : '') + ' kind-' + p.kind + '">' +
        esc(p.line) + '</div>';
    }).join('');
    procR.innerHTML = html;
    procR.scrollTop = procR.scrollHeight;
  }

  // esc -> HTML 转义
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  // colDims -> 收集维度滑条值
  function colDims() {
    var fd = {};
    if (!dimSh) return fd;
    Array.prototype.forEach.call(dimSh.querySelectorAll('input[type=range]'), function (el) {
      fd[el.name] = el.value;
    });
    return fd;
  }

  // renChips -> 渲染附件芯片
  function renChips() {
    if (!chips) return;
    if (!atts.length) {
      chips.classList.add('is-hidden');
      chips.innerHTML = '';
      return;
    }
    chips.classList.remove('is-hidden');
    chips.innerHTML = atts.map(function (a, i) {
      return '<span class="chip" data-i="' + i + '">' + esc(a.name) +
        ' <button type="button" data-remove="' + i + '">×</button></span>';
    }).join('');
  }

  // setBusy -> 切换发送/停止按钮忙碌态
  function setBusy(on) {
    busy = on;
    sendB.disabled = on;
    if (stopB) stopB.classList.toggle('is-hidden', !on);
  }

  // applyDn -> 用 done/state 包更新 UI
  function applyDn(data) {
    if (!data) return;
    if (data.stage) setSt(data.stage);
    if (data.scores && Object.keys(data.scores).length) {
      RL.update(data.scores);
      if (needRdr(data.stage)) openRd();
      else closeRd();
    } else if (data.stage === 'chat' || data.stage === 'examples') {
      closeRd();
    } else if (needRdr(data.stage) && (data.canvas && data.canvas.lines)) {
      // 有画布无分数时仍开雷达区
      openRd();
    }
    if (data.canvas && data.canvas.lines && window.CV) {
      showLv();
      sh.classList.add('has-canvas');
      CV.render(poemEl, data.canvas);
    } else if (data.poem && !sh.classList.contains('has-canvas')) {
      showLv();
      PM.setInstant(poemEl, data.poem);
    } else if (data.poem && sh.classList.contains('has-canvas') && window.CV && data.canvas) {
      CV.render(poemEl, data.canvas);
    }
    if (data.thought) intLn.textContent = data.thought;
    if (data.show_actions != null && actMenu) {
      actMenu.classList.toggle('is-hidden', !data.show_actions);
    }
    if (data.session_id) sid = data.session_id;
    if (data.sessions) renSess(data.sessions);
    if (data.run_status) sh.setAttribute('data-run-status', data.run_status);
    if (data.checkpoint_id != null) sh.setAttribute('data-checkpoint', data.checkpoint_id || '');
    // 只在 examples 阶段重渲卡片
    if (data.stage === 'examples' && data.examples) {
      renEx(data.examples);
    } else if (data.stage && data.stage !== 'examples' && exPanel) {
      exPanel.classList.add('is-hidden');
    }
  }

  // renSess -> 渲会话侧栏
  function renSess(sessions) {
    if (!sessLs) return;
    sessLs.innerHTML = (sessions || []).map(function (s) {
      var on = s.id === sid ? ' on' : '';
      return '<li class="session-item' + on + '" data-id="' + s.id + '">' +
        '<span class="session-title">' + esc(s.title || ui('session_fallback', 'Untitled')) + '</span>' +
        '<span class="session-meta">' + esc(
          (window.UI_I18N && window.UI_I18N.stages && window.UI_I18N.stages[s.stage]) || s.stage || ''
        ) + '</span></li>';
    }).join('');
  }

  // recon -> 拉取 /api/state 纠偏
  async function recon() {
    try {
      var resp = await fetch('/api/state?session_id=' + encodeURIComponent(sid || ''), {
        credentials: 'same-origin'
      });
      if (!resp.ok) return;
      var data = await resp.json();
      applyDn(data);
    } catch (e) {}
  }

  // runStr -> POST /api/stream 并消费 SSE
  async function runStr(payload) {
    if (busy && abCtrl) {
      abCtrl.abort();
      try {
        await fetch('/api/interrupt', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({ session_id: sid })
        });
      } catch (e) {}
    }
    setBusy(true);
    abCtrl = new AbortController();
    var thotBody = null;
    var intBuf = '';
    var thotBuf = '';

    try {
      var body;
      var headers = { credentials: 'same-origin', signal: abCtrl.signal };
      payload.session_id = sid || payload.session_id;
      if (atts.length) {
        var fd = new FormData();
        fd.append('message', payload.message || '');
        if (payload.action) fd.append('action', payload.action);
        if (payload.resume_from) fd.append('resume_from', payload.resume_from);
        if (payload.session_id) fd.append('session_id', payload.session_id);
        var dims = payload.form || {};
        Object.keys(dims).forEach(function (k) { fd.append(k, dims[k]); });
        atts.forEach(function (a) {
          fd.append('files', new Blob([a.content], { type: 'text/plain' }), a.name);
        });
        body = fd;
        headers.method = 'POST';
        headers.body = fd;
      } else {
        headers.method = 'POST';
        headers.headers = { 'Content-Type': 'application/json' };
        headers.body = JSON.stringify(payload);
      }

      var resp = await fetch('/api/stream', headers);
      if (!resp.ok) {
        addMsg('assistant', '请求失败：' + resp.status);
        await recon();
        return;
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var buf = '';

      // 按空行切 SSE block
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += decoder.decode(chunk.value, { stream: true });
        var parts = buf.split('\n\n');
        buf = parts.pop();
        parts.forEach(function (block) {
          hBlk(block);
        });
      }
      if (buf.trim()) hBlk(buf);
    } catch (err) {
      if (err.name !== 'AbortError') {
        addMsg('assistant', String(err));
        await recon();
      } else {
        pushProc(ui('proc_stopped', '已停止'), false, 'warn');
      }
    } finally {
      setBusy(false);
      abCtrl = null;
      atts = [];
      renChips();
      strm.parentElement.scrollTop = strm.parentElement.scrollHeight;
    }

    // hBlk -> 解析单个 SSE 事件块
    function hBlk(block) {
      var lines = block.split('\n');
      var event = 'message';
      var dataLine = '';
      lines.forEach(function (line) {
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        if (line.indexOf('data:') === 0) dataLine += line.slice(5).trim();
      });
      if (!dataLine) return;
      var data = {};
      try { data = JSON.parse(dataLine); } catch (e) { return; }

      if (event === 'stage') {
        setSt(data.stage);
        if (data.stage === 'examples') {
          noThot = true;
          closeRd();
          pushProc(ui('ex_generating', '正在生成三组对照…'), false, 'info');
        } else if (data.stage === 'chat') {
          noThot = false;
          closeRd();
        } else {
          noThot = false;
        }
      } else if (event === 'session') {
        if (data.id) sid = data.id;
      } else if (event === 'thought_start') {
        showLv();
        if (data.mode === 'intent') {
          intBuf = '';
          intLn.textContent = '';
        } else if (!noThot) {
          thotBuf = '';
          thotBody = addMsg('assistant', '', true);
        } else {
          thotBuf = '';
          thotBody = null;
        }
      } else if (event === 'thought') {
        thotBuf += data.delta || '';
        if (thotBody && window.renderMessage) {
          window.renderMessage(thotBody, thotBuf, true);
        } else if (thotBody) {
          thotBody.textContent = thotBuf;
        }
      } else if (event === 'intent') {
        if (data.text) {
          intLn.textContent = data.text;
          intBuf = data.text;
        } else {
          intBuf += data.delta || '';
          intLn.textContent = intBuf;
        }
      } else if (event === 'examples') {
        renEx(data);
        noThot = false;
      } else if (event === 'radar') {
        showLv();
        RL.update(data);
        var st = sh.getAttribute('data-stage') || '';
        if (needRdr(st)) openRd();
        else closeRd();
      } else if (event === 'poem') {
        showLv();
        // 已有画布时不拿纯文本冲掉槽位
        if (sh.classList.contains('has-canvas') && window.CV) {
        } else {
          PM.morph(poemEl, data.to || '');
        }
      } else if (event === 'canvas_init' || event === 'canvas') {
        showLv();
        sh.classList.add('has-canvas');
        if (window.CV) CV.render(poemEl, data);
        if (needRdr(sh.getAttribute('data-stage') || '')) openRd();
      } else if (event === 'op') {
        showLv();
        sh.classList.add('has-canvas');
        if (data.canvas && window.CV) {
          CV.render(poemEl, data.canvas);
          requestAnimationFrame(function () {
            CV.applyOpHighlight(poemEl, data.op);
          });
        } else if (data.op && data.op.text && poemEl) {
          PM.morph(poemEl, (poemEl.textContent || '') + '');
        }
      } else if (event === 'process') {
        pushProc(data.line, data.waiting, data.kind);
      } else if (event === 'checkpoint') {
        var ck = data.message || data.id || '';
        var last = procLs.length ? procLs[procLs.length - 1].line : '';
        if (ck && ck !== last) pushProc(ck, true, 'ask');
        if (actMenu) actMenu.classList.remove('is-hidden');
      } else if (event === 'degraded') {
        pushProc(data.message || ui('proc_degraded', '降级模式'), false, 'warn');
      } else if (event === 'message') {
        if (data.role === 'assistant' && thotBody) {
          if (window.renderMessage) window.renderMessage(thotBody, data.content || thotBuf, false);
          else thotBody.textContent = data.content || thotBuf;
          thotBody = null;
        } else if (data.role === 'assistant') {
          addMsg('assistant', data.content || '', false);
        }
      } else if (event === 'done') {
        applyDn(data);
        if (thotBody && thotBuf && window.renderMessage) {
          window.renderMessage(thotBody, thotBuf, false);
          thotBody = null;
        }
      } else if (event === 'error') {
        addMsg('assistant', data.message || ui('proc_error', '出错了'));
        pushProc(data.message || ui('proc_error', '错误'), false, 'warn');
      }
    }
  }

  frm.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = (inp.value || '').trim();
    if (!text && !atts.length) return;
    if (text) addMsg('user', text);
    inp.value = '';
    autoSz();
    var resume = sh.getAttribute('data-run-status') === 'awaiting'
      ? (sh.getAttribute('data-checkpoint') || 'skeleton_ready')
      : null;
    runStr({
      message: text,
      form: colDims(),
      resume_from: resume || undefined
    });
  });

  inp.addEventListener('input', autoSz);
  inp.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      frm.requestSubmit();
    }
  });
  autoSz();

  if (stopB) {
    stopB.addEventListener('click', function () {
      if (abCtrl) abCtrl.abort();
      fetch('/api/interrupt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ session_id: sid })
      });
      setBusy(false);
    });
  }

  if (attB && fileIn) {
    attB.addEventListener('click', function () { fileIn.click(); });
    fileIn.addEventListener('change', function () {
      Array.prototype.forEach.call(fileIn.files || [], function (f) {
        var name = (f.name || '').toLowerCase();
        if (/\.(png|jpe?g|gif|webp|bmp|svg)$/.test(name)) return;
        var reader = new FileReader();
        reader.onload = function () {
          atts.push({ name: f.name, content: String(reader.result || '') });
          renChips();
        };
        reader.readAsText(f);
      });
      fileIn.value = '';
    });
  }
  if (chips) {
    chips.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-remove]');
      if (!btn) return;
      atts.splice(parseInt(btn.getAttribute('data-remove'), 10), 1);
      renChips();
    });
  }

  if (plusB && actMenu) {
    plusB.addEventListener('click', function () {
      if (sh.getAttribute('data-stage') === 'chat') return;
      actMenu.classList.toggle('is-hidden');
    });
  }

  if (actMenu) {
    actMenu.addEventListener('click', function (e) {
      var btn = e.target.closest('button[data-action]');
      if (!btn) return;
      var act = btn.getAttribute('data-action');
      if (act === 'new') {
        closeRd();
        runStr({ action: 'new', form: colDims() }).then(function () {
          location.href = '/chat';
        });
        return;
      }
      var payload = { action: act, form: colDims() };
      if (act === 'continue' || act === 'confirm') {
        payload.resume_from = sh.getAttribute('data-checkpoint') || 'skeleton_ready';
      }
      runStr(payload);
    });
  }

  var togDims = document.getElementById('toggle-dims');
  if (togDims && dimSh) {
    togDims.addEventListener('click', function () {
      dimSh.classList.toggle('is-hidden');
    });
  }
  var saveDims = document.getElementById('save-dims');
  if (saveDims) {
    saveDims.addEventListener('click', function () {
      runStr({ action: 'save_dims', form: colDims() });
      dimSh.classList.add('is-hidden');
    });
  }

  var rClose = document.getElementById('radar-close');
  if (rClose) rClose.addEventListener('click', closeRd);

  var sessNew = document.getElementById('session-new');
  if (sessNew) {
    sessNew.addEventListener('click', function () {
      closeRd();
      runStr({ action: 'new' }).then(function () { location.href = '/chat'; });
    });
  }
  if (sessLs) {
    sessLs.addEventListener('click', function (e) {
      var item = e.target.closest('.session-item');
      if (!item) return;
      var id = item.getAttribute('data-id');
      if (!id || id === sid) return;
      if (abCtrl) abCtrl.abort();
      location.href = '/chat?session_id=' + encodeURIComponent(id);
    });
  }

  // 起草关卡且有分数才默认开雷达
  try {
    var sc = JSON.parse(sh.getAttribute('data-scores') || '{}');
    var st0 = sh.getAttribute('data-stage') || 'chat';
    if (sc && Object.keys(sc).length && needRdr(st0)) openRd();
    else closeRd();
  } catch (e) {
    closeRd();
  }
  window.whenLibsReady(function () {
    hh();
    try {
      var ex = JSON.parse(sh.getAttribute('data-examples') || 'null');
      var stH = sh.getAttribute('data-stage') || 'chat';
      if (stH === 'examples' && ex && ex.examples) renEx(ex);
    } catch (e) {}
  });
})();
