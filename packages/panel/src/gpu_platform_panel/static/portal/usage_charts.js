(() => {
  const blue = '#3d57ac';
  const teal = '#148279';
  const chartColors = [blue, teal, '#a96734', '#795c9c', '#a54b68', '#527483', '#78793d', '#6575ac'];
  const familyColors = {GPU: [blue, '#795c9c', '#a96734', '#527483'], NPU: [teal, '#78793d', '#a54b68']};
  const axisInk = '#526079';
  const gridInk = '#e8edf4';
  const locale = document.documentElement.lang || undefined;
  const number = new Intl.NumberFormat(locale, {maximumFractionDigits: 2});
  const charts = new WeakMap();
  const axisLabel = {color: axisInk, hideOverlap: true, margin: 10, fontSize: 11};
  const axisTooltip = {trigger: 'axis', renderMode: 'richText', confine: true};
  const grid = (left = 47) => ({left, right: 23, top: 12, bottom: 31});
  const valueAxis = extra => ({
    type: 'value', min: 0, axisLabel, splitLine: {lineStyle: {color: gridInk, type: 'dashed'}}, ...extra,
  });
  const categoryAxis = (data, extra) => ({
    type: 'category', data, axisLabel, axisTick: {show: false}, axisLine: {lineStyle: {color: gridInk}}, ...extra,
  });
  const display = (value, unit = '') => value == null ? '—' : number.format(value) + (unit === '%' ? unit : ` ${unit}`).trimEnd();
  const seriesLegend = (name, color, missing = false) => ({name, color, missing});

  function setLegend(panel, entries) {
    const legend = panel?.querySelector('[data-chart-legend]');
    if (!legend) return;
    legend.replaceChildren(...entries.map(({name, color, missing}) => {
      const item = document.createElement('span');
      const mark = document.createElement('i');
      mark.style.backgroundColor = color;
      mark.setAttribute('aria-hidden', 'true');
      item.append(mark, name);
      if (missing) item.className = 'no-samples';
      return item;
    }));
  }

  function disposeChart(element) {
    const state = charts.get(element);
    state?.observer.disconnect();
    state?.chart.dispose();
    charts.delete(element);
  }

  function showChart(element, options, entries = []) {
    if (!window.echarts) return;
    const panel = element.closest('.usage-chart-panel');
    try {
      setLegend(panel, entries);
      if (panel) panel.hidden = false;
      element.hidden = false;
      let state = charts.get(element);
      if (!state) {
        const chart = window.echarts.init(element, null, {renderer: 'canvas'});
        const observer = new ResizeObserver(() => {
          if (element.clientWidth && element.clientHeight) chart.resize();
        });
        observer.observe(element);
        state = {chart, observer};
        charts.set(element, state);
      }
      state.chart.setOption({color: chartColors, animation: false, textStyle: {color: axisInk}, ...options}, true);
      return state.chart;
    } catch (_) {
      disposeChart(element);
      if (panel) panel.hidden = true;
      element.hidden = true;
    }
  }

  const chartOptions = {
    capacity(data, labels) {
      const capacity = data[labels.kind];
      const color = labels.kind === 'NPU' ? teal : blue;
      return [{
        color: [color, '#e9edf5'],
        tooltip: {show: false},
        series: [{
          type: 'pie', radius: ['72%', '99%'], silent: true,
          label: {show: false}, labelLine: {show: false}, emphasis: {disabled: true},
          itemStyle: {borderRadius: 6},
          data: [
            {name: labels.allocated, value: capacity.allocated},
            {name: labels.available, value: Math.max(0, capacity.available - capacity.allocated)},
          ],
        }],
      }];
    },
    trend(data) {
      const positions = {GPU: 0, NPU: 0};
      const entries = data.series.map(({name, kind}) => {
        const colors = familyColors[kind];
        const label = name.replace(/^(NVIDIA GeForce |NVIDIA )/, '').replace(/ · (GPU|NPU)$/, '');
        return seriesLegend(label, colors[positions[kind]++ % colors.length]);
      });
      return [{
        grid: grid(),
        tooltip: {...axisTooltip, valueFormatter: value => display(value, 'h')},
        xAxis: categoryAxis(data.dates, {axisLabel: {...axisLabel, formatter: date => date.slice(5)}}),
        yAxis: valueAxis({axisLabel: {...axisLabel, formatter: '{value}h'}}),
        series: data.series.map((row, index) => ({
          name: row.name, type: 'bar', stack: 'hours', data: row.values, barMaxWidth: 36,
          itemStyle: {color: entries[index].color},
        })),
      }, entries];
    },
    ranking(data, labels) {
      const entries = [
        {key: 'gpu', ...seriesLegend(labels.gpu, blue)},
        {key: 'npu', ...seriesLegend(labels.npu, teal)},
      ].filter(({key}) => data.some(row => row[key] > 0));
      return [{
        grid: grid(112),
        tooltip: {...axisTooltip, valueFormatter: value => display(value, 'h')},
        xAxis: valueAxis({axisLabel: {...axisLabel, formatter: '{value}h'}}),
        yAxis: categoryAxis(data.map(row => row.name), {
          inverse: true, axisLabel: {...axisLabel, width: 96, overflow: 'truncate', interval: 0},
        }),
        series: entries.map(({key, name, color}) => ({
          name, type: 'bar', stack: 'hours', data: data.map(row => row[key]), barMaxWidth: 16,
          itemStyle: {color, borderRadius: [0, 3, 3, 0]},
        })),
      }, entries];
    },
  };

  function renderCharts(root) {
    if (!window.echarts) return;
    root.querySelectorAll('[data-chart]').forEach(element => {
      const source = document.getElementById(element.dataset.source);
      const [options, entries] = chartOptions[element.dataset.chart](JSON.parse(source.textContent), element.dataset);
      showChart(element, options, entries);
    });
  }

  function replaceCharts(root, html) {
    root.querySelectorAll('[data-chart]').forEach(disposeChart);
    root.innerHTML = html;
    renderCharts(root);
  }

  function historyTimeWindow(data) {
    const interval = data.step * 1000;
    const min = data.timestamps[0];
    const max = Math.max(min + interval, data.timestamps.at(-1));
    let first = data.timestamps.length, last = 0;
    data.nodes.forEach(node => node.points.forEach((point, index) => {
      if (point[3]) {
        first = Math.min(first, index);
        last = Math.max(last, index);
      }
    }));
    const firstSample = data.timestamps[first];
    const lastSample = data.timestamps[last];
    const padding = Math.max(interval, (lastSample - firstSample) * 0.05);
    return {
      min, max, interval,
      startValue: Math.max(min, firstSample - padding),
      endValue: Math.min(max, lastSample + padding),
    };
  }

  function historyValueAxis(unit) {
    const padding = ({min, max}) => Math.max(unit === '%' ? 1 : 0.01, (max - min) * 0.08, Math.abs(max) * 0.01);
    const finite = ({min, max}) => Number.isFinite(min) && Number.isFinite(max);
    return valueAxis({
      scale: true,
      min: extent => finite(extent) ? Math.max(unit === '°C' ? -Infinity : 0, extent.min - padding(extent)) : 0,
      max: extent => finite(extent) ? Math.min(unit === '%' ? 100 : Infinity, extent.max + padding(extent)) : 1,
      axisLabel: {...axisLabel, formatter: value => display(value, unit)},
    });
  }

  function historyChart(data, labels, range) {
    const layout = grid();
    const time = new Intl.DateTimeFormat(locale, {
      timeZone: data.zone, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    });
    const zoom = {
      xAxisIndex: 0, filterMode: 'filter', minValueSpan: range.interval,
      startValue: range.startValue, endValue: range.endValue,
    };
    return [{
      grid: {...layout, bottom: 71},
      tooltip: {...axisTooltip, formatter: points => {
        if (!points.length) return '';
        const title = time.format(points[0].value[0]);
        const values = points.filter(point => point.value[1] != null).map(point =>
          `${point.seriesName}  ${labels.mean} ${display(point.value[1], data.unit)}  ${labels.peak} ${display(point.value[2], data.unit)}`);
        return [title, ...values].join('\n');
      }},
      xAxis: {
        type: 'time', min: range.min, max: range.max, splitNumber: 5, minInterval: range.interval,
        axisLabel: {...axisLabel, formatter: stamp => time.format(stamp)},
        axisLine: {lineStyle: {color: gridInk}}, axisTick: {show: false},
      },
      yAxis: historyValueAxis(data.unit),
      dataZoom: [
        {id: 'history-slider', type: 'slider', ...zoom, left: layout.left, right: layout.right,
          bottom: 8, height: 22, showDetail: false,
          borderColor: gridInk, backgroundColor: '#f5f7fb', fillerColor: 'rgba(61,87,172,.14)',
          handleStyle: {color: '#fff', borderColor: blue}},
        {id: 'history-inside', type: 'inside', ...zoom, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false},
      ],
      series: data.nodes.filter(node => node.samples).map(node => ({
        name: node.name, type: 'line', data: node.points.map(([mean, , peak], index) => [data.timestamps[index], mean, peak]),
        encode: {x: 0, y: 1}, symbol: 'circle', symbolSize: 4, connectNulls: false,
        showSymbol: node.points.filter(point => point[3]).length <= 24,
        lineStyle: {width: 2}, itemStyle: {color: chartColors[node.color % chartColors.length]},
      })),
    }, data.nodes.map(node => seriesLegend(node.name, chartColors[node.color % chartColors.length], !node.samples))];
  }

  function renderHistory(control) {
    const section = control.closest('section');
    const panel = section.querySelector('[data-history-panel]');
    const plot = section.querySelector('[data-history-plot]');
    const resetZoom = section.querySelector('[data-history-zoom-reset]');
    const empty = section.querySelector('[data-history-empty]');
    const details = section.querySelector('[data-history-disclosure]');
    const summary = section.querySelector('[data-history-summary]');
    const error = section.querySelector('[data-history-error]');
    const coverage = control.querySelector('[data-history-coverage]');
    const dropdown = control.querySelector('[data-history-dropdown]');
    const counter = control.querySelector('[data-history-node-count]');
    const nodes = [...control.querySelectorAll('input[type=checkbox][name=nodes]')];
    const noSamples = empty.textContent;
    let pending, fitWindow;

    function draw(data) {
      empty.hidden = data.has_data;
      empty.textContent = data.nodes.length ? noSamples : gettext('Select a node to compare performance.');
      details.hidden = !data.has_data;
      coverage.textContent = data.coverage_note ?? '';
      coverage.hidden = !data.coverage_note;
      panel.hidden = true;
      if (!data.has_data) {
        fitWindow = undefined;
        disposeChart(plot);
        return;
      }
      plot.setAttribute('aria-label', data.label);
      fitWindow = historyTimeWindow(data);
      const [options, entries] = historyChart(data, plot.dataset, fitWindow);
      showChart(plot, options, entries);
      summary.replaceChildren(...data.nodes.map(node => {
        const row = document.createElement('tr');
        [node.name, display(node.mean, data.unit), display(node.peak, data.unit), number.format(node.samples)].forEach(value => {
          row.insertCell().textContent = value;
        });
        return row;
      }));
    }

    async function refresh() {
      const checked = nodes.filter(input => input.checked);
      counter.textContent = `${checked.length} / ${nodes.length}`;
      const params = new URLSearchParams(new FormData(control));
      if (checked.length) {
        params.delete('nodes');
        checked.forEach(input => params.append('nodes', input.value));
      }
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      control.setAttribute('aria-busy', 'true');
      error.hidden = true;
      const timeout = setTimeout(() => controller.abort('timeout'), 15000);
      try {
        const response = await fetch(`${control.dataset.historyUrl}?${params}`, {
          cache: 'no-store', signal: controller.signal, credentials: 'same-origin',
        });
        if (!response.ok || response.redirected) throw new Error('refresh');
        const data = await response.json();
        if (pending !== controller) return;
        draw(data);
        const page = new URL(location.href);
        for (const key of ['nodes', 'days', 'metric']) page.searchParams.delete(key);
        for (const [key, value] of params) page.searchParams.append(key, value);
        window.history.replaceState(window.history.state, '', page);
      } catch (_) {
        if (pending === controller) {
          error.textContent = gettext('Refresh failed. Try again.');
          error.hidden = false;
        }
      } finally {
        clearTimeout(timeout);
        if (pending === controller) control.removeAttribute('aria-busy');
      }
    }

    draw(JSON.parse(document.getElementById('history-points').textContent));
    resetZoom.addEventListener('click', () => {
      if (!fitWindow) return;
      const {startValue, endValue} = fitWindow;
      charts.get(plot)?.chart.dispatchAction({type: 'dataZoom', dataZoomId: 'history-slider', startValue, endValue});
    });
    control.addEventListener('change', refresh);
    control.addEventListener('submit', event => {
      event.preventDefault();
      refresh();
    });
    document.addEventListener('click', event => {
      if (!dropdown.contains(event.target)) dropdown.open = false;
    });
    dropdown.addEventListener('keydown', event => {
      if (event.key === 'Escape' && dropdown.open) {
        dropdown.open = false;
        dropdown.querySelector('summary').focus();
      }
    });
  }

  function bindUsageSort(root) {
    root.querySelectorAll('[data-usage-sort]').forEach(table => {
      const body = table.tBodies[0];
      if (!body || [...body.rows].some(row => row.cells.length !== table.tHead.rows[0].cells.length)) return;
      [...table.tHead.rows[0].cells].forEach((heading, index) => {
        heading.querySelector('button').addEventListener('click', () => {
          const descending = heading.getAttribute('aria-sort') !== 'descending';
          [...table.tHead.rows[0].cells].forEach(cell => cell.removeAttribute('aria-sort'));
          heading.setAttribute('aria-sort', descending ? 'descending' : 'ascending');
          const rows = [...body.rows].sort((a, b) => {
            const left = a.cells[index], right = b.cells[index];
            const difference = left.dataset.value !== undefined
              ? Number(left.dataset.value) - Number(right.dataset.value)
              : left.textContent.trim().localeCompare(right.textContent.trim());
            return descending ? -difference : difference;
          });
          body.append(...rows);
        });
      });
    });
  }

  renderCharts(document);
  bindUsageSort(document);
  const history = document.querySelector('[data-history-url]');
  if (history) renderHistory(history);

  const usageFilters = document.querySelector('[data-usage-filters]');
  let appliedUsageFilters = [];
  if (usageFilters) {
    const results = document.querySelector('[data-usage-history-results]');
    const status = document.querySelector('[data-usage-history-status]');
    const fields = [...usageFilters.querySelectorAll('input, select')];
    const start = usageFilters.elements.from;
    const end = usageFilters.elements.to;
    const describeFilters = () => fields.filter(input => input.value).map(input => {
      const label = input.closest('label').firstChild.textContent.trim();
      const value = input.selectedOptions?.[0]?.textContent || input.value;
      return label + ' ' + value;
    });
    const constrainDates = () => {
      start.max = end.value;
      end.min = start.value;
    };
    let pending;

    async function refreshUsageHistory() {
      constrainDates();
      pending?.abort();
      pending = undefined;
      status.hidden = true;
      if (!usageFilters.reportValidity()) {
        results.removeAttribute('aria-busy');
        return;
      }
      const params = new URLSearchParams(new FormData(usageFilters));
      fields.filter(input => !input.value).forEach(input => params.delete(input.name));
      const selected = describeFilters();
      const controller = new AbortController();
      pending = controller;
      results.setAttribute('aria-busy', 'true');
      const timeout = setTimeout(() => controller.abort('timeout'), 15000);
      let failure = gettext('Refresh failed. Try again.');
      try {
        const response = await fetch(`${usageFilters.dataset.usageHistoryUrl}?${params}`, {
          cache: 'no-store', signal: controller.signal, credentials: 'same-origin',
        });
        if (response.redirected) throw new Error('refresh');
        const payload = await response.json();
        if (!response.ok) {
          failure = payload.error || failure;
          throw new Error('refresh');
        }
        if (pending !== controller) return;
        const detailsOpen = results.querySelector('.usage-disclosure')?.open;
        replaceCharts(results, payload.html);
        bindUsageSort(results);
        if (detailsOpen) results.querySelector('.usage-disclosure').open = true;
        appliedUsageFilters = selected;
        const page = new URL(location.href);
        page.search = params.toString();
        window.history.replaceState(window.history.state, '', page);
      } catch (_) {
        if (pending === controller) {
          status.textContent = failure;
          status.hidden = false;
        }
      } finally {
        clearTimeout(timeout);
        if (pending === controller) results.removeAttribute('aria-busy');
      }
    }

    constrainDates();
    appliedUsageFilters = describeFilters();
    usageFilters.addEventListener('change', refreshUsageHistory);
    usageFilters.addEventListener('submit', event => {
      event.preventDefault();
      refreshUsageHistory();
    });
    usageFilters.querySelector('[data-usage-filter-reset]').addEventListener('click', event => {
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      fields.forEach(input => { input.value = ''; });
      refreshUsageHistory();
    });
  }

  const copy = document.querySelector('[data-usage-copy]');
  if (copy) {
    copy.hidden = false;
    copy.addEventListener('click', () => {
      const report = [...document.querySelectorAll('[data-report-part]')].map(part => part.innerText.trim());
      const time = document.querySelector('[data-report-time]').textContent.trim();
      const period = document.querySelector('[data-report-period]').textContent.trim();
      document.getElementById(copy.dataset.copy).value = [document.querySelector('h1').innerText, time, period, ...appliedUsageFilters, ...report].join('\n\n');
    }, {capture: true});
  }

  const live = document.querySelector('[data-usage-url]');
  if (live) {
    const button = document.querySelector('[data-usage-refresh]');
    const status = document.querySelector('[data-usage-refresh-status]');
    button.hidden = false;
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const response = await fetch(live.dataset.usageUrl, {
          cache: 'no-store', signal: AbortSignal.timeout(10000), credentials: 'same-origin',
        });
        if (!response.ok || response.redirected) throw new Error('refresh');
        const payload = await response.json();
        replaceCharts(live, payload.html);
        status.hidden = true;
      } catch (_) {
        status.textContent = gettext('Refresh failed. Try again.');
        status.hidden = false;
      } finally {
        button.disabled = false;
      }
    });
  }
})();
