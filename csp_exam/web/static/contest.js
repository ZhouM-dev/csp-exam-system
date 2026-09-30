/* ============================================================
   CSP 训练站 · 共用前端脚本（倒计时 + 悬浮窗）

   倒计时的截止时刻**由服务端下发**（渲染比赛页时算好，见 ui.timer_bar），
   前端只负责走秒：

       initCountdown(el, bar)                          时间戳在 bar 的 data-* 上
       initCountdown(el, bar, deadline)                deadline = Unix 秒
       initCountdown(el, bar, deadline, {now: 服务端的"现在"})

   原型里用 sessionStorage 存「本场结束时刻」，换标签页 / 重开页面会重新起算，
   真考试会算错；这里一个键都不存，刷新多少次都是同一个截止时刻。

   这份文件放在 csp_exam/web/static/contest.js，由 ui.py 的 page() 内联进每个
   页面（原因：线上 /static/ 目前只路由了 katex）。以后 server.py 里加了
   /static/ 路由，也可以改成用 script 标签引入 /static/contest.js。

   注意：这个文件会被内联进 <script> 里，所以正文里**不能出现闭合的 script 标签**
   （哪怕是注释里也不行，HTML 解析器见到它就提前结束脚本，整页 JS 全废）。
   ============================================================ */
(function () {
  'use strict';

  var URGENT_SEC = 30 * 60;      // 最后 30 分钟计时条转红

  function two(n) { return (n < 10 ? '0' : '') + n; }

  /** 秒 → HH:MM:SS（小时不封顶，对齐真实系统的 254:08:59） */
  function fmt(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    sec = Math.floor(sec);
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    return (h < 10 ? '0' : '') + h + ':' + two(m) + ':' + two(s);
  }

  /** 把「毫秒时间戳 / 秒时间戳 / 剩余秒数」统一成毫秒时间戳；认不出来返回 0。

      服务端下发的是**秒**（JS 的 Date.now() 是毫秒，差 1000 倍，所以得认一下）。
      只有小到不可能是时间戳（< 1e9，约 2001 年）时才按「剩余秒数」兜底：
      那种用法是「从现在起算」，刷新页面就会重置，**只适合原型演示**。 */
  function toMs(v) {
    var n = Number(v);
    if (!isFinite(n) || n <= 0) return 0;
    if (n > 1e11) return n;              // 毫秒时间戳
    if (n > 1e9) return n * 1000;        // 秒时间戳（服务端下发的那种）
    return Date.now() + n * 1000;        // 兜底：剩余秒数
  }

  function attr(node, names) {
    if (!node || !node.getAttribute) return null;
    for (var i = 0; i < names.length; i++) {
      var v = node.getAttribute(names[i]);
      if (v !== null && v !== '') return v;
    }
    return null;
  }

  /**
   * 启动倒计时。
   * @param {HTMLElement} el       显示时间的元素（.timer-value）
   * @param {HTMLElement} bar      外层容器（切紧急/结束配色，也用来读 data-*）
   * @param {number} [deadline]    截止时刻；不给就从 bar 的 data-deadline / data-deadline-ms 读
   * @param {object} [opts]        {now: 服务端下发的"现在"（同 deadline 的单位）}
   * @returns {number|null}        定时器 id（没有截止时刻就返回 null，不倒计）
   */
  window.initCountdown = function (el, bar, deadline, opts) {
    if (!el) return null;

    var end = (deadline === undefined || deadline === null || deadline === '')
      ? toMs(attr(bar, ['data-deadline', 'data-deadline-ms']))
      : toMs(deadline);
    if (!end) {                  // 没设截止时刻：不猜、不"从现在起算"
      el.textContent = '--:--:--';
      return null;
    }

    /* 学生机时钟可能不准：拿服务端下发的"现在"和本地时钟比一次，算出偏移。
       之后每秒只走本地时间，不再依赖客户端的绝对时间。 */
    var serverNow = (opts && opts.now !== undefined)
      ? toMs(opts.now)
      : toMs(attr(bar, ['data-server-now', 'data-now']));
    var skewMs = serverNow ? (serverNow - Date.now()) : 0;
    var timer = null;

    function leftSec() {
      return Math.max(0, Math.floor((end - (Date.now() + skewMs)) / 1000));
    }

    function tick() {
      var left = leftSec();
      el.textContent = fmt(left);
      if (bar && bar.classList) {
        bar.classList.toggle('timer-urgent', left > 0 && left <= URGENT_SEC);
        bar.classList.toggle('timer-over', left <= 0);
      }
      if (left > 0) return;
      if (timer) { clearInterval(timer); timer = null; }
      if (bar) {
        var note = bar.querySelector('.timer-note');
        var over = bar.getAttribute('data-over-note');
        if (note && over) note.textContent = over;
      }
      var form = document.querySelector('form[data-submit-guard]');
      if (form) form.classList.add('is-closed');
    }

    tick();
    timer = setInterval(tick, 1000);
    return timer;
  };

  /** 还剩多少秒（0 = 已结束或没设截止时刻）。页面做别的判断时可以用。 */
  window.countdownLeft = function (bar) {
    var end = toMs(attr(bar, ['data-deadline', 'data-deadline-ms']));
    if (!end) return 0;
    return Math.max(0, Math.floor((end - Date.now()) / 1000));
  };

  /* ================= 悬浮窗（配合 ui.py 的 modal() 外壳）=================
     打开：点 [data-modal-open="id"] 的元素（按钮/链接都行）
     关闭：点 [data-modal-close] 或 .modal-close、点遮罩空白处、按 Esc */
  function asModal(m) {
    if (!m) return null;
    return typeof m === 'string' ? document.getElementById(m) : m;
  }

  window.openModal = function (m) {
    m = asModal(m);
    if (!m) return;
    m.hidden = false;
    document.body.style.overflow = 'hidden';
  };

  window.closeModal = function (m) {
    m = asModal(m);
    if (!m) return;
    m.hidden = true;
    document.body.style.overflow = '';
  };

  window.closeAllModals = function () {
    var list = document.querySelectorAll('.modal-mask');
    for (var i = 0; i < list.length; i++) list[i].hidden = true;
    document.body.style.overflow = '';
  };

  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var opener = t.closest('[data-modal-open]');
    if (opener) {
      var target = opener.getAttribute('data-modal-open') || opener.getAttribute('href') || '';
      if (target.charAt(0) === '#') target = target.slice(1);
      openModal(target);
      return;
    }
    if (t.closest('[data-modal-close]') || t.closest('.modal-close')) {
      closeModal(t.closest('.modal-mask'));
      return;
    }
    if (t.classList && t.classList.contains('modal-mask')) closeModal(t);   // 点遮罩
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' && e.key !== 'Esc') return;
    var open = document.querySelectorAll('.modal-mask:not([hidden])');
    if (!open.length) return;
    for (var i = 0; i < open.length; i++) open[i].hidden = true;
    document.body.style.overflow = '';
  });
})();
