const state = { evidence: null, summary: null, control: null, simulation: null, simulationScenarios: [], simulationComparison: null, simulationTimer: null, clientPollTimer: null, powerQualityPollTimer: null, powerQuality: null, simulationBusy: false, notificationSettings: null, systemSettings: null, adminToken: null, approvalDeckIndex: 0, approvalDeckSelectedId: null, approvalDeckExpandedIds: new Set(), integration: null };
const NS = "http://www.w3.org/2000/svg";
const COLORS = ["#0066cc", "#1d1d1f", "#7a7a7a", "#d2d2d7", "#2997ff"];
const SAFE_OPERATIONAL_DEFAULTS = {
  campus_limit_override_kva: null,
  facility_limit_overrides_kva: {},
  critical_floor_overrides_kva: {},
  risk_medium_ratio: 0.85,
  risk_high_ratio: 0.95,
  peak_energy_usd_per_kwh: 0.2173,
  standard_energy_usd_per_kwh: 0.115,
  offpeak_energy_usd_per_kwh: 0.0588,
  demand_charge_usd_per_kva_month: 7.78
};

function fmt(value, digits = 1) { return Number(value).toFixed(digits); }
function pct(value, digits = 1) { return `${fmt(Number(value) * 100, digits)}%`; }
function eventMetric(value, events, digits = 1, percent = true) {
  if (Number(events || 0) <= 0 || value === null || value === undefined || !Number.isFinite(Number(value))) return "N/A — no events";
  return percent ? pct(value, digits) : fmt(value, digits);
}
function finiteMetric(value, digits = 2, suffix = "") {
  return Number.isFinite(Number(value)) ? `${fmt(value, digits)}${suffix}` : "N/A";
}
function esc(value) { return String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char])); }
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2400);
}
function apiErrorMessage(payload, fallback) {
  if (typeof payload === "string" && payload.trim()) return payload.trim();
  if (!payload || typeof payload !== "object") return fallback;
  const detail = payload.detail ?? payload.message ?? payload.error;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const messages = detail.map(item => {
      if (!item || typeof item !== "object") return String(item || "");
      const location = Array.isArray(item.loc)
        ? item.loc.filter(part => part !== "body").join(" → ")
        : "";
      const message = String(item.msg || item.message || "Invalid value");
      return location ? `${location}: ${message}` : message;
    }).filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  try {
    return JSON.stringify(detail ?? payload);
  } catch (_) {
    return fallback;
  }
}
class APIRequestError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "APIRequestError";
    this.status = Number(status || 0);
    this.payload = payload;
  }
}

async function getJSON(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new APIRequestError(
      `The backend could not be reached. ${error?.message || "Check that SIMBA-EMS is running."}`,
      0,
      null
    );
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => ({}))
    : await response.text().catch(() => "");
  if (!response.ok) {
    throw new APIRequestError(
      apiErrorMessage(payload, `${response.status} ${response.statusText}`),
      response.status,
      payload
    );
  }
  return payload;
}
function svgNode(tag, attributes = {}, text = "") {
  const node = document.createElementNS(NS, tag);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text !== "") node.textContent = text;
  return node;
}
function chartCanvas(id, height = 340) {
  const target = document.getElementById(id);
  target.replaceChildren();
  const chart = svgNode("svg", {
    viewBox: `0 0 760 ${height}`,
    role: "img",
    "aria-label": target.previousElementSibling?.textContent?.trim() || "Data chart",
    preserveAspectRatio: "xMidYMid meet"
  });
  target.appendChild(chart);
  return { chart, width: 760, height };
}
function addText(chart, x, y, text, options = {}) {
  chart.appendChild(svgNode("text", {
    x, y,
    fill: options.fill || "#62727d",
    "font-size": options.size || 11,
    "font-family": "Inter, Segoe UI, system-ui, -apple-system, Arial",
    "text-anchor": options.anchor || "middle",
    transform: options.rotate ? `rotate(${options.rotate} ${x} ${y})` : ""
  }, text));
}
function addLegend(chart, series, x = 90, y = 18) {
  series.forEach((item, index) => {
    const offset = index * 150;
    chart.appendChild(svgNode("rect", { x: x + offset, y: y - 10, width: 14, height: 9, rx: 2, fill: item.color }));
    addText(chart, x + offset + 20, y - 2, item.name, { anchor: "start", size: 11, fill: "#31434e" });
  });
}
function rangeTicks(maxValue, count = 5) {
  const safe = Math.max(maxValue, 1);
  const rough = safe / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / power;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * power;
  const top = Math.ceil(safe / step) * step;
  return { step, top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step) };
}
function drawGrid(chart, box, scale, label = "") {
  scale.ticks.forEach(value => {
    const y = box.bottom - (value / scale.top) * (box.bottom - box.top);
    chart.appendChild(svgNode("line", { x1: box.left, y1: y, x2: box.right, y2: y, stroke: "#dbe4e8", "stroke-width": 1 }));
    addText(chart, box.left - 10, y + 4, fmt(value, value < 10 ? 1 : 0), { anchor: "end", size: 10 });
  });
  if (label) addText(chart, 18, (box.top + box.bottom) / 2, label, { rotate: -90, size: 11, fill: "#31434e" });
}
function renderBarChart(id, labels, series, yLabel = "", height = 340) {
  const denseLabels = labels.length > 6;
  const effectiveHeight = denseLabels ? Math.max(height, 400) : height;
  const { chart, width } = chartCanvas(id, effectiveHeight);
  const box = { left: 66, right: width - 24, top: 44, bottom: effectiveHeight - (denseLabels ? 112 : 62) };
  const finite = series.flatMap(item => item.values).map(Number).filter(Number.isFinite);
  const maxValue = Math.max(...finite, 1);
  const scale = rangeTicks(maxValue * 1.12);
  drawGrid(chart, box, scale, yLabel);
  addLegend(chart, series.map((item, i) => ({ ...item, color: item.color || COLORS[i] })));
  const groupWidth = (box.right - box.left) / Math.max(labels.length, 1);
  const inner = groupWidth * 0.70;
  const barWidth = inner / Math.max(series.length, 1);
  labels.forEach((label, labelIndex) => {
    const groupX = box.left + labelIndex * groupWidth + (groupWidth - inner) / 2;
    series.forEach((item, seriesIndex) => {
      const value = Number(item.values[labelIndex] || 0);
      const h = (Math.max(value, 0) / scale.top) * (box.bottom - box.top);
      chart.appendChild(svgNode("rect", {
        x: groupX + seriesIndex * barWidth + 1,
        y: box.bottom - h,
        width: Math.max(barWidth - 3, 2),
        height: h,
        rx: 3,
        fill: item.color || COLORS[seriesIndex]
      }));
      if (labels.length <= 4) addText(chart, groupX + seriesIndex * barWidth + barWidth / 2, box.bottom - h - 8, fmt(value, 2), { size: 11, fill: "#31434e" });
    });
    const x = box.left + labelIndex * groupWidth + groupWidth / 2;
    if (denseLabels) addText(chart, x + 4, box.bottom + 24, label, { size: 10, rotate: -38, anchor: "end", fill: "#4f5f68" });
    else addText(chart, x, box.bottom + 20, label, { size: 11, fill: "#4f5f68" });
  });
}
function renderLineChart(id, labels, series, yLabel = "", height = 340) {
  const { chart, width } = chartCanvas(id, height);
  const box = { left: 62, right: width - 22, top: 42, bottom: height - 58 };
  const finiteValues = series.flatMap(item => item.values).map(Number).filter(Number.isFinite);
  const maxValue = Math.max(...finiteValues, 1);
  const scale = rangeTicks(maxValue * 1.1);
  drawGrid(chart, box, scale, yLabel);
  addLegend(chart, series.map((item, i) => ({ ...item, color: item.color || COLORS[i] })));
  const xFor = index => labels.length === 1 ? (box.left + box.right) / 2 : box.left + (index / Math.max(labels.length - 1, 1)) * (box.right - box.left);
  series.forEach((item, seriesIndex) => {
    let segment = [];
    const flush = () => {
      if (segment.length >= 2) chart.appendChild(svgNode("polyline", { points: segment.join(" "), fill: "none", stroke: item.color || COLORS[seriesIndex], "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      segment = [];
    };
    item.values.forEach((value, index) => {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) { flush(); return; }
      const x = xFor(index);
      const y = box.bottom - (numeric / scale.top) * (box.bottom - box.top);
      segment.push(`${x},${y}`);
      chart.appendChild(svgNode("circle", { cx: x, cy: y, r: 4, fill: item.color || COLORS[seriesIndex], stroke: "#fff", "stroke-width": 2 }));
    });
    flush();
  });
  const step = Math.max(1, Math.ceil(labels.length / 8));
  labels.forEach((label, index) => {
    if (index % step === 0 || index === labels.length - 1) addText(chart, xFor(index), box.bottom + 18, label, { size: 9, rotate: labels.length > 8 ? -30 : 0 });
  });
}
function heatColor(value, min, max) {
  const ratio = max === min ? 0.5 : Math.max(0, Math.min(1, (value - min) / (max - min)));
  const hue = 175 - ratio * 145;
  const light = 94 - ratio * 43;
  return `hsl(${hue} 62% ${light}%)`;
}
function renderHeatmap(id, xLabels, yLabels, values, height = 430, showNumbers = false) {
  const { chart, width } = chartCanvas(id, height);
  const box = { left: 72, right: width - 30, top: 28, bottom: height - 60 };
  const flat = values.flat().map(Number);
  const min = Math.min(...flat), max = Math.max(...flat);
  const cellW = (box.right - box.left) / xLabels.length;
  const cellH = (box.bottom - box.top) / yLabels.length;
  values.forEach((row, rowIndex) => row.forEach((value, columnIndex) => {
    chart.appendChild(svgNode("rect", {
      x: box.left + columnIndex * cellW,
      y: box.top + rowIndex * cellH,
      width: cellW + 0.3,
      height: cellH + 0.3,
      fill: heatColor(Number(value), min, max),
      stroke: "rgba(255,255,255,.55)",
      "stroke-width": 0.5
    }));
    if (showNumbers) addText(chart, box.left + columnIndex * cellW + cellW / 2, box.top + rowIndex * cellH + cellH / 2 + 4, String(value), { size: 12, fill: Number(value) > (min + max) / 2 ? "#fff" : "#18313a" });
  }));
  yLabels.forEach((label, index) => addText(chart, box.left - 9, box.top + index * cellH + cellH / 2 + 4, label, { anchor: "end", size: 10 }));
  const xStep = Math.max(1, Math.ceil(xLabels.length / 10));
  xLabels.forEach((label, index) => {
    if (index % xStep === 0 || index === xLabels.length - 1) addText(chart, box.left + index * cellW + cellW / 2, box.bottom + 17, label, { size: 9, rotate: -25 });
  });
  addText(chart, box.left, height - 14, `Lower ${fmt(min, 1)}`, { anchor: "start", size: 10 });
  addText(chart, box.right, height - 14, `Higher ${fmt(max, 1)}`, { anchor: "end", size: 10 });
}

function activatePanel(panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  document.querySelectorAll(".tab").forEach(item => item.classList.toggle("active", item.dataset.tab === panelId));
  document.querySelectorAll(".panel").forEach(item => item.classList.toggle("active", item.id === panelId));
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function activateTabs() {
  document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => activatePanel(button.dataset.tab)));
  const requested = new URLSearchParams(window.location.search).get("tab");
  if (requested && document.getElementById(requested)) activatePanel(requested);
}
function renderKPIs(summary) {
  const cards = [
    ["Facilities", summary.dataset.facilities, `${summary.dataset.intervals.toLocaleString()} half-hour intervals`],
    ["Usable data", `${fmt(summary.dataset.usable_percent, 2)}%`, "After conservative cleaning"],
    ["Forecast MAE", `${fmt(summary.forecast.model_mae_kva, 2)} kVA`, `${fmt(summary.forecast.mae_reduction_percent, 1)}% below persistence`],
    ["High-risk warning", pct(summary.peak_risk.high_warning_recall, 1), "Medium or high warning"],
    ["Critical miss", pct(summary.peak_risk.critical_miss_rate, 2), "High event classified low"]
  ];
  document.getElementById("kpi-grid").innerHTML = cards.map(([label, value, detail]) => `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="detail">${detail}</div></div>`).join("");
}
function renderForecast() {
  const metrics = state.summary?.live_model?.metrics || {};
  const order = ["30_minutes", "2_hours", "6_hours", "24_hours"];
  const labels = ["30 min", "2 hours", "6 hours", "24 hours"];
  renderBarChart("forecast-chart", labels, [
    { name: "Forecast model", values: order.map(key => Number(metrics[key]?.mae_kva || 0)), color: COLORS[0] },
    { name: "Persistence", values: order.map(key => Number(metrics[key]?.persistence_mae_kva || 0)), color: COLORS[2] }
  ], "MAE (kVA)");
  renderBarChart("rolling-chart", labels, [
    { name: "MAE improvement", values: order.map(key => Number(metrics[key]?.mae_improvement_vs_persistence_percent || 0)), color: COLORS[0] }
  ], "Improvement (%)");
}
function renderEvidence(evidence) {
  const aggregate = evidence.aggregate_visualisation_data;
  if (aggregate.status !== "generated") return;
  const heat = aggregate.campus_demand_heatmap;
  renderHeatmap("heatmap-chart", heat.slots, heat.days, heat.normalized_mean_kva, 430, false);
  const monthly = aggregate.monthly_energy_index;
  renderLineChart("monthly-chart", monthly.map(row => row.month), [{ name: "Energy index", values: monthly.map(row => row.energy_index), color: COLORS[0] }], "Index", 430);
  const profile = aggregate.day_type_profiles;
  renderLineChart("profile-chart", [...new Set(profile.map(row => row.time))], ["Weekday", "Weekend"].map((day, index) => ({
    name: day,
    values: profile.filter(row => row.day_type === day).map(row => row.normalized_kva),
    color: COLORS[index]
  })), "Normalized demand");
  const facilities = aggregate.facility_aggregate_aliases.slice(0, 12);
  renderBarChart("facility-chart", facilities.map(row => row.facility), [{ name: "Energy share", values: facilities.map(row => row.energy_share_percent), color: COLORS[2] }], "Energy share (%)", 420);
  const q = evidence.dataset_quality;
  const quality = [
    [q.missing_intervals, "Missing intervals", "Inserted explicitly, not interpolated"],
    [q.partial_intervals, "Partial intervals", "Excluded from benchmark targets"],
    [q.negative_active_rows, "Negative active rows", "Flagged and excluded pending verification"],
    [q.forecast_usable_rows.toLocaleString(), "Forecast-usable rows", `${fmt(q.forecast_usable_percent, 3)}% of the completed grid`]
  ];
  document.getElementById("quality-grid").innerHTML = quality.map(([value, label, detail]) => `<div><strong>${esc(value)}</strong><span>${esc(label)}. ${esc(detail)}</span></div>`).join("");
}
function renderControl(control, evidence) {
  const rule = control.simple_rule_controller;
  const ai = control.forecast_assisted_controller;
  const labels = ["High warning recall", "Exact high recall", "False action rate"];
  renderBarChart("control-chart", labels, [
    { name: "Simple rule", values: [rule.high_warning_recall_medium_or_high * 100, rule.high_exact_recall * 100, rule.false_action_rate_on_actual_low * 100], color: COLORS[1] },
    { name: "Forecast-assisted", values: [ai.high_warning_recall_medium_or_high * 100, ai.high_exact_recall * 100, ai.false_action_rate_on_actual_low * 100], color: COLORS[0] }
  ], "Percent", 430);
  const comparison = control.comparison;
  document.getElementById("tradeoff").innerHTML = [
    [`${fmt(comparison.critical_miss_rate_reduction_percentage_points, 2)} pp`, "Reduction in high events missed as low"],
    [comparison.critical_misses_avoided, "Critical misses avoided across the April test"],
    [`${fmt(comparison.high_warning_recall_gain_percentage_points, 2)} pp`, "Gain in high-event warning recall"],
    [`+${fmt(comparison.additional_action_rate_percentage_points, 2)} pp`, "Additional operator preparation alerts"],
    [`+${fmt(comparison.additional_false_action_rate_percentage_points, 2)} pp`, "False-action trade-off on actual low intervals"]
  ].map(([value, label]) => `<div class="metric"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`).join("");
  const peak = evidence.peak_risk;
  renderHeatmap("confusion-chart", ["Low", "Medium", "High"], ["Low", "Medium", "High"], peak.confusion_matrix, 430, true);
}
async function loadAlerts() {
  const payload = await getJSON("/api/alerts");
  const list = document.getElementById("alert-list");
  if (!payload.alerts.length) { list.innerHTML = '<div class="empty">No active alerts require operator attention.</div>'; return; }
  list.innerHTML = payload.alerts.map(alert => {
    const point = Number(alert.forecast_kva ?? alert.current_kva ?? 0);
    const upper = Number(alert.forecast_upper_kva ?? point);
    return `
    <article class="alert ${esc(alert.risk)}" data-alert="${esc(alert.alert_id)}">
      <div><div class="alert-title-row"><span class="queue-rank">Priority ${Number(alert.priority_rank || 1)}</span><span class="risk-badge ${esc(alert.risk)}">${esc(alert.risk)}</span></div><span class="alert-title">${esc(alert.facility_name)}</span>
        <div class="alert-meta">${esc(String(alert.timestamp).replace("T", " "))} · ${esc(alert.tariff_period)} · ${Number(alert.risk_lead_minutes || 30)} min lead</div><div class="priority-explanation">${esc(alert.priority_reason || "Priority is recalculated from the latest conservative forecast.")}</div></div>
      <div class="alert-demand"><strong>${Number(alert.current_kva).toFixed(1)} kVA now</strong>
        <div class="forecast-bounds"><span>${point.toFixed(1)} kVA expected</span><span>${upper.toFixed(1)} kVA conservative upper</span><span>${Number(alert.facility_limit_kva).toFixed(1)} kVA limit</span></div>
        <div class="alert-meta">${esc(alert.recommended_action)}</div>
        <div class="alert-meta">Planning reduction: ${Number(alert.planning_reduction_kva || 0).toFixed(1)} kVA. External messages notify only. Approval remains on this dashboard.</div>
        <details class="explanation-card"><summary>Why this recommendation</summary>${alert.explanation?.summary ? `<p><strong>${esc(alert.explanation.summary)}</strong></p>` : ""}<ul>${(alert.explanation?.reasons || [
          `The conservative forecast is ${upper.toFixed(1)} kVA against a ${Number(alert.facility_limit_kva).toFixed(1)} kVA limit.`,
          `The earliest relevant horizon is ${Number(alert.risk_lead_minutes || 30)} minutes during the ${String(alert.tariff_period || "current")} tariff period.`,
          `The proposed response is limited to ${Number(alert.planning_reduction_kva || 0).toFixed(1)} kVA and remains subject to operator approval.`
        ]).map(reason=>`<li>${esc(reason)}</li>`).join("")}</ul><small>${esc(alert.explanation?.boundary || "This explanation is derived from measured values, validated forecasts and configured rules. It cannot approve or execute an action.")}</small></details></div>
      <div class="alert-actions">
        <button class="confirm" data-decision="confirm" ${alert.requires_action === false ? "disabled" : ""}>${alert.requires_action === false ? "Monitor" : "Confirm"}</button><button data-decision="defer">Defer</button><button data-decision="dismiss">Dismiss</button><button data-decision="mute">Mute</button>
      </div>
    </article>`;
  }).join("");
  list.querySelectorAll("button[data-decision]").forEach(button => button.addEventListener("click", async () => {
    const card = button.closest(".alert");
    const alert = payload.alerts.find(item => item.alert_id === card.dataset.alert);
    const note = prompt("Operator note (optional):", "") || "";
    try {
      await getJSON("/api/operator-decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          alert_id: alert.alert_id,
          decision: button.dataset.decision,
          operator: "demo-operator",
          note,
          requested_reduction_kva: alert.planning_reduction_kva,
          origin: "dashboard"
        })
      });
      toast(`Decision recorded: ${button.dataset.decision}`);
      await loadDecisionLog();
    } catch (error) { toast(`Could not save decision: ${error.message}`); }
  }));
}
async function loadDecisionLog() {
  const payload = await getJSON("/api/operator-decisions?limit=30");
  const element = document.getElementById("decision-log");
  if (!payload.items.length) { element.innerHTML = '<div class="empty">No operator decisions recorded.</div>'; return; }
  element.innerHTML = payload.items.map(row => `<div class="log-row"><span>${esc(row.recorded_utc.replace("T", " ").slice(0, 19))}</span><strong>${esc(row.decision)}</strong><span>${esc(row.alert_id)} · ${esc(row.operator)}${row.note ? ` · ${esc(row.note)}` : ""}</span></div>`).join("");
}

async function loadNotificationStatus() {
  const payload = await getJSON("/api/notifications/status");
  const email = payload.email || {};
  const emailCount = Number(email.recipient_count || 0);
  const channels = [
    ["Gmail", email.configured ? "Ready" : "Setup required", emailCount ? `${emailCount} enabled recipient${emailCount === 1 ? "" : "s"}` : "No recipients", email.provider || "Gmail SMTP", email.configured],
    ["Approval", "Dashboard only", "No reply-to-approve", "Email cannot execute control", true]
  ];
  document.getElementById("notification-status").innerHTML = channels.map(([name, stateText, recipient, provider, ready]) => `<article class="notification-channel ${ready ? "ready" : "pending"}"><div><span>${esc(name)}</span><strong>${esc(stateText)}</strong></div><p>${esc(recipient)}</p><small>${esc(provider)}</small></article>`).join("");
  const modeNote = document.getElementById("notification-mode-note");
  modeNote.textContent = payload.mode === "live"
    ? `Live Gmail delivery · ${payload.minimum_risk || "high"} risk and above · ${Number(payload.cooldown_minutes || 0)} min cooldown · ${Number(payload.delivery_attempts || 1)} attempt${Number(payload.delivery_attempts || 1) === 1 ? "" : "s"}`
    : `${riskLabel(payload.mode || "dry_run")} mode · emails are composed and logged without external delivery.`;
}
async function loadNotificationLog() {
  const payload = await getJSON("/api/notifications/events?limit=40");
  const element = document.getElementById("notification-log");
  if (!payload.items?.length) { element.innerHTML = '<div class="empty">No email notification events recorded.</div>'; return; }
  element.innerHTML = payload.items.map(row => `<div class="notification-event"><div><span class="risk-badge ${esc(row.risk)}">email</span><strong>${esc(row.facility_name)}</strong></div><span>${esc(String(row.recorded_utc).replace("T", " ").slice(0, 19))}${row.recipient_masked ? ` · ${esc(row.recipient_label || row.recipient_masked)} · ${esc(row.recipient_masked)}` : ""}</span><p>${esc(row.status)} · ${esc(row.provider || "")} · ${esc(row.detail || "")}</p></div>`).join("");
}
async function processNotifications() {
  const payload = await getJSON("/api/notifications/process", { method: "POST" });
  toast(`${Number(payload.processed || 0)} email event${Number(payload.processed || 0) === 1 ? "" : "s"} processed`);
  await Promise.all([loadNotificationStatus(), loadNotificationLog()]);
}
async function testNotification() {
  const payload = await getJSON("/api/notifications/test?channel=email", { method: "POST" });
  toast(`Gmail test: ${payload.status || payload.mode || "recorded"}`);
  await loadNotificationLog();
}

function recipientRow(recipient = {}) {
  return `<div class="recipient-row" data-id="${esc(recipient.id || "")}"><input class="recipient-label" type="text" maxlength="80" placeholder="Name or role" value="${esc(recipient.label || "")}"><input class="recipient-address" type="email" maxlength="254" placeholder="manager@example.com" value="${esc(recipient.address || "")}"><label class="recipient-enabled"><input type="checkbox" ${recipient.enabled === false ? "" : "checked"}> Enabled</label><button class="recipient-remove" type="button" aria-label="Remove recipient">×</button></div>`;
}
function bindRecipientRemovers(container) { container.querySelectorAll(".recipient-remove").forEach(button => button.addEventListener("click", () => button.closest(".recipient-row").remove())); }
function addRecipientRow(recipient = {}) { const container = document.getElementById("email-recipient-list"); container.insertAdjacentHTML("beforeend", recipientRow(recipient)); bindRecipientRemovers(container); }
function collectRecipients() { return [...document.querySelectorAll("#email-recipient-list .recipient-row")].map(row => ({ id: row.dataset.id || null, label: row.querySelector(".recipient-label").value.trim(), address: row.querySelector(".recipient-address").value.trim(), enabled: row.querySelector(".recipient-enabled input").checked })).filter(item => item.address); }
function fillRecipientSelect(recipients) { const select = document.getElementById("test-email-target"); const enabled = (recipients || []).filter(item => item.enabled !== false); select.innerHTML = enabled.length ? enabled.map(item => `<option value="${esc(item.address)}">${esc(item.label || item.address)}</option>`).join("") : '<option value="">No email recipients configured</option>'; }
function setSecretPlaceholder(id, isSet, label) { const input = document.getElementById(id); input.value = ""; input.placeholder = isSet ? `${label} stored. Leave blank to keep it.` : `Enter ${label.toLowerCase()}`; }
function updateGmailTransportStatus(savedEmail = null) { const port = Number(document.getElementById("setting-gmail-port").value || 465); const security = document.getElementById("setting-gmail-security").value; const valid = (port === 465 && security === "ssl") || (port === 587 && security === "starttls"); const status = document.getElementById("gmail-config-status"); const stored = savedEmail?.gmail_app_password_set; const credentialText = stored === true ? " App password stored." : stored === false ? " App password not stored." : ""; status.textContent = valid ? `Transport valid: port ${port} with ${security === "ssl" ? "SSL" : "STARTTLS"}.${credentialText}` : "Transport mismatch. Use port 465 with SSL, or port 587 with STARTTLS."; status.dataset.state = valid ? "ok" : "error"; return valid; }
function syncGmailTransport(source) { const port = document.getElementById("setting-gmail-port"); const security = document.getElementById("setting-gmail-security"); if (source === "port") security.value = Number(port.value) === 465 ? "ssl" : "starttls"; if (source === "security") port.value = security.value === "ssl" ? "465" : "587"; updateGmailTransportStatus(); }
function clampNumber(value, minimum, maximum, fallback) { const parsed = Number(value); return Number.isFinite(parsed) ? Math.min(Math.max(parsed, minimum), maximum) : fallback; }
function setClampedNumber(id, value, minimum, maximum, fallback) { const element = document.getElementById(id); const clamped = clampNumber(value, minimum, maximum, fallback); element.value = String(clamped); return clamped; }
function validateNotificationSettingsForm() { const cooldown = setClampedNumber("setting-cooldown", document.getElementById("setting-cooldown").value, 0, 1440, 60); const attempts = setClampedNumber("setting-attempts", document.getElementById("setting-attempts").value, 1, 5, 2); if (!updateGmailTransportStatus(state.notificationSettings?.email || null)) throw new Error("Gmail transport mismatch. Use port 465 with SSL, or port 587 with STARTTLS."); const sender = document.getElementById("setting-gmail-user"); if (document.getElementById("setting-email-enabled").checked && sender.value && !sender.checkValidity()) throw new Error("Enter a valid Gmail sender address."); const dashboardUrl = document.getElementById("setting-dashboard-url"); if (dashboardUrl.value && !dashboardUrl.checkValidity()) throw new Error("Enter a valid dashboard URL, including http:// or https://."); return { cooldown, attempts }; }

function adminHeaders(extra = {}) {
  if (!state.adminToken) throw new APIRequestError("Admin login is required.", 401, null);
  return { ...extra, "X-Admin-Token": state.adminToken };
}
function isAdminSessionError(error) {
  const status = Number(error?.status || 0);
  const message = String(error?.message || "").toLowerCase();
  return status === 401 || status === 403 || message.includes("valid admin session") || message.includes("admin login is required");
}
function closeDialogIfOpen(dialog) {
  if (dialog?.open) dialog.close();
}
function showDialogIfClosed(dialog) {
  if (dialog && !dialog.open) dialog.showModal();
}
function showAdminLogin(message = "") {
  state.adminToken = null;
  closeDialogIfOpen(document.getElementById("admin-dialog"));
  const result = document.getElementById("admin-login-result");
  if (result) result.textContent = message;
  const dialog = document.getElementById("admin-login-dialog");
  showDialogIfClosed(dialog);
  window.setTimeout(() => document.getElementById("admin-login-password")?.focus(), 0);
}
function handleAdminError(error, outputId = null) {
  const message = error?.message || "The Admin request failed.";
  if (outputId) {
    const output = document.getElementById(outputId);
    if (output) output.textContent = message;
  }
  if (isAdminSessionError(error)) {
    showAdminLogin("Your Admin session is missing or expired. Sign in again.");
    return;
  }
  toast(message);
}
async function adminJSON(url, options = {}) {
  try {
    return await getJSON(url, { ...options, headers: adminHeaders(options.headers || {}) });
  } catch (error) {
    if (isAdminSessionError(error)) state.adminToken = null;
    throw error;
  }
}
async function adminPost(url, body = {}) { return adminJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }

async function loadNotificationSettings() {
  const payload = await getJSON("/api/notifications/settings"); state.notificationSettings = payload; const email = payload.email || {};
  document.getElementById("setting-mode").value = payload.mode || "dry_run"; document.getElementById("setting-min-risk").value = payload.minimum_risk || "high";
  setClampedNumber("setting-cooldown", payload.cooldown_minutes, 0, 1440, 60); setClampedNumber("setting-attempts", payload.delivery_attempts, 1, 5, 2);
  document.getElementById("setting-dashboard-url").value = payload.dashboard_url || "http://127.0.0.1:8000/?tab=operations"; document.getElementById("setting-email-enabled").checked = email.enabled !== false;
  document.getElementById("setting-gmail-user").value = email.gmail_user || ""; document.getElementById("setting-gmail-host").value = email.gmail_host || "smtp.gmail.com"; document.getElementById("setting-gmail-port").value = String(email.gmail_port || 465); document.getElementById("setting-gmail-security").value = email.gmail_security || (Number(email.gmail_port || 465) === 465 ? "ssl" : "starttls");
  updateGmailTransportStatus(email); setSecretPlaceholder("setting-gmail-password", email.gmail_app_password_set, "Gmail app password");
  const list = document.getElementById("email-recipient-list"); list.innerHTML = ""; (email.recipients || []).forEach(item => addRecipientRow(item)); if (!email.recipients?.length) addRecipientRow(); fillRecipientSelect(email.recipients || []);
  document.getElementById("settings-save-status").textContent = payload.settings_error || `Revision ${Number(payload.revision || 0)} loaded.`; return payload;
}
function notificationSettingsPayload() { const valid = validateNotificationSettingsForm(); return { mode: document.getElementById("setting-mode").value, minimum_risk: document.getElementById("setting-min-risk").value, cooldown_minutes: valid.cooldown, delivery_attempts: valid.attempts, retry_backoff_seconds: Number(state.notificationSettings?.retry_backoff_seconds || .75), dashboard_url: document.getElementById("setting-dashboard-url").value.trim(), email: { enabled: document.getElementById("setting-email-enabled").checked, recipients: collectRecipients(), gmail_user: document.getElementById("setting-gmail-user").value.trim(), gmail_app_password: document.getElementById("setting-gmail-password").value.trim() || null, gmail_host: document.getElementById("setting-gmail-host").value.trim(), gmail_port: Number(document.getElementById("setting-gmail-port").value || 465), gmail_security: document.getElementById("setting-gmail-security").value } }; }
async function saveNotificationSettings(silent = false) { const status = document.getElementById("settings-save-status"); status.textContent = "Saving email settings…"; const response = await getJSON("/api/notifications/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(notificationSettingsPayload()) }); state.notificationSettings = response.settings; await Promise.all([loadNotificationSettings(), loadNotificationStatus(), loadNotificationLog()]); status.textContent = `Saved. Revision ${Number(response.settings?.revision || 0)} is active without restart.`; if (!silent) toast("Email settings saved"); return response; }
async function testSettingsRecipient() { await saveNotificationSettings(true); const recipient = document.getElementById("test-email-target").value; if (!recipient) throw new Error("Add and enable an email recipient first."); const result = await getJSON("/api/notifications/test-target", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ channel: "email", recipient }) }); document.getElementById("settings-test-result").textContent = `Email test: ${result.status}. ${result.event?.detail || ""}`; await loadNotificationLog(); toast(`Email test ${result.status}`); }
async function openNotificationSettings() { await loadNotificationSettings(); document.getElementById("settings-dialog").showModal(); }

async function loginAdmin() {
  const passwordField = document.getElementById("admin-login-password");
  const resultField = document.getElementById("admin-login-result");
  const password = passwordField.value;
  if (resultField) resultField.textContent = "Signing in…";
  const result = await getJSON("/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
  state.adminToken = result.token;
  passwordField.value = "";
  closeDialogIfOpen(document.getElementById("admin-login-dialog"));
  if (resultField) resultField.textContent = "";
  await openAdmin();
  if (result.must_change_password) toast("Admin login succeeded. Change the temporary password before deployment.");
}
function chronosRouteLabel(value) {
  const mapping = {
    existing: "Existing validated route",
    chronos2: "Chronos-2",
    hybrid_chronos_existing: "Validated route + Chronos-2"
  };
  return mapping[value] || String(value || "Not selected").replaceAll("_", " ");
}
function renderChronosAdminStatus(payload) {
  const element = document.getElementById("admin-chronos-status");
  if (!element) return;
  const runtime = payload?.runtime || {};
  const setup = payload?.setup || {};
  const routing = payload?.routing || {};
  const selected = routing.selected_by_horizon || runtime.selected_horizons || {};
  const statusText = setup.status === "success" ? "Installed and benchmarked" : runtime.ready ? "Installed" : "Pending setup";
  const rows = ["30_minutes", "2_hours", "6_hours", "24_hours"].map(horizon => {
    const route = selected[horizon] || {};
    const model = typeof route === "string" ? route : route.model;
    const test = typeof route === "object" && route ? route.test_metrics || {} : {};
    return `<div><span>${esc(horizon.replaceAll("_", " "))}</span><strong>${esc(chronosRouteLabel(model || "existing"))}</strong><small>${Number.isFinite(Number(test.mae_kva)) ? `Test MAE ${fmt(test.mae_kva, 2)} kVA · recall ${eventMetric(test.high_risk_recall, test.high_risk_events, 1)} · F1 ${eventMetric(test.high_risk_f1, test.high_risk_events, 2, false)}` : "Metrics appear after the local benchmark."}</small></div>`;
  }).join("");
  element.innerHTML = `<div><span>Status</span><strong>${esc(statusText)}</strong><small>${runtime.ready ? "Local inference available" : "No effect on the existing validated forecast until setup succeeds"}</small></div><div><span>Fine-tuned checkpoint</span><strong>${runtime.fine_tuned ? "Available" : "Not available"}</strong><small>${runtime.package_available ? "Chronos Python package ready" : "Optional package not installed"}</small></div><div><span>Automatic routing</span><strong>${runtime.eligible_for_automatic_routing ? "Eligible" : "Existing routes retained"}</strong><small>Accuracy and recall guardrails decide each horizon.</small></div><div><span>Last inference</span><strong>${fmt(runtime.last_inference_latency_ms || 0, 2)} ms</strong><small>${Number(runtime.failure_count || 0)} failures · ${Number(runtime.fallback_count || 0)} fallbacks</small></div>${rows}<div class="chronos-command"><strong>Local setup</strong><small>${esc(payload?.installation_command || "Run INSTALL_AND_TRAIN_CHRONOS2.bat as Administrator.")}</small></div>`;
}
async function refreshChronosAdminStatus() {
  const payload = await adminJSON("/api/admin/chronos2/status");
  renderChronosAdminStatus(payload);
  return payload;
}
function renderChronosEvidence(validation) {
  const summary = document.getElementById("chronos-evidence-summary");
  const table = document.getElementById("chronos-evidence-table");
  if (!summary || !table) return;
  if (!validation || validation.status !== "pass") {
    summary.textContent = "Pending local installation and benchmark";
    table.innerHTML = '<div class="empty">The existing validated models remain active. Run INSTALL_AND_TRAIN_CHRONOS2.bat to create Chronos-2 zero-shot, fine-tuned and hybrid evidence using the same chronological validation and final-test periods.</div>';
    return;
  }
  const selected = validation.selected_by_horizon || {};
  const training = validation.training || {};
  const eligible = validation.summary?.eligible_horizons || [];
  const variant = validation.deployment_variant === "finetuned" ? "Fine-tuned LoRA" : "Zero-shot";
  summary.textContent = `${training.succeeded ? "Zero-shot + LoRA benchmarked" : "Zero-shot benchmarked"} · default ${variant} · ${eligible.length} horizon${eligible.length === 1 ? "" : "s"} eligible`;
  const modelLabels = {existing:"Existing router", chronos_zero_shot:"Chronos-2 zero-shot", chronos_lora:"Chronos-2 fine-tuned"};
  const comparisonRows = ["30_minutes", "2_hours", "6_hours", "24_hours"].map(horizon => {
    const cells = ["existing", "chronos_zero_shot", "chronos_lora"].map(name => {
      const metric = validation.models?.[name]?.test?.[horizon] || {};
      return `<td>${Number.isFinite(Number(metric.mae_kva)) ? `${fmt(metric.mae_kva, 2)} kVA` : "Pending"}</td>`;
    }).join("");
    return `<tr><td>${esc(horizon.replaceAll("_", " "))}</td>${cells}</tr>`;
  }).join("");
  const rows = ["30_minutes", "2_hours", "6_hours", "24_hours"].map(horizon => {
    const route = selected[horizon] || {};
    const test = route.test_metrics || {};
    const highRiskEvents = Number(test.high_risk_events || 0);
    const recall = highRiskEvents > 0 && Number.isFinite(Number(test.high_risk_recall)) ? pct(test.high_risk_recall, 1) : "N/A";
    const f1 = highRiskEvents > 0 && Number.isFinite(Number(test.high_risk_f1)) ? fmt(test.high_risk_f1, 2) : "N/A";
    const reason = highRiskEvents > 0
      ? (route.reason || "Validation guardrails retained the existing route.")
      : `${route.reason || "Validation guardrails retained the existing route."} No high-risk events occurred in this horizon's final-test sample, so recall and F1 are not applicable.`;
    return `<tr><td>${esc(horizon.replaceAll("_", " "))}</td><td>${esc(chronosRouteLabel(route.model || "existing"))}</td><td>${Number.isFinite(Number(test.mae_kva)) ? `${fmt(test.mae_kva, 2)} kVA` : "Pending"}</td><td>${recall}</td><td>${f1}</td><td class="wrap-cell">${esc(reason)}</td></tr>`;
  }).join("");
  table.innerHTML = `<h4>Direct model comparison</h4><table class="cost-table"><thead><tr><th>Horizon</th><th>${modelLabels.existing}</th><th>${modelLabels.chronos_zero_shot}</th><th>${modelLabels.chronos_lora}</th></tr></thead><tbody>${comparisonRows}</tbody></table><div class="evidence-boundary">${esc(validation.variant_selection?.reason || "The Chronos variant is selected from validation evidence only.")}</div><h4>Deployed route</h4><table class="cost-table"><thead><tr><th>Horizon</th><th>Default route</th><th>Final-test MAE</th><th>Recall</th><th>F1</th><th>Why selected</th></tr></thead><tbody>${rows}</tbody></table><div class="evidence-boundary">${esc(validation.claim_boundary || "Routing is selected from validation evidence and reported on the untouched final test period.")}</div>`;
}


function powerQualityRiskLabel(value) {
  return ({critical:"Critical PF risk", attention:"PF attention", normal:"PF acceptable"})[String(value || "normal")] || "PF status";
}
function powerQualityRiskClass(value) {
  return String(value) === "critical" ? "high" : String(value) === "attention" ? "medium" : "low";
}
function powerQualityModelLabel(value) {
  return ({seasonal_persistence:"Previous-day guard", chronos:"Chronos-2", hybrid_chronos_seasonal:"Chronos-2 + previous-day guard"})[String(value || "")] || String(value || "Not selected").replaceAll("_", " ");
}
function renderPowerQuality(payload) {
  state.powerQuality = payload || {};
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const statusNote = document.getElementById("power-quality-status-note");
  const kpis = document.getElementById("power-quality-kpis");
  const horizonsElement = document.getElementById("power-quality-horizon-grid");
  const table = document.getElementById("power-quality-table");
  const guidance = document.getElementById("power-quality-guidance");
  const homeStrip = document.getElementById("home-power-quality-strip");
  const impact = document.getElementById("impact-power-quality-opportunity");
  if (!items.length) {
    const reason = payload?.reason || "Run TRAIN_POWER_QUALITY_FORECASTS.bat after placing the authorised UZ dataset ZIP in training_data.";
    if (statusNote) statusNote.textContent = payload?.refreshing ? "Forecast refresh running" : "Power-quality model pending";
    if (kpis) kpis.innerHTML = `<div class="empty full-span">${esc(reason)}</div>`;
    if (horizonsElement) horizonsElement.innerHTML = "";
    if (table) table.innerHTML = `<div class="empty">${esc(reason)}</div>`;
    if (guidance) guidance.innerHTML = `<div><span>Status</span><strong>${esc(reason)}</strong></div>`;
    if (homeStrip) homeStrip.innerHTML = `<div class="power-quality-home-item"><span>Energy and power quality</span><strong>Training required</strong><small>${esc(reason)}</small></div>`;
    if (impact) impact.innerHTML = `<div class="empty">${esc(reason)}</div>`;
    return;
  }
  const riskOrder = {critical:2, attention:1, normal:0};
  const ranked = [...items].sort((a,b) => (riskOrder[b.power_factor_risk] || 0) - (riskOrder[a.power_factor_risk] || 0) || Math.abs(Number(b.current_reactive_power_kvar || 0)) - Math.abs(Number(a.current_reactive_power_kvar || 0)));
  const focus = ranked[0];
  const focusHorizons = focus.forecasts || {};
  const h30 = focusHorizons["30_minutes"] || {};
  const sourceText = payload.source === "trained_multivariate_chronos2" ? "Validated multivariate model" : "Previous-day safety guard";
  if (statusNote) statusNote.textContent = `${sourceText}${payload.refreshing ? " · refreshing" : ""}`;
  if (kpis) {
    const cards = [
      ["Current active power", `${fmt(focus.current_active_power_kw || 0, 1)} kW`, focus.facility_name],
      ["Current reactive power", `${fmt(focus.current_reactive_power_kvar || 0, 1)} kVAR`, "Signed inductive/capacitive behaviour"],
      ["Current power factor", fmt(focus.current_power_factor || 0, 3), powerQualityRiskLabel(focus.power_factor_risk)],
      ["30-minute conservative PF", fmt(h30.conservative_power_factor || focus.conservative_power_factor || 0, 3), "Operational threshold 0.950"],
      ["Next interval energy", `${fmt(h30.forecast_interval_energy_kwh || 0, 1)} kWh`, `${fmt(h30.forecast_interval_reactive_energy_kvarh_estimated || 0, 1)} estimated kVARh`]
    ];
    kpis.innerHTML = cards.map(([label,value,detail]) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="detail">${esc(detail)}</div></div>`).join("");
  }
  const order = ["30_minutes","2_hours","6_hours","24_hours"];
  if (horizonsElement) horizonsElement.innerHTML = order.map(key => {
    const row = focusHorizons[key] || {};
    return `<div class="horizon-card ${powerQualityRiskClass(row.power_factor_risk)}"><span>${esc(key.replaceAll("_", " "))}</span><strong>PF ${fmt(row.conservative_power_factor || 0, 3)}</strong><small>${fmt(row.forecast_active_power_kw || 0, 1)} kW · ${fmt(row.forecast_reactive_power_kvar || 0, 1)} kVAR</small><small>${fmt(row.forecast_interval_energy_kwh || 0, 1)} kWh · ${fmt(row.forecast_interval_reactive_energy_kvarh_estimated || 0, 1)} est. kVARh</small><em>${esc(powerQualityRiskLabel(row.power_factor_risk))}</em></div>`;
  }).join("");
  if (document.getElementById("power-quality-chart")) {
    renderBarChart(
      "power-quality-chart",
      ["Now", "30 min", "2 h", "6 h", "24 h"],
      [
        {name:"Power factor", values:[Number(focus.current_power_factor || 0) * 100, ...order.map(key => Number(focusHorizons[key]?.conservative_power_factor || 0) * 100)], color:COLORS[0]},
        {name:"Acceptable threshold", values:Array(5).fill(95), color:COLORS[3]}
      ],
      "Power factor (%)",
      340
    );
  }
  if (guidance) guidance.innerHTML = [
    ["Priority facility", focus.facility_name || focus.facility_id],
    ["Forecast condition", powerQualityRiskLabel(h30.power_factor_risk)],
    ["Recommended response", h30.recommended_action || focus.recommended_action || "Continue monitoring."],
    ["Apparent-power cross-check", `${fmt(h30.forecast_apparent_power_kva_crosscheck || 0, 1)} kVA`],
    ["Tariff period", String(h30.tariff_period || "unknown").replaceAll("_", " ")],
    ["AI safety boundary", "The model forecasts risk. It does not switch capacitor banks or bypass electrical protection."]
  ].map(([label,value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  if (table) table.innerHTML = `<table class="cost-table power-quality-table"><thead><tr><th>Facility</th><th>Current kW</th><th>Current kVAR</th><th>PF now</th><th>PF in 30 min</th><th>30-min kWh</th><th>Est. kVARh</th><th>Risk</th><th>Recommended response</th></tr></thead><tbody>${ranked.map(item => { const row=item.forecasts?.["30_minutes"] || {}; return `<tr><td>${esc(item.facility_name || item.facility_id)}</td><td>${fmt(item.current_active_power_kw || 0, 1)}</td><td>${fmt(item.current_reactive_power_kvar || 0, 1)}</td><td>${fmt(item.current_power_factor || 0, 3)}</td><td>${fmt(row.conservative_power_factor || item.conservative_power_factor || 0, 3)}</td><td>${fmt(row.forecast_interval_energy_kwh || 0, 1)}</td><td>${fmt(row.forecast_interval_reactive_energy_kvarh_estimated || 0, 1)}</td><td><span class="risk-badge ${powerQualityRiskClass(row.power_factor_risk || item.power_factor_risk)}">${esc(powerQualityRiskLabel(row.power_factor_risk || item.power_factor_risk))}</span></td><td class="wrap-cell">${esc(row.recommended_action || item.recommended_action || "Continue monitoring.")}</td></tr>`; }).join("")}</tbody></table>`;
  const criticalCount = ranked.filter(item => item.power_factor_risk === "critical").length;
  const attentionCount = ranked.filter(item => item.power_factor_risk === "attention").length;
  const reactiveBurden = ranked.reduce((sum,item) => sum + Number(item.forecasts?.["30_minutes"]?.forecast_interval_reactive_energy_kvarh_estimated || 0), 0);
  const energyTotal = ranked.reduce((sum,item) => sum + Number(item.forecasts?.["30_minutes"]?.forecast_interval_energy_kwh || 0), 0);
  const costProxy = ranked.reduce((sum,item) => sum + Number(item.forecasts?.["30_minutes"]?.forecast_energy_cost_proxy_usd || 0), 0);
  if (homeStrip) homeStrip.innerHTML = [
    ["Energy forecast", `${fmt(energyTotal, 1)} kWh`, "Expected across available facilities in the next interval"],
    ["Reactive burden", `${fmt(reactiveBurden, 1)} est. kVARh`, "Operational estimate from signed reactive-power forecasts"],
    ["Power-factor attention", `${criticalCount + attentionCount} facilities`, criticalCount ? `${criticalCount} critical forecast${criticalCount === 1 ? "" : "s"}` : "No critical PF forecast"],
    ["Model source", sourceText, payload.refreshing ? "A trained refresh is running in the background" : "Current forecast available"]
  ].map(([label,value,detail]) => `<div class="power-quality-home-item"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`).join("");
  if (impact) impact.innerHTML = `<div class="power-quality-impact-grid"><div><span>Next-interval active energy</span><strong>${fmt(energyTotal, 1)} kWh</strong><small>Forecast before approved demand actions</small></div><div><span>Reactive-energy exposure</span><strong>${fmt(reactiveBurden, 1)} est. kVARh</strong><small>Planning estimate; reconcile against billing registers</small></div><div><span>Facilities needing attention</span><strong>${criticalCount + attentionCount}</strong><small>${criticalCount} critical · ${attentionCount} attention</small></div><div><span>Energy cost proxy</span><strong>${usd(costProxy, 2)}</strong><small>TOU interval proxy, not an invoice</small></div></div><div class="evidence-boundary">${esc(payload.claim_boundary || "Power-quality outputs are operational forecasts. Billing-grade reactive energy and savings require meter-register reconciliation.")}</div>`;
}

async function loadPowerQualityForecasts(force = false) {
  const payload = await getJSON(`/api/power-quality-forecasts${force ? "?force=true" : ""}`);
  renderPowerQuality(payload);
  return payload;
}

function renderPowerQualityEvidence(validation) {
  const summary = document.getElementById("power-quality-evidence-summary");
  const table = document.getElementById("power-quality-evidence-table");
  if (!summary || !table) return;
  if (!validation || validation.status !== "pass") {
    summary.textContent = "Pending local power-quality training";
    table.innerHTML = '<div class="empty">The existing kVA demand model remains active. Put one authorised UZ dataset ZIP in training_data and run TRAIN_POWER_QUALITY_FORECASTS.bat to train and validate active-kW and signed-kVAR forecasting.</div>';
    return;
  }
  const dataset = validation.dataset || {};
  const deployment = validation.deployment_variant === "power_quality_finetuned" ? "Power-quality LoRA" : "Installed source Chronos";
  summary.textContent = `${Number(dataset.cleaned_source_rows || 0).toLocaleString()} cleaned readings · ${Number(dataset.facilities || 0)} facilities · default ${deployment}`;
  const routes = validation.selected_by_target_horizon || {};
  const horizons = ["30_minutes","2_hours","6_hours","24_hours"];
  const rows = horizons.map(horizon => {
    const active = routes.active_power_kw?.[horizon] || {};
    const reactive = routes.reactive_power_kvar?.[horizon] || {};
    const activeMetric = active.test_metrics || {};
    const reactiveMetric = reactive.test_metrics || {};
    return `<tr><td>${esc(horizon.replaceAll("_", " "))}</td><td>${esc(powerQualityModelLabel(active.model))}</td><td>${finiteMetric(activeMetric.mae, 2, " kW")}</td><td>${esc(powerQualityModelLabel(reactive.model))}</td><td>${finiteMetric(reactiveMetric.mae, 2, " kVAR")}</td><td class="wrap-cell">${esc(active.reason || reactive.reason || "Selected from chronological validation evidence.")}</td></tr>`;
  }).join("");
  const derived = validation.selected_route_metrics?.test?.derived || {};
  const pf = derived.power_factor || {};
  const kwh = derived.interval_energy_kwh || {};
  const kvarh = derived.interval_reactive_energy_kvarh_estimated || {};
  const pfRecall = eventMetric(pf.low_pf_recall, pf.low_pf_events, 1);
  const pfF1 = eventMetric(pf.low_pf_f1, pf.low_pf_events, 2, false);
  table.innerHTML = `<div class="quality-grid power-quality-evidence-summary"><div><span>PF MAE</span><strong>${finiteMetric(pf.mae, 4)}</strong><small>Derived from forecast kW and kVAR</small></div><div><span>Low-PF recall</span><strong>${esc(pfRecall)}</strong><small>${Number(pf.low_pf_events || 0)} final-test event${Number(pf.low_pf_events || 0) === 1 ? "" : "s"}</small></div><div><span>Low-PF F1</span><strong>${esc(pfF1)}</strong><small>Undefined when no events occur</small></div><div><span>Interval-energy MAE</span><strong>${finiteMetric(kwh.mae, 2, " kWh")}</strong><small>${finiteMetric(kwh.wape_percent, 2, "% WAPE")}</small></div><div><span>Reactive-energy MAE</span><strong>${finiteMetric(kvarh.mae, 2, " est. kVARh")}</strong><small>${finiteMetric(kvarh.wape_percent, 2, "% WAPE")}</small></div></div><h4>Deployed routes by target</h4><table class="cost-table"><thead><tr><th>Horizon</th><th>Active-power route</th><th>Test MAE</th><th>Reactive-power route</th><th>Test MAE</th><th>Why selected</th></tr></thead><tbody>${rows}</tbody></table><div class="evidence-boundary"><strong>Why two targets:</strong> ${esc(validation.summary?.why_two_targets || "Active kW and signed reactive kVAR contain the independent information. kWh, estimated kVARh and power factor are derived to maintain physical consistency.")}</div><div class="evidence-boundary">${esc(validation.claim_boundary || "Billing-grade reactive energy and savings require meter-register reconciliation.")}</div>`;
}

function renderPowerQualityAdminStatus(payload) {
  const element = document.getElementById("admin-power-quality-status");
  if (!element) return;
  const runtime = payload?.runtime || {};
  const routing = payload?.routing || runtime.routing || {};
  const setup = payload?.setup || {};
  const selected = routing.selected_by_target_horizon || runtime.selected_by_target_horizon || {};
  const routeRows = ["30_minutes","2_hours","6_hours","24_hours"].map(horizon => {
    const active = selected.active_power_kw?.[horizon] || {};
    const reactive = selected.reactive_power_kvar?.[horizon] || {};
    return `<div><span>${esc(horizon.replaceAll("_", " "))}</span><strong>${esc(powerQualityModelLabel(active.model))}</strong><small>kW ${finiteMetric(active.test_metrics?.mae, 2, " MAE")} · kVAR ${finiteMetric(reactive.test_metrics?.mae, 2, " MAE")}</small></div>`;
  }).join("");
  element.innerHTML = `<div><span>Status</span><strong>${runtime.ready ? "Ready" : "Training required"}</strong><small>${payload?.refreshing ? "Inference refresh is running" : "Non-blocking local inference"}</small></div><div><span>Deployment</span><strong>${esc(String(runtime.deployment_variant || routing.deployment_variant || setup.deployment_variant || "pending").replaceAll("_", " "))}</strong><small>${runtime.fine_tuned ? "Separate power-quality checkpoint available" : "Existing demand model remains unchanged"}</small></div><div><span>Targets</span><strong>Active kW + signed kVAR</strong><small>Derived: kWh, estimated kVARh, apparent kVA and PF</small></div><div><span>Runtime</span><strong>${fmt(runtime.last_inference_latency_ms || 0, 1)} ms</strong><small>${Number(runtime.failure_count || 0)} failures · ${Number(runtime.fallback_count || 0)} fallbacks</small></div>${routeRows}<div class="chronos-command"><strong>Training command</strong><small>${esc(payload?.training_command || "Run TRAIN_POWER_QUALITY_FORECASTS.bat as Administrator.")}</small></div>`;
}

async function refreshPowerQualityAdminStatus() {
  const payload = await adminJSON("/api/admin/power-quality/status");
  renderPowerQualityAdminStatus(payload);
  return payload;
}

async function loadSystemSettings() {
  const payload = await adminJSON("/api/system-settings"); state.systemSettings = payload.settings || {}; state.simulationScenarios = payload.scenarios || [];
  const simulation = state.systemSettings.simulation || {}, adaptive = state.systemSettings.adaptive_learning || {}, model = state.systemSettings.model || {};
  const select = document.getElementById("setting-simulation-scenario"); select.innerHTML = state.simulationScenarios.map(item => `<option value="${esc(item.scenario_id)}">${esc(item.name)} · ${Number(item.facility_count || 0)} facilities</option>`).join(""); select.value = simulation.scenario_id || state.simulationScenarios[0]?.scenario_id || "";
  document.getElementById("setting-simulation-controller").value = simulation.controller_mode || "ai_assisted"; document.getElementById("setting-playback-seconds").value = String(clampNumber(simulation.playback_interval_seconds, .5, 30, 10)); document.getElementById("setting-pause-recommendation").checked = simulation.pause_on_recommendation === true; document.getElementById("setting-auto-start").checked = simulation.auto_start !== false; document.getElementById("setting-auto-compare").checked = simulation.auto_compare_on_load === true;
  document.getElementById("admin-model-mode").value = model.selection_mode || "automatic"; document.getElementById("setting-adaptive-enabled").checked = adaptive.enabled !== false; document.getElementById("setting-adaptive-min").value = String(clampNumber(adaptive.minimum_observations,4,96,8)); document.getElementById("setting-adaptive-gain").value = String(clampNumber(adaptive.correction_gain,0,1,.55)); document.getElementById("setting-adaptive-cap").value = String(clampNumber(adaptive.maximum_correction_percent_of_limit,0,15,5)); document.getElementById("setting-adaptive-window").value = String(clampNumber(adaptive.residual_window,48,1000,192)); document.getElementById("setting-retrain-interval").value = String(clampNumber(adaptive.retraining_interval_new_readings,96,10000,336));
  const [modelStatus, adaptiveStatus, facilities, chronosStatus, powerQualityStatus] = await Promise.all([getJSON("/api/model-status"), getJSON("/api/adaptive-learning/status"), adminJSON("/api/admin/test-facilities"), adminJSON("/api/admin/chronos2/status"), adminJSON("/api/admin/power-quality/status")]);
  document.getElementById("admin-model-status").textContent = `${modelStatus.model_family}. Active mode: ${modelStatus.active_mode}. LSTM ${modelStatus.neural_models_ready?.lstm ? "ready" : "unavailable"}; Transformer ${modelStatus.neural_models_ready?.transformer ? "ready" : "unavailable"}; Chronos-2 ${modelStatus.chronos2?.ready ? "ready" : "optional"}.`;
  renderChronosAdminStatus(chronosStatus);
  renderPowerQualityAdminStatus(powerQualityStatus);
  const operationalSettings = state.systemSettings.operational;
  const safeOperationalSettings = operationalSettings && typeof operationalSettings === "object" && !Array.isArray(operationalSettings)
    ? operationalSettings
    : SAFE_OPERATIONAL_DEFAULTS;
  document.getElementById("admin-operational-json").value = JSON.stringify(safeOperationalSettings, null, 2);
  document.getElementById("adaptive-config-status").textContent = `${Number(adaptiveStatus.total_residual_updates || 0).toLocaleString()} verified residual updates. Manual tests and replay values are excluded.`;
  document.getElementById("admin-test-facility").innerHTML = (facilities.items || []).map(item => `<option value="${esc(item)}">${esc(item)}</option>`).join(""); return payload;
}
function systemSettingsPayload() {
  let operational;
  try { operational = JSON.parse(document.getElementById("admin-operational-json").value || "{}"); }
  catch (error) { throw new Error(`Operational guardrails contain invalid JSON: ${error.message}`); }
  if (!operational || Array.isArray(operational) || typeof operational !== "object") throw new Error("Operational guardrails must be a JSON object.");
  return {
    simulation: {
      scenario_id: document.getElementById("setting-simulation-scenario").value,
      controller_mode: document.getElementById("setting-simulation-controller").value,
      playback_interval_seconds: clampNumber(document.getElementById("setting-playback-seconds").value,.5,30,10),
      pause_on_recommendation: document.getElementById("setting-pause-recommendation").checked,
      auto_start: document.getElementById("setting-auto-start").checked,
      auto_compare_on_load: document.getElementById("setting-auto-compare").checked
    },
    model: { selection_mode: document.getElementById("admin-model-mode").value },
    adaptive_learning: {
      enabled: document.getElementById("setting-adaptive-enabled").checked,
      minimum_observations: Math.round(clampNumber(document.getElementById("setting-adaptive-min").value,4,96,8)),
      correction_gain: clampNumber(document.getElementById("setting-adaptive-gain").value,0,1,.55),
      maximum_correction_percent_of_limit: clampNumber(document.getElementById("setting-adaptive-cap").value,0,15,5),
      residual_window: Math.round(clampNumber(document.getElementById("setting-adaptive-window").value,48,1000,192)),
      retraining_interval_new_readings: Math.round(clampNumber(document.getElementById("setting-retrain-interval").value,96,10000,336))
    },
    operational
  };
}

async function saveSystemSettings() { const response = await adminJSON("/api/system-settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(systemSettingsPayload()) }); state.systemSettings = response.settings || {}; if (response.simulation) renderSimulationState(response.simulation); document.getElementById("admin-save-status").textContent = `Saved revision ${Number(response.settings?.revision || 0)}. Runtime model and replay reset are active.`; toast("Admin settings saved"); return response; }
async function openAdmin() {
  if (!state.adminToken) {
    showAdminLogin();
    return;
  }
  try {
    await loadSystemSettings();
    showDialogIfClosed(document.getElementById("admin-dialog"));
  } catch (error) {
    if (isAdminSessionError(error)) {
      showAdminLogin("Your previous Admin session is no longer valid. Sign in again.");
      return;
    }
    throw error;
  }
}
async function runControlledTest() { const values = [1,2,3,4].map(index => Number(document.getElementById(`admin-test-${index}`).value)); if (values.some(value => !Number.isFinite(value) || value < 0)) throw new Error("Enter four valid non-negative kVA values."); const requestId = `proof-${Date.now()}-${Math.random().toString(16).slice(2,10)}`; const result = await adminJSON("/api/admin/test-forecast", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: requestId, facility_id: document.getElementById("admin-test-facility").value, values_kva: values, model_mode: document.getElementById("admin-test-model").value }) }); const order = ["30_minutes","2_hours","6_hours","24_hours"]; document.getElementById("admin-test-result").innerHTML = `<div class="proof-boundary"><strong>${esc(result.facility_id)} · ${riskLabel(result.risk)} risk</strong><span>Isolated test. Production state changed: ${result.production_state_changed ? "yes" : "no"}. Adaptive learning updated: ${result.adaptive_learning_updated ? "yes" : "no"}.</span></div><div class="proof-grid">${order.map(key => { const row=result.forecasts?.[key]||{}; const models=row.model_predictions||{}; return `<div><span>${esc(key.replaceAll("_"," "))}</span><strong>${fmt(row.forecast_kva||0,1)} kVA</strong><small>Selected: ${esc(row.selected_model||"")} · upper ${fmt(row.forecast_upper_kva||0,1)} kVA</small><details><summary>All model outputs</summary>${Object.entries(models).map(([name,value])=>`<p>${esc(name.replaceAll("_"," "))}: ${fmt(value,1)} kVA</p>`).join("")}</details></div>`; }).join("")}</div>`; }
async function changeAdminPassword() { const result = await adminJSON("/api/admin/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: document.getElementById("admin-current-password").value, new_password: document.getElementById("admin-new-password").value }) }); state.adminToken = null; document.getElementById("admin-dialog").close(); document.getElementById("admin-password-result").textContent = result.status; toast("Admin password changed. Log in again."); }
async function logoutAdmin() { if (state.adminToken) await adminJSON("/api/admin/logout", { method: "POST" }).catch(()=>{}); state.adminToken = null; document.getElementById("admin-dialog").close(); toast("Admin session closed"); }

function loadSafeOperationalDefaults() { document.getElementById("admin-operational-json").value = JSON.stringify(SAFE_OPERATIONAL_DEFAULTS, null, 2); document.getElementById("admin-save-status").textContent = "Safe defaults loaded locally. Press Save admin settings to apply."; }
async function refreshAdminDiagnostics() { const payload = await adminJSON("/api/admin/diagnostics"); document.getElementById("admin-diagnostics-result").textContent = JSON.stringify(payload, null, 2); }

async function openAbout() {
  const payload = await getJSON("/api/about");
  const comparison = payload.model_comparison || {};
  const models = comparison.models || {};
  const selected = payload.selected_metrics || {};
  const names = ["gradient_boosting","lstm","transformer","hybrid_gb_lstm","hybrid_gb_transformer","hybrid_lstm_transformer","hybrid_all"];
  const horizonOrder = ["30_minutes","2_hours","6_hours","24_hours"];
  const dataset = payload.dataset || {};
  document.getElementById("about-content").innerHTML = `
    <section class="about-hero"><span class="eyebrow">WHY THIS IS A PRODUCT</span><h3>Meter data becomes a forecast, a safe decision and a verified outcome.</h3><p>${esc(payload.product?.problem || payload.product?.purpose || "")}</p><div class="about-track-position">${esc(payload.product?.track_position || "")}</div></section>
    <section class="about-kpis">
      <div><span>Validated rows</span><strong>${Number(dataset.rows || 0).toLocaleString()}</strong></div>
      <div><span>Facilities</span><strong>${Number(dataset.facilities || 0)}</strong></div>
      <div><span>History window</span><strong>${Number(dataset.sequence_length_intervals || 0)} intervals</strong></div>
      <div><span>Final test</span><strong>${esc(dataset.splits?.test_period || "Chronological holdout")}</strong></div>
    </section>
    <section class="settings-section"><h3>Automatic production selection</h3><p>${esc(payload.selection_reason || "")}</p><div class="selection-metric-grid">${horizonOrder.map(horizon => { const row=selected[horizon]||{}; return `<div><span>${esc(horizon.replaceAll("_"," "))}</span><strong>${esc(String(row.model||"unavailable").replaceAll("_"," "))}</strong><small>MAE ${fmt(row.mae_kva||0,2)} kVA · recall ${pct(row.high_risk_recall||0,1)} · F1 ${fmt(row.high_risk_f1||0,2)}</small></div>`; }).join("")}</div></section>
    <section class="settings-section"><h3>Validated model comparison</h3><div class="model-metric-table"><table class="cost-table"><thead><tr><th>Model</th><th>30-min MAE</th><th>30-min recall</th><th>30-min F1</th><th>Mean MAE</th></tr></thead><tbody>${names.filter(name=>models[name]).map(name=>`<tr><td>${esc(name.replaceAll("_"," "))}</td><td>${fmt(models[name].horizons?.["30_minutes"]?.mae_kva||0,2)} kVA</td><td>${pct(models[name].horizons?.["30_minutes"]?.high_risk_recall||0,1)}</td><td>${fmt(models[name].horizons?.["30_minutes"]?.high_risk_f1||0,2)}</td><td>${fmt(models[name].mean_mae_kva||0,2)} kVA</td></tr>`).join("")}</tbody></table></div></section>
    <section class="settings-section"><h3>Chronos-2 local benchmark</h3>${payload.chronos2_comparison?.status === "pass" ? `<p>${esc(payload.chronos2_comparison.summary?.default || "Chronos-2 is used only where it clears validation accuracy and recall guardrails.")}</p><div class="selection-metric-grid">${horizonOrder.map(horizon=>{const row=payload.chronos2_comparison.selected_by_horizon?.[horizon]||{};const test=row.test_metrics||{};return `<div><span>${esc(horizon.replaceAll("_"," "))}</span><strong>${esc(chronosRouteLabel(row.model||"existing"))}</strong><small>MAE ${fmt(test.mae_kva||0,2)} kVA · recall ${pct(test.high_risk_recall||0,1)} · F1 ${fmt(test.high_risk_f1||0,2)}</small></div>`;}).join("")}</div>` : '<div class="empty">Chronos-2 evidence will appear after the local setup and benchmark transaction succeeds. Existing validated models remain the default meanwhile.</div>'}</section>
    <section class="settings-section"><h3>Recommendation explanation</h3><p>SIMBA-EMS explains each recommendation from measured demand, recent trend, expected and conservative forecasts, facility limits, model agreement, tariff period and engineering rules. Chronos-2 supplies numerical time-series forecasts; it is not used as an unrestricted text generator.</p></section>
    <section class="settings-section"><h3>Development Track evidence</h3><div class="deployment-grid">${(payload.development_evidence||[]).map(item=>`<div><strong>${esc(item)}</strong></div>`).join("")}</div></section>
    <section class="settings-section"><h3>Safety boundary</h3><div class="deployment-grid">${(payload.safety||[]).map(item=>`<div><strong>${esc(item)}</strong></div>`).join("")}</div></section>
    <section class="viz-callout"><strong>Claim boundary</strong><p>${esc(payload.claim_boundary||"")}</p></section>`;
  document.getElementById("about-dialog").showModal();
}

async function openStatus() { const payload = await getJSON("/api/health/deep"); document.getElementById("status-content").innerHTML = Object.entries(payload).map(([key,value])=>`<div class="status-row"><span>${esc(key.replaceAll("_"," "))}</span><strong>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</strong></div>`).join(""); document.getElementById("status-dialog").showModal(); }
function bindMenusAndSettings() {
  const menu=document.getElementById("more-menu"),button=document.getElementById("menu-open"); button.addEventListener("click",()=>{menu.hidden=!menu.hidden;button.setAttribute("aria-expanded",String(!menu.hidden));}); document.addEventListener("click",event=>{if(!menu.hidden&&!menu.contains(event.target)&&event.target!==button){menu.hidden=true;button.setAttribute("aria-expanded","false");}});
  menu.querySelectorAll("[data-menu-action]").forEach(item=>item.addEventListener("click",()=>{menu.hidden=true;const action=item.dataset.menuAction;if(action==="settings")openNotificationSettings().catch(error=>toast(error.message));if(action==="admin")openAdmin().catch(error=>toast(error.message));if(action==="about")openAbout().catch(error=>toast(error.message));if(action==="status")openStatus().catch(error=>toast(error.message));}));
  document.getElementById("setting-gmail-port").addEventListener("change",()=>syncGmailTransport("port")); document.getElementById("setting-gmail-security").addEventListener("change",()=>syncGmailTransport("security")); document.getElementById("add-email-recipient").addEventListener("click",()=>addRecipientRow()); document.getElementById("settings-save").addEventListener("click",()=>saveNotificationSettings().catch(error=>{document.getElementById("settings-save-status").textContent=error.message;toast(error.message);})); document.getElementById("settings-cancel").addEventListener("click",()=>document.getElementById("settings-dialog").close()); document.getElementById("settings-test-email").addEventListener("click",()=>testSettingsRecipient().catch(error=>{document.getElementById("settings-test-result").textContent=error.message;toast(error.message);}));
  document.getElementById("admin-login-submit").addEventListener("click",()=>loginAdmin().catch(error=>{document.getElementById("admin-login-result").textContent=error.message;})); document.getElementById("admin-login-cancel").addEventListener("click",()=>closeDialogIfOpen(document.getElementById("admin-login-dialog"))); document.getElementById("admin-save").addEventListener("click",()=>saveSystemSettings().catch(error=>handleAdminError(error,"admin-save-status"))); document.getElementById("admin-test-run").addEventListener("click",()=>runControlledTest().catch(error=>handleAdminError(error,"admin-test-result"))); document.getElementById("admin-guardrails-default").addEventListener("click",loadSafeOperationalDefaults); document.getElementById("admin-diagnostics-refresh").addEventListener("click",()=>refreshAdminDiagnostics().catch(error=>handleAdminError(error,"admin-diagnostics-result"))); document.getElementById("admin-chronos-refresh").addEventListener("click",()=>refreshChronosAdminStatus().catch(error=>handleAdminError(error,"admin-chronos-status"))); document.getElementById("admin-power-quality-refresh").addEventListener("click",()=>refreshPowerQualityAdminStatus().catch(error=>handleAdminError(error,"admin-power-quality-status"))); document.getElementById("admin-password-change").addEventListener("click",()=>changeAdminPassword().catch(error=>handleAdminError(error,"admin-password-result"))); document.getElementById("admin-logout").addEventListener("click",()=>logoutAdmin().catch(error=>handleAdminError(error)));
  ["settings-dialog","admin-login-dialog","admin-dialog","about-dialog","status-dialog"].forEach(id=>{const dialog=document.getElementById(id);dialog.querySelector("form")?.addEventListener("submit",event=>event.preventDefault());dialog.addEventListener("click",event=>{if(event.target===dialog)dialog.close();});});
}


function edgeValue(value, fallback = "Not reported") {
  return value === null || value === undefined || value === "" ? fallback : value;
}
async function loadEdgeStatus() {
  const [status, readings] = await Promise.all([
    getJSON("/api/edge-status"),
    getJSON("/api/meter-readings?limit=12")
  ]);
  const store = status.store || {};
  const gateway = status.gateway || {};
  const cards = [
    ["Gateway state", edgeValue(gateway.state, "Not started"), edgeValue(gateway.gateway_id, "Run START_SIMBA_EMS.bat")],
    ["Received readings", Number(store.received_count || 0).toLocaleString(), "Accepted by the runtime API store"],
    ["Buffered locally", Number(gateway.buffered_readings || 0).toLocaleString(), "Waiting for server recovery"],
    ["Last facility", edgeValue(store.latest_facility_id, "None"), "Facility from the meter replay"],
    ["Operating boundary", "Monitoring only", "No automatic switching"]
  ];
  document.getElementById("edge-kpis").innerHTML = cards.map(([label, value, detail]) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="detail">${esc(detail)}</div></div>`).join("");
  const details = [
    ["Gateway ID", edgeValue(gateway.gateway_id)],
    ["Facility", edgeValue(gateway.facility_id)],
    ["Mode", edgeValue(gateway.mode)],
    ["Sent readings", Number(gateway.sent_readings || 0).toLocaleString()],
    ["Failed batches", Number(gateway.failed_batches || 0).toLocaleString()],
    ["Last success", edgeValue(gateway.last_success_utc)],
    ["Last error", edgeValue(gateway.last_error, "None")]
  ];
  document.getElementById("edge-status-detail").innerHTML = details.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  const element = document.getElementById("edge-readings");
  if (!readings.items.length) {
    element.innerHTML = '<div class="empty">No meter readings received yet. Run START_SIMBA_EMS.bat.</div>';
    return;
  }
  element.innerHTML = readings.items.map(row => `<div class="reading-row"><span>${esc(String(row.timestamp).replace("T", " "))}</span><strong>${fmt(row.kva, 1)} kVA</strong><span>${fmt(row.kwh, 1)} kWh</span><span>PF ${fmt(row.power_factor, 2)}</span></div>`).join("");
}



function riskLabel(value) {
  const safe = String(value || "unknown").toLowerCase().replaceAll("_", " ");
  return safe.replace(/\b\w/g, char => char.toUpperCase());
}
async function loadLiveForecasts() {
  const [model, livePayload, simulationPayload] = await Promise.all([
    getJSON("/api/model-status"),
    getJSON("/api/live-forecasts?limit=48"),
    getJSON("/api/simulation/state")
  ]);
  const liveItems = livePayload.items || [];
  const metrics = model.metrics || {};
  const shortMetric = metrics["30_minutes"] || {};
  const classification = shortMetric.classification || {};
  const simulationForecasts = simulationPayload.forecasts || [];
  const facilityMap = new Map((simulationPayload.facilities || []).map(item => [item.facility_id, item]));
  const riskRank = { high: 3, medium: 2, low: 1 };
  const ranked = [...simulationForecasts].sort((a, b) => (riskRank[b.risk] || 0) - (riskRank[a.risk] || 0) || Number(b.utilization_percent || 0) - Number(a.utilization_percent || 0));
  const focus = ranked.find(item => String(item.model_alias || "").includes("Central Kitchens")) || ranked[0] || null;
  const campus = simulationPayload.campus || {};
  const cards = [
    ["Forecast service", model.ready ? "Ready" : "Unavailable", "Validated automatic model routing"],
    ["Institution demand", `${fmt(campus.controlled_kva || 0, 1)} kVA`, `${fmt(campus.current_utilization_percent || 0, 1)}% of planning limit`],
    ["30-minute outlook", `${fmt(campus.forecast_kva || 0, 1)} kVA`, `${fmt(campus.forecast_utilization_percent || 0, 1)}% of planning limit`],
    ["Highest current risk", ranked[0] ? riskLabel(ranked[0].risk) : "Waiting", ranked[0]?.facility_name || "No facility forecast"],
    ["Facilities forecast", simulationForecasts.length, `${liveItems.length} traceable live-store record${liveItems.length === 1 ? "" : "s"}`]
  ];
  document.getElementById("live-kpis").innerHTML = cards.map(([label, value, detail]) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="detail">${esc(detail)}</div></div>`).join("");
  document.getElementById("model-quality-strip").innerHTML = [
    ["High-risk precision", classification.precision, "How many warnings were true peak events"],
    ["High-risk recall", classification.recall, "How many peak events were warned in advance"],
    ["High-risk F1", classification.f1, "Balance between recall and alert discipline"],
    ["P95 absolute error", shortMetric.p95_abs_error_kva !== undefined ? `${fmt(shortMetric.p95_abs_error_kva, 2)} kVA` : null, "Tail-error visibility"]
  ].map(([label, value, detail]) => `<div><span>${esc(label)}</span><strong>${typeof value === "number" ? pct(value, 1) : esc(value ?? "Pending retrain")}</strong><small>${esc(detail)}</small></div>`).join("");

  const statusRows = [
    ["Runtime selection", "Automatic validated routing"],
    ["Training period", `${model.training_period?.start || ""} to ${model.training_period?.end || ""}`],
    ["Validation period", `${model.validation_period?.start || ""} to ${model.validation_period?.end || ""}`],
    ["Held-out test", `${model.test_period?.start || ""} to ${model.test_period?.end || ""}`],
    ["Required history", `${Number(model.minimum_history_intervals || 49)} consecutive half-hour intervals`],
    ["Facilities", Number(model.facility_count || simulationForecasts.length || 0).toLocaleString()],
    ["Operating mode", model.operating_mode || "advisory"],
    ["Latest operational interval", String(simulationPayload.current_timestamp || "").replace("T", " ").slice(0, 19)],
    ["Error", model.error || "None"]
  ];
  document.getElementById("live-model-status").innerHTML = statusRows.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");

  if (!focus) {
    document.getElementById("live-horizon-grid").innerHTML = '<div class="empty">Waiting for current facility forecasts.</div>';
    document.getElementById("live-forecast-chart").innerHTML = '<div class="empty">No current forecast is available.</div>';
    document.getElementById("live-forecast-table").innerHTML = '<div class="empty">Waiting for operational state.</div>';
    return;
  }

  const horizonOrder = ["30_minutes", "2_hours", "6_hours", "24_hours"];
  const horizonLabels = { "30_minutes": "30 min", "2_hours": "2 hours", "6_hours": "6 hours", "24_hours": "24 hours" };
  document.getElementById("live-horizon-grid").innerHTML = horizonOrder.map(key => {
    const row = focus.horizons?.[key] || {};
    const upper = Number(row.forecast_upper_kva ?? row.forecast_kva ?? 0);
    const margin = Number(row.uncertainty_margin_kva || 0);
    const limit = Number(focus.limit_kva || 1);
    const risk = upper / Math.max(limit, 1e-9) >= .95 ? "high" : upper / Math.max(limit, 1e-9) >= .85 ? "medium" : "low";
    return `<article class="horizon-card ${risk}"><span>${horizonLabels[key]} · ${esc(focus.facility_name)}</span><strong>${fmt(row.forecast_kva || 0, 1)} kVA</strong><small>Upper ${fmt(upper, 1)} kVA · +${fmt(margin, 1)} uncertainty</small><small>${fmt(upper / Math.max(limit, 1e-9) * 100, 1)}% of limit · ${riskLabel(risk)}</small></article>`;
  }).join("");

  const focusFacility = facilityMap.get(focus.facility_id) || {};
  const horizons = focus.horizons || {};
  renderLineChart(
    "live-forecast-chart",
    ["Current", "30 min", "2 h", "6 h", "24 h"],
    [
      { name: "Expected demand", values: [Number(focusFacility.controlled_kva || 0), ...horizonOrder.map(key => Number(horizons[key]?.forecast_kva || 0))], color: COLORS[0] },
      { name: "Conservative upper", values: [Number(focusFacility.controlled_kva || 0), ...horizonOrder.map(key => Number(horizons[key]?.forecast_upper_kva ?? horizons[key]?.forecast_kva ?? 0))], color: COLORS[4] },
      { name: "Facility limit", values: Array(5).fill(Number(focus.limit_kva || focusFacility.limit_kva || 0)), color: COLORS[3] }
    ],
    `${focus.facility_name} demand (kVA)`
  );

  document.getElementById("live-forecast-table").innerHTML = `<table class="cost-table live-table">
    <thead><tr><th>Facility</th><th>Current</th><th>30 min expected / upper</th><th>2 h</th><th>6 h</th><th>24 h</th><th>Limit</th><th>Risk</th><th>Current response</th></tr></thead>
    <tbody>${ranked.map(row => {
      const facility = facilityMap.get(row.facility_id) || {};
      const h30 = row.horizons?.["30_minutes"] || {};
      return `<tr><td>${esc(row.facility_name || row.facility_id)}</td><td>${fmt(facility.controlled_kva || 0, 1)} kVA</td><td>${fmt(h30.forecast_kva ?? row.forecast_kva ?? 0, 1)} / ${fmt(h30.forecast_upper_kva ?? row.forecast_upper_kva ?? h30.forecast_kva ?? 0, 1)}</td><td>${fmt(row.horizons?.["2_hours"]?.forecast_kva || 0, 1)}</td><td>${fmt(row.horizons?.["6_hours"]?.forecast_kva || 0, 1)}</td><td>${fmt(row.horizons?.["24_hours"]?.forecast_kva || 0, 1)}</td><td>${fmt(row.limit_kva || facility.limit_kva || 0, 1)} kVA</td><td>${riskBadge(row.risk || facility.risk)}</td><td>${fmt(facility.actual_reduction_kva || 0, 1)} kVA</td></tr>`;
    }).join("")}</tbody>
  </table>`;
}

function usd(value, digits = 0) {
  return `$${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

// Backwards-compatible adapter for Stage 4 cost widgets.
// Older Stage 4 files called barChart(id, labels, numericValues, yLabel).
// The current dashboard chart engine uses renderBarChart(id, labels, series, yLabel).
function barChart(id, labels, valuesOrSeries, yLabel = "") {
  const series = Array.isArray(valuesOrSeries)
    && valuesOrSeries.length > 0
    && typeof valuesOrSeries[0] === "object"
    && valuesOrSeries[0] !== null
    && Array.isArray(valuesOrSeries[0].values)
      ? valuesOrSeries
      : [{
          name: yLabel || "Value",
          values: Array.isArray(valuesOrSeries)
            ? valuesOrSeries.map(value => Number(value || 0))
            : [],
          color: COLORS[0]
        }];
  return renderBarChart(id, labels, series, yLabel);
}

async function loadCostImpact() {
  const [planningPayload, impactPayload, powerQualityPayload] = await Promise.all([
    getJSON("/api/cost-impact"),
    getJSON("/api/simulation/impact"),
    getJSON("/api/power-quality-forecasts")
  ]);
  renderPowerQuality(powerQualityPayload);
  const summary = planningPayload.summary || {};
  const metrics = impactPayload.metrics || {};
  const campus = impactPayload.campus || {};
  const actions = impactPayload.actions || impactPayload.active_actions || [];
  const approvedPeakPlan = Number(metrics.approved_peak_reduction_plan_kva || metrics.authorised_reduction_kva || 0);
  const approvedProjectedPeak = Number(metrics.approved_projected_peak_kva ?? Math.max(Number(metrics.baseline_peak_kva || 0) - approvedPeakPlan, 0));
  const energySaving = Number(metrics.approved_energy_cost_saving_estimate_usd || 0);
  const demandSaving = Number(metrics.approved_demand_charge_saving_estimate_usd || 0);
  const totalSaving = Number(metrics.approved_total_cost_saving_estimate_usd || energySaving + demandSaving);
  const approvedActions = Number(metrics.approved_actions || 0);
  const cards = [
    ["Approved reduction plan", `${fmt(approvedPeakPlan, 1)} kVA`, `${approvedActions} operator-approved action${approvedActions === 1 ? "" : "s"}`],
    ["Energy cost avoided", usd(energySaving, 2), `${fmt(metrics.energy_shifted_kwh || 0, 1)} kWh shifted · ${fmt(metrics.energy_curtailed_kwh || 0, 1)} kWh curtailed`],
    ["Demand-charge exposure reduced", usd(demandSaving, 2), "Approved peak reduction × configured monthly demand rate"],
    ["Approved-response estimate", usd(totalSaving, 2), "Available even when external control ports are disabled"],
    ["Approved response", `${approvedActions} action${approvedActions === 1 ? "" : "s"}`, String(impactPayload.control_gateway?.mode || "simulation").replaceAll("_", " ") + " control path"]
  ];
  document.getElementById("cost-kpis").innerHTML = cards.map(([label, value, detail]) => `<div class="kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="detail">${esc(detail)}</div></div>`).join("");

  renderBarChart(
    "cost-monthly-chart",
    ["Observed session peak", "Approved-plan peak", "Planning limit"],
    [{ name: "Demand", values: [Number(metrics.baseline_peak_kva || 0), approvedProjectedPeak, Number(campus.limit_kva || 0)], color: COLORS[0] }],
    "kVA",
    350
  );
  renderBarChart(
    "cost-scenario-chart",
    ["Energy saving", "Demand-charge saving", "Total estimate"],
    [{ name: "Approved-response estimate", values: [energySaving, demandSaving, totalSaving], color: COLORS[0] }],
    "USD planning estimate",
    350
  );

  document.getElementById("cost-monthly-table").innerHTML = actions.length ? `<table class="cost-table"><thead><tr><th>Facility</th><th>Load group</th><th>Phase</th><th>Reduction</th><th>Control command</th><th>Starts</th></tr></thead><tbody>${actions.map(row => `<tr><td>${esc(row.facility_name)}</td><td>${esc(row.load_group_name)}</td><td>${esc(row.phase)}</td><td>${fmt(row.reduction_kva, 1)} kVA</td><td>${esc(String(row.control_command?.status || "recorded").replaceAll("_", " "))}</td><td>${esc(String(row.starts_at || "").replace("T", " ").slice(0, 16))}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">Approve a recommendation to calculate its reduction and cost estimate.</div>';

  const gateway = impactPayload.control_gateway || {};
  const assumptionRows = [
    ["Scenario", impactPayload.scenario?.name || "Current operational session"],
    ["Controller", riskLabel(String(impactPayload.controller_mode || "").replaceAll("_", " "))],
    ["Progress", `${fmt(impactPayload.progress_percent || 0, 0)}%`],
    ["Approved peak reduction", `${fmt(approvedPeakPlan, 1)} kVA`],
    ["Approved saving estimate", usd(totalSaving, 2)],
    ["Public tariff anchor", `${fmt((summary.average_end_user_tariff_usd_per_kwh || 0) * 100, 2)} USc/kWh`],
    ["Control gateway", String(gateway.mode || "simulation").replaceAll("_", " ")],
    ["External control ports", gateway.live_enabled ? "Enabled for authorised pilot" : "Disabled — estimate still available"],
    ["Critical-load violations", Number(metrics.critical_load_violations || 0)],
    ["Estimate basis", metrics.approved_saving_estimate_basis || "Approved duration, reduction and configured tariff rates"]
  ];
  document.getElementById("cost-assumptions").innerHTML = assumptionRows.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
}

async function postJSON(url, payload = {}) {
  return getJSON(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

function timeLabel(timestamp) {
  return String(timestamp || "").slice(11, 16);
}

function riskBadge(risk) {
  const safe = ["low", "medium", "high"].includes(String(risk)) ? String(risk) : "low";
  return `<span class="risk-badge ${esc(safe)}">${esc(safe)}</span>`;
}

function decisionStatusLabel(status) {
  return ({not_approved:"Not approved", approved:"Approved", disapproved:"Disapproved", acknowledged:"Acknowledged"})[status] || "Not approved";
}

function renderRecordedActions(payload) {
  const summaryElement = document.getElementById("live-action-summary");
  const tableElement = document.getElementById("live-action-table");
  if (!summaryElement || !tableElement) return;
  const actions = payload.action_history || [];
  const metrics = payload.metrics || {};
  const currentReduction = Number(metrics.current_reduction_kva || 0);
  const authorisedReduction = Number(metrics.authorised_reduction_kva || 0);
  summaryElement.innerHTML = [
    ["Measured now", `${fmt(currentReduction, 1)} kVA`],
    ["Authorised", `${fmt(authorisedReduction, 1)} kVA`],
    ["Peak reduced", `${fmt(metrics.peak_reduction_kva || 0, 1)} kVA`],
    ["Approved actions", Number(metrics.approved_actions || 0)]
  ].map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
  tableElement.innerHTML = actions.length ? `<table class="cost-table"><thead><tr><th>Recorded</th><th>Facility</th><th>Approved action</th><th>Reduction</th><th>Phase</th><th>Control gateway</th></tr></thead><tbody>${actions.map(action => `<tr><td>${esc(String(action.recorded_utc || "").replace("T", " ").slice(0, 19))}</td><td>${esc(action.facility_name || action.facility_id)}</td><td>${esc(String(action.action || "").replaceAll("_", " "))} · ${esc(action.load_group_name || action.load_group)}</td><td>${fmt(action.reduction_kva || 0, 1)} kVA</td><td>${esc(action.phase || "recorded")}</td><td>${esc(String(action.control_command?.status || "recorded").replaceAll("_", " "))}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">No approved action has been recorded. The table updates immediately after dashboard approval.</div>';
}

function renderApprovalDeck(payload) {
  const container = document.getElementById("simulation-recommendation");
  const approveButton = document.getElementById("simulation-approve");
  if (!container || !approveButton) return;
  const deck = payload.approval_deck || {};
  const items = Array.isArray(deck.items) ? deck.items : [];
  const counts = deck.counts || {};
  if (!items.length) {
    state.approvalDeckIndex = 0;
    state.approvalDeckSelectedId = null;
    approveButton.disabled = true;
    approveButton.textContent = "No card to approve";
    const recommendation = payload.recommendation || {};
    if (recommendation.escalation) {
      const finding = (payload.anomalies || []).find(item => item.facility_id === recommendation.facility_id) || {};
      container.innerHTML = `<div class="recommendation-head"><div><span class="recommendation-label escalation">Investigation required</span><h2>${esc(recommendation.facility_name || "Abnormal demand")}</h2></div><span class="control-blocked">Control blocked</span></div><p class="recommendation-reason">${esc(recommendation.reason || "The after-hours demand differs from its preceding reference.")}</p><div class="recommendation-numbers"><div><span>Reference</span><strong>${fmt(finding.reference_kva || 0, 1)} kVA</strong></div><div><span>Observed</span><strong>${fmt(finding.observed_kva || 0, 1)} kVA</strong></div><div><span>Deviation</span><strong>${fmt(finding.deviation_percent || 0, 1)}%</strong></div></div><div class="investigation-action"><span>Operator response</span><strong>${esc(recommendation.recommended_action || "Investigate equipment state and validate the meter reading.")}</strong></div><div class="safety-lock warning-lock">${esc(recommendation.safety_boundary || "Protected loads are escalated for investigation and are never automatically interrupted.")}</div>`;
    } else {
      container.innerHTML = `<div class="recommendation-head"><div><span class="recommendation-label quiet">Approval deck</span><h2>No recommendation yet</h2></div>${riskBadge(payload.campus?.risk || "low")}</div><p class="recommendation-reason">The AI is monitoring the next demand windows. New recommendations will be added here without removing earlier operator records.</p><div class="safety-lock">Critical loads remain excluded</div>`;
    }
    return;
  }
  let index = items.findIndex(item => item.recommendation_id === state.approvalDeckSelectedId);
  if (index < 0) {
    const newestPending = [...items].reverse().findIndex(item => item.decision_status === "not_approved");
    index = newestPending >= 0 ? items.length - 1 - newestPending : items.length - 1;
  }
  state.approvalDeckIndex = Math.max(0, Math.min(index, items.length - 1));
  const item = items[state.approvalDeckIndex];
  state.approvalDeckSelectedId = item.recommendation_id;
  const status = String(item.decision_status || "not_approved");
  const canApprove = status === "not_approved" && item.execution_available !== false;
  approveButton.disabled = !canApprove;
  approveButton.textContent = canApprove ? "Approve visible card" : decisionStatusLabel(status);
  const actions = (item.actions || []).map(action => `<li><strong>${esc(action.load_group_name)}</strong><span>${esc(String(action.action || "").replaceAll("_", " "))} · ${fmt(action.reduction_kva || 0, 1)} kVA · ${Number(action.duration_minutes || 0)} min</span></li>`).join("");
  const expiredText = status === "not_approved" && !canApprove ? '<div class="deck-expired">Retained for audit. This card has passed its safe execution window; acknowledge or disapprove it and review a newer card.</div>' : "";
  const decisionButtons = status === "not_approved" ? `<div class="deck-decision-actions"><button class="deck-approve" data-deck-decision="approve" ${canApprove ? "" : "disabled"}>Approve this action</button><button data-deck-decision="acknowledge">Acknowledge</button><button data-deck-decision="disapprove">Disapprove</button></div>` : `<div class="deck-decision-record"><strong>${esc(decisionStatusLabel(status))}</strong><span>${esc(item.decision?.operator || "dashboard operator")}${item.decision?.recorded_utc ? ` · ${esc(String(item.decision.recorded_utc).replace("T", " ").slice(0, 19))}` : ""}</span></div>`;
  const detailsOpen = state.approvalDeckExpandedIds.has(item.recommendation_id);
  container.innerHTML = `<div class="deck-header"><div><span class="recommendation-label">Approval deck</span><h2>${esc(item.facility_name || "Facility recommendation")}</h2></div><div class="deck-counter"><strong>${state.approvalDeckIndex + 1}</strong><span>of ${items.length}</span></div></div><div class="deck-counts"><span>${Number(counts.not_approved || 0)} not approved</span><span>${Number(counts.approved || 0)} approved</span><span>${Number(counts.acknowledged || 0)} acknowledged</span><span>${Number(counts.disapproved || 0)} disapproved</span></div><div class="deck-card-status ${esc(status)}">${esc(decisionStatusLabel(status))}${item.current ? " · current interval" : ""}</div><p class="recommendation-reason">${esc(item.reason || "Demand may exceed the configured planning limit.")}</p><div class="recommendation-numbers compact"><div><span>Forecast</span><strong>${fmt(item.forecast_kva || 0, 1)} kVA</strong></div><div><span>Limit</span><strong>${fmt(item.facility_limit_kva || 0, 1)} kVA</strong></div><div><span>Safe reduction</span><strong>${fmt(item.planned_reduction_kva || 0, 1)} kVA</strong></div></div>${expiredText}<details class="deck-details" data-recommendation-id="${esc(item.recommendation_id)}" ${detailsOpen ? "open" : ""}><summary>Action plan and AI explanation</summary><ul class="action-plan">${actions || "<li>No controllable action remained available.</li>"}</ul><p>${esc(item.reason || "")}</p><small>The forecast identifies risk. Engineering rules limit the response. The operator alone authorises the command.</small></details>${decisionButtons}<div class="deck-navigation"><button id="deck-prev" ${state.approvalDeckIndex === 0 ? "disabled" : ""}>← Previous</button><span>Swipe through the decision record</span><button id="deck-next" ${state.approvalDeckIndex >= items.length - 1 ? "disabled" : ""}>Next →</button></div><div class="safety-lock">Critical loads excluded · command gateway: ${esc(String(payload.control_gateway?.mode || "simulation").replaceAll("_", " "))}</div>`;
  const detailsElement = container.querySelector("details.deck-details");
  detailsElement?.addEventListener("toggle", () => {
    if (detailsElement.open) state.approvalDeckExpandedIds.add(item.recommendation_id);
    else state.approvalDeckExpandedIds.delete(item.recommendation_id);
  });
  document.getElementById("deck-prev")?.addEventListener("click", () => { state.approvalDeckIndex -= 1; state.approvalDeckSelectedId = items[state.approvalDeckIndex]?.recommendation_id || null; renderApprovalDeck(payload); });
  document.getElementById("deck-next")?.addEventListener("click", () => { state.approvalDeckIndex += 1; state.approvalDeckSelectedId = items[state.approvalDeckIndex]?.recommendation_id || null; renderApprovalDeck(payload); });
  container.querySelectorAll("button[data-deck-decision]").forEach(button => button.addEventListener("click", () => {
    const decision = button.dataset.deckDecision;
    if (decision === "approve") approveSimulationRecommendation([item.recommendation_id]).catch(error => toast(`Approval failed: ${error.message}`));
    else decideSimulationRecommendation(item.recommendation_id, decision).catch(error => toast(`Decision failed: ${error.message}`));
  }));
}

async function decideSimulationRecommendation(recommendationId, decision) {
  if (state.simulationBusy) return;
  state.simulationBusy = true;
  try {
    const payload = await postJSON("/api/simulation/recommendation-decision", {
      request_id: `decision-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`,
      recommendation_id: recommendationId,
      decision,
      operator: "dashboard-operator",
      note: "Recorded from the Home approval deck."
    });
    renderSimulationState(payload.state);
    toast(`Recommendation ${decision === "acknowledge" ? "acknowledged" : "disapproved"}`);
  } finally { state.simulationBusy = false; }
}

async function loadIntegrationStatus() {
  const payload = await getJSON("/api/integration/status");
  state.integration = payload;
  const meter = payload.meter_ingestion || {};
  const forecast = payload.forecasting || {};
  const control = payload.control_gateway || {};
  const simulation = state.simulation || {};
  const deck = simulation.approval_deck || {};
  const metrics = simulation.metrics || {};
  const steps = [
    ["1", "Meter connection", meter.status === "receiving" ? `${Number(meter.stored_readings || 0).toLocaleString()} readings received` : "Secure ingestion API ready", meter.status !== "error"],
    ["2", "Clean and validate", "Schema, timestamp, range and duplicate checks", true],
    ["3", "AI forecast", `${esc(String(forecast.active_mode || "automatic").replaceAll("_", " "))} · four horizons`, forecast.status === "ready"],
    ["4", "Operator approval", `${Number(deck.counts?.not_approved || 0)} card${Number(deck.counts?.not_approved || 0) === 1 ? "" : "s"} awaiting decision`, true],
    ["5", "Control gateway", control.live_enabled ? "Authorised live gateway ready" : `${esc(String(control.mode || "simulation").replaceAll("_", " "))} mode`, control.ready !== false],
    ["6", "Verify impact", `${fmt(metrics.current_reduction_kva || 0, 1)} kVA measured now`, true]
  ];
  const element = document.getElementById("home-pipeline");
  if (element) element.innerHTML = steps.map(([number, label, detail, ready]) => `<article class="pipeline-step ${ready ? "ready" : "pending"}"><span>${number}</span><div><strong>${esc(label)}</strong><small>${detail}</small></div></article>`).join("");
  return payload;
}


function activateHelpDots() {
  document.querySelectorAll(".help-dot").forEach(item => {
    if (!item.hasAttribute("tabindex")) item.setAttribute("tabindex", "0");
    if (!item.hasAttribute("role")) item.setAttribute("role", "note");
  });
}

function simulationRows(payload) {
  const rows = [...(payload.timeline || [])];
  if (!rows.length || rows[rows.length - 1].index !== payload.cursor) {
    rows.push({
      index: payload.cursor,
      timestamp: payload.current_timestamp,
      campus: payload.campus
    });
  }
  return rows;
}

function renderSimulationState(payload) {
  state.simulation = payload;
  const campus = payload.campus || {};
  const metrics = payload.metrics || {};
  const model = payload.model || {};
  const scenario = payload.scenario || {};
  const playback = payload.playback || {};
  const recommendations = Array.isArray(payload.recommendations) ? payload.recommendations : (payload.recommendation?.available ? [payload.recommendation] : []);
  const sessionLabel = document.getElementById("client-session-label");
  if (sessionLabel) {
    if (payload.status === "completed") sessionLabel.textContent = "Operational session complete";
    else if (recommendations.length) sessionLabel.textContent = `${recommendations.length} safe action${recommendations.length === 1 ? "" : "s"} require review`;
    else if (playback.status === "running") sessionLabel.textContent = "Live replay advancing";
    else if (playback.reason === "operator_approval_required") sessionLabel.textContent = "Replay paused for operator review";
    else sessionLabel.textContent = "Forecast monitoring active";
  }
  const modelLabel = document.getElementById("active-model-label");
  if (modelLabel) modelLabel.textContent = "Validated multi-model forecast";

  const latency = Number(model.latest_batch_inference_latency_ms || metrics.mean_inference_latency_ms || 0);
  const modelCount = Number(model.model_forecast_count || 0);
  const fallbackCount = Number(model.fallback_forecast_count || 0);
  const cards = [
    ["Current interval", timeLabel(payload.current_timestamp), `${esc(payload.tariff_period || "unknown")} tariff period`, "Each operational interval represents 30 minutes of meter data."],
    ["Current institution demand", `${fmt(campus.controlled_kva || 0, 1)} kVA`, `${fmt(campus.current_utilization_percent || 0, 1)}% of planning limit`, "The sum of all monitored facilities after any approved response."],
    ["Next-interval forecast", `${fmt(campus.forecast_kva || 0, 1)} kVA`, `${fmt(campus.forecast_utilization_percent || 0, 1)}% of planning limit`, "The sum of the 30-minute facility forecasts."],
    ["Reduction now", `${fmt(Math.max(Number(campus.baseline_kva || 0) - Number(campus.controlled_kva || 0), 0), 1)} kVA`, `Peak reduced ${fmt(metrics.peak_reduction_kva || 0, 1)} kVA so far · ${Number(metrics.approved_actions || 0)} approved action${Number(metrics.approved_actions || 0) === 1 ? "" : "s"}`, "Current-interval demand reduction, with the verified peak reduction accumulated across the session."],
    ["Inference latency", latency > 0 ? `${fmt(latency, 2)} ms` : "Not measured", `${modelCount} model forecasts${fallbackCount ? ` · ${fallbackCount} fallback` : ""}`, "Measured backend time for the current batch of facility forecasts."],
  ];
  document.getElementById("simulation-kpis").innerHTML = cards.map(([label, value, detail, tip]) => `<div class="kpi"><div class="label">${esc(label)} <span class="help-dot" data-tooltip="${esc(tip)}">?</span></div><div class="value">${value}</div><div class="detail">${detail}</div></div>`).join("");

  const forecasts = payload.forecasts || [];
  const focus = forecasts.find(item => String(item.model_alias || "").includes("Central Kitchens")) || forecasts[0] || {};
  const order = ["30_minutes", "2_hours", "6_hours", "24_hours"];
  const labels = { "30_minutes": "30 minutes", "2_hours": "2 hours", "6_hours": "6 hours", "24_hours": "24 hours" };
  document.getElementById("simulation-focus-horizons").innerHTML = order.map(key => {
    const row = focus.horizons?.[key] || {};
    const upper = Number(row.forecast_upper_kva ?? row.forecast_kva ?? 0);
    const risk = upper / Math.max(Number(focus.limit_kva || 1), 1e-9) >= .95 ? "high" : upper / Math.max(Number(focus.limit_kva || 1), 1e-9) >= .85 ? "medium" : "low";
    return `<div class="horizon-card ${risk}"><span>${labels[key]}</span><strong>${fmt(row.forecast_kva || 0, 1)} kVA</strong><small>Upper ${fmt(upper, 1)} kVA · ${esc(focus.facility_name || "Focus facility")}</small>${riskBadge(risk)}</div>`;
  }).join("");

  const rows = (payload.chart_timeline || simulationRows(payload)).map(row => ({ ...row, campus: row.campus || {} }));
  const futureTimestamp = (() => {
    const parsed = new Date(payload.current_timestamp);
    if (Number.isNaN(parsed.getTime())) return "Next";
    parsed.setMinutes(parsed.getMinutes() + 30);
    return parsed.toISOString();
  })();
  const chartRows = [...rows, { index: Number(payload.cursor || 0) + 1, timestamp: futureTimestamp, campus: { baseline_kva: null, controlled_kva: null, forecast_kva: Number(campus.forecast_kva || 0), limit_kva: Number(campus.limit_kva || 0) } }];
  renderLineChart(
    "simulation-chart",
    chartRows.map(row => timeLabel(row.timestamp) || "Next"),
    [
      { name: "Measured replay", values: chartRows.map(row => row.campus.baseline_kva), color: COLORS[2] },
      { name: "Controlled", values: chartRows.map(row => row.campus.controlled_kva), color: COLORS[0] },
      { name: "30-min forecast", values: chartRows.map(row => row.campus.forecast_kva), color: COLORS[1] },
      { name: "Planning limit", values: chartRows.map(row => Number(row.campus.limit_kva || campus.limit_kva || 0)), color: COLORS[3] }
    ],
    "Institution demand (kVA)",
    430
  );
  const playbackText = playback.status === "running" ? `Automatic replay · ${fmt(playback.playback_interval_seconds || 0, 1)} seconds per interval` : playback.reason === "operator_approval_required" ? "Paused for operator approval" : "Replay paused";
  document.getElementById("simulation-progress").innerHTML = `<div><span style="width:${Math.max(0, Math.min(100, Number(payload.progress_percent || 0)))}%"></span></div><p>${fmt(payload.progress_percent || 0, 0)}% complete. Interval ${Number(payload.cursor || 0) + 1} of ${payload.total_steps}. ${esc(playbackText)}. ${esc(scenario.demonstration_goal || "")}</p>`;

  renderApprovalDeck(payload);
  renderRecordedActions(payload);
  if (state.integration) loadIntegrationStatus().catch(error => console.error(error));

  const riskRank = { high: 3, medium: 2, low: 1 };
  const forecastMap = new Map(forecasts.map(item => [item.facility_id, item]));
  const sortedFacilities = [...(payload.facilities || [])].sort((a, b) => {
    const fa = forecastMap.get(a.facility_id) || {};
    const fb = forecastMap.get(b.facility_id) || {};
    return (riskRank[fb.risk] || 0) - (riskRank[fa.risk] || 0) || Number(fb.utilization_percent || 0) - Number(fa.utilization_percent || 0);
  });
  document.getElementById("simulation-facilities").innerHTML = `<table class="cost-table simulation-table"><thead><tr><th>Facility</th><th>Current</th><th>30 min</th><th>2 h</th><th>6 h</th><th>24 h</th><th>Limit</th><th>Risk</th><th>Available response</th><th>Reduction applied</th></tr></thead><tbody>${sortedFacilities.map(facility => {
    const forecast = forecastMap.get(facility.facility_id) || {};
    const rec = recommendations.find(item => item.facility_id === facility.facility_id);
    const focusClass = String(forecast.model_alias || "").includes("Central Kitchens") ? " class=\"focus-row\"" : "";
    return `<tr${focusClass}><td>${esc(facility.facility_name)}</td><td>${fmt(facility.controlled_kva, 1)} kVA</td><td>${fmt(forecast.horizons?.["30_minutes"]?.forecast_kva ?? forecast.forecast_kva ?? 0, 1)}</td><td>${fmt(forecast.horizons?.["2_hours"]?.forecast_kva || 0, 1)}</td><td>${fmt(forecast.horizons?.["6_hours"]?.forecast_kva || 0, 1)}</td><td>${fmt(forecast.horizons?.["24_hours"]?.forecast_kva || 0, 1)}</td><td>${fmt(facility.limit_kva, 1)} kVA</td><td>${riskBadge(forecast.risk || facility.risk)}</td><td>${rec ? `${fmt(rec.planned_reduction_kva, 1)} kVA` : "None"}</td><td>${fmt(facility.actual_reduction_kva, 1)} kVA</td></tr>`;
  }).join("")}</tbody></table>`;

  const autoButton = document.getElementById("admin-run-auto");
  if (autoButton) autoButton.textContent = playback.status === "running" ? "Pause automatic replay" : playback.reason === "operator_approval_required" ? "Resume after review" : "Start automatic replay";
  activateHelpDots();
}

async function loadSimulationState() {
  const payload = await getJSON("/api/simulation/state");
  renderSimulationState(payload);
  return payload;
}

async function loadSimulationScenarios() {
  const payload = await adminJSON("/api/simulation/scenarios");
  state.simulationScenarios = payload.items || [];
  const selector = document.getElementById("setting-simulation-scenario");
  selector.innerHTML = state.simulationScenarios.map(item => `<option value="${esc(item.scenario_id)}">${esc(item.name)} · ${Number(item.facility_count || 0)} facilities</option>`).join("");
}

async function resetSimulation() {
  const simulation = state.systemSettings?.simulation || {};
  const payload = await adminPost("/api/simulation/reset", {
    scenario_id: simulation.scenario_id || state.simulationScenarios[0]?.scenario_id,
    controller_mode: simulation.controller_mode || "ai_assisted"
  });
  renderSimulationState(payload);
  const adminStatus = document.getElementById("admin-run-status");
  if (adminStatus) adminStatus.textContent = "Replay reset. Start automatic replay when ready.";
  toast("Simulation reset");
}

async function stepSimulation(count = 1) {
  if (state.simulationBusy) return;
  state.simulationBusy = true;
  try {
    const payload = await adminPost("/api/simulation/step", { count });
    renderSimulationState(payload.state);
  } finally {
    state.simulationBusy = false;
  }
}

async function approveSimulationRecommendation(recommendationIds = []) {
  if (state.simulationBusy) return;
  state.simulationBusy = true;
  try {
    const requestId = `approval-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    const payload = await postJSON("/api/simulation/approve-recommendation", {
      request_id: requestId,
      recommendation_ids: recommendationIds,
      operator: "dashboard-operator"
    });
    renderSimulationState(payload.state);
    if (payload.applied) {
      toast(`${payload.applied} safe load action${payload.applied === 1 ? "" : "s"} approved`);
      loadCostImpact().catch(error => console.error(error));
      loadIntegrationStatus().catch(error => console.error(error));
    } else {
      const reason = payload.reason || payload.skipped?.[0]?.reason || "No action remained available";
      toast(`Approval not applied: ${reason}`);
    }
  } finally {
    state.simulationBusy = false;
  }
}

async function setSimulationPlayback(action) {
  if (state.simulationBusy) return;
  state.simulationBusy = true;
  try {
    const payload = await adminPost("/api/simulation/playback", { action });
    renderSimulationState(payload.state);
    const adminStatus = document.getElementById("admin-run-status");
    if (adminStatus) adminStatus.textContent = action === "start" ? "Automatic replay is running on the backend." : "Automatic replay is paused.";
  } finally {
    state.simulationBusy = false;
  }
}

function stopSimulationAutoRun() {
  return setSimulationPlayback("stop");
}

function toggleSimulationAutoRun() {
  const running = state.simulation?.playback?.status === "running";
  return setSimulationPlayback(running ? "stop" : "start").catch(error => {
    const adminStatus = document.getElementById("admin-run-status");
    if (adminStatus) adminStatus.textContent = error.message;
    toast(`Replay control failed: ${error.message}`);
  });
}

async function compareSimulationControllers() {
  const scenarioId = state.systemSettings?.simulation?.scenario_id || state.simulationScenarios[0]?.scenario_id;
  const button = document.getElementById("admin-run-compare");
  button.disabled = true;
  button.textContent = "Running comparison…";
  try {
    const payload = await adminJSON(`/api/simulation/comparison?scenario_id=${encodeURIComponent(scenarioId)}`);
    state.simulationComparison = payload;
    const comparison = payload.comparison || {};
    const controllers = payload.controllers || {};
    document.getElementById("admin-run-status").innerHTML = `<div class="comparison-summary"><div><span>Forecast reduction vs no control</span><strong>${fmt(comparison.ai_peak_reduction_vs_no_control_kva || 0, 1)} kVA</strong></div><div><span>Rule reduction vs no control</span><strong>${fmt(comparison.simple_peak_reduction_vs_no_control_kva || 0, 1)} kVA</strong></div><div><span>Forecast advantage vs rule</span><strong>${fmt(comparison.ai_additional_peak_reduction_vs_simple_rule_kva || 0, 1)} kVA</strong></div><div><span>Critical violations</span><strong>${Number(comparison.all_critical_load_violations || 0)}</strong></div></div><p>Identical baseline, limits and load constraints were used for all controllers. Only decision timing changed.</p>`;
    toast("Controller comparison completed");
  } finally {
    button.disabled = false;
    button.textContent = "Compare controllers";
  }
}

function bindSimulationControls() {
  document.getElementById("simulation-approve").addEventListener("click", () => { const id = state.approvalDeckSelectedId; if (id) approveSimulationRecommendation([id]).catch(error => toast(`Approval failed: ${error.message}`)); });
  document.getElementById("admin-run-reset").addEventListener("click", () => resetSimulation().catch(error => { document.getElementById("admin-run-status").textContent = error.message; }));
  document.getElementById("admin-run-step").addEventListener("click", () => stepSimulation(1).catch(error => { document.getElementById("admin-run-status").textContent = error.message; }));
  document.getElementById("admin-run-auto").addEventListener("click", toggleSimulationAutoRun);
  document.getElementById("admin-run-compare").addEventListener("click", () => compareSimulationControllers().catch(error => { document.getElementById("admin-run-status").textContent = error.message; }));
}

function renderInstitutionalCaseEvidence(payload) {
  const target = document.getElementById("institutional-case-evidence");
  const boundary = document.getElementById("institutional-case-boundary");
  if (!target || !boundary) return;
  if (!payload || payload.status === "not_generated") {
    target.innerHTML = '<div class="empty full-span">Institutional case evidence is not available.</div>';
    boundary.textContent = "";
    return;
  }
  const cost = Number(payload.annual_electricity_expenditure_usd || 0);
  const shares = payload.active_semester_cost_breakdown_percent || {};
  const targetPercent = payload.conservative_saving_target_percent || {};
  const targetUsd = payload.conservative_annual_saving_target_usd || {};
  const cards = [
    ["Annual electricity pressure", `${payload.annual_expenditure_qualifier === "more_than" ? ">" : ""}${usd(cost, 0)}`, "Documented institutional expenditure baseline"],
    ["Peak + demand exposure", `${fmt(payload.peak_and_demand_cost_exposure_percent || 0, 1)}%`, `${fmt(shares.peak_energy || 0, 1)}% peak energy + ${fmt(shares.demand || 0, 1)}% demand charges`],
    ["Reactive-energy share", `${fmt(shares.reactive || 0, 1)}%`, "Evidence of facility-level power-factor opportunity"],
    ["Conservative reduction target", `${fmt(targetPercent.minimum || 0, 0)}–${fmt(targetPercent.maximum || 0, 0)}%`, `${usd(targetUsd.minimum || 0, 0)}–${usd(targetUsd.maximum || 0, 0)} per year`],
    ["Operational evidence", `${Number(payload.facility_count || 0)} facilities`, `${Number(payload.regularised_readings || 0).toLocaleString()} regularised half-hour readings`]
  ];
  target.innerHTML = cards.map(([label, value, detail]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`).join("");
  boundary.textContent = payload.claim_boundary || "The reduction range is a pilot target, not a realised saving.";
}

async function loadReadinessEvidence() {
  const payload = await getJSON("/api/readiness-evidence");
  const edge = payload.edge_runtime || {};
  const validation = payload.model_validation || {};
  const simulation = payload.simulation || {};
  const edgeModel = edge.model || {};
  const edgeLatency = edge.latency_ms || {};
  const shortMetric = validation.horizons?.["30_minutes"] || {};
  const longMetric = validation.horizons?.["6_hours"] || {};
  const classification = shortMetric.classification || {};
  const cards = [
    ["Model bundle", `${fmt(edgeModel.bundle_size_megabytes || 0, 3)} MB`, edge.status === "pass" ? "Portable local inference" : "Benchmark available after validation"],
    ["P95 inference", `${fmt(edgeLatency.p95 || 0, 2)} ms`, edge.status === "pass" ? "Suitable for an edge gateway" : "Benchmark available after validation"],
    ["30-minute test MAE", `${fmt(shortMetric.mae_kva || 0, 2)} kVA`, "Held-out April 2026"],
    ["High-risk recall", classification.recall !== undefined ? eventMetric(classification.recall, classification.events ?? classification.high_risk_events ?? classification.positive_events ?? 1, 1) : "Pending", "Held-out peak-event detection"],
    ["High-risk F1", classification.f1 !== undefined ? eventMetric(classification.f1, classification.events ?? classification.high_risk_events ?? classification.positive_events ?? 1, 2, false) : "Pending", "Recall and alert precision balance"],
    ["6-hour improvement", `${fmt(longMetric.mae_improvement_vs_persistence_percent || 0, 1)}%`, "Against persistence"],
    ["Closed-loop scenarios", Number(simulation.scenario_count || 0), "Forecast, approval, response and audit"]
  ];
  document.getElementById("readiness-grid").innerHTML = cards.map(([label, value, detail]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`).join("");
  renderChronosEvidence(payload.chronos2_validation || {});
  renderPowerQualityEvidence(payload.power_quality_validation || {});
  renderInstitutionalCaseEvidence(payload.institutional_case || {});
}

async function init() {
  activateTabs();
  activateHelpDots();
  bindSimulationControls();
  bindMenusAndSettings();
  document.getElementById("refresh-alerts").addEventListener("click", () => loadAlerts().catch(error => toast(`Alert refresh failed: ${error.message}`)));
  document.getElementById("refresh-edge").addEventListener("click", () => loadEdgeStatus().catch(error => toast(`Edge status failed: ${error.message}`)));
  document.getElementById("refresh-cost").addEventListener("click", () => loadCostImpact().catch(error => toast(`Impact refresh failed: ${error.message}`)));
  document.getElementById("refresh-live").addEventListener("click", () => Promise.all([loadLiveForecasts(), loadPowerQualityForecasts(true), loadAlerts()]).catch(error => toast(`Forecast refresh failed: ${error.message}`)));
  document.getElementById("process-notifications").addEventListener("click", () => processNotifications().catch(error => toast(`Notification processing failed: ${error.message}`)));
  document.getElementById("test-email").addEventListener("click", () => testNotification().catch(error => toast(`Gmail test failed: ${error.message}`)));
  document.getElementById("refresh-client-view").addEventListener("click", () => Promise.all([loadSimulationState(), loadLiveForecasts(), loadPowerQualityForecasts(true), loadAlerts()]).catch(error => toast(`Refresh failed: ${error.message}`)));
  document.getElementById("open-operations").addEventListener("click", () => activatePanel("operations"));

  const status = document.getElementById("api-status");
  try {
    const health = await getJSON("/api/health");
    status.textContent = health.status === "online" ? "API online" : health.status;
    status.classList.add("online");
  } catch (error) {
    status.textContent = "API unavailable";
    toast(`Health check failed: ${error.message}`);
    return;
  }

  try {
    await loadSimulationState();
    if (state.clientPollTimer) window.clearInterval(state.clientPollTimer);
    state.clientPollTimer = window.setInterval(() => {
      loadSimulationState().catch(error => console.error("Operational polling failed", error));
    }, 1000);
    if (state.powerQualityPollTimer) window.clearInterval(state.powerQualityPollTimer);
    state.powerQualityPollTimer = window.setInterval(() => {
      loadPowerQualityForecasts().catch(error => console.error("Power-quality polling failed", error));
    }, 12000);
  } catch (error) {
    toast(`Live operational state failed to load: ${error.message}`);
  }

  try {
    [state.summary, state.evidence, state.control] = await Promise.all([
      getJSON("/api/summary"),
      getJSON("/api/evidence"),
      getJSON("/api/control-comparison")
    ]);
    renderKPIs(state.summary);
    renderForecast();
    renderEvidence(state.evidence);
    renderControl(state.control, state.evidence);
  } catch (error) {
    toast(`Core evidence failed to load: ${error.message}`);
  }

  const optionalLoads = await Promise.allSettled([
    loadAlerts(),
    loadDecisionLog(),
    loadEdgeStatus(),
    loadLiveForecasts(),
    loadPowerQualityForecasts(),
    loadCostImpact(),
    loadReadinessEvidence(),
    loadNotificationStatus(),
    loadNotificationLog(),
    loadIntegrationStatus()
  ]);
  optionalLoads.forEach(result => {
    if (result.status === "rejected") {
      console.error(result.reason);
    }
  });
}
init();
