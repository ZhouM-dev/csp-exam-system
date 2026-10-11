/* Tabler dropdowns and native pickers; teacher upload routes and paths are unchanged. */
(function () {
  'use strict';
  document.querySelectorAll('#csp-admin-sidebar a.nav-link').forEach(link => {
    const target = new URL(link.href, location.href);
    if (target.pathname === location.pathname &&
        (target.searchParams.get('c') || '') === (new URL(location.href).searchParams.get('c') || '')) {
      link.classList.add('active'); link.setAttribute('aria-current', 'page');
    }
  });
  document.querySelectorAll('[data-admin-upload]').forEach(area => {
    const folder = document.getElementById(area.dataset.folderId);
    const zip = document.getElementById(area.dataset.zipId);
    const status = area.querySelector('[data-admin-upload-status]');
    if (!folder || !zip || !status) return;
    function describe(input) {
      const files = Array.from(input.files || []);
      if (!files.length) return;
      (input === folder ? zip : folder).value = '';
      const bytes = files.reduce((sum, file) => sum + file.size, 0);
      const name = input === folder ? files[0].webkitRelativePath.split('/')[0] + '/' : files[0].name;
      status.textContent = name + ' · ' + files.length + ' 个文件 · ' + (bytes / 1024 / 1024).toFixed(2) + ' MiB';
      status.classList.remove('text-danger');
    }
    area.querySelectorAll('[data-admin-picker]').forEach(button => button.addEventListener('click', () => {
      (button.dataset.adminPicker === 'folder' ? folder : zip).click();
    }));
    folder.addEventListener('change', () => describe(folder));
    zip.addEventListener('change', () => describe(zip));
    area.querySelector('[data-admin-upload-clear]').addEventListener('click', () => {
      folder.value = ''; zip.value = ''; status.textContent = '尚未选择文件。';
      folder.dispatchEvent(new Event('change')); zip.dispatchEvent(new Event('change'));
    });
  });
})();
