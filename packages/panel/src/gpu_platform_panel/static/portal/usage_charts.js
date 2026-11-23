(() => {
  const palette = ['#5149c9', '#177d78', '#be6a29', '#416bb0', '#a84f75', '#54823c'];
  const tooltip = {trigger: 'axis', renderMode: 'richText'};

  const chartOptions = {
    metrics(data, labels) {
      const dateTime = new Intl.DateTimeFormat(undefined, {
        timeZone: labels.zone, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
      return {
        grid: {left: 52, right: 24, top: 38, bottom: 72},
        tooltip: {...tooltip, valueFormatter: value => value == null ? '—' : `${value} ${labels.unit}`},
        legend: {},
        xAxis: {type: 'category', data: data.map(point => dateTime.format(point[0])), axisLabel: {hideOverlap: true}},
        yAxis: {type: 'value', name: labels.unit, max: labels.unit === '%' ? 100 : undefined},
        dataZoom: [{type: 'inside'}, {type: 'slider', bottom: 8}],
        series: [
          {name: labels.mean, type: 'line', showSymbol: false, data: data.map(point => point[1])},
          {name: labels.peak, type: 'line', showSymbol: false, lineStyle: {type: 'dashed'}, data: data.map(point => point[3])},
        ],
      };
    },
    trend(data, labels) {
      return {
        grid: {left: 56, right: 20, top: 54, bottom: 40},
        tooltip, legend: {type: 'scroll'},
        xAxis: {type: 'category', data: data.dates, axisLabel: {hideOverlap: true}},
        yAxis: {type: 'value', name: labels.hours},
        series: data.series.map(series => ({name: series.name, type: 'bar', stack: 'hours', data: series.values})),
      };
    },
    ranking(data, labels) {
      return {
        grid: {left: 20, right: 28, top: 42, bottom: 20, outerBoundsMode: 'auto'},
        tooltip, legend: {},
        xAxis: {type: 'value'},
        yAxis: {type: 'category', inverse: true, data: data.map(row => row.name), axisLabel: {width: 100, overflow: 'truncate'}},
        series: [
          {name: labels.gpu, type: 'bar', stack: 'hours', data: data.map(row => row.gpu)},
          {name: labels.npu, type: 'bar', stack: 'hours', data: data.map(row => row.npu)},
        ],
      };
    },
  };

  if (window.echarts) {
    document.querySelectorAll('[data-chart]').forEach(element => {
      const panel = element.closest('.usage-chart-panel');
      let chart;
      try {
        const source = document.getElementById(element.dataset.source);
        const options = chartOptions[element.dataset.chart](JSON.parse(source.textContent), element.dataset);
        panel.hidden = false;
        chart = window.echarts.init(element);
        chart.setOption({color: palette, animation: false, ...options});
        new ResizeObserver(() => chart.resize()).observe(element);
      } catch (_) {
        chart?.dispose();
        panel.hidden = true;
      }
    });
  }

  const copy = document.querySelector('[data-usage-copy]');
  if (copy) {
    copy.hidden = false;
    copy.addEventListener('click', () => {
      const form = document.querySelector('.usage-filters');
      const filters = [...form.querySelectorAll('input, select')].flatMap(input => {
        const label = input.closest('label').firstChild.textContent.trim();
        const selected = [...(input.options || [])].find(option => option.defaultSelected);
        const value = selected?.value ? selected.textContent : input.defaultValue;
        return value ? [label + ' ' + value] : [];
      });
      const report = [...document.querySelectorAll('[data-report-part]')].map(part => part.innerText.trim());
      document.getElementById(copy.dataset.copy).value = [document.querySelector('h1').innerText, ...filters, ...report].join('\n\n');
    }, {capture: true});
  }

  document.querySelectorAll('[data-usage-sort]').forEach(table => {
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
        live.innerHTML = payload.html;
        status.textContent = '';
      } catch (_) {
        status.textContent = gettext('Refresh failed. Try again.');
      } finally {
        button.disabled = false;
      }
    });
  }
})();
