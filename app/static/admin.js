(function () {
  'use strict';
  var prefix = 'gev-import:';
  var currentUrl = new URL(location.href);
  var completed = currentUrl.searchParams.get('imported_connection');
  if (completed && /^\d+$/.test(completed)) {
    try { sessionStorage.removeItem(prefix + completed); } catch (_) { /* Storage may be disabled. */ }
    currentUrl.searchParams.delete('imported_connection');
    history.replaceState(history.state, '', currentUrl.pathname + currentUrl.search + currentUrl.hash);
  }
  var errors = document.getElementById('form-errors');
  if (errors) errors.focus();
  var form = document.querySelector('[data-import-basket]');
  if (!form) return;
  var key = prefix + form.dataset.importBasket;
  var stored = {};
  try { stored = JSON.parse(sessionStorage.getItem(key) || '{}'); } catch (_) { /* Continue without storage. */ }
  if (!stored || typeof stored !== 'object') stored = {};
  var selected = new Map();
  Object.entries(stored.items || {}).forEach(function (entry) {
    if (entry[0] && entry[0].length <= 255 && typeof entry[1] === 'string') selected.set(entry[0], entry[1]);
  });
  var initial = JSON.parse(form.querySelector('[data-import-initial]').textContent);
  if (form.querySelector('[data-import-recovery]')) {
    var recovered = new Map();
    initial.forEach(function (id) { recovered.set(id, selected.get(id) || id); });
    selected = recovered;
  }
  var boxes = Array.from(form.querySelectorAll('input[name="external_ids"]'));
  var project = form.querySelector('[name="project_id"]');
  if (!form.querySelector('[data-import-recovery]') && !new URL(location.href).searchParams.has('project_id') && stored.project) {
    if (Array.from(project.options).some(function (option) { return option.value === stored.project; })) project.value = stored.project;
  }
  boxes.forEach(function (box) { if (selected.has(box.value)) selected.set(box.value, box.dataset.name); });
  var limit = form.querySelector('[data-import-limit]');
  function update() {
    boxes.forEach(function (box) { box.checked = selected.has(box.value); });
    form.querySelector('[data-selected-count]').textContent = form.dataset.countMessage.replace('{count}', selected.size);
    var list = form.querySelector('[data-selection-list]');
    list.replaceChildren();
    selected.forEach(function (name, id) {
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = name + ' ×';
      remove.setAttribute('aria-label', form.dataset.removeLabel + ': ' + name);
      remove.addEventListener('click', function () { selected.delete(id); update(); });
      list.appendChild(remove);
    });
    limit.hidden = selected.size <= 50;
    limit.textContent = form.dataset.limitMessage;
    document.querySelector('[data-import-project-filter]').value = project.value;
    document.querySelectorAll('[data-import-pagination] a').forEach(function (link) {
      var url = new URL(link.href);
      url.searchParams.set('project_id', project.value);
      link.href = url.pathname + url.search;
    });
    try { sessionStorage.setItem(key, JSON.stringify({ items: Object.fromEntries(selected), project: project.value })); } catch (_) { /* Page selection still works. */ }
  }
  boxes.forEach(function (box) {
    box.addEventListener('change', function () {
      if (box.checked) selected.set(box.value, box.dataset.name);
      else selected.delete(box.value);
      update();
    });
  });
  form.querySelector('[data-select-page]').addEventListener('click', function () {
    boxes.forEach(function (box) { selected.set(box.value, box.dataset.name); });
    update();
  });
  form.querySelector('[data-deselect-page]').addEventListener('click', function () {
    boxes.forEach(function (box) { selected.delete(box.value); });
    update();
  });
  project.addEventListener('change', update);
  form.addEventListener('submit', function (event) {
    if (!selected.size || selected.size > 50) {
      event.preventDefault();
      limit.hidden = false;
      limit.textContent = form.dataset.limitMessage;
      return;
    }
    var hidden = form.querySelector('[data-import-hidden]');
    hidden.replaceChildren();
    var visible = new Set(boxes.map(function (box) { return box.value; }));
    selected.forEach(function (_, id) {
      if (visible.has(id)) return;
      var input = document.createElement('input');
      input.type = 'hidden'; input.name = 'external_ids'; input.value = id;
      hidden.appendChild(input);
    });
  });
  // Back/forward may restore controls and the old in-memory basket. Storage is
  // the source of truth after an import succeeds in this tab.
  window.addEventListener('pageshow', function () {
    var saved;
    try { saved = JSON.parse(sessionStorage.getItem(key) || '{}'); } catch (_) { return; }
    selected = new Map();
    if (saved && typeof saved === 'object') {
      Object.entries(saved.items || {}).forEach(function (entry) {
        if (entry[0] && entry[0].length <= 255 && typeof entry[1] === 'string') selected.set(entry[0], entry[1]);
      });
    }
    form.querySelector('[data-import-hidden]').replaceChildren();
    update();
  });
  update();
})();
