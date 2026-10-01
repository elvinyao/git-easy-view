(function ($) {
  'use strict';
  $(function () {
    const dialog = document.getElementById('project-inspector');
    if (!dialog || !dialog.showModal) return;
    const body = $('#project-inspector-body'), feedback = $('.inspector-feedback');
    const keys = ['inspect_repo', 'inspect_branch', 'inspect_target', 'inspect_commit', 'inspect_detail'];
    let selection, opener;
    function relativeTimes(root) {
      const formatter = new Intl.RelativeTimeFormat(document.documentElement.lang || 'en', {numeric: 'auto'});
      $(root).find('time[data-relative-time]').each(function () {
        let value = this.getAttribute('datetime');
        if (!/(Z|[+-]\d\d:\d\d)$/.test(value)) value += 'Z';
        const seconds = (Date.parse(value) - Date.now()) / 1000;
        if (!Number.isFinite(seconds)) return;
        const unit = Math.abs(seconds) < 60 ? ['second', 1] : Math.abs(seconds) < 3600 ? ['minute', 60] : Math.abs(seconds) < 86400 ? ['hour', 3600] : ['day', 86400];
        this.textContent = formatter.format(Math.round(seconds / unit[1]), unit[0]);
      });
    }
    function clearSelection(url) {keys.forEach(function (key) {url.searchParams.delete(key);}); return url;}
    function stopRequest() {
      const previous = selection;
      selection = null;
      if (previous && previous.request) previous.request.abort();
      body.removeAttr('aria-busy');
    }
    function closeDrawer(update) {
      stopRequest();
      if (dialog.open) dialog.close();
      if (update) history.replaceState(null, '', clearSelection(new URL(location.href)));
      if (opener && opener.isConnected) opener.focus({preventScroll: true});
    }
    function loadInspection(endpoint, push) {
      const url = new URL(endpoint, location.origin);
      const repository = /^\/repositories\/(\d+)\/inspect$/.exec(url.pathname);
      if (url.origin !== location.origin || !repository || !/^\d+$/.test(url.searchParams.get('branch_id') || '')) return;
      const previous = selection, current = {};
      selection = current;
      if (previous && previous.request) previous.request.abort();
      const project = clearSelection(new URL(location.href));
      url.searchParams.set('return_to', project.pathname + project.search + (project.hash || '#repository-' + repository[1]));
      url.searchParams.set('lang', document.documentElement.lang);
      if (push) {
        project.searchParams.set('inspect_repo', repository[1]);
        for (const pair of [['branch', 'branch_id'], ['target', 'target_id'], ['commit', 'commit'], ['detail', 'detail']]) {
          if (url.searchParams.has(pair[1])) project.searchParams.set('inspect_' + pair[0], url.searchParams.get(pair[1]));
        }
        history.pushState(null, '', project);
      }
      body.empty().attr('aria-busy', 'true');
      feedback.text(dialog.dataset.loading);
      if (!dialog.open) {dialog.showModal(); dialog.querySelector('.inspector-close').focus();}
      current.request = $.ajax({url: url.pathname + url.search, dataType: 'html', timeout: 20000})
        .done(function (html) {
          if (selection !== current) return;
          body.html(html); feedback.empty(); relativeTimes(body);
        }).fail(function (_request, status) {
          if (selection !== current || status === 'abort') return;
          feedback.text(dialog.dataset.failed + ' ').append($('<button>', {type: 'button', class: 'inspector-retry', text: dialog.dataset.retry}).on('click', function () {loadInspection(url, false);}));
        }).always(function () {if (selection === current) body.removeAttr('aria-busy');});
    }
    function restoreSelection() {
      const params = new URL(location.href).searchParams;
      const repo = params.get('inspect_repo'), branch = params.get('inspect_branch');
      if (!/^\d+$/.test(repo || '') || !/^\d+$/.test(branch || '')) {closeDrawer(false); return;}
      const endpoint = new URL('/repositories/' + repo + '/inspect', location.origin);
      endpoint.searchParams.set('branch_id', branch);
      for (const pair of [['target', 'target_id'], ['commit', 'commit'], ['detail', 'detail']]) {
        if (params.has('inspect_' + pair[0])) endpoint.searchParams.set(pair[1], params.get('inspect_' + pair[0]));
      }
      loadInspection(endpoint, false);
    }
    $(document).on('click', '[data-inspect-url]', function (event) {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.which > 1) return;
      event.preventDefault(); opener = this; loadInspection(this.dataset.inspectUrl, true);
    });
    dialog.addEventListener('cancel', function (event) {event.preventDefault(); closeDrawer(true);});
    $('.inspector-close').on('click', function () {closeDrawer(true);});
    $(dialog).on('click', '[data-copy]', function () {
      if (!navigator.clipboard) {feedback.text(dialog.dataset.copyFailed); return;}
      navigator.clipboard.writeText(this.dataset.copy).then(function () {feedback.text(dialog.dataset.copySuccess);}, function () {feedback.text(dialog.dataset.copyFailed);});
    });
    $(window).on('popstate', function () {
      const params = new URL(location.href).searchParams;
      const compact = params.get('density') === 'compact';
      const opened = new Set((params.get('open_repos') || '').split(','));
      const closed = new Set((params.get('closed_repos') || '').split(','));
      const legacy = new Set((params.get('expanded') || '').split(','));
      $('.repository-branches').each(function () {
        const id = this.closest('[data-overview-repository]').dataset.overviewRepository;
        this.open = !closed.has(id) && (!compact || opened.has(id) || legacy.has(id));
      });
      restoreSelection();
    });
    $(window).on('pagehide', stopRequest);
    function revealTarget(hash, force) {
      if (!hash) return;
      const target = document.getElementById(hash.slice(1));
      if (!target) return;
      const repository = target.closest('[data-overview-repository]');
      const closed = new Set((new URL(location.href).searchParams.get('closed_repos') || '').split(','));
      if (repository && (force || !closed.has(repository.dataset.overviewRepository))) {
        const details = repository.querySelector('.repository-branches');
        details.open = true;
        persistRepository(details);
      }
    }
    function persistRepository(details) {
        const url = new URL(location.href), compact = url.searchParams.get('density') === 'compact';
        const id = details.closest('[data-overview-repository]').dataset.overviewRepository;
        for (const key of ['open_repos', 'closed_repos']) {
          const ids = new Set((url.searchParams.get(key) || '').split(',').filter(Boolean));
          if (key === 'closed_repos' ? !details.open : compact && details.open) ids.add(id); else ids.delete(id);
          const value = Array.from(ids).slice(0, 100).join(',');
          if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
          $('.project-filters input[name="' + key + '"]').val(value);
          $('.overview-list > .pagination a').each(function () {
            const target = new URL(this.href);
            if (value) target.searchParams.set(key, value); else target.searchParams.delete(key);
            this.href = target.pathname + target.search + target.hash;
          });
        }
        history.replaceState(null, '', url);
        $('.overview-refresh').attr('href', url.pathname + url.search + url.hash);
        $('.overview-open, .overview-repo-name a, .summary-count').each(function () {
          const target = new URL(this.href), match = /^\/repositories\/(\d+)/.exec(target.pathname);
          if (!match) return;
          const project = clearSelection(new URL(url));
          target.searchParams.set('return_to', project.pathname + project.search + '#repository-' + match[1]);
          this.href = target.pathname + target.search;
        });
    }
    $('.repository-branches').each(function () {
      this.addEventListener('toggle', function () {persistRepository(this);});
    });
    $('.repository-branches > summary').on('click', function (event) {
      event.preventDefault();
      const details = this.parentElement;
      details.open = !details.open;
      // Persist in this event, before an immediate refresh can cancel toggle.
      persistRepository(details);
    });
    $(document).on('click', '.overview-repo-nav a, .summary-repository, .summary-count', function () {
      if (this.pathname === location.pathname) revealTarget(this.hash, true);
    });
    $(window).on('hashchange', function () {revealTarget(location.hash, true);});
    revealTarget(location.hash);
    relativeTimes(document);
    $(document).on('gev:updated', function () {relativeTimes(document);});
    restoreSelection();
  });
})(jQuery);
