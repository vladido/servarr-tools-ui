"use strict";

const languageKey = "servarr-tools-language";
const translations = {
  en: {
    subtitle: "Unified operations dashboard", language: "Language", mode: "Mode",
    running: "Running", queued: "Queued", last_run: "Last run",
    last_success: "Last successful cycle", useful_actions: "Recent useful actions",
    last_error: "Last error", summary: "Summary", run_now: "Run now",
    recent_audit: "Recent audit", time: "Time", service: "Service", status: "Status",
    reason: "Reason", subject: "Subject", healthy: "Healthy", degraded: "Degraded",
    unavailable: "Unavailable", live: "live", dry_run: "dry-run", unknown: "unknown",
    true: "true", false: "false", manual: "manual", scheduled: "scheduled",
    run_queued: "Run queued", run_failed: "Run failed", request_unavailable: "request unavailable",
  },
  ru: {
    subtitle: "Единая панель управления", language: "Язык", mode: "Режим",
    running: "Выполняется", queued: "В очереди", last_run: "Последний цикл",
    last_success: "Последний успешный цикл", useful_actions: "Последние полезные действия",
    last_error: "Последняя ошибка", summary: "Итоги", run_now: "Запустить сейчас",
    recent_audit: "Последние события", time: "Время", service: "Сервис", status: "Статус",
    reason: "Причина", subject: "Объект", healthy: "Исправен", degraded: "Сбои",
    unavailable: "Недоступен", live: "рабочий", dry_run: "пробный", unknown: "неизвестно",
    true: "да", false: "нет", manual: "вручную", scheduled: "по расписанию",
    run_queued: "Запуск поставлен в очередь", run_failed: "Ошибка запуска", request_unavailable: "запрос недоступен",
  },
};
let currentLanguage = "en";
let dateTimeFormatter;
let durationFormatter;

function t(key) {
  return translations[currentLanguage][key] ?? translations.en[key] ?? key;
}

function applyLanguage(language, persist = true) {
  currentLanguage = language === "ru" ? "ru" : "en";
  document.documentElement.lang = currentLanguage;
  dateTimeFormatter = new Intl.DateTimeFormat(currentLanguage === "ru" ? "ru-RU" : "en-US", {
    dateStyle: "medium", timeStyle: "medium",
  });
  durationFormatter = new Intl.NumberFormat(currentLanguage === "ru" ? "ru-RU" : "en-US", {
    maximumFractionDigits: 1,
  });
  for (const element of document.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  const selector = document.querySelector("#language-select");
  if (selector) selector.value = currentLanguage;
  if (persist) {
    try { localStorage.setItem(languageKey, currentLanguage); } catch (_error) {}
  }
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateTimeFormatter.format(date);
}

function localizeExisting(root) {
  for (const element of root.querySelectorAll("[data-timestamp]")) {
    element.textContent = formatTimestamp(element.dataset.timestamp);
  }
  for (const element of root.querySelectorAll("[data-duration-seconds]")) {
    const value = Number(element.dataset.durationSeconds);
    element.textContent = Number.isFinite(value) ? `${durationFormatter.format(value)} ${currentLanguage === "ru" ? "с" : "s"}` : "—";
  }
}

function setField(card, field, value) {
  const element = card.querySelector(`[data-field="${field}"]`);
  if (element) element.textContent = value ?? "—";
}

function appendPart(element, value) {
  if (!value) return;
  if (element.childNodes.length) element.append(" · ");
  element.append(value);
}

function timestampNode(value) {
  if (!value) return null;
  const element = document.createElement("time");
  element.dateTime = value;
  element.dataset.timestamp = value;
  element.textContent = formatTimestamp(value);
  return element;
}

function renderTimestampField(card, field, value) {
  const element = card.querySelector(`[data-field="${field}"]`);
  if (!element) return;
  element.replaceChildren();
  element.append(timestampNode(value) || "—");
}

function renderLastRun(card, run) {
  const element = card.querySelector('[data-field="last_run"]');
  if (!element) return;
  element.replaceChildren();
  if (!run || typeof run !== "object") {
    element.append("—");
    return;
  }
  appendPart(element, timestampNode(run.finished_at || run.started_at));
  appendPart(element, run.trigger ? t(run.trigger) : run.detail);
  if (Number.isFinite(run.duration_seconds)) {
    const duration = document.createElement("span");
    duration.dataset.durationSeconds = String(run.duration_seconds);
    duration.textContent = `${durationFormatter.format(run.duration_seconds)} ${currentLanguage === "ru" ? "с" : "s"}`;
    appendPart(element, duration);
  }
  if (!element.childNodes.length) element.append("—");
}

function posterNode(action) {
  if (!Number.isSafeInteger(action.series_id) || action.series_id <= 0) return null;
  const frame = document.createElement("span");
  frame.className = "action-poster";
  const image = document.createElement("img");
  image.src = `/api/posters/${action.series_id}`;
  image.width = 40;
  image.height = 60;
  image.loading = "lazy";
  image.alt = "";
  image.addEventListener("error", () => image.hidden = true);
  frame.appendChild(image);
  return frame;
}

function renderActions(card, actions) {
  const element = card.querySelector('[data-field="useful_actions"]');
  if (!element) return;
  element.replaceChildren();
  if (!Array.isArray(actions) || actions.length === 0) {
    element.append("—");
    return;
  }
  const list = document.createElement("ul");
  list.className = "useful-actions";
  for (const action of actions.slice(0, 5)) {
    const item = document.createElement("li");
    const poster = posterNode(action);
    if (poster) item.appendChild(poster);
    const detail = document.createElement("span");
    detail.className = "action-detail";
    appendPart(detail, timestampNode(action.timestamp));
    appendPart(detail, action.status ? t(action.status) : null);
    for (const field of ["reason", "subject"]) appendPart(detail, action[field]);
    item.appendChild(detail);
    list.appendChild(item);
  }
  element.appendChild(list);
}

function setHealth(card, value) {
  const element = card.querySelector('[data-field="health"]');
  if (!element) return;
  const state = ["healthy", "degraded", "unavailable"].includes(value) ? value : "unavailable";
  element.dataset.health = state;
  element.textContent = t(state);
  element.classList.remove("health-healthy", "health-degraded", "health-unavailable");
  element.classList.add(`health-${state}`);
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status", {headers: {Accept: "application/json"}});
    const payload = await response.json();
    for (const [slug, status] of Object.entries(payload.services || {})) {
      const card = document.querySelector(`[data-service="${CSS.escape(slug)}"]`);
      if (!card) continue;
      setHealth(card, status.health);
      setField(card, "mode", status.dry_run === true ? t("dry_run") : status.dry_run === false ? t("live") : t("unknown"));
      setField(card, "running", typeof status.running === "boolean" ? t(String(status.running)) : "—");
      setField(card, "queued", typeof status.queued === "boolean" ? t(String(status.queued)) : "—");
      renderLastRun(card, status.last_run);
      renderTimestampField(card, "last_success_at", status.last_success_at);
      setField(card, "last_error", status.last_error || status.error);
      setField(card, "summary", Object.entries(status.summary || {}).map(([key, value]) => `${key}: ${value}`).join(", ") || "—");
      const button = card.querySelector("[data-run-now]");
      if (button) button.disabled = status.capabilities?.run_now !== true;
    }
  } catch (_error) {
    // Keep the server-rendered state when the BFF itself is unreachable.
  }
}

async function refreshAudit() {
  try {
    const response = await fetch("/api/audit?limit=25", {headers: {Accept: "application/json"}});
    const payload = await response.json();
    const body = document.querySelector("#audit-body");
    const errors = document.querySelector("#audit-errors");
    if (!body) return;
    body.replaceChildren();
    for (const item of payload.items || []) {
      const row = document.createElement("tr");
      const timeCell = document.createElement("td");
      timeCell.append(timestampNode(item.timestamp) || "—");
      row.appendChild(timeCell);
      for (const field of ["service", "status", "reason", "subject"]) {
        const cell = document.createElement("td");
        cell.textContent = field === "status" && item[field] ? t(item[field]) : (item[field] || "—");
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
    for (const [slug, actions] of Object.entries(payload.useful_actions || {})) {
      const card = document.querySelector(`[data-service="${CSS.escape(slug)}"]`);
      if (card) renderActions(card, actions);
    }
    if (errors) {
      errors.replaceChildren();
      for (const [slug, message] of Object.entries(payload.errors || {})) {
        const item = document.createElement("li");
        item.setAttribute("data-audit-error", slug);
        item.textContent = `${slug}: ${message}`;
        errors.appendChild(item);
      }
    }
  } catch (_error) {
    // Audit outages do not disturb service cards.
  }
}

for (const button of document.querySelectorAll("[data-run-now]")) {
  button.addEventListener("click", async () => {
    const feedback = button.closest(".card")?.querySelector("[data-run-feedback]");
    button.disabled = true;
    try {
      const response = await fetch(`/api/services/${encodeURIComponent(button.dataset.runNow)}/runs`, {
        method: "POST",
        headers: {"X-Servarr-UI": "1"},
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        // The HTTP status still provides useful feedback for a malformed body.
      }
      if (response.ok) {
        if (feedback) feedback.textContent = payload.status ? `${t("run_queued")}: ${t(payload.status)}` : t("run_queued");
        await refreshAudit();
      } else if (feedback) {
        feedback.textContent = payload.error || `${t("run_failed")} (HTTP ${response.status})`;
      }
    } catch (_error) {
      if (feedback) feedback.textContent = `${t("run_failed")}: ${t("request_unavailable")}`;
    } finally {
      await refreshStatus();
    }
  });
}

let initialLanguage;
try { initialLanguage = localStorage.getItem(languageKey); } catch (_error) {}
if (!translations[initialLanguage]) initialLanguage = "en";
applyLanguage(initialLanguage, false);
document.querySelector("#language-select")?.addEventListener("change", async (event) => {
  applyLanguage(event.target.value);
  localizeExisting(document);
  await Promise.all([refreshStatus(), refreshAudit()]);
});
localizeExisting(document);
for (const image of document.querySelectorAll(".action-poster img")) {
  image.addEventListener("error", () => image.hidden = true);
}
refreshStatus();
refreshAudit();
setInterval(refreshStatus, 15000);
setInterval(refreshAudit, 30000);
