document.addEventListener('DOMContentLoaded', function () {
  // 提交前确认
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      var msg = form.getAttribute('data-confirm');
      if (!confirm(msg)) {
        e.preventDefault();
      }
    });
  });

  // flash 消息 4 秒后淡出
  setTimeout(function () {
    document.querySelectorAll('.flash').forEach(function (el) {
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 700);
    });
  }, 4000);
});
