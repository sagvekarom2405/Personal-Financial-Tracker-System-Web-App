// Robust chart rendering helpers
function destroyIfExists(canvas) {
  if (!canvas) return;
  if (typeof Chart.getChart === 'function') {
    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();
  }
}

function hasData(arr) {
  return Array.isArray(arr) && arr.length > 0 && arr.some(v => v !== null && v !== undefined && v !== 0);
}

function showMessageInCanvasArea(canvas, message) {
  try {
    const wrapper = canvas.closest('.chart-wrapper') || canvas.parentElement;
    if (!wrapper) return;
    // remove any existing overlay
    const existing = wrapper.querySelector('.chart-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.className = 'chart-overlay';
    overlay.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:transparent;padding:16px;pointer-events:none;';
    const text = document.createElement('div');
    text.style.cssText = 'background:rgba(255,255,255,0.9);color:#374151;padding:12px 16px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,0.08);font-size:14px;text-align:center;pointer-events:auto;';
    text.innerText = message;
    overlay.appendChild(text);

    // position wrapper relatively so absolute overlay works
    if (getComputedStyle(wrapper).position === 'static') wrapper.style.position = 'relative';
    wrapper.appendChild(overlay);
  } catch (e) {
    console.warn('Could not show chart message', e);
  }
}

// CATEGORY PIE CHART
fetch('/expense-category-data')
  .then(res => {
    if (res.status === 403) {
      // Premium feature locked
      return { __locked: true };
    }
    if (!res.ok) throw new Error('Failed to fetch category data');
    return res.json();
  })
  .then(data => {
    const canvas = document.getElementById('expensePieChart');
    if (!canvas) return;

    destroyIfExists(canvas);
    if (data && data.__locked) {
      showMessageInCanvasArea(canvas, 'Premium feature — upgrade to view this chart');
      // Render a muted placeholder chart to keep layout
      new Chart(canvas, { type: 'pie', data: { labels: ['Locked'], datasets: [{ data: [1], backgroundColor: ['#f3f4f6'] }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } } } });
      return;
    }

    const labels = (data && data.labels) || [];
    const values = (data && data.values) || [];

    if (!hasData(values)) {
      new Chart(canvas, {
        type: 'pie',
        data: { labels: ['No data'], datasets: [{ data: [1], backgroundColor: ['#e5e7eb'] }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } } }
      });
      return;
    }

    new Chart(canvas, {
      type: 'pie',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40', '#10b981', '#ef4444']
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });
  })
  .catch(err => console.error("Error loading pie chart:", err));

// INCOME BREAKDOWN CHART
fetch('/income-category-data')
  .then(res => {
    if (res.status === 403) {
      return { __locked: true };
    }
    if (!res.ok) throw new Error('Failed to fetch income data');
    return res.json();
  })
  .then(data => {
    const canvas = document.getElementById('incomePieChart');
    if (!canvas) return;

    destroyIfExists(canvas);
    if (data && data.__locked) {
      showMessageInCanvasArea(canvas, 'Premium feature — upgrade to view this chart');
      new Chart(canvas, { type: 'pie', data: { labels: ['Locked'], datasets: [{ data: [1], backgroundColor: ['#f3f4f6'] }] }, options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } } } });
      return;
    }

    const labels = (data && data.labels) || [];
    const values = (data && data.values) || [];

    if (!hasData(values)) {
      new Chart(canvas, {
        type: 'pie',
        data: { labels: ['No data'], datasets: [{ data: [1], backgroundColor: ['#e5e7eb'] }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } } }
      });
      return;
    }

    new Chart(canvas, {
      type: 'pie',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: ['#10b981', '#34d399', '#6ee7b7', '#a7f3d0', '#059669', '#047857']
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });
  })
  .catch(err => console.error("Error loading income chart:", err));
