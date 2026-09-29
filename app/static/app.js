/* Server-rendered fragments; URL preserves filter/expansion state. */
(function ($) {
  'use strict';
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
    slot.prop('hidden', false).attr('aria-busy', 'true');
    current.request = $.ajax({url: '/repositories/' + id + '/branches?' + params.toString(), dataType: 'html'})
      .done(function (html) { if (ownsSlot()) slot.html(html); })
      .fail(function (_request, status) {
        if (!ownsSlot() || status === 'abort') return;
        slot.empty().append($('<div>', {class: 'empty', text: '分支加载失败。权限可能已变更，或服务暂不可用。 '}))
          .find('.empty').append($('<button>', {type: 'button', class: 'retry-branches', text: '重试', 'data-page': page || 1}));
      }).always(function () {if (ownsSlot()) slot.removeAttr('aria-busy');});
  }
  $(function () {
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
    let timer, attempts = 0;
    function poll(repository, job) {
      $.getJSON('/repositories/' + repository + '/jobs/' + job).done(function (data) {
        const terminal = ['succeeded', 'failed', 'superseded', 'auth_failed'].includes(data.status);
        $('.job-status').text(data.status === 'succeeded' ? '分析已完成，正在刷新…' : data.queue_pending ? '请求已保存，等待队列恢复。' : terminal ? '任务已结束，请刷新或联系管理员查看失败记录。' : '后台分析中…');
        if (data.status === 'succeeded') {const url = new URL(location.href); url.searchParams.delete('job_id'); location.replace(url);}
        else if (!terminal && ++attempts < 60) timer = setTimeout(function () {poll(repository, job);}, 3000);
      }).fail(function () {$('.job-status').text('任务状态暂不可用，请刷新后重试。');});
    }
    const job = new URL(location.href).searchParams.get('job_id');
    if (job && /^\d+$/.test(job) && $('.job-status').length) poll($('.job-status').data('repository'), job);
    $(window).on('pagehide', function () {clearTimeout(timer);});
  });
})(jQuery);
