"use strict";
const $ = (selector) => document.querySelector(selector);
const state = {view: "overview", resource: "orders", recordsLoaded: false, offset: 0, total: 0, limit: 8, busy: false, actions: new Map(), tableRequest: 0};
const money = (cents) => new Intl.NumberFormat("en-IE", {style: "currency", currency: "EUR"}).format(cents / 100);
const node = (tag, cls, text) => {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = String(text);
  return el;
};
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: "same-origin", ...options,
    headers: {"Content-Type": "application/json", ...options.headers}});
  const body = await response.json();
  if (response.status === 401) {
    location.replace("/login");
    throw new Error("Your session has ended. Please sign in again.");
  }
  if (!response.ok) throw new Error(body.error?.message || (response.status === 422 ? "Please check the supplied values." : "The service is unavailable. Try again."));
  return body;
}
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { $("#toast").hidden = true; }, 4500);
}
function errorAt(selector, error) {
  const el = $(selector);
  el.textContent = error ? error.message || String(error) : "";
  el.hidden = !error;
}
function badge(value, label) { return node("span", `badge ${value}`, label || value); }
function recordLink(resource, id, label) {
  const button = node("button", "record-link", label || id);
  button.type = "button";
  button.addEventListener("click", () => openRecord(resource, id));
  return button;
}
async function refreshDashboard() {
  const data = await api("/api/dashboard");
  $("#metric-customers").textContent = data.customers;
  $("#metric-orders").textContent = data.orders;
  $("#metric-outstanding").textContent = money(data.outstanding_cents);
  $("#metric-overdue").textContent = `${money(data.overdue_cents)} overdue`;
  $("#metric-stock").textContent = String(data.stock_alerts).padStart(2, "0");
  renderCharts(data);
}
const pageLabels = {overview: "Overview", assistant: "ERP Agent", customers: "Customers", orders: "Orders", stock: "Inventory", invoices: "Invoices"};
function navigate(view, {focus = true} = {}) {
  if (!Object.hasOwn(pageLabels, view)) view = "overview";
  state.view = view;
  const records = !["overview", "assistant"].includes(view);
  $("#overview-view").hidden = view !== "overview";
  $("#assistant-view").hidden = view !== "assistant";
  $("#records-panel").hidden = !records;
  document.querySelectorAll("[data-nav]").forEach(button => {
    const active = button.dataset.nav === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const label = pageLabels[view];
  $("#current-page").textContent = label;
  $("#page-eyebrow").textContent = records ? "YOUR ERP RECORDS" : view === "assistant" ? "YOUR BUSINESS, IN CONVERSATION" : "YOUR OPERATIONS, CONNECTED";
  $("#page-title").replaceChildren(document.createTextNode(view === "overview" ? "A clearer view of your business" : label), node("span", "", "."));
  $("#page-subtitle").textContent = view === "overview" ? "Sales, balances, and stock. Your business at a glance."
    : view === "assistant" ? "Ask a question. Find the answer. Keep things moving."
    : `Search and explore your ${label.toLowerCase()} in one place.`;
  document.title = `smartERP — ${label}`;
  if (location.hash !== `#${view}`) location.hash = view;
  if (records && (state.resource !== view || !state.recordsLoaded)) selectResource(view);
  if (records) $("#records-title").textContent = `${label} records`;
  if (focus) {
    window.scrollTo({top: 0});
    (view === "assistant" ? $("#question") : $("#page-title")).focus({preventScroll: true});
  }
}
function svgNode(tag, attributes = {}, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attributes).forEach(([key, value]) => el.setAttribute(key, String(value)));
  if (text !== undefined) el.textContent = text;
  return el;
}
function chartSvg(viewBox, label) {
  const svg = svgNode("svg", {viewBox, role: "img", "aria-label": label});
  svg.append(svgNode("title", {}, label));
  return svg;
}
function renderCharts(data) {
  const charts = data.charts;
  const sales = charts.sales;
  const total = sales.reduce((sum, item) => sum + item.total_cents, 0);
  const monthLabel = month => new Intl.DateTimeFormat("en-GB", {month: "short", timeZone: "UTC"}).format(new Date(`${month}-01T00:00:00Z`));
  $("#sales-total").textContent = money(total);
  $("#sales-period").textContent = `${monthLabel(sales[0].month)} – ${monthLabel(sales.at(-1).month)} ${data.as_of.slice(0, 4)} · through ${data.as_of}`;
  const salesSvg = chartSvg("0 0 600 230", `Monthly sales: ${sales.map(item => `${item.month}: ${money(item.total_cents)}`).join(", ")}`);
  const max = Math.max(...sales.map(item => item.total_cents), 100);
  for (let i = 0; i <= 4; i++) {
    const y = 18 + i * 43;
    salesSvg.append(svgNode("line", {x1: 66, x2: 575, y1: y, y2: y, class: "chart-gridline"}));
    salesSvg.append(svgNode("text", {x: 55, y: y + 4, "text-anchor": "end", class: "chart-axis"}, new Intl.NumberFormat("en-IE", {style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1}).format(max * (1 - i / 4) / 100)));
  }
  const points = sales.map((item, i) => [66 + i * 101.8, 190 - item.total_cents / max * 172]);
  salesSvg.append(svgNode("polygon", {points: `66,190 ${points.map(p => p.join(",")).join(" ")} 575,190`, class: "sales-area"}));
  salesSvg.append(svgNode("polyline", {points: points.map(p => p.join(",")).join(" "), class: "sales-line"}));
  points.forEach(([x, y], i) => {
    const dot = svgNode("circle", {cx: x, cy: y, r: 4, class: "sales-point"});
    dot.append(svgNode("title", {}, `${sales[i].month}: ${money(sales[i].total_cents)}`));
    salesSvg.append(dot, svgNode("text", {x, y: 218, "text-anchor": "middle", class: "chart-axis"}, monthLabel(sales[i].month)));
  });
  $("#sales-chart").replaceChildren(salesSvg);
  if (!total) $("#sales-chart").append(node("p", "chart-empty", "No sales recorded in this period."));

  const invoiceLabels = {paid: "Paid", issued: "Issued · not overdue", overdue: "Overdue", draft: "Draft"};
  const balances = Object.entries(charts.invoice_balances);
  const invoiceTotal = balances.reduce((sum, [, value]) => sum + value, 0);
  const ring = node("div", "invoice-ring");
  const donut = chartSvg("0 0 180 180", `Invoice balances: ${balances.map(([key, value]) => `${invoiceLabels[key]} ${money(value)}`).join(", ")}`);
  donut.append(svgNode("circle", {cx: 90, cy: 90, r: 68, class: "donut-track"}));
  let offset = 0;
  balances.forEach(([key, value]) => {
    if (!value) return;
    const length = value / invoiceTotal * 100;
    const arc = svgNode("circle", {cx: 90, cy: 90, r: 68, pathLength: 100, "stroke-dasharray": `${length} ${100 - length}`, "stroke-dashoffset": -offset, transform: "rotate(-90 90 90)", class: `donut-arc invoice-${key}`});
    arc.append(svgNode("title", {}, `${invoiceLabels[key]}: ${money(value)}`));
    donut.append(arc); offset += length;
  });
  const center = node("div", "donut-center"); center.append(node("strong", "", money(invoiceTotal)), node("span", "", "Total invoiced"));
  ring.append(donut, center);
  const legend = node("div", "invoice-legend");
  balances.forEach(([key, value]) => {
    const row = node("div", "legend-row");
    row.append(node("span", `legend-mark invoice-${key}`), node("span", "", invoiceLabels[key]), node("strong", "", money(value)));
    legend.append(row);
  });
  $("#invoice-chart").replaceChildren(ring, legend);
  if (!invoiceTotal) legend.append(node("p", "chart-empty", "No invoice balances yet."));
  renderBars("#customer-chart", charts.top_customers, item => item.name, item => item.total_cents, item => money(item.total_cents), "customers");
  renderBars("#stock-chart", charts.stock, item => item.name, item => item.available, item => `${item.available} available · reorder at ${item.reorder_threshold}`, "stock");
}
function renderBars(selector, items, label, value, display, resource) {
  const container = $(selector); container.replaceChildren();
  if (!items.length) { container.append(node("p", "chart-empty", resource === "stock" ? "No products to display." : "No customer sales in this period.")); return; }
  const max = Math.max(1, ...items.map(item => Math.max(value(item), item.reorder_threshold || 0)));
  items.forEach(item => {
    const row = node("div", "bar-row"), heading = node("div", "bar-heading");
    heading.append(recordLink(resource, item.id, label(item)), node("span", item.low_stock ? "stock-alert" : "", display(item)));
    const bar = chartSvg("0 0 500 12", `${label(item)}: ${display(item)}`);
    bar.setAttribute("preserveAspectRatio", "none");
    bar.append(svgNode("rect", {width: 500, height: 12, rx: 4, class: "bar-track"}), svgNode("rect", {width: value(item) / max * 500, height: 12, rx: 4, class: item.low_stock ? "bar-fill low-stock-fill" : "bar-fill"}));
    if (resource === "stock") {
      const x = item.reorder_threshold / max * 500;
      bar.append(svgNode("line", {x1: x, x2: x, y1: 0, y2: 12, class: "stock-threshold"}));
    }
    row.append(heading, bar); container.append(row);
  });
}
function selectResource(resource) {
  state.resource = resource; state.offset = 0; state.recordsLoaded = false;
  $("#record-search").value = "";
  $("#record-search").placeholder = `Search ${resource === "stock" ? "inventory" : resource}…`;
  document.querySelectorAll("[data-tab]").forEach(el => el.setAttribute("aria-selected", String(el.dataset.tab === resource)));
  $("#record-view").setAttribute("aria-labelledby", `tab-${resource}`);
  const filter = $("#status-filter");
  filter.replaceChildren();
  const values = resource === "orders" ? [["", "All statuses"], ["confirmed", "Confirmed"], ["fulfilled", "Fulfilled"], ["cancelled", "Cancelled"]]
    : resource === "invoices" ? [["", "All invoices"], ["overdue", "Overdue"], ["issued", "Issued"], ["draft", "Draft"], ["paid", "Paid"]]
    : resource === "stock" ? [["", "All products"], ["low_stock", "Low stock"]] : [["", "All customers"]];
  values.forEach(([value, text]) => { const opt = node("option", "", text); opt.value = value; filter.append(opt); });
  filter.hidden = resource === "customers";
  return loadTable();
}
async function loadTable() {
  const request = ++state.tableRequest;
  const resource = state.resource;
  const params = new URLSearchParams({limit: state.limit, offset: state.offset, q: $("#record-search").value.trim()});
  const filter = $("#status-filter").value;
  if (filter === "overdue" || filter === "low_stock") params.set(filter, "true");
  else if (filter) params.set("status", filter);
  $("#record-body").setAttribute("aria-busy", "true");
  errorAt("#table-error", null);
  try {
    const data = await api(`/api/erp/${resource}?${params}`);
    if (request !== state.tableRequest) return;
    state.total = data.total;
    state.recordsLoaded = true;
    const columns = resource === "orders" ? ["ORDER", "CUSTOMER", "STATUS", "AMOUNT"]
      : resource === "invoices" ? ["INVOICE", "CUSTOMER", "STATUS", "AMOUNT"]
      : resource === "stock" ? ["PRODUCT", "AVAILABLE", "STATUS", "PRICE"] : ["CUSTOMER", "COMPANY", "CONTACT"];
    const head = node("tr");
    columns.forEach((text, i) => head.append(node("th", i === columns.length - 1 && resource !== "customers" ? "amount" : "", text)));
    $("#record-head").replaceChildren(head);
    const body = $("#record-body"); body.replaceChildren();
    for (const item of data.items) {
      const row = node("tr"), first = node("td");
      first.append(recordLink(resource, item.id));
      if (resource === "stock") first.append(node("span", "secondary-text", item.name));
      row.append(first);
      if (resource === "customers") {
        row.append(node("td", "", item.name), node("td", "", item.contact));
      } else if (resource === "stock") {
        row.append(node("td", "", item.available));
        const status = node("td"); status.append(badge(item.low_stock ? "low" : "fulfilled", item.low_stock ? "Low stock" : "In stock"));
        row.append(status, node("td", "amount", money(item.price_cents)));
      } else {
        row.append(node("td", "", item.customer_name));
        const status = node("td"); status.append(badge(item.overdue ? "overdue" : item.status));
        row.append(status, node("td", "amount", money(item.total_cents)));
      }
      body.append(row);
    }
    if (!data.items.length) { const row = node("tr"), td = node("td", "", "No matching records. Try a different search."); td.colSpan = columns.length; row.append(td); body.append(row); }
    $("#record-count").textContent = data.total ? `Showing ${state.offset + 1}–${Math.min(state.offset + state.limit, data.total)} of ${data.total}` : "No records";
    $("#previous-page").disabled = state.offset === 0;
    $("#next-page").disabled = state.offset + state.limit >= data.total;
  } catch (error) { if (request === state.tableRequest) errorAt("#table-error", error); }
  finally { if (request === state.tableRequest) $("#record-body").removeAttribute("aria-busy"); }
}
function detailField(grid, label, value) {
  const group = node("div"); group.append(node("dt", "", label));
  const dd = node("dd"); dd.append(value instanceof Node ? value : document.createTextNode(String(value)));
  group.append(dd); grid.append(group);
}
function lineTable(lines, cls = "detail-lines") {
  const table = node("table", cls), head = node("thead"), tr = node("tr");
  ["Product", "Qty", "Unit price", "Total"].forEach(text => tr.append(node("th", "", text)));
  head.append(tr); table.append(head);
  const body = node("tbody");
  lines.forEach(line => { const row = node("tr"); row.append(node("td", "", line.name), node("td", "", line.quantity), node("td", "amount", money(line.unit_price_cents)), node("td", "amount", money(line.total_cents))); body.append(row); });
  table.append(body); return table;
}
async function openRecord(resource, id) {
  const dialog = $("#record-dialog"), content = $("#record-detail");
  $("#detail-title").textContent = id;
  $("#detail-type").textContent = resource === "stock" ? "INVENTORY RECORD" : `${resource.slice(0, -1).toUpperCase()} RECORD`;
  content.replaceChildren(node("p", "detail-note", "Loading record…"));
  if (!dialog.open) dialog.showModal();
  try {
    const item = await api(`/api/erp/${resource}/${encodeURIComponent(id)}`);
    if ($("#detail-title").textContent !== id) return;
    const grid = node("dl", "detail-grid"); content.replaceChildren(grid);
    if (resource === "customers") {
      detailField(grid, "Company", item.name); detailField(grid, "Contact", item.contact); detailField(grid, "Email", item.email);
      const button = node("button", "primary-button", "Ask about this customer");
      button.addEventListener("click", () => { dialog.close(); ask(`Show recent orders and unpaid invoices for ${item.name} (${item.id}).`); }); content.append(button);
    } else if (resource === "stock") {
      [["Product", item.name], ["Unit price (net)", money(item.price_cents)], ["On hand", item.on_hand], ["Reserved", item.reserved], ["Available", item.available], ["Reorder threshold", item.reorder_threshold]].forEach(([k, v]) => detailField(grid, k, v));
      content.append(badge(item.low_stock ? "low" : "fulfilled", item.low_stock ? "Low available stock" : "In stock"));
    } else {
      detailField(grid, "Customer", recordLink("customers", item.customer_id, item.customer_name));
      detailField(grid, "Status", badge(item.status));
      detailField(grid, resource === "orders" ? "Order date" : "Issue date", item.date || item.issue_date);
      if (item.due_date) detailField(grid, "Due date", item.due_date);
      if (item.order_id) detailField(grid, "Order", recordLink("orders", item.order_id));
      if (item.invoice_id) detailField(grid, "Invoice", recordLink("invoices", item.invoice_id));
      content.append(lineTable(item.lines));
      const total = node("div", "detail-total"); total.append(node("span", "", "Net total"), node("span", "", money(item.total_cents))); content.append(total);
      if (resource === "orders" && item.invoice_eligible) {
        const button = node("button", "primary-button", "Prepare draft invoice");
        button.addEventListener("click", async () => {
          button.disabled = true; button.textContent = "Preparing preview…";
          try { const action = await api("/api/actions/prepare", {method: "POST", body: JSON.stringify({order_id: id})}); state.actions.set(action.id, action); renderActions(); dialog.close(); navigate("assistant"); $("#chat-scroll").scrollTop = $("#chat-scroll").scrollHeight; toast("Preview ready. Review it before confirming."); }
          catch (error) { toast(error.message); button.disabled = false; button.textContent = "Prepare draft invoice"; }
        });
        content.append(button, node("p", "detail-note", "Creates a preview only. You'll review the exact invoice and confirm it in the assistant panel."));
      } else if (resource === "orders") content.append(node("p", "detail-note", "This order is cancelled or already has an invoice."));
      else content.append(node("p", "detail-note", "This is a fictional invoice. Issuing, emailing, and payment processing are outside this demo."));
    }
  } catch (error) { content.replaceChildren(node("p", "notice error", error.message)); }
}
function addMessage(text, role = "assistant", response = null) {
  $("#welcome")?.remove();
  const container = node("article", `message ${role}`);
  container.append(node("span", "message-avatar", role === "user" ? "JD" : "✧"));
  const main = node("div", "message-main");
  main.append(node("div", "message-label", role === "user" ? "You" : "ERP Agent"), role === "assistant" ? renderAnswer(text) : node("div", "message-text", text));
  container.append(main);
  if (response?.references?.length) {
    const links = node("div", "source-links");
    links.hidden = true;
    response.references.slice(0, 16).forEach(ref => { const button = recordLink(ref.resource, ref.id); button.className = "source-link"; links.append(button); }); main.append(links);
  }
  $("#messages").append(container);
  $("#chat-scroll").scrollTop = $("#chat-scroll").scrollHeight;
  return container;
}
async function ask(text) {
  navigate("assistant");
  if (state.busy || !text.trim()) return;
  state.busy = true; $("#send-button").disabled = true; $("#question").value = "";
  addMessage(text, "user");
  const thinking = addMessage("Checking your ERP records…"); thinking.classList.add("thinking");
  try {
    const response = await api("/api/chat", {method: "POST", body: JSON.stringify({message: text})});
    thinking.remove();
    if (response.history_reset) addMessage("This conversation grew long, so earlier context was cleared. Your ERP records and pending actions are preserved.");
    const message = addMessage(response.answer, "assistant", response);
    if (response.error) message.classList.add("error");
    (response.pending_actions || []).forEach(action => state.actions.set(action.id, action));
    await refreshActions();
  } catch (error) { thinking.remove(); addMessage(error.message, "error"); }
  finally { state.busy = false; $("#send-button").disabled = false; if (state.view === "assistant") $("#question").focus(); $("#chat-scroll").scrollTop = $("#chat-scroll").scrollHeight; }
}
function renderActions() {
  if ([...state.actions.values()].some(action => action.state === "pending")) $("#welcome")?.remove();
  const scroll = $("#chat-scroll");
  const previousScroll = scroll.scrollTop;
  const wasAtBottom = scroll.scrollHeight - scroll.clientHeight - previousScroll < 20;
  const tray = $("#action-tray"); tray.replaceChildren();
  const actions = [...state.actions.values()]
    .filter(action => action.state !== "cancelled")
    .sort((a, b) => b.expires_at.localeCompare(a.expires_at)).slice(0, 5);
  for (const action of actions) {
    if (action.state === "pending" && Date.parse(action.expires_at) <= Date.now()) action.state = "expired";
    const card = node("article", "action-card"), top = node("div", "action-top");
    top.append(node("h3", "", "Draft invoice preview"), badge(action.state)); card.append(top);
    const p = action.preview;
    card.append(node("p", "", `${p.customer.name} · ${p.order_id}\nDue ${p.due_date} · EUR, net of tax`), node("div", "action-total", money(p.total_cents)));
    if (action.state === "pending") {
      card.append(lineTable(p.lines));
      const expires = new Intl.DateTimeFormat("en-GB", {hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris"}).format(new Date(action.expires_at));
      card.append(node("p", "", `No invoice exists yet. Preview expires at ${expires} (Paris).`));
      const buttons = node("div", "action-buttons"), confirm = node("button", "primary-button", "Confirm draft invoice"), cancel = node("button", "secondary-button", "Cancel");
      const status = node("p", "action-status"); status.setAttribute("role", "status");
      async function resolve(operation) {
        confirm.disabled = true; cancel.disabled = true; status.textContent = "Saving your decision…";
        try {
          const data = await api(`/api/actions/${action.id}/${operation}`, {method: "POST"});
          state.actions.set(action.id, data.action); renderActions();
          toast(operation === "confirm" ? `Draft ${data.invoice.id} created. Inventory unchanged.` : "Invoice preview cancelled.");
          await Promise.all([refreshDashboard(), loadTable()]);
        } catch (error) {
          status.textContent = error.message; status.classList.add("error");
          // A failed response may follow a committed transaction. Refresh before offering another decision.
          try { await refreshActions(); } catch (_) { confirm.disabled = false; cancel.disabled = false; }
          toast(error.message);
        }
      }
      confirm.addEventListener("click", () => resolve("confirm")); cancel.addEventListener("click", () => resolve("cancel"));
      buttons.append(confirm, cancel); card.append(buttons, status);
    } else if (action.invoice_id) {
      const status = node("p", "action-status", "Created after your confirmation: "); status.append(recordLink("invoices", action.invoice_id)); card.append(status);
    } else card.append(node("p", "action-status", `Preview ${action.state}. No invoice created by this action.`));
    tray.append(card);
  }
  scroll.scrollTop = wasAtBottom ? scroll.scrollHeight : previousScroll;
}
async function refreshActions() {
  const data = await api("/api/session");
  $("#profile-name").textContent = data.username;
  $("#profile-initials").textContent = data.username.slice(0, 2).toUpperCase();
  $("#configuration-note").hidden = data.llm_configured;
  state.actions.clear(); data.actions.forEach(action => state.actions.set(action.id, action));
  if (data.actions.some(action => action.state === "pending")) $("#welcome")?.remove();
  renderActions();
}
document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => navigate(button.dataset.tab, {focus: false})));
document.querySelectorAll("[data-nav]").forEach(button => button.addEventListener("click", () => navigate(button.dataset.nav)));
document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => navigate(button.dataset.go)));
window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1);
  if (view !== "main" && view !== state.view) navigate(view);
});
document.querySelectorAll("[data-prompt]").forEach(button => button.addEventListener("click", () => ask(button.dataset.prompt)));
$("#acme-prompt").addEventListener("click", () => ask("Show Acme Office's (CUS-001) recent orders and unpaid invoices."));
$("#sign-out").addEventListener("click", async () => {
  $("#sign-out").disabled = true;
  try { await api("/api/auth/logout", {method: "POST"}); location.replace("/login"); }
  catch (error) { toast(error.message); $("#sign-out").disabled = false; }
});
window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
$("#chat-form").addEventListener("submit", event => { event.preventDefault(); ask($("#question").value); });
$("#question").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("#chat-form").requestSubmit(); } });
$("#filter-form").addEventListener("submit", event => { event.preventDefault(); state.offset = 0; loadTable(); });
$("#status-filter").addEventListener("change", () => { state.offset = 0; loadTable(); });
$("#previous-page").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadTable(); });
$("#next-page").addEventListener("click", () => { state.offset += state.limit; loadTable(); });
$("#close-dialog").addEventListener("click", () => $("#record-dialog").close());
$("#record-dialog").addEventListener("click", event => { if (event.target === $("#record-dialog")) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
$("#today-label").textContent = new Intl.DateTimeFormat("en-GB", {day: "numeric", month: "short", year: "numeric", timeZone: "Europe/Paris"}).format(new Date());
async function initialize() {
  errorAt("#global-error", null);
  try {
    const health = await api("/health");
    $("#connection-label").textContent = `ERP connected · ${health.mcp_tools} tools`;
    $("#connection-indicator").className = "connection-dot";
    await Promise.all([refreshDashboard(), refreshActions()]);
  } catch (error) {
    $("#connection-label").textContent = "ERP unavailable"; $("#connection-indicator").className = "connection-dot offline";
    errorAt("#global-error", new Error(`${error.message} Start both services, then refresh this page.`));
  }
}
navigate(location.hash.slice(1), {focus: false});
initialize();
setInterval(() => { if (!document.hidden && state.actions.size) renderActions(); }, 30000);

