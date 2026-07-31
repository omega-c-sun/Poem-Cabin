(function () {
  // tok -> 按中英混排切成可动画 token
  function tok(txt) {
    txt = txt || '';
    return txt.split(/(\s+)/).filter(function (x) { return x.length; }).reduce(function (acc, part) {
      if (/^\s+$/.test(part)) {
        acc.push(part);
        return acc;
      }
      var chs = part.match(/[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]/g) || [part];
      return acc.concat(chs);
    }, []);
  }

  // renTok -> 把 token 渲成 span
  function renTok(el, toks, cls) {
    el.innerHTML = '';
    toks.forEach(function (t) {
      var sp = document.createElement('span');
      sp.className = 'tok ' + (cls || '');
      sp.textContent = t;
      el.appendChild(sp);
    });
  }

  // PM -> 诗行淡入淡出切换
  window.PM = {
    cur: '',
    setInstant: function (el, txt) {
      this.cur = txt || '';
      renTok(el, tok(this.cur), 'in');
      if (window.renderMath) window.renderMath(el);
    },
    morph: function (el, nxt) {
      var self = this;
      var fr = tok(this.cur || '');
      var to = tok(nxt || '');
      // 先淡出旧 token，再换新并淡入
      renTok(el, fr, 'in');
      var nds = Array.prototype.slice.call(el.querySelectorAll('.tok'));
      nds.forEach(function (n) { n.classList.add('out'); });
      setTimeout(function () {
        renTok(el, to, 'out');
        requestAnimationFrame(function () {
          Array.prototype.forEach.call(el.querySelectorAll('.tok'), function (n) {
            n.classList.remove('out');
            n.classList.add('in');
          });
        });
        self.cur = nxt || '';
        if (window.renderMath) window.renderMath(el);
      }, 280);
    }
  };
})();
