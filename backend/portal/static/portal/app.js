(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
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
        status.textContent = paused ? '已暂停' : '自动刷新中';
        performancePanel.classList.remove('performance-disconnected');
      } catch (_) {
        status.textContent = '刷新失败，请重试';
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
      paused = !paused; pause.textContent = paused ? '继续' : '暂停';
      pause.setAttribute('aria-pressed', String(paused)); status.textContent = paused ? '已暂停' : '自动刷新中'; schedule();
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
    button.title = visible ? '隐藏令牌' : '显示令牌';
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-pressed', String(visible));
    button.querySelector('use').setAttribute('href', visible ? '#i-eye-off' : '#i-eye');
  }));
  $$('[data-resource-settings]').forEach(form => {
    const unlimited = $('[name=unlimited]', form);
    const update = () => $$('[data-quota-fields]', form).forEach(panel => {
      panel.hidden = unlimited.checked;
      $$('input', panel).forEach(input => {input.disabled = unlimited.checked; input.required = !unlimited.checked;});
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
    const gpuData = $('#id_gpu_requests',requestForm), gpuRows = $('[data-gpu-rows]',requestForm);
    const gpuLimits = JSON.parse($('#gpu-request-limits').textContent);
    const sourceWorkspaces=JSON.parse($('#source-workspaces').textContent);
    let previousSource=requestForm.dataset.bound==='true'?$('#id_source_environment')?.value:null;
    let gpuSettings = [], renderedGpuCount = -1;
    try { const value=JSON.parse(gpuData.value); if (Array.isArray(value)) gpuSettings=value; } catch (_) {}
    const shareMaximum = (values,caps,total,minimum) => {
      if (values.some((v,i)=>v!=='max'&&(!Number.isInteger(v)||v<minimum||v>caps[i]))) return null;
      let remaining=(total??caps.reduce((sum,v)=>sum+v,0))-values.reduce((sum,v)=>sum+(v==='max'?0:v),0);
      let pending=values.flatMap((v,i)=>v==='max'?[i]:[]);
      if (remaining<pending.length*minimum||pending.some(i=>caps[i]<minimum)) return null;
      const result=values.map(v=>v==='max'?0:v);
      while (remaining>0&&pending.length) {
        const share=Math.max(1,Math.floor(remaining/pending.length));
        for (const i of pending) { const amount=Math.min(share,caps[i]-result[i],remaining);result[i]+=amount;remaining-=amount; }
        pending=pending.filter(i=>result[i]<caps[i]);
      }
      return result.every(v=>v>=minimum)?result:null;
    };
    const resolveMaximum = selected => {
      if (!selected.some(row=>Object.values(row).includes('max'))) return selected;
      let best=null,bestScore=null;
      const visit = indices => {
        if (indices.length<selected.length) {
          gpuLimits.devices.forEach((_,i)=>{if(!indices.includes(i))visit([...indices,i]);});return;
        }
        const devices=indices.map(i=>gpuLimits.devices[i]);
        const compute=shareMaximum(selected.map(r=>r.compute_percent),devices.map(()=>gpuLimits.compute_per_card_percent),gpuLimits.compute_total_percent,2);
        const memory=shareMaximum(selected.map(r=>r.memory_gib),devices.map(d=>Math.min(d.memory_gib,gpuLimits.memory_per_card_gib)),gpuLimits.memory_total_gib,1);
        if (!compute||!memory||devices.some((d,i)=>compute[i]!==100&&Math.floor(d.sm_count*compute[i]/200)*2===0)) return;
        const score=[compute.reduce((s,v)=>s+v,0),memory.reduce((s,v)=>s+v,0)];
        if (!bestScore||score[0]>bestScore[0]||(score[0]===bestScore[0]&&score[1]>bestScore[1])) {
          bestScore=score;best=compute.map((p,i)=>({compute_percent:p,memory_gib:memory[i]}));
        }
      };
      if (selected.length<=gpuLimits.devices.length) visit([]);
      return best;
    };
    const updateGpuRows = count => {
      if (renderedGpuCount !== count) {
        gpuRows.replaceChildren();
        for (let index=0;index<count;index++) {
          const values=gpuSettings[index] || {compute_percent:Math.min(100,gpuLimits.compute_per_card_percent),memory_gib:Math.min(8,gpuLimits.memory_per_card_gib||8)};
          gpuSettings[index]={...values};
          const row=document.createElement('tr'), label=document.createElement('th');
          label.scope='row';label.textContent=`第 ${index+1} 张`;row.append(label);
          for (const [key,min,max,title] of [['compute_percent',2,gpuLimits.compute_per_card_percent,'算力百分比'],['memory_gib',1,gpuLimits.memory_per_card_gib,'显存 GiB']]) {
            const cell=document.createElement('td'),control=document.createElement('div'),input=document.createElement('input'),button=document.createElement('button');
            control.className='gpu-value-control';
            input.type='number';input.step='1';input.min=String(min);if(max)input.max=String(max);
            input.required=true;input.value=values[key]==='max'?'':(values[key]??'');input.dataset.gpuKey=key;
            input.setAttribute('aria-label',`第 ${index+1} 张 GPU ${title}`);
            button.type='button';button.textContent='最大';button.dataset.gpuMax=key;button.disabled=!gpuLimits.devices.length;
            button.setAttribute('aria-label',`第 ${index+1} 张 GPU 最大${title}`);
            button.addEventListener('click',()=>{gpuSettings[index][key]=gpuSettings[index][key]==='max'?input.valueAsNumber:'max';update();});
            control.append(input,button);cell.append(control);row.append(cell);
          }
          gpuRows.append(row);
        }
        renderedGpuCount=count;
      }
      [...gpuRows.rows].forEach((row,index)=>{
        $$('input',row).forEach(input=>{const key=input.dataset.gpuKey;if(gpuSettings[index][key]!=='max')gpuSettings[index][key]=input.valueAsNumber;});
      });
      const selected=gpuSettings.slice(0,count);
      const resolved=resolveMaximum(selected);
      [...gpuRows.rows].forEach((row,index)=>{
        $$('input',row).forEach(input=>{
          const key=input.dataset.gpuKey,maximum=selected[index][key]==='max';
          input.readOnly=maximum;if(maximum)input.value=resolved?.[index][key]??'';
          $(`[data-gpu-max=${key}]`,row).setAttribute('aria-pressed',String(maximum));
        });
      });
      const error=$('[data-gpu-max-error]',requestForm),message=resolved?'':'当前配置无法同时满足各卡额度，请调整其他行或 GPU 数量。';
      error.hidden=!message;error.textContent=message;$('#id_gpu').setCustomValidity(message);
      gpuData.value=JSON.stringify(selected);
      $('[data-gpu-editor]',requestForm).hidden=!count;
      for(const [key,selector] of [['compute_percent','[data-gpu-compute-total]'],['memory_gib','[data-gpu-memory-total]']]) {
        $(selector,requestForm).textContent=resolved?.every(value=>Number.isFinite(value[key])) ? resolved.reduce((sum,value)=>sum+value[key],0) : '—';
      }
    };
    const update = () => {
      const environment = $('[name=source_kind]:checked', requestForm)?.value === 'environment';
      const gpu = Number($('#id_gpu')?.value || 0);
      updateGpuRows(gpu);
      $$('[data-gpu-field]',requestForm).forEach(el => el.hidden=!gpu);
      $$('[data-source]', requestForm).forEach(el => {
        const shown = el.dataset.source === (environment ? 'environment' : 'image');
        el.hidden = !shown;
        $$('input,select', el).forEach(input => input.disabled = !shown);
      });
      const workspace = $('#id_workspace_gib');
      if (workspace) {
        workspace.required=true;
        const source=$('#id_source_environment')?.value;
        if(environment&&source!==previousSource&&sourceWorkspaces[source])workspace.value=sourceWorkspaces[source].size_gib;
        previousSource=environment?source:null;
      }
      $$('[data-preview]').forEach(el => el.textContent = $('#id_' + el.dataset.preview)?.value || '0');
      if (requestForm.dataset.requestMode === 'interactive') {
        const from = new Date($('#id_starts_at').value), to = new Date($('#id_ends_at').value);
        const hours = (to - from) / 3600000;
        $('[data-duration]').textContent = hours > 0 ? `${Math.round(hours * 10) / 10} 小时` : '请选择时段';
      }
    };
    requestForm.addEventListener('input', update); requestForm.addEventListener('change', update); update();
  }
  const capacityPanel=$('[data-capacity-url]');
  if(capacityPanel) {
    let refreshing=false;
    const showCapacity=data=>{
      const number=v=>typeof v==='number'?String(Math.round(v*10)/10):'—';
      $('[data-capacity-stale]').hidden=data.fresh;
      $('[data-capacity-cpu]').textContent=number(data.cpu?.available);
      $('[data-capacity-memory]').textContent=number(data.memory_gib?.available);
      $('[data-capacity-storage]').textContent=number(data.storage_gib);
      $('[data-capacity-work]').textContent=data.workspace_gib.available===null?'不限':number(data.workspace_gib.available);
      const list=$('[data-capacity-gpus]');list.replaceChildren();
      data.gpus.forEach(gpu=>{
        const card=document.createElement('article'),heading=document.createElement('h3');
        card.className='capacity-gpu';heading.textContent=`GPU ${gpu.index}`;card.append(heading);
        for(const [label,value,unit,total] of [['算力',gpu.compute_percent,'%',100],['显存',gpu.memory_gib,' GiB',gpu.memory_total_gib]]) {
          const row=document.createElement('div'),name=document.createElement('span'),amount=document.createElement('strong');
          name.textContent=label;amount.textContent=number(value)+unit;row.append(name,amount);
          const meter=document.createElement('div'),bar=document.createElement('i');meter.className='meter';bar.style.width=`${Math.max(0,Math.min(100,total?value/total*100:0))}%`;meter.append(bar);card.append(row,meter);
        }
        list.append(card);
      });
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
      icon.setAttribute('href', '#i-check'); button.title = '已复制'; button.setAttribute('aria-label', '已复制');
    } else button.textContent = '已复制';
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
  const formatTime = iso => iso ? new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit'}).format(new Date(iso)) : '结束待定';
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
      const gpuDetails = (block.gpu_requests || []).map((spec, index) => `第 ${index + 1} 张 ${spec.compute_percent}% · ${spec.memory_gib} GiB`).join(' / ');
      const detail = `${block.title}${block.owner ? ' · ' + block.owner : ''}\n${formatTime(block.start)} → ${formatTime(block.end)}\n${gpuDetails || '0 GPU'} · ${block.cpu} 核 · ${block.memory} GiB`;
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
    if (toggle && !blocks.length) track.append(node('span', 'allocation-empty', label.queued.length ? '等待启动' : '空闲'));
    track.style.minHeight = Math.max(compact ? 43 : 58, lanes.length * 34 + 16) + 'px';
    if (data.now !== null) { const line = node('div', 'now-line'); line.style.left = data.now + '%'; track.append(line); }
    return element;
  }
  async function load() {
    controller?.abort(); controller = new AbortController();
    try {
      const response = await fetch(panel.dataset.timeline + '?date=' + dateInput.value, {signal: controller.signal}); if (!response.ok) return;
      const data = await response.json();
      const container = $('[data-timeline-rows]', panel); container.replaceChildren(); tooltip.hidden = true;
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
          queue.append(node('span', 'timeline-queue-label', '排队 ' + group.queued.length));
          for (const task of group.queued) {
            const item = node(task.url ? 'a' : 'div', 'timeline-queue-item');
            if (task.url) item.href = task.url;
            item.append(node('span', '', task.title), node('small', '', task.status_label));
            item.title = `${task.title} · ${task.status_label} · ${task.gpu} GPU · ${task.cpu} 核 · ${task.memory_gib} GiB`;
            items.append(item);
          }
          queue.append(items); section.append(queue);
        }
        for (const row of group.rows) {
          const blocks = group.blocks.filter(block => block.row === row.id);
          if (row.id.startsWith('reservations') && !blocks.length) continue;
          details.append(allocationRow(row, blocks, data, true));
        }
        section.append(details); container.append(section);
      }
    } catch (error) { if (error.name !== 'AbortError') console.debug('Timeline refresh delayed'); }
  }
  dateInput.addEventListener('change', load);
  $$('[data-day]',panel).forEach(button=>button.addEventListener('click',()=>{const date=new Date(dateInput.value+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+Number(button.dataset.day));dateInput.value=date.toISOString().slice(0,10);load();}));
  $('[data-today]',panel).addEventListener('click',()=>{dateInput.value=today();load();});
  load();setInterval(load,30000);
})();

document.querySelectorAll('[data-node-select=request]').forEach(select => select.addEventListener('change', () => { const url = new URL(location.href); url.searchParams.set('node', select.value); url.searchParams.delete('image'); url.searchParams.delete('source'); location.assign(url); }));

document.querySelectorAll('[data-dataset-node]').forEach(select => select.addEventListener('change', () => { select.form.elements.path.value = ''; select.form.requestSubmit(); }));
