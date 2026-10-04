(function () {
  'use strict';
  var form = document.querySelector('[data-source-form]');
  if (!form) return;
  var kind = form.querySelector('[name="kind"]');
  var path = form.querySelector('[name="local_path"]');
  var dialog = document.querySelector('[data-directory-dialog]');
  var opener = form.querySelector('[data-directory-open]');
  var controller;
  var current;
  var requested;
  var parent;
  function sourceChanged() {
    var local = kind.value === 'local';
    form.querySelector('[data-local-source]').hidden = !local;
    path.disabled = !local;
    path.required = local;
    form.querySelectorAll('[data-remote-source]').forEach(function (node) {
      node.hidden = local;
      node.querySelectorAll('input').forEach(function (input) { input.disabled = local; });
    });
    form.querySelector('[name="base_url"]').required = !local;
    var save = form.querySelector('[data-source-save]');
    save.textContent = local ? save.dataset.localLabel : save.dataset.remoteLabel;
  }
  sourceChanged();
  kind.addEventListener('change', sourceChanged);
  var status = dialog.querySelector('[data-directory-status]');
  var list = dialog.querySelector('[data-directory-list]');
  var select = dialog.querySelector('[data-directory-select]');
  var up = dialog.querySelector('[data-directory-parent]');
  async function load(value) {
    if (controller) controller.abort();
    var owned = new AbortController();
    controller = owned;
    requested = value;
    select.disabled = true;
    up.disabled = true;
    list.replaceChildren();
    status.textContent = dialog.dataset.loadLabel;
    var url = '/admin/local/directories' + (value ? '?path=' + encodeURIComponent(value) : '');
    try {
      var response = await fetch(url, { signal: owned.signal, headers: { Accept: 'application/json' } });
      if (!response.ok || response.redirected) throw new Error('Directory unavailable');
      var result = await response.json();
      if (controller !== owned || !dialog.open) return;
      current = result.path;
      parent = result.parent;
      dialog.querySelector('[data-directory-current]').textContent = current;
      up.disabled = !parent;
      select.disabled = false;
      status.textContent = result.directories.length ? '' : dialog.dataset.emptyLabel;
      result.directories.forEach(function (entry) {
        var item = document.createElement('li');
        var button = document.createElement('button');
        button.type = 'button';
        button.textContent = entry.name + (entry.git ? ' · ' + dialog.dataset.gitLabel : '');
        button.addEventListener('click', function () { load(entry.path); });
        item.appendChild(button);
        list.appendChild(item);
      });
    } catch (error) {
      if (controller !== owned || error.name === 'AbortError') return;
      status.textContent = dialog.dataset.errorLabel;
    }
  }
  opener.addEventListener('click', function () { dialog.showModal(); load(path.value); });
  dialog.querySelector('[data-directory-close]').addEventListener('click', function () { dialog.close(); });
  dialog.querySelector('[data-directory-retry]').addEventListener('click', function () { load(requested); });
  up.addEventListener('click', function () { if (parent) load(parent); });
  select.addEventListener('click', function () { path.value = current; dialog.close(); path.focus(); });
  dialog.addEventListener('close', function () { if (controller) controller.abort(); });
})();
