/* Explicit synchronization batches use cached, scoped status reads only. */
(function () {
  'use strict';
  document.querySelectorAll('[data-job-progress]').forEach(function (panel) {
    const initial = JSON.parse(panel.querySelector('[data-job-initial]').textContent);
    const labels = JSON.parse(panel.querySelector('[data-job-labels]').textContent);
    const messages = JSON.parse(panel.querySelector('[data-job-messages]').textContent);
    const summary = panel.querySelector('[data-job-summary]');
    const retry = panel.querySelector('[data-job-retry]');
    const statusURL = new URL(initial.status_url, location.href);
    const expectedIDs = initial.jobs.map(function (job) { return job.job_id; });
    const maxChecks = 120;
    let checks = 0;
    let failures = 0;
    let timer = null;
    let controller = null;
    let stopped = initial.terminal;
    let suspended = false;
    let requestVersion = 0;
    let refreshed = new URL(location.href).searchParams.get('jobs_refreshed') === '1';

    function pause(message, retryLabel) {
      stopped = true;
      clearTimeout(timer);
      summary.textContent = message;
      retry.textContent = retryLabel;
      retry.hidden = false;
    }

    function display(batch) {
      if (!Array.isArray(batch.jobs) || batch.jobs.length !== expectedIDs.length ||
          !Object.prototype.hasOwnProperty.call(messages, batch.state) ||
          typeof batch.terminal !== 'boolean') throw new Error('Invalid status');
      batch.jobs.forEach(function (job, index) {
        if (job.job_id !== expectedIDs[index] || !Object.prototype.hasOwnProperty.call(labels, job.status)) {
          throw new Error('Invalid job');
        }
      });
      batch.jobs.forEach(function (job) {
        const row = panel.querySelector('[data-progress-job="' + job.job_id + '"]');
        row.dataset.state = job.status;
        row.querySelector('[data-job-state]').textContent = labels[job.status];
        row.querySelector('[data-job-message]').textContent = job.queue_pending ? panel.dataset.queuePending : messages[job.status];
        const recovery = row.querySelector('[data-connection-recovery]');
        if (recovery) recovery.hidden = job.status !== 'auth_failed';
      });
      summary.textContent = messages[batch.state];
      panel.dataset.state = batch.state;
      if (batch.terminal) {
        stopped = true;
        retry.hidden = true;
        // A failed API stage can still publish a fresh Git snapshot. Refresh
        // every completed batch once while retaining its actual outcomes.
        if (!refreshed) {
          refreshed = true;
          if (batch.successful) summary.textContent = panel.dataset.refreshing;
          const destination = new URL(location.href);
          destination.searchParams.set('jobs_refreshed', '1');
          location.replace(destination.href);
        }
      }
    }

    async function check() {
      if (stopped) return;
      if (checks >= maxChecks) {
        pause(panel.dataset.stillRunning, panel.dataset.refreshLabel);
        return;
      }
      checks += 1;
      const version = ++requestVersion;
      const activeController = new AbortController();
      controller = activeController;
      const timeout = setTimeout(function () { activeController.abort(); }, 10000);
      try {
        const response = await fetch(statusURL.href, {
          credentials: 'same-origin', cache: 'no-store',
          headers: {Accept: 'application/json'}, signal: activeController.signal
        });
        if (!response.ok) throw new Error('Status unavailable');
        const batch = await response.json();
        if (stopped || version !== requestVersion) return;
        display(batch);
        failures = 0;
        retry.hidden = true;
      } catch (error) {
        if (stopped || version !== requestVersion) return;
        failures += 1;
        summary.textContent = panel.dataset.unavailable;
        if (failures >= 3) pause(panel.dataset.unavailable, panel.dataset.retryLabel);
      } finally {
        clearTimeout(timeout);
        if (controller === activeController) controller = null;
        if (!stopped && version === requestVersion) timer = setTimeout(check, Math.min(2000 * (failures + 1), 6000));
      }
    }

    retry.addEventListener('click', function () {
      if (!stopped) return;
      checks = failures = 0;
      stopped = false;
      retry.hidden = true;
      check();
    });
    window.addEventListener('pagehide', function () {
      suspended = !stopped;
      stopped = true;
      requestVersion += 1;
      clearTimeout(timer);
      if (controller) controller.abort();
    });
    window.addEventListener('pageshow', function (event) {
      if (event.persisted && suspended) {
        suspended = false;
        stopped = false;
        check();
      }
    });
    if (statusURL.origin !== location.origin) {
      pause(panel.dataset.unavailable, panel.dataset.retryLabel);
      retry.disabled = true;
    } else if (!stopped) check();
  });
}());
