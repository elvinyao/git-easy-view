document.addEventListener('DOMContentLoaded', () => {
  const messages = JSON.parse(document.getElementById('release-messages')?.textContent || '{}');
  const text = (element, value) => { if (element) element.textContent = value || ''; };
  const showDrawer = (id, action) => {
    const drawer = document.getElementById(id);
    if (!drawer) return;
    if (!drawer.open) drawer.showModal();
    if (action) {
      const form = drawer.querySelector('[data-operation="' + action + '"]');
      if (form) {
        const details = form.closest('details');
        if (details) details.open = true;
        form.scrollIntoView({block: 'start'});
        form.querySelector('input:not([type=hidden]),textarea,select,button')?.focus({preventScroll: true});
      }
    }
  };
  document.querySelectorAll('[data-release-open]').forEach(button => {
    button.addEventListener('click', () => showDrawer(button.dataset.releaseOpen, button.dataset.operationTarget));
  });
  document.querySelectorAll('dialog[data-open-on-error]').forEach(dialog => showDrawer(dialog.id, dialog.dataset.openOnError));
  document.querySelectorAll('[data-confirm]').forEach(button => {
    button.addEventListener('click', event => {
      if (!window.confirm(messages[button.dataset.confirm])) event.preventDefault();
    });
  });
  // These searches filter choices locally; Enter must never submit a release,
  // production registration or platform binding with the previous selection.
  document.querySelectorAll('input[type=search]').forEach(input => {
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') event.preventDefault();
    });
  });
  document.querySelectorAll('[data-select-search]').forEach(input => {
    const select = document.getElementById(input.dataset.selectSearch);
    if (!select) return;
    input.addEventListener('input', () => {
      const query = input.value.trim().toLocaleLowerCase();
      Array.from(select.options).forEach(option => {
        const matches = option.textContent.toLocaleLowerCase().includes(query);
        option.hidden = Boolean(option.value && option.value !== select.value && !matches);
      });
    });
  });

  const filterMRs = row => {
    const select = row?.querySelector('[data-mr-select]');
    if (!select) return;
    const branch = row.querySelector('[data-branch-input]')?.value;
    const target = row.querySelector('[data-merge-target]')?.value;
    if (!branch || !target) return;
    Array.from(select.options).forEach(option => {
      if (!option.value || !option.dataset.source) return;
      const matches = option.dataset.source === branch && option.dataset.target === target && option.dataset.sameRepo === '1';
      // Retain a previously selected MR, so a changed version cannot silently erase evidence.
      option.hidden = !matches && !option.selected;
      option.disabled = !matches && !option.selected;
    });
  };
  document.querySelectorAll('[data-version-fields]').forEach(group => {
    const branch = group.querySelector('[data-branch-input]');
    const tag = group.querySelector('[data-tag-input]');
    const oid = group.querySelector('[data-pinned-oid]');
    const update = (changedField = '') => {
      const option = (tag.value ? tag : branch).selectedOptions[0];
      const selectedOID = option?.dataset.oid || '';
      const repin = changedField === 'tag' || (changedField === 'branch' && !tag.value);
      const changed = oid.value && selectedOID !== oid.value && !repin;
      // Only selecting the tag again can accept a moved tag. Changing another
      // field must not silently replace a recovered, explicitly pinned version.
      if (repin || !oid.value) oid.value = selectedOID;
      text(group.querySelector('[data-version-hash]'), oid.value ? oid.value.slice(0, 8) : '—');
      group.querySelector('[data-version-hash]').title = oid.value;
      if (!changed) {
        text(group.querySelector('[data-version-subject]'), option?.dataset.subject);
        text(group.querySelector('[data-version-time]'), option?.dataset.time);
      }
      group.querySelector('[data-version-changed]').hidden = !changed;
      filterMRs(group.closest('[data-repository-version]') || group.parentElement);
    };
    branch.addEventListener('change', () => update('branch'));
    tag.addEventListener('change', () => update('tag'));
    update();
  });
  document.querySelectorAll('[data-merge-target]').forEach(target => {
    target.addEventListener('change', () => filterMRs(target.closest('[data-repository-version]')));
  });

  const wizard = document.querySelector('[data-release-wizard]');
  if (wizard) {
    const checkboxes = Array.from(wizard.querySelectorAll('[name=repos]'));
    const versionRows = Array.from(wizard.querySelectorAll('[data-repository-version]'));
    const selectedIDs = () => new Set(checkboxes.filter(input => input.checked).map(input => input.value));
    const selectedRows = () => versionRows.filter(row => selectedIDs().has(row.dataset.repositoryVersion));
    const selectionError = wizard.querySelector('[data-selection-error]');
    const syncState = () => {
      const ids = selectedIDs();
      text(wizard.querySelector('[data-selected-count]'), String(ids.size));
      versionRows.forEach(row => {
        row.hidden = !ids.has(row.dataset.repositoryVersion);
        row.disabled = row.hidden;
      });
      selectionError.hidden = ids.size <= 50;
      let ready = ids.size > 0 && ids.size <= 50;
      selectedRows().forEach(row => {
        const branch = row.querySelector('[data-branch-input]');
        const tag = row.querySelector('[data-tag-input]');
        const target = row.querySelector('[data-merge-target]');
        const oid = row.querySelector('[data-pinned-oid]').value;
        ready = ready && Boolean(tag.value && oid && tag.selectedOptions[0]?.dataset.oid === oid &&
          branch.value && !branch.selectedOptions[0]?.hasAttribute('data-unavailable') && target.value &&
          target.value !== branch.value && !target.selectedOptions[0]?.hasAttribute('data-unavailable'));
      });
      wizard.querySelector('[data-incomplete-review]').hidden = ready || ids.size === 0;
      wizard.querySelector('[name=intent][value=start]').disabled = !ready;
      return ids.size;
    };
    const validSelection = () => {
      const size = syncState();
      const valid = size > 0 && size <= 50;
      selectionError.hidden = valid;
      if (!valid) selectionError.scrollIntoView({block: 'center'});
      return valid;
    };
    checkboxes.forEach(input => input.addEventListener('change', syncState));
    wizard.querySelectorAll('[data-branch-input],[data-tag-input],[data-merge-target]').forEach(select => {
      select.addEventListener('change', syncState);
    });
    wizard.querySelector('[data-repository-search]').addEventListener('input', event => {
      const query = event.target.value.trim().toLocaleLowerCase();
      const rows = Array.from(wizard.querySelectorAll('[data-repository-choice]'));
      rows.forEach(row => { row.hidden = !row.dataset.search.includes(query); });
      wizard.querySelector('[data-repository-empty]').hidden = rows.some(row => !row.hidden);
    });
    wizard.querySelector('[data-select-visible]').addEventListener('click', () => {
      let size = selectedIDs().size;
      checkboxes.forEach(input => {
        if (!input.closest('[data-repository-choice]').hidden && !input.checked && size < 50) { input.checked = true; size++; }
      });
      syncState();
    });
    wizard.querySelector('[data-clear-selection]').addEventListener('click', () => { checkboxes.forEach(input => { input.checked = false; }); syncState(); });
    wizard.addEventListener('submit', event => {
      if (!validSelection()) event.preventDefault();
    });
    wizard.classList.add('is-enhanced');
    syncState();
    const fields = wizard.dataset.errorFields.split(',').filter(Boolean);
    fields.forEach(name => {
      const field = document.getElementById('field-' + name);
      const details = field?.closest('details');
      if (details) details.open = true;
      field?.setAttribute('aria-invalid', 'true');
    });
  }

  document.querySelectorAll('[data-list-search]').forEach(input => {
    const root = input.closest('[data-searchable-list]');
    input.addEventListener('input', () => {
      const query = input.value.toLocaleLowerCase().trim();
      const rows = Array.from(root.querySelectorAll('[data-search-row]'));
      rows.forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase().includes(query); });
      const empty = root.querySelector('[data-no-matches]');
      if (empty) empty.hidden = rows.some(row => !row.hidden);
    });
  });

  const recovery = document.querySelector('[data-form-recovery]') || wizard;
  document.querySelectorAll('#form-errors a').forEach(link => link.addEventListener('click', event => {
    const name = link.hash.slice('#field-'.length);
    const field = recovery?.elements?.namedItem(name) || document.getElementById('field-' + name);
    if (!field || field.type === 'hidden') return;
    event.preventDefault();
    for (let parent = field.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
      if (parent.tagName === 'DIALOG' && !parent.open) parent.showModal();
    }
    field.focus();
  }));

  document.querySelectorAll('[data-source-picker]').forEach(form => {
    const button = form.querySelector('[data-load-repositories]');
    const connection = form.querySelector('[name=connection_id]');
    const select = form.querySelector('[data-source-repository]');
    const manual = form.querySelector('[data-source-id]');
    const status = form.querySelector('[data-source-status]');
    const search = form.querySelector('[data-source-search]');
    let generation = 0;
    let controller;
    const load = async () => {
      const currentGeneration = ++generation;
      controller?.abort();
      controller = new AbortController();
      button.disabled = true;
      text(status, messages.loading);
      try {
        const response = await fetch(form.dataset.lookupUrl + '?connection_id=' + encodeURIComponent(connection.value), {credentials:'same-origin', headers:{Accept:'application/json'}, signal: controller.signal});
        if (!response.ok || response.redirected) throw new Error('Lookup unavailable');
        const payload = await response.json();
        if (currentGeneration !== generation) return;
        const previous = manual.value;
        select.replaceChildren(new Option(messages.selectRepository, ''));
        payload.repositories.forEach(repo => select.add(new Option(repo.full_name + (repo.default_branch ? ' · ' + repo.default_branch : ''), repo.external_id)));
        if (previous && !Array.from(select.options).some(option => option.value === previous)) select.add(new Option(previous, previous));
        select.value = previous;
        text(status, payload.incomplete ? messages.partial : payload.repositories.length ? messages.loaded : messages.noRepositories);
        search.value = '';
        select.focus();
      } catch (error) {
        if (error.name !== 'AbortError' && currentGeneration === generation) text(status, messages.lookupFailed);
      } finally {
        if (currentGeneration === generation) button.disabled = false;
      }
    };
    button.addEventListener('click', load);
    select.addEventListener('change', () => { manual.value = select.value; });
    manual.addEventListener('input', () => { select.value = Array.from(select.options).some(option => option.value === manual.value) ? manual.value : ''; });
    search.addEventListener('input', () => {
      const query = search.value.toLocaleLowerCase().trim();
      Array.from(select.options).forEach(option => { option.hidden = Boolean(option.value && option.value !== select.value && !option.textContent.toLocaleLowerCase().includes(query)); });
    });
    connection.addEventListener('change', () => {
      ++generation;
      controller?.abort();
      button.disabled = false;
      select.replaceChildren(new Option(messages.selectRepository, ''));
      manual.value = '';
      text(status, '');
    });
  });
});
