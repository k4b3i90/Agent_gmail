const state = {
  dashboard: null,
  draftedMessages: new Set(),
};

const elements = {
  connectionStatus: document.querySelector("#connectionStatus"),
  accountName: document.querySelector("#accountName"),
  lastSync: document.querySelector("#lastSync"),
  messagesToday: document.querySelector("#messagesToday"),
  needsReply: document.querySelector("#needsReply"),
  attentionCount: document.querySelector("#attentionCount"),
  downloadedCount: document.querySelector("#downloadedCount"),
  dailyReport: document.querySelector("#dailyReport"),
  weeklyReport: document.querySelector("#weeklyReport"),
  messages: document.querySelector("#messages"),
  rules: document.querySelector("#rules"),
  importantSenders: document.querySelector("#importantSenders"),
  downloads: document.querySelector("#downloads"),
  activity: document.querySelector("#activity"),
  draftBox: document.querySelector("#draftBox"),
  toast: document.querySelector("#toast"),
  syncButton: document.querySelector("#syncButton"),
  connectButton: document.querySelector("#connectButton"),
  ruleForm: document.querySelector("#ruleForm"),
  importantSenderForm: document.querySelector("#importantSenderForm"),
};

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.clearTimeout(showToast.timeoutId);
  showToast.timeoutId = window.setTimeout(() => {
    elements.toast.classList.remove("visible");
  }, 2800);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Nie udalo sie wykonac akcji.");
  return payload;
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function renderList(container, items, renderItem) {
  container.innerHTML = "";
  items.forEach((item) => container.append(renderItem(item)));
}

function renderDashboard(payload) {
  state.dashboard = payload;
  elements.connectionStatus.textContent = payload.connection.status === "demo" ? "Tryb demo" : "Polaczono";
  elements.accountName.textContent = payload.connection.account;
  elements.lastSync.textContent = payload.connection.lastSync || "jeszcze nie uruchomiono";
  elements.messagesToday.textContent = payload.stats.messagesToday;
  elements.needsReply.textContent = payload.stats.needsReply;
  elements.attentionCount.textContent = payload.stats.attention;
  elements.downloadedCount.textContent = payload.stats.downloaded;

  const sortedMessages = [...payload.messages].sort((first, second) => {
    if (first.attention !== second.attention) return first.attention ? -1 : 1;
    if (first.needsReply !== second.needsReply) return first.needsReply ? -1 : 1;
    return second.receivedAt.localeCompare(first.receivedAt);
  });

  renderList(elements.dailyReport, payload.report.daily, (item) => createElement("li", "", item));
  renderList(elements.weeklyReport, payload.report.weekly, (item) => createElement("li", "", item));
  renderList(elements.messages, sortedMessages, renderMessage);
  renderList(elements.rules, payload.rules, renderRule);
  renderList(elements.importantSenders, payload.importantSenders, renderImportantSender);
  renderList(elements.downloads, payload.downloads, renderDownload);
  renderList(elements.activity, payload.activity.slice(0, 8), (item) => createElement("li", "", item));
}

function renderMessage(message) {
  const card = createElement("article", "message");
  const head = createElement("div", "message-head");
  const titleWrap = createElement("div");
  titleWrap.append(createElement("h3", "", message.subject));
  titleWrap.append(createElement("span", "message-meta", `${message.from} - ${message.receivedAt}`));
  head.append(titleWrap);
  head.append(createElement("span", `tag ${message.priority === "wysoki" ? "priority" : ""}`, message.priority));

  const summary = createElement("p", "", message.summary);
  const checklist = renderMessageChecklist(message);

  const actions = createElement("div", "message-actions");
  const draftButton = createElement("button", "secondary", "Napisz szkic");
  draftButton.type = "button";
  draftButton.addEventListener("click", () => loadDraft(message.id));
  actions.append(draftButton);

  card.append(head, summary, checklist, actions);
  return card;
}

function renderMessageChecklist(message) {
  const checklist = createElement("div", "action-checklist");
  const downloaded = message.downloadedAttachments.length > 0;
  const hasLabel = Boolean(message.gmailLabel);
  const draftReady = state.draftedMessages.has(message.id);

  checklist.append(
    renderActionItem({
      icon: "!",
      title: "Wazny nadawca",
      detail: message.attentionReason || "Brak priorytetu",
      checked: message.attention,
      tone: message.attention ? "attention" : "",
    })
  );

  checklist.append(
    renderActionItem({
      icon: "DL",
      title: downloaded ? "Zapisano plik na dysku" : "Pobieranie dokumentow",
      detail: downloaded ? message.downloadedAttachments.map((item) => item.path).join(", ") : message.downloadStatus,
      checked: downloaded,
      tone: downloaded ? "success" : "",
    })
  );

  checklist.append(
    renderActionItem({
      icon: "TAG",
      title: "Etykieta Gmail",
      detail: message.gmailLabel || "Brak etykiety",
      checked: hasLabel,
      tone: hasLabel ? "success" : "",
    })
  );

  checklist.append(
    renderActionItem({
      icon: "AI",
      title: message.needsReply ? "Odpowiedz / szkic AI" : "Odpowiedz niewymagana",
      detail: draftReady ? "Szkic odpowiedzi przygotowany" : message.needsReply ? "Czeka na przygotowanie szkicu" : "Agent nie widzi potrzeby odpowiedzi",
      checked: draftReady || !message.needsReply,
      tone: draftReady ? "success" : "",
    })
  );

  return checklist;
}

function renderActionItem({ icon, title, detail, checked, tone }) {
  const item = createElement("div", `action-item ${tone || ""}`);
  const iconBox = createElement("span", "action-icon", icon);
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = checked;
  checkbox.disabled = true;
  checkbox.setAttribute("aria-label", title);

  const copy = createElement("div", "action-copy");
  copy.append(createElement("strong", "", title));
  copy.append(createElement("span", "", detail));
  item.append(iconBox, checkbox, copy);
  return item;
}

function renderRule(rule) {
  const card = createElement("article", "rule");
  const head = createElement("div", "rule-head");
  const titleWrap = createElement("div");
  titleWrap.append(createElement("h3", "", rule.name));
  titleWrap.append(createElement("span", "rule-meta", rule.sender));
  head.append(titleWrap);
  head.append(createElement("span", "pill", rule.label));
  card.append(head);
  card.append(createElement("p", "rule-meta", `Folder: ${rule.folder}`));
  card.append(createElement("p", "rule-meta", `Slowa: ${rule.keywords.join(", ") || "brak"}`));
  return card;
}

function renderImportantSender(sender) {
  const card = createElement("article", "important-sender");
  const head = createElement("div", "rule-head");
  const titleWrap = createElement("div");
  titleWrap.append(createElement("h3", "", sender.name));
  titleWrap.append(createElement("span", "rule-meta", sender.email));
  head.append(titleWrap);
  head.append(createElement("span", "pill attention-pill", "priorytet"));
  card.append(head);
  card.append(createElement("p", "rule-meta", `Powod: ${sender.reason}`));
  card.append(createElement("p", "rule-meta", `Etykieta: ${sender.label}`));
  return card;
}

function renderDownload(download) {
  const card = createElement("article", "download");
  const head = createElement("div", "rule-head");
  const titleWrap = createElement("div");
  titleWrap.append(createElement("h3", "", download.file));
  titleWrap.append(createElement("span", "rule-meta", `${download.sender} - ${download.downloadedAt}`));
  head.append(titleWrap);
  head.append(createElement("span", "pill", download.status));
  card.append(head);
  card.append(createElement("p", "rule-meta", `Folder: ${download.path}`));
  card.append(createElement("p", "rule-meta", `Regula: ${download.rule}`));
  return card;
}

async function loadDashboard() {
  renderDashboard(await api("/api/dashboard"));
}

async function syncDemo() {
  elements.syncButton.disabled = true;
  elements.syncButton.textContent = "Synchronizuje...";
  try {
    renderDashboard(await api("/api/sync", { method: "POST", body: "{}" }));
    showToast("Synchronizacja zakonczona. Dokumenty zapisano w folderach z regul.");
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.syncButton.disabled = false;
    elements.syncButton.textContent = "Uruchom synchronizacje demo";
  }
}

async function loadDraft(messageId) {
  elements.draftBox.textContent = "Pisze szkic...";
  try {
    const payload = await api("/api/draft", { method: "POST", body: JSON.stringify({ messageId }) });
    elements.draftBox.textContent = payload.draft;
    state.draftedMessages.add(messageId);
    if (state.dashboard) renderDashboard(state.dashboard);
  } catch (error) {
    elements.draftBox.textContent = error.message;
  }
}

async function addRule(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(elements.ruleForm).entries());
  try {
    renderDashboard(await api("/api/rules", { method: "POST", body: JSON.stringify(payload) }));
    elements.ruleForm.reset();
    showToast("Regula dodana.");
  } catch (error) {
    showToast(error.message);
  }
}

async function addImportantSender(event) {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(elements.importantSenderForm).entries());
  try {
    renderDashboard(await api("/api/important-senders", { method: "POST", body: JSON.stringify(payload) }));
    elements.importantSenderForm.reset();
    showToast("Wazny nadawca dodany. Pasujace wiadomosci beda oznaczone do uwagi.");
  } catch (error) {
    showToast(error.message);
  }
}

elements.syncButton.addEventListener("click", syncDemo);
elements.connectButton.addEventListener("click", () => showToast("Nastepny krok: konfiguracja Google OAuth i zgody Gmail API."));
elements.ruleForm.addEventListener("submit", addRule);
elements.importantSenderForm.addEventListener("submit", addImportantSender);

loadDashboard().catch((error) => showToast(error.message));
