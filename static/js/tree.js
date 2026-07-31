(function () {
  // 滚到底 + 滑条旁数字联动
  var strm = document.getElementById('chat-stream');
  if (strm) {
    strm.scrollTop = strm.scrollHeight;
  }
  document.querySelectorAll('.sliders input[type=range]').forEach(function (inp) {
    var sp = inp.parentElement.querySelector('span');
    inp.addEventListener('input', function () {
      if (sp) sp.textContent = inp.value;
    });
  });
})();
