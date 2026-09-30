/* ============================================================
   CSP 训练站 · 考场脚本（本地原型）
   倒计时：真实系统右上角是「距离考试结束：254:08:59」常驻大号计时。
   静态原型里用「浏览器会话内的截止时刻」来算，刷新页面不会重置
   （真实系统用的是服务端时间，接后端时换成服务端下发的时间戳即可）。
   ============================================================ */

(function () {
  'use strict';

  var KEY = 'csp:exam-deadline';

  function two(n) { return (n < 10 ? '0' : '') + n; }

  function fmt(sec) {
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    return (h < 10 ? '0' : '') + h + ':' + two(m) + ':' + two(s);
  }

  /**
   * 启动倒计时。
   * @param {HTMLElement} el      显示时间的元素
   * @param {HTMLElement} bar     外层容器（用于切换紧急/结束配色）
   * @param {number} totalSeconds 本场考试时长（秒）
   */
  window.initCountdown = function (el, bar, totalSeconds) {
    if (!el) return;

    var deadline = parseInt(sessionStorage.getItem(KEY) || '0', 10);
    // 没有存过、或者存的已经过期超过 1 分钟，就重新起算
    if (!deadline || deadline < Date.now() - 60000) {
      deadline = Date.now() + totalSeconds * 1000;
      sessionStorage.setItem(KEY, String(deadline));
    }

    function tick() {
      var left = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
      el.textContent = fmt(left);
      if (bar) {
        bar.classList.toggle('timer-urgent', left > 0 && left <= 1800);  // 最后 30 分钟转红
        bar.classList.toggle('timer-over', left <= 0);
      }
      if (left <= 0) {
        clearInterval(timer);
        var note = bar && bar.querySelector('.timer-note');
        if (note) note.textContent = '考试已结束，提交通道已关闭（真实考场以监考指令为准）';
        var form = document.querySelector('form[data-submit-guard]');
        if (form) form.classList.add('is-closed');
      }
    }

    tick();
    var timer = setInterval(tick, 1000);
  };

  /** 把倒计时重置（原型里用来演示"重新开始一场"）。 */
  window.resetCountdown = function () {
    sessionStorage.removeItem(KEY);
  };
})();
