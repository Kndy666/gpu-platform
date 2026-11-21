(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const number = value => Number.isFinite(Number(value)) ? String(Math.round(Number(value) * 10) / 10) : '—';
  const adminMenu = $('[data-admin-menu]');
  if (adminMenu) {
    const compactNavigation = window.matchMedia('(max-width: 700px)');
    const updateNavigation = () => { adminMenu.open = !compactNavigation.matches; };
    updateNavigation();
    compactNavigation.addEventListener('change', updateNavigation);
  }
  const storageNodes = $('[data-storage-user]');
  if (storageNodes) {
    $$('[data-storage-node]', storageNodes).forEach(details => {
      const key = `storage-volumes:${storageNodes.dataset.storageUser}:${details.dataset.storageNode}`;
      try {
        if (!details.hasAttribute('data-force-open')) details.open = localStorage.getItem(key) === 'open';
      } catch (_) {}
      details.addEventListener('toggle', () => {
        try { localStorage.setItem(key, details.open ? 'open' : 'closed'); } catch (_) {}
      });
    });
  }
  const performancePanel = $('[data-performance-url]');
  if (performancePanel) {
    const input = $('[data-performance-interval]'), status = $('[data-performance-status]');
    const manual = $('[data-performance-refresh]'), pause = $('[data-performance-pause]');
    let seconds = 5, timer = null, busy = false, paused = false;
    try {
      const saved = Number(localStorage.getItem('performance-refresh-seconds'));
      if (Number.isInteger(saved) && saved >= 1 && saved <= 3600) seconds = saved;
    } catch (_) {}
    input.value = String(seconds);
    const schedule = () => {
      clearTimeout(timer);
      if (!paused && !document.hidden) timer = setTimeout(refresh, seconds * 1000);
    };
    const refresh = async () => {
      if (busy || document.hidden) return;
      clearTimeout(timer); busy = true; manual.disabled = true;
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(performancePanel.dataset.performanceUrl, {credentials:'same-origin', cache:'no-store', signal:controller.signal});
        if (!response.ok || response.redirected || !response.headers.get('content-type')?.includes('application/json')) throw new Error('refresh');
        const data = await response.json();
        performancePanel.innerHTML = data.html;
        status.textContent = paused ? gettext('Paused') : gettext('Refreshing automatically');
        performancePanel.classList.remove('performance-disconnected');
      } catch (_) {
        status.textContent = gettext('Refresh failed. Try again.');
        performancePanel.classList.add('performance-disconnected');
      } finally {
        clearTimeout(timeout); busy = false; manual.disabled = false; schedule();
      }
    };
    input.addEventListener('change', () => {
      if (!input.checkValidity() || !input.value) {input.reportValidity(); return;}
      seconds = input.valueAsNumber;
      try {localStorage.setItem('performance-refresh-seconds', String(seconds));} catch (_) {}
      schedule();
    });
    pause.addEventListener('click', () => {
      paused = !paused; pause.textContent = paused ? gettext('Resume') : gettext('Pause');
      pause.setAttribute('aria-pressed', String(paused)); status.textContent = paused ? gettext('Paused') : gettext('Refreshing automatically'); schedule();
    });
    manual.addEventListener('click', refresh);
    document.addEventListener('visibilitychange', schedule);
    schedule();
  }
  $$('[data-stop-form]').forEach(form => form.addEventListener('submit', () => {
    const button = $('button[type=submit]', form), pending = button.dataset.pendingLabel;
    button.disabled = true; button.title = pending; button.setAttribute('aria-label', pending);
    if (!button.hasAttribute('data-icon-action')) button.textContent = pending;
  }));
  $$('[data-token-reveal]').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.tokenReveal);
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    button.title = visible ? gettext('Hide token') : gettext('Show token');
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-pressed', String(visible));
    button.querySelector('use').setAttribute('href', visible ? '#i-eye-off' : '#i-eye');
  }));
  $$('[data-resource-settings]').forEach(form => {
    const unlimited = $('[name=unlimited]', form);
    const update = () => $$('[data-quota-fields]', form).forEach(panel => {
      panel.hidden = unlimited.checked;
      $$('input', panel).forEach(input => {input.disabled = unlimited.checked; });
    });
    unlimited.addEventListener('change', update);
    if (form.dataset.memberCreate === 'true') {
      const group = $('[name=group]', form), defaults = JSON.parse($('#member-group-defaults').textContent);
      group.addEventListener('change', () => {
        const values = defaults[group.value];
        if (!values) return;
        Object.entries(values).forEach(([name, value]) => {
          const field = $(`[name="${name}"]`, form);
          if (!field) return;
          if (field.type === 'checkbox') field.checked = value;
          else field.value = value ?? '';
        });
        update();
      });
    }
    update();
  });
  const requestForm = $('[data-request-mode]');
  if (requestForm) {
    const sourceWorkspaces = JSON.parse($('#source-workspaces').textContent);
    const definitions = JSON.parse($('#allocation-definitions').textContent);
    const allocationCapacity = JSON.parse($('#allocation-capacity').textContent);
    const shareMaximum = (values, caps, total, minimum) => {
      const fixed = values.reduce((sum, value) => sum + (value === 'max' ? 0 : Number(value)), 0);
      let available = Math.floor(Math.min(total ?? caps.reduce((sum, value) => sum + value, 0), caps.reduce((sum, value) => sum + value, 0)) - fixed);
      const result = values.map(value => value === 'max' ? 0 : Number(value));
      let pending = values.map((value, index) => value === 'max' ? index : -1).filter(index => index >= 0);
      if (available < pending.length * minimum || result.some((value, index) => values[index] !== 'max' && (value < minimum || value > caps[index]))) return null;
      while (available > 0 && pending.length) {
        const share = Math.max(1, Math.floor(available / pending.length));
        pending.forEach(index => {const amount = Math.min(share, caps[index] - result[index], available); result[index] += amount; available -= amount;});
        pending = pending.filter(index => result[index] < caps[index]);
      }
      return result.every(value => value >= minimum) ? result : null;
    };
    let previousSource = requestForm.dataset.bound === 'true' ? $('#id_source_environment')?.value : null;
    const compactNumber = value => Number.isFinite(Number(value)) ? String(Math.round(Number(value) * 10) / 10) : '—';
    $$('[data-allocation-group]', requestForm).forEach(editor => {
      const hidden = $('input[type=hidden]', editor), container = $('[data-allocation-rows]', editor);
      const count = $('[data-allocation-count="' + editor.dataset.allocationGroup + '"]', requestForm), table = $('[data-allocation-table]', editor);
      const resources = definitions[editor.dataset.allocationGroup];
      const initialValue = resource => {
        const amount = Number(resource.default ?? resource.minimum ?? 1);
        const limit = resource.per_allocation_limit ?? resource.maximum;
        return limit == null ? amount : Math.min(amount, Number(limit));
      };
      let rows = [];
      try {rows = JSON.parse(hidden.value || '[]');} catch (_) {}
      if (!Array.isArray(rows)) rows = [];
      const resolveResource = resource => {
        const increment = Math.min(1, Number(resource.step ?? 1));
        const toUnits = value => Number(value) / increment;
        const capacities = (allocationCapacity.gpus || []).map(device =>
          Math.floor(toUnits(Math.min(Number(resource.per_allocation_limit ?? resource.maximum ?? Number.MAX_SAFE_INTEGER),
            Number(resource.allocation_field === 'memory_gib' ? device.memory_total_gib : resource.maximum ?? Number.MAX_SAFE_INTEGER)))));
        capacities.sort((a, b) => b - a);
        const caps = rows.map((_, index) => capacities[index] ??
          Math.floor(toUnits(resource.maximum ?? resource.limit ?? initialValue(resource))));
        const values = rows.map(row => row[resource.key] === 'max' ? 'max' : toUnits(row[resource.key] ?? initialValue(resource)));
        const resolved = shareMaximum(values, caps,
          resource.limit == null ? null : Math.floor(toUnits(resource.limit)), Math.ceil(toUnits(resource.minimum ?? 1)));
        return resolved?.map(amount => amount * increment);
      };
      const refreshMaximum = () => resources.forEach(resource => {
        const resolved = resolveResource(resource);
        $$('[data-resource-key="' + resource.key + '"]', container).forEach((input, index) => {
          if (rows[index][resource.key] === 'max') input.value = resolved ? String(resolved[index]) : '';
        });
      });
      const summary = document.querySelector('[data-allocation-summary="' + editor.dataset.allocationGroup + '"]');
      const save = () => {
        hidden.value = JSON.stringify(rows);
        if (summary) summary.textContent = String(rows.length);
        resources.forEach(resource => {
          const total = document.querySelector('[data-allocation-total="' + editor.dataset.allocationGroup + ':' + resource.key + '"]');
          const resolved = resolveResource(resource);
          if (total) total.textContent = resolved ? String(resolved.reduce((sum, value) => sum + value, 0)) : '—';
        });
      };
      const render = () => {
        count.value = String(rows.length); table.hidden = rows.length === 0; editor.hidden = rows.length === 0;
        container.replaceChildren();
        rows.forEach((values, index) => {
          const row = document.createElement('tr'), title = document.createElement('th');
          title.scope = 'row'; title.textContent = String(index + 1); row.append(title);
          resources.forEach(resource => {
            const cell = document.createElement('td'), wrap = document.createElement('div');
            const input = document.createElement('input'), maximum = document.createElement('button');
            wrap.className = 'gpu-value-control';
            input.type = 'number';
            const resolved = resolveResource(resource);
            input.value = values[resource.key] === 'max' ? resolved ? String(resolved[index]) : '' : String(values[resource.key] ?? initialValue(resource));
            input.min = resource.minimum ?? 0; input.step = resource.step || (resource.integral ? 1 : 'any');
            if (resource.per_allocation_limit != null || resource.maximum != null) input.max = resource.per_allocation_limit ?? resource.maximum;
            input.setAttribute('aria-label', resource.label_text + ' ' + (index + 1));
            input.dataset.resourceKey = resource.key; input.required = true; input.readOnly = values[resource.key] === 'max';
            input.addEventListener('input', () => {values[resource.key] = input.value; save(); refreshMaximum();});
            maximum.type = 'button'; maximum.textContent = gettext('Maximum');
            maximum.setAttribute('aria-pressed', String(values[resource.key] === 'max'));
            maximum.addEventListener('click', () => {values[resource.key] = values[resource.key] === 'max' ? Number(input.value) || initialValue(resource) : 'max'; save(); render();});
            wrap.append(input, maximum); cell.append(wrap); row.append(cell);
          });
          container.append(row);
        });
      };
      count.addEventListener('change', () => {
        if (!count.checkValidity()) {count.reportValidity(); return;}
        const wanted = count.valueAsNumber;
        if (wanted === rows.length) return;
        rows = rows.slice(0, wanted);
        while (rows.length < wanted) rows.push(Object.fromEntries(resources.map(resource => [resource.key, initialValue(resource)])));
        save(); render();
      });
      save(); render();
    });
    const update = () => {
      const environment = $('[name=source_kind]:checked', requestForm)?.value === 'environment';
      $$('[data-source]', requestForm).forEach(element => {
        const shown = element.dataset.source === (environment ? 'environment' : 'image'); element.hidden = !shown;
        $$('input,select', element).forEach(input => {input.disabled = !shown;});
      });
      const workspace = document.getElementById('id_volume:work'), source = $('#id_source_environment')?.value;
      if (workspace && environment && source !== previousSource && sourceWorkspaces[source]) workspace.value = sourceWorkspaces[source].size_gib;
      previousSource = environment ? source : null;
      $$('[data-preview]').forEach(element => {element.textContent = compactNumber(document.getElementById('id_' + element.dataset.preview)?.value || 0);});
      if (requestForm.dataset.requestMode === 'interactive') {
        const hours = (new Date($('#id_ends_at').value) - new Date($('#id_starts_at').value)) / 3600000;
        $('[data-duration]').textContent = hours > 0 ? interpolate(gettext('%(hours)s hours'), {hours: Math.round(hours * 10) / 10}, true) : gettext('Select a time range');
      }
    };
    $('#id_node', requestForm).addEventListener('change', event => {
      const url = new URL(window.location.href); url.searchParams.set('node', event.target.value); url.searchParams.delete('image'); url.searchParams.delete('source'); window.location.assign(url);
    });
    requestForm.addEventListener('input', update); requestForm.addEventListener('change', update); update();
  }
  const imageSync = $('[data-image-sync]');
  if (imageSync) {
    const image = $('#sync-image'), target = $('#image-target');
    const update = () => {
      const selected = image.selectedOptions[0];
      imageSync.action = image.value || '';
      [...target.options].forEach(option => {
        option.disabled = !!option.value && (option.value === selected?.dataset.node || option.dataset.architecture !== selected?.dataset.architecture);
      });
      if (target.selectedOptions[0]?.disabled) target.value = '';
    };
    image.addEventListener('change', update); update();
    imageSync.addEventListener('submit', event => {if (!image.value) event.preventDefault();});
  }
  const datasetSync = document.querySelector('form[action$="/datasets/"]');
  if (datasetSync && $('#sync-source', datasetSync)) {
    const source = $('#sync-source'), target = $('#sync-target');
    const update = () => {
      [...target.options].forEach(option => {option.disabled = option.value === source.value;});
      if (target.selectedOptions[0]?.disabled) target.value = [...target.options].find(option => !option.disabled)?.value || '';
    };
    source.addEventListener('change', update); update();
  }
  const syncJobs = $('[data-sync-jobs]');
  if (syncJobs) {
    let refreshing = false;
    const refreshSyncJobs = async () => {
      if (refreshing || document.hidden) return;
      refreshing = true;
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(location.href, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
        if (!response.ok || response.redirected) return;
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        const rows = $('[data-sync-jobs]', page);
        if (rows) syncJobs.replaceChildren(...rows.childNodes);
      } catch (_) {} finally {clearTimeout(timeout); refreshing = false;}
    };
    setInterval(refreshSyncJobs, 5000);
    document.addEventListener('visibilitychange', refreshSyncJobs);
  }
  const transport = document.querySelector('.node-form [name=transport]');
  if (transport) transport.addEventListener('change', () => {
    const url = new URL(location.href); url.searchParams.set('transport', transport.value); location.assign(url);
  });
  const capacityPanel=$('[data-capacity-url]');
  if(capacityPanel) {
    let refreshing=false;
    const showCapacity=data=>{
      const quantity = (value, unit) => {
        const sizes = {B: 1, KiB: 1024, MiB: 1024**2, GiB: 1024**3, TiB: 1024**4};
        if (!(unit in sizes)) return number(value) + ' ' + (unit === 'percent' ? '%' : unit === 'cores' ? gettext('cores') : unit);
        const bytes = Number(value) * sizes[unit];
        const [label, divisor] = Object.entries(sizes).reverse().find(([, size]) => Math.abs(bytes) >= size || size === 1);
        return number(bytes / divisor) + ' ' + label;
      };
      $('[data-capacity-stale]').hidden=data.fresh;
      const list=$('[data-capacity-resources]'); list.replaceChildren();
      data.resources.filter(resource=>resource.volume_kind !== "home" && !resource.allocation_group && resource.accounting !== "image").forEach(resource=>{
        const row=document.createElement('div'), label=document.createElement('span'), available=document.createElement('strong'), quota=document.createElement('span');
        label.textContent=resource.label; available.textContent=quantity(resource.available, resource.unit);
        quota.textContent=gettext('Quota available')+': '+(resource.quota_available===null?gettext('Unlimited'):quantity(resource.quota_available, resource.unit));
        row.append(label,available,quota);
        if (resource.quota_reserved != null) {
          const reserved = document.createElement('span');
          reserved.textContent = gettext('Reserved by you') + ' ' + quantity(resource.quota_reserved, resource.unit);
          row.append(reserved);
        }
        list.append(row);
      });
      const gpus = $('[data-capacity-gpus]');
      if (gpus && Array.isArray(data.gpus)) {
        gpus.replaceChildren();
        data.gpus.forEach(gpu => {
          const tile = document.createElement('article'), heading = document.createElement('h3');
          tile.className = 'capacity-gpu'; heading.textContent = (data.device_label || 'GPU') + ' ' + gpu.index; tile.append(heading);
          const wholeGiB = Math.floor(gpu.memory_gib), remainingMiB = (gpu.memory_gib - wholeGiB) * 1024;
          const memory = wholeGiB ? wholeGiB + ' GiB' + (remainingMiB ? ' ' + number(remainingMiB) + ' MiB' : '') :
            quantity(gpu.memory_gib, 'GiB');
          [[gettext('Compute'), number(gpu.compute_percent) + '%', gpu.compute_percent],
           [data.device_memory_label || gettext('GPU memory'), memory, gpu.memory_gib * 100 / gpu.memory_total_gib]].forEach(([title, amount, percent]) => {
            const row = document.createElement('div'), label = document.createElement('span'), value = document.createElement('strong');
            label.textContent = title; value.textContent = amount; row.append(label, value);
            const meter = document.createElement('div'), fill = document.createElement('i');
            meter.className = 'meter'; fill.style.width = Math.max(0, Math.min(100, percent || 0)) + '%'; meter.append(fill);
            tile.append(row, meter);
          });
          gpus.append(tile);
        });
      }
    };
    const refresh=async()=>{
      if(refreshing||document.hidden)return;refreshing=true;
      try{const response=await fetch(capacityPanel.dataset.capacityUrl + '?node=' + encodeURIComponent(document.querySelector('[name=node]')?.value || ''),{cache:'no-store'});if(response.ok)showCapacity(await response.json());}
      catch(_){$('[data-capacity-stale]').hidden=false;}finally{refreshing=false;}
    };
    setInterval(refresh,15000);document.addEventListener('visibilitychange',refresh);
  }
  $$('[data-copy]').forEach(button => {
    const label = button.textContent, title = button.title, aria = button.getAttribute('aria-label');
    const icon = button.querySelector('use'), href = icon?.getAttribute('href');
    let reset;
    button.addEventListener('click', async () => {
    const target = document.getElementById(button.dataset.copy);
    const text = (target?.value ?? target?.textContent)?.trim(); if (!text) return;
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else { const input = document.createElement('textarea'); input.value = text; input.style.position = 'fixed'; input.style.opacity = '0'; document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); }
    clearTimeout(reset);
    if (icon && button.hasAttribute('data-icon-action')) {
      icon.setAttribute('href', '#i-check'); button.title = gettext('Copied'); button.setAttribute('aria-label', gettext('Copied'));
    } else button.textContent = gettext('Copied');
    reset = setTimeout(() => {
      if (icon && button.hasAttribute('data-icon-action')) {
        icon.setAttribute('href', href); button.title = title; button.setAttribute('aria-label', aria);
      } else button.textContent = label;
    }, 1600);
    });
  });
  const watch = $('[data-task-watch]');
  if (watch) {
    let checking = false;
    const refreshTask = async () => {
      if (checking || document.hidden) return;
      checking = true;
      try {
        const response = await fetch(watch.dataset.taskWatch, {cache: 'no-store'});
        if (!response.ok) return;
        const state = await response.json();
        if (state.status !== watch.dataset.status ||
            String(state.ssh_port ?? '') !== watch.dataset.sshPort ||
            state.stop_requested !== (watch.dataset.stopRequested === 'true') ||
            (state.error || '') !== watch.dataset.error) location.reload();
      } catch (_) {} finally { checking = false; }
    };
    refreshTask();
    setInterval(refreshTask, 5000);
    document.addEventListener('visibilitychange', refreshTask);
  }
  const panel = $('[data-timeline]');
  if (!panel) return;
  const dateInput = $('[data-timeline-date]', panel), tooltip = $('.timeline-tooltip', panel);
  const today = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
  dateInput.value = today();
  const node = (tag, className, text) => { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; };
  const formatTime = iso => iso ? new Intl.DateTimeFormat(document.documentElement.lang, {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit'}).format(new Date(iso)) : gettext('End time pending');
  let controller;
  const expandedNodes = new Set();
  function allocationRow(label, blocks, data, compact = false, toggle = null) {
    const element = node('div', 'allocation-row' + (toggle ? ' node-timeline-summary' : ''));
    const title = toggle || node('div', 'allocation-label', label.name);
    if (!toggle && label.detail) title.append(node('small', '', label.detail));
    element.append(title);
    const track = node('div', 'allocation-track'); element.append(track);
    const lanes = [];
    for (const block of [...blocks].sort((a, b) => a.left - b.left)) {
      let lane = lanes.findIndex(end => end <= block.left); if (lane < 0) lane = lanes.length;
      lanes[lane] = block.left + block.width;
      const bar = node(block.url ? 'a' : 'div', 'allocation-block ' + block.state + (block.open ? ' open' : ''));
      if (block.url) bar.href = block.url;
      bar.style.left = block.left + '%'; bar.style.width = block.width + '%'; bar.style.top = (8 + lane * 34) + 'px';
      bar.append(node('span', '', block.title));
      if (compact && block.amount) bar.append(node('b', '', block.amount));
      const deviceLabel = block.device_label || 'GPU';
      const gpuDetails = (block.gpu_requests || []).map((spec, index) => `${deviceLabel} ${index + 1}: ${number(spec.compute_percent)}%  ${number(spec.memory_gib)} GiB`).join(' / ');
      const detail = `${block.title}${block.owner ? '  ' + block.owner : ''}\n${formatTime(block.start)} → ${formatTime(block.end)}\n${gpuDetails || `${number(block.gpu)} ${deviceLabel}`}  ${number(block.cpu)} ${gettext('cores')}  ${number(block.memory)} GiB`;
      bar.setAttribute('aria-label', detail.replaceAll('\n', ' '));
      if (block.open && data.now !== null) {
        const tail = node('div', 'allocation-tail'); tail.style.left = (block.left + block.width) + '%';
        tail.style.width = Math.max(0, 100 - block.left - block.width) + '%'; tail.style.top = (22 + lane * 34) + 'px'; track.append(tail);
      }
      const showTooltip = (x, y) => {
        tooltip.textContent = detail; tooltip.hidden = false;
        tooltip.style.left = Math.max(8, Math.min(x + 14, innerWidth - 310)) + 'px';
        tooltip.style.top = Math.max(8, Math.min(y + 14, innerHeight - 120)) + 'px';
      };
      bar.addEventListener('pointermove', event => showTooltip(event.clientX, event.clientY));
      bar.addEventListener('pointerleave', () => tooltip.hidden = true);
      bar.addEventListener('focus', () => { const rect = bar.getBoundingClientRect(); showTooltip(rect.left, rect.bottom); });
      bar.addEventListener('blur', () => tooltip.hidden = true); track.append(bar);
    }
    if (toggle && !blocks.length) track.append(node('span', 'allocation-empty', label.queued.length ? gettext('Waiting to start') : gettext('Idle')));
    track.style.minHeight = Math.max(compact ? 43 : 58, lanes.length * 34 + 16) + 'px';
    if (data.now !== null) { const line = node('div', 'now-line'); line.style.left = data.now + '%'; track.append(line); }
    return element;
  }
  async function load() {
    controller?.abort();
    const requestController = new AbortController(); controller = requestController;
    panel.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(panel.dataset.timeline + '?date=' + dateInput.value, {signal: requestController.signal}); if (!response.ok) return;
      const data = await response.json();
      if (requestController.signal.aborted) return;
      const container = $('[data-timeline-rows]', panel), fragment = document.createDocumentFragment();
      for (const group of data.groups) {
        const section = node('div', 'timeline-node'); section.dataset.node = group.id;
        const toggle = node('button', 'allocation-label timeline-toggle', group.name); toggle.type = 'button';
        const details = node('div', 'timeline-details'); details.id = 'timeline-node-' + group.id;
        details.hidden = !expandedNodes.has(group.id);
        toggle.setAttribute('aria-expanded', String(!details.hidden)); toggle.setAttribute('aria-controls', details.id);
        toggle.addEventListener('click', () => {
          details.hidden = !details.hidden; toggle.setAttribute('aria-expanded', String(!details.hidden));
          if (details.hidden) expandedNodes.delete(group.id); else expandedNodes.add(group.id);
        });
        section.append(allocationRow(group, group.entries, data, false, toggle));
        if (group.queued.length) {
          const queue = node('div', 'timeline-queue'), items = node('div', 'timeline-queue-items');
          queue.append(node('span', 'timeline-queue-label', gettext('Queued ') + group.queued.length));
          for (const task of group.queued) {
            const item = node(task.url ? 'a' : 'div', 'timeline-queue-item');
            if (task.url) item.href = task.url;
            item.append(node('span', '', task.title), node('small', '', task.status_label));
            item.title = `${task.title}  ${task.status_label}  ${number(task.gpu)} ${task.device_label || 'GPU'}  ${number(task.cpu)} ${gettext('cores')}  ${number(task.memory_gib)} GiB`;
            items.append(item);
          }
          queue.append(items); section.append(queue);
        }
        for (const row of group.rows) {
          const blocks = group.blocks.filter(block => block.row === row.id);
          if (row.id.startsWith('reservations') && !blocks.length) continue;
          details.append(allocationRow(row, blocks, data, true));
        }
        section.append(details); fragment.append(section);
      }
      container.replaceChildren(fragment); tooltip.hidden = true;
    } catch (error) { if (error.name !== 'AbortError') console.debug('Timeline refresh delayed'); }
    finally { if (controller === requestController) panel.setAttribute('aria-busy', 'false'); }
  }
  dateInput.addEventListener('change', load);
  $$('[data-day]',panel).forEach(button=>button.addEventListener('click',()=>{const date=new Date(dateInput.value+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+Number(button.dataset.day));dateInput.value=date.toISOString().slice(0,10);load();}));
  $('[data-today]',panel).addEventListener('click',()=>{dateInput.value=today();load();});
  load();setInterval(load,30000);
})();

document.querySelectorAll('[data-node-select=request]').forEach(select => select.addEventListener('change', () => { const url = new URL(location.href); url.searchParams.set('node', select.value); url.searchParams.delete('image'); url.searchParams.delete('source'); location.assign(url); }));

document.querySelectorAll('[data-node-filter]').forEach(select => {
  select.addEventListener('change', () => select.form.requestSubmit());
});
document.querySelectorAll('[data-dataset-node]').forEach(select => select.addEventListener('change', () => { select.form.elements.path.value = ''; select.form.requestSubmit(); }));
