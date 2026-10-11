/* One Tabler upload area; preserve native form navigation and every relative path. */
(function () {
  'use strict';
  const MAX_FILES = 1024;
  function validate(entries, maxBytes) {
    if (!entries.length) throw new Error('文件夹为空，请选择包含代码的考号文件夹。');
    if (entries.length > MAX_FILES) throw new Error('文件数量超过 1024 个，请移除编译产物后重新选择。');
    let bytes = 0;
    const names = new Set();
    for (const entry of entries) {
      const path = entry.path;
      if (!path || path.startsWith('/') || path.split('/').some(p => !p || p === '.' || p === '..')) {
        throw new Error('文件路径无效，请重新选择完整考号文件夹。');
      }
      if (names.has(path)) throw new Error('存在重复文件路径，请重新选择。');
      names.add(path);
      bytes += entry.file.size;
      if (bytes > maxBytes) throw new Error('所选内容超过上传大小上限，请移除编译产物后重试。');
    }
    return bytes;
  }
  function writeFormData(data, entries) {
    data.delete('files'); data.delete('zipfile');
    for (const entry of entries) data.append('files', entry.file, entry.path);
  }
  async function walk(root, maxBytes) {
    const entries = [];
    let bytes = 0;
    async function visit(node, path, depth) {
      if (depth > 32) throw new Error('文件夹层级过深，请检查考号目录。');
      if (node.isFile) {
        const file = await new Promise((resolve, reject) => node.file(resolve, reject));
        entries.push({file, path}); bytes += file.size;
        if (entries.length > MAX_FILES || bytes > maxBytes) throw new Error('文件数量或大小超限，请移除编译产物后重试。');
      } else if (node.isDirectory) {
        const reader = node.createReader();
        // Directory readers may return only the first 100 entries per call.
        for (;;) {
          const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
          if (!batch.length) break;
          for (const child of batch) await visit(child, path + '/' + child.name, depth + 1);
        }
      } else throw new Error('不能读取所选内容，请使用选择按钮或打包 ZIP。');
    }
    await visit(root, root.name, 0);
    validate(entries, maxBytes);
    return entries;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {validate, writeFormData, walk};
  if (typeof document === 'undefined') return;
  document.querySelectorAll('[data-unified-upload]').forEach(form => {
    const drop = form.querySelector('[data-upload-drop]');
    const folder = form.querySelector('#upload-folder'), zip = form.querySelector('#upload-zip');
    const selection = form.querySelector('[data-upload-selection]');
    const status = form.querySelector('[data-upload-status]');
    const submit = form.querySelector('[data-upload-submit]');
    const maxBytes = Number(form.dataset.maxBytes);
    let entries = [], revision = 0, reading = false, submitting = false;
    function message(text, error) {
      status.textContent = text;
      status.classList.toggle('text-danger', !!error);
      status.classList.toggle('text-secondary', !error);
      status.setAttribute('role', error ? 'alert' : 'status');
    }
    function clear() {
      revision++; entries = []; reading = false; folder.value = ''; zip.value = '';
      selection.hidden = true; submit.disabled = true;
      message('尚未选择文件。选择内容后再提交。', false);
    }
    function choose(next, title) {
      try {
        const bytes = validate(next, maxBytes);
        entries = next; selection.hidden = false; submit.disabled = false;
        form.querySelector('[data-upload-name]').textContent = title;
        form.querySelector('[data-upload-meta]').textContent = next.length + ' 个文件 · ' + (bytes / 1024).toFixed(1) + ' KiB';
        const list = form.querySelector('[data-upload-list]'); list.replaceChildren();
        next.slice(0, 100).forEach(entry => {
          const li = document.createElement('li'); li.textContent = entry.path; list.appendChild(li);
        });
        if (next.length > 100) {
          const li = document.createElement('li'); li.textContent = '另有 ' + (next.length - 100) + ' 个文件，全部会提交。'; list.appendChild(li);
        }
        message('已选择，尚未提交。提交后由回执核对目录与文件名。', false);
      } catch (error) { entries = []; selection.hidden = true; submit.disabled = true; message(error.message, true); }
    }
    form.querySelectorAll('[data-upload-picker]').forEach(button => button.addEventListener('click', () => {
      if (!submitting) (button.dataset.uploadPicker === 'folder' ? folder : zip).click();
    }));
    folder.addEventListener('change', () => {
      if (!folder.files.length) return;
      revision++; reading = false; zip.value = '';
      choose(Array.from(folder.files, file => ({file, path: file.webkitRelativePath})), folder.files[0].webkitRelativePath.split('/')[0] + '/');
    });
    zip.addEventListener('change', () => {
      if (!zip.files.length) return;
      revision++; reading = false; folder.value = '';
      const file = zip.files[0];
      if (!/\.zip$/i.test(file.name)) { clear(); message('请选择 ZIP 压缩包。', true); return; }
      choose([{file, path: file.name}], file.name);
    });
    form.querySelector('[data-upload-clear]').addEventListener('click', clear);
    for (const type of ['dragenter', 'dragover', 'dragleave', 'drop']) drop.addEventListener(type, event => {
      event.preventDefault(); event.stopPropagation();
      drop.classList.toggle('border-primary', type === 'dragenter' || type === 'dragover');
    });
    drop.addEventListener('drop', async event => {
      if (submitting) return;
      const ticket = ++revision;
      // Read handles synchronously before the protected DataTransfer expires.
      const items = Array.from(event.dataTransfer.items || []).filter(item => item.kind === 'file');
      const handles = items.map(item => item.webkitGetAsEntry ? item.webkitGetAsEntry() : null);
      const files = Array.from(event.dataTransfer.files || []);
      entries = []; selection.hidden = true; submit.disabled = true; folder.value = ''; zip.value = '';
      reading = true; message('正在读取提交内容…', false);
      try {
        if (items.length !== 1) throw new Error('每次请拖入一个完整考号文件夹或一个 ZIP。');
        const root = handles[0];
        if (root && root.isDirectory) {
          const next = await walk(root, maxBytes);
          if (ticket === revision) choose(next, root.name + '/');
        } else {
          const file = files[0];
          if (!file || !/\.zip$/i.test(file.name)) throw new Error('请拖入完整考号文件夹或 ZIP，不要单独拖入代码文件。');
          if (ticket === revision) choose([{file, path: file.name}], file.name);
        }
      } catch (error) { if (ticket === revision) message(error.message || '读取失败，请改用选择按钮。', true); }
      finally { if (ticket === revision) reading = false; }
    });
    form.addEventListener('formdata', event => writeFormData(event.formData, entries));
    form.addEventListener('submit', event => {
      if (reading || submitting || !entries.length || form.classList.contains('is-closed')) {
        event.preventDefault(); message(form.classList.contains('is-closed') ? '比赛已结束，不能提交。' : '请先选择文件夹或 ZIP。', true); return;
      }
      submitting = true; submit.disabled = true; submit.textContent = '正在提交…';
      form.querySelectorAll('[data-upload-picker], [data-upload-clear], [data-upload-choose]').forEach(button => { button.disabled = true; });
      message('正在上传，请等待交卷回执。', false);
      // Restore controls if navigation fails or the browser returns from its back/forward cache.
    });
    window.addEventListener('pageshow', () => {
      submitting = false; submit.disabled = !entries.length; submit.textContent = '提交我的代码';
      form.querySelectorAll('[data-upload-picker], [data-upload-clear], [data-upload-choose]').forEach(button => { button.disabled = false; });
    });
  });
})();
