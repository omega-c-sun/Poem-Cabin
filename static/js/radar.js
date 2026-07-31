(function () {
  // normSc -> 把七维分数统一成 0-100
  function normSc(sc) {
    var ks = ['rhyme', 'rhythm', 'tension', 'paradox', 'metaphor', 'freshness', 'depth'];
    return ks.map(function (k) {
      var v = sc && sc[k];
      if (v == null) return 0;
      return v <= 1 ? Math.round(v * 100) : Math.round(v);
    });
  }

  // mkOpt -> 组装 echarts 雷达配置
  function mkOpt(sc) {
    var labs = (window.UI_I18N && window.UI_I18N.radar) || ['韵脚', '节律', '张力', '悖论', '隐喻', '新鲜', '哲深'];
    return {
      animationDuration: 450,
      radar: {
        center: ['50%', '52%'],
        radius: '56%',
        indicator: labs.map(function (n) {
          return { name: n, max: 100 };
        }),
        splitArea: { areaStyle: { color: ['rgba(47,93,80,0.04)', 'rgba(47,93,80,0.1)'] } },
        axisName: {
          color: '#4a4a4a',
          fontSize: 12,
          fontFamily: 'DM Sans, PingFang SC, sans-serif',
          padding: [3, 4],
          formatter: function (name) { return name; }
        },
        nameGap: 12,
        splitNumber: 4
      },
      series: [{
        type: 'radar',
        data: [{
          value: normSc(sc),
          areaStyle: { color: 'rgba(184,107,60,0.28)' },
          lineStyle: { color: '#b86b3c' },
          itemStyle: { color: '#2f5d50' }
        }]
      }]
    };
  }

  // RL -> 雷达图挂载/更新
  window.RL = {
    chart: null,
    mount: function (el, sc) {
      if (!el || typeof echarts === 'undefined') return;
      if (!this.chart) this.chart = echarts.init(el);
      this.chart.setOption(mkOpt(sc || {}), true);
      var self = this;
      window.addEventListener('resize', function () {
        if (self.chart) self.chart.resize();
      });
    },
    update: function (sc) {
      if (!this.chart) {
        var el = document.getElementById('radar');
        if (el) this.mount(el, sc);
        return;
      }
      // 更新后稍等布局再 resize，避免 dock 动画中尺寸错
      this.chart.setOption(mkOpt(sc || {}));
      setTimeout(function () {
        if (RL.chart) RL.chart.resize();
      }, 320);
    },
    resize: function () {
      if (this.chart) this.chart.resize();
    }
  };

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.radar').forEach(function (el) {
      var raw = el.getAttribute('data-scores') || '{}';
      var sc = {};
      try { sc = JSON.parse(raw); } catch (e) {}
      RL.mount(el, sc);
    });
  });
})();
