/* Server-rendered fragments; URL preserves filter/expansion state. */
(function ($) {
  'use strict';
  const messages = JSON.parse(document.getElementById('i18n-messages').textContent);
  const branchLoads = new WeakMap();
  function updateURL(key, value) {
    const url = new URL(window.location.href);
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    history.replaceState(null, '', url);
    // Keep GET form submissions aligned with expansion/pagination changes.
    if (key === 'expanded' || /^bp_\d+$/.test(key)) {
      $('.filters').each(function () {
        let input = $(this).find('input[type=hidden]').filter(function () {return this.name === key;});
        if (!input.length) input = $('<input>', {type: 'hidden', name: key}).appendTo(this);
        input.val(value || '');
      });
    }
  }
  function loadBranches(slot, page) {
    const element = slot[0], previous = branchLoads.get(element), current = {};
    // Transfer ownership before abort: abort may synchronously call fail/always.
    branchLoads.set(element, current);
    if (previous && previous.request) previous.request.abort();
    const ownsSlot = function () {return branchLoads.get(element) === current;};
    const id = slot.data('repo-id'), params = new URLSearchParams(window.location.search);
    params.set('page', page || 1);
    params.set('fragment', '1');
    slot.prop('hidden', false).attr('aria-busy', 'true');
    current.request = $.ajax({url: '/repositories/' + id + '/branches?' + params.toString(), dataType: 'html'})
      .done(function (html) { if (ownsSlot()) slot.html(html); })
      .fail(function (_request, status) {
        if (!ownsSlot() || status === 'abort') return;
        slot.empty().append($('<div>', {class: 'empty', text: messages.branches_failed + ' '}))
          .find('.empty').append($('<button>', {type: 'button', class: 'retry-branches', text: messages.retry, 'data-page': page || 1}));
      }).always(function () {if (ownsSlot()) slot.removeAttr('aria-busy');});
  }
  $(function () {
    $('.filters').on('submit', function () {
      // A narrower filter can invalidate the previous branch page. Expansion
      // stays shareable; pagination starts at the first matching branch.
      $(this).find('input[type=hidden]').filter(function () {return /^bp_\d+$/.test(this.name);}).remove();
    });
    $('.expand').each(function () { if ($(this).data('expanded')) loadBranches($('#branches-' + $(this).data('repo-id')), $(this).data('page')); });
    $('.branch-slot[data-auto]').each(function () { loadBranches($(this), $(this).data('page')); });
    $(document).on('click', '.expand', function () {
      const button = $(this), id = String(button.data('repo-id')), expanded = button.attr('aria-expanded') !== 'true';
      const ids = new Set((new URL(window.location.href).searchParams.get('expanded') || '').split(',').filter(Boolean));
      button.attr('aria-expanded', expanded); const slot = $('#branches-' + id);
      if (expanded) {ids.add(id); loadBranches(slot, new URL(window.location.href).searchParams.get('bp_' + id) || 1);} else {ids.delete(id); slot.prop('hidden', true);}
      updateURL('expanded', Array.from(ids).slice(0, 100).join(','));
    });
    $(document).on('click', '.branch-slot .branch-page, .retry-branches', function (event) {
      event.preventDefault(); const slot = $(this).closest('.branch-slot'), page = $(this).data('page');
      updateURL(slot.data('auto') ? 'page' : 'bp_' + slot.data('repo-id'), page); loadBranches(slot, page);
    });
    const workspace = $('.git-workspace');
    if (workspace.length) {
      let selection;
      const content = $('#git-content'), status = $('#git-load-status');
      function syncSelection(url) {
        const branch = url.searchParams.get('branch_id');
        $('.git-branch-link').each(function () {
          const selected = branch ? String($(this).data('branch-id')) === branch : $(this).hasClass('git-all-branches');
          $(this).toggleClass('selected', selected).attr('aria-current', String(selected));
          // Keep branch and commit links rooted in the current filter/page state.
          const target = new URL(this.href);
          for (const key of ['q', 'role', 'relation', 'page', 'lang', 'return_to', 'target_id', 'repo_q', 'attention']) {
            if (url.searchParams.has(key)) target.searchParams.set(key, url.searchParams.get(key)); else target.searchParams.delete(key);
          }
          this.href = target.toString();
        });
        $('.git-tree-pagination a').each(function () {
          const target = new URL(this.href);
          for (const key of ['branch_id', 'commit', 'detail']) {
            if (url.searchParams.has(key)) target.searchParams.set(key, url.searchParams.get(key)); else target.searchParams.delete(key);
          }
          this.href = target.toString();
        });
        $('.git-toolbar-filters .git-icon-button').attr('href', url.toString());
      }
      function loadLog(url, push) {
        const previous = selection, current = {};
        selection = current;
        if (previous && previous.request) previous.request.abort();
        content.attr('aria-busy', 'true');
        status.text(messages.log_loading);
        current.request = $.ajax({url: '/repositories/' + workspace.data('repository') + '/log' + url.search, dataType: 'html'})
          .done(function (html) {
            if (selection !== current) return;
            content.html(html);
            if (push) history.pushState(null, '', url);
            syncSelection(url);
            status.text(messages.log_hint);
          }).fail(function (_request, result) {
            if (selection !== current || result === 'abort') return;
            status.text(messages.log_failed + ' ').append($('<a>', {href: url.toString(), text: messages.retry}));
          }).always(function () {if (selection === current) content.removeAttr('aria-busy');});
      }
      workspace.on('click', '.git-branch-link, .git-commit-link', function (event) {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.which > 1) return;
        event.preventDefault();
        loadLog(new URL(this.href), true);
      });
      workspace.on('change', '.git-toolbar-filters select', function () {document.getElementById('git-filters').requestSubmit();});
      workspace.on('click', '.git-collapse-groups', function () {$('.git-branch-group').prop('open', false);});
      function showDetail(tab, focus) {
        $('[data-git-tab]').each(function () {
          const selected = $(this).data('git-tab') === tab;
          $(this).attr({'aria-selected': String(selected), tabindex: selected ? '0' : '-1'});
          if (selected && focus) this.focus();
        });
        $('#git-commit-details').prop('hidden', tab !== 'commit');
        $('#git-branch-details').prop('hidden', tab !== 'branch');
        const url = new URL(location.href);
        url.searchParams.set('detail', tab);
        history.replaceState(null, '', url);
        syncSelection(url);
      }
      workspace.on('click', '[data-git-tab]', function () {showDetail($(this).data('git-tab'), false);});
      workspace.on('keydown', '[data-git-tab]', function (event) {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        showDetail(event.key === 'Home' ? 'commit' : event.key === 'End' ? 'branch' : $(this).data('git-tab') === 'commit' ? 'branch' : 'commit', true);
      });
      workspace.on('click', '[data-copy]', function () {
        if (!navigator.clipboard) {status.text(messages.copy_failed); return;}
        navigator.clipboard.writeText($(this).attr('data-copy')).then(function () {status.text(messages.hash_copied);}, function () {status.text(messages.copy_failed);});
      });
      $(window).on('popstate', function () {loadLog(new URL(location.href), false);});
    }
    let timer, attempts = 0;
    function poll(repository, job) {
      $.getJSON('/repositories/' + repository + '/jobs/' + job).done(function (data) {
        const terminal = ['succeeded', 'failed', 'superseded', 'auth_failed'].includes(data.status);
        $('.job-status').text(data.status === 'succeeded' ? messages.job_succeeded : data.queue_pending ? messages.job_pending : terminal ? messages.job_finished : messages.job_running);
        if (data.status === 'succeeded') {const url = new URL(location.href); url.searchParams.delete('job_id'); location.replace(url);}
        else if (!terminal && ++attempts < 60) timer = setTimeout(function () {poll(repository, job);}, 3000);
      }).fail(function () {$('.job-status').text(messages.job_unavailable);});
    }
    const job = new URL(location.href).searchParams.get('job_id');
    if (job && /^\d+$/.test(job) && $('.job-status').length) poll($('.job-status').data('repository'), job);
    $(window).on('pagehide', function () {clearTimeout(timer);});
  });
})(jQuery);
