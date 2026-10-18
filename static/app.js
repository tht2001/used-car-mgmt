document.addEventListener('DOMContentLoaded', function () {
  // CSRF：给每个表单追加 hidden input[name=csrf_token]（若尚未存在），
  // token 取自 <meta name="csrf-token">
  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute('content') : '';
  if (token) {
    document.querySelectorAll('form').forEach(function (form) {
      if (!form.querySelector('input[name="csrf_token"]')) {
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'csrf_token';
        input.value = token;
        form.appendChild(input);
      }
    });
  }

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
