const state = {
  topics: [],
  cities: [],
  signalTypes: ["event", "news", "signal", "discussion"],
  profile: null,
  feed: [],
  saved: [],
  stats: null,
  insights: null,
  sourceStatuses: [],
  activeTopics: new Set(),
  activeSignalTypes: new Set(["event", "news", "signal", "discussion"]),
  introTopics: new Set(["tech", "startups", "news", "education"]),
  introSignalTypes: new Set(["event", "news", "signal", "discussion"]),
  activeView: "feed",
  search: "",
  kindFilter: "all",
  sortMode: "relevance",
  autoTimer: null,
  countdownTimer: null,
  darkMode: false,
  focusedIndex: -1,
  keyboardHintsVisible: false,
};

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function highlightText(text, query) {
  if (!query) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const queryEscaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return escaped.replace(new RegExp(`(${queryEscaped})`, "gi"), '<mark class="search-hl">$1</mark>');
}

function pct(value) {
  return `${Math.round(Number(value) * 100)}`;
}

const KIND_LABELS = { event: "Event", news: "News", discussion: "Discussion", signal: "Signal" };
function kindLabel(kind) {
  return KIND_LABELS[kind] || "Signal";
}

// Topic chip is noise when it just repeats the kind (e.g. News + "news") or is generic.
function topicChip(topic, kind) {
  if (!topic || topic === kind || topic === "news" || topic === "other") return "";
  return `<span class="chip">${escapeHtml(topic)}</span>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

// ─── Toast Notifications ──────────────────────────────────────
function showToast(message, type = "") {
  const container = $("#toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// ─── Dark Mode ────────────────────────────────────────────────
function initDarkMode() {
  state.darkMode = localStorage.getItem("contextcast:dark") === "1";
  applyDarkMode();
}

function toggleDarkMode() {
  state.darkMode = !state.darkMode;
  localStorage.setItem("contextcast:dark", state.darkMode ? "1" : "0");
  applyDarkMode();
}

const SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

function applyDarkMode() {
  document.body.classList.toggle("dark", state.darkMode);
  $("#darkToggle").innerHTML = state.darkMode ? SUN_ICON : MOON_ICON;
}

// ─── Card Modal ───────────────────────────────────────────────
function extractDomain(url) {
  if (!url) return "";
  try {
    const d = new URL(url).hostname;
    return d.startsWith("www.") ? d.slice(4) : d;
  } catch { return ""; }
}

// ─── Card Modal ───────────────────────────────────────────────
function openCardModal(item) {
  const event = item.event;
  const date = dateLabel(event);
  const modal = $("#cardModal");
  const body = $("#cardModalBody");
  const domain = event.source_domain || extractDomain(event.url);
  const imgHtml = event.image_url
    ? `<div class="modal-image"><img src="${escapeHtml(event.image_url)}" alt="" loading="lazy" onerror="this.parentElement.style.display='none'"></div>`
    : "";

  body.innerHTML = `
    ${imgHtml}
    <div class="modal-title">${highlightText(event.title, state.search.trim().toLowerCase())}</div>
    <div class="modal-meta">
      <span class="kind ${escapeHtml(event.kind || "event")}">${escapeHtml(kindLabel(event.kind || "event"))}</span>
      ${topicChip(event.topic, event.kind || "event")}
      <span class="kind">${escapeHtml(date)}</span>
      <span class="kind">${escapeHtml(event.city)}</span>
      ${domain ? `<span class="source-domain">${escapeHtml(domain)}</span>` : ""}
      ${event.venue && event.venue !== event.source ? `<span class="kind">${escapeHtml(event.venue)}</span>` : ""}
    </div>
    ${event.url ? `
      <a class="og-link" href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">
        <div class="og-link-content">
          <span class="og-link-domain">${escapeHtml(domain || "source")}</span>
          <span class="og-link-title">${escapeHtml(event.title)}</span>
          <span class="og-link-arrow">Read original article ↗</span>
        </div>
      </a>
    ` : ""}
    <div class="modal-description">${escapeHtml(event.description)}</div>
    ${event.summary ? `<div class="why" style="margin-bottom:16px">${escapeHtml(event.summary)}</div>` : ""}
    <div class="modal-scores">
      ${bar("taste", item.semantic_score)}
      ${bar("content", item.content_score || 0)}
      ${bar("near", item.proximity_score)}
      ${bar("fresh", item.recency_score)}
      ${bar("graph", item.graph_score)}
      ${bar("novelty", item.novelty_score || 0)}
      ${bar("momentum", item.momentum_score || 0)}
      ${bar("diversity", item.diversity_score || 0)}
    </div>
    <div class="why">${escapeHtml(item.explanation)}</div>
    <div class="modal-actions" style="margin-top:16px">
      <button class="action save" data-action="save" data-id="${escapeHtml(event.id)}">Save</button>
      <button class="action" data-action="click" data-id="${escapeHtml(event.id)}">Track interest</button>
      <button class="action quiet" data-action="not_interested" data-id="${escapeHtml(event.id)}">Mute topic</button>
      ${event.url ? `<a class="action open-ext" href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">Open source ↗</a>` : ""}
    </div>
  `;

  modal.classList.remove("hidden");

  body.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/interact", {
        method: "POST",
        body: JSON.stringify({
          user_id: "demo",
          event_id: button.dataset.id,
          action: button.dataset.action,
        }),
      });
      showToast(
        button.dataset.action === "save" ? "Saved to your trail" :
        button.dataset.action === "not_interested" ? "Muted this topic" :
        "Interest tracked",
        button.dataset.action === "not_interested" ? "mute" : "success"
      );
      closeCardModal();
      await refreshQuiet();
    });
  });
}

function closeCardModal() {
  $("#cardModal").classList.add("hidden");
}

// ─── Daily Digest ─────────────────────────────────────────────
function renderDailyDigest() {
  const wrap = $("#dailyDigest");
  const topItems = state.feed.slice(0, 3);
  if (topItems.length < 2) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = `
    <article class="daily-digest-card">
      <p class="eyebrow">Daily digest</p>
      <h3>Top signals right now</h3>
      <div class="digest-items">
        ${topItems.map((item, i) => `
          <div class="digest-item" data-digest-index="${i}">
            <div class="digest-rank">${i + 1}</div>
            <div>
              <div class="digest-item-title">${escapeHtml(item.event.title)}</div>
              <div class="digest-item-meta">${escapeHtml(item.event.topic)} · ${escapeHtml(item.event.city)} · ${escapeHtml(dateLabel(item.event))}</div>
            </div>
            <div class="digest-score">${pct(item.score)}</div>
          </div>
        `).join("")}
      </div>
    </article>
  `;

  wrap.querySelectorAll(".digest-item").forEach((el) => {
    el.style.cursor = "pointer";
    el.addEventListener("click", () => {
      const idx = parseInt(el.dataset.digestIndex);
      if (state.feed[idx]) openCardModal(state.feed[idx]);
    });
  });
}

// ─── Render Functions ─────────────────────────────────────────
function renderCities() {
  $("#city").innerHTML = state.cities
    .map((city) => `<option value="${escapeHtml(city)}">${escapeHtml(city)}</option>`)
    .join("");
  $("#introCity").innerHTML = $("#city").innerHTML;
}

function renderTopicButtons(wrap, activeSet, onChange) {
  wrap.innerHTML = "";
  state.topics.forEach((topic) => {
    const button = document.createElement("button");
    button.className = `topic ${activeSet.has(topic) ? "active" : ""}`;
    button.textContent = topic;
    button.addEventListener("click", () => {
      if (activeSet.has(topic)) {
        activeSet.delete(topic);
      } else {
        activeSet.add(topic);
      }
      onChange();
    });
    wrap.appendChild(button);
  });
}

function renderTopics() {
  renderTopicButtons($("#topics"), state.activeTopics, renderTopics);
  renderTopicButtons($("#introTopics"), state.introTopics, renderTopics);
}

function renderSignalTypes() {
  renderSignalTypeButtons($("#signalTypes"), state.activeSignalTypes, renderSignalTypes);
  renderSignalTypeButtons($("#introSignalTypes"), state.introSignalTypes, renderSignalTypes);
}

function renderSignalTypeButtons(wrap, activeSet, onChange) {
  wrap.innerHTML = "";
  state.signalTypes.forEach((kind) => {
    const button = document.createElement("button");
    button.className = `topic ${activeSet.has(kind) ? "active" : ""}`;
    button.textContent = kind;
    button.addEventListener("click", () => {
      if (activeSet.has(kind)) {
        activeSet.delete(kind);
      } else {
        activeSet.add(kind);
      }
      onChange();
    });
    wrap.appendChild(button);
  });
}

function renderQuickStats() {
  const stats = state.stats || {};
  const topTopic = Object.entries(stats.topic_counts || {}).sort((a, b) => b[1] - a[1])[0];
  const topCity = Object.entries(stats.cities || {}).sort((a, b) => b[1] - a[1])[0];
  const nextRun = stats.ingest?.next_run ? timeUntil(stats.ingest.next_run) : "soon";
  $("#quickStats").innerHTML = [
    ["Indexed", stats.events_indexed || 0, "signals"],
    ["Sources", stats.source_count || Object.keys(stats.sources || {}).length, "active"],
    ["Top genre", topTopic ? topTopic[0] : "none", topTopic ? `${topTopic[1]} items` : ""],
    ["Auto refresh", nextRun, "every 5 min"],
  ]
    .map(
      ([label, value, note]) => `
        <article class="stat-tile">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <small>${escapeHtml(note)}</small>
        </article>
      `
    )
    .join("");
}

function renderSourceStrip() {
  const statuses = state.sourceStatuses;
  if (!statuses.length) {
    const result = state.stats?.ingest?.last_result;
    if (result?.statuses?.length) {
      state.sourceStatuses = result.statuses;
      renderSourceStrip();
      return;
    }
    $("#sourceStrip").innerHTML = `<span class="source-pill idle">Live sources ready, auto-refresh every 5 min</span>`;
    return;
  }
  $("#sourceStrip").innerHTML = statuses
    .slice(0, 18)
    .map((status) => {
      const className = status.ok ? "ok" : "bad";
      const label = status.ok ? `${status.count} pulled` : "blocked";
      const tip = status.ok
        ? (status.ms ? `${status.ms} ms` : "")
        : status.error || "";
      return `<span class="source-pill ${className}" title="${escapeHtml(tip)}">${escapeHtml(status.source)}: ${escapeHtml(label)}</span>`;
    })
    .join("");
}

function updateKindCounts() {
  const query = state.search.trim().toLowerCase();
  const matchesSearch = (item) => {
    if (!query) return true;
    const e = item.event;
    return `${e.title} ${e.summary} ${e.description} ${e.city} ${e.source} ${e.topic}`
      .toLowerCase()
      .includes(query);
  };
  const pool = state.feed.filter(matchesSearch);
  const counts = { all: pool.length, event: 0, news: 0, discussion: 0, signal: 0 };
  pool.forEach((item) => {
    const kind = item.event.kind || "event";
    if (kind in counts) counts[kind] += 1;
  });
  document.querySelectorAll("#kindSegment .count").forEach((el) => {
    el.textContent = counts[el.dataset.count] ?? 0;
  });
}

function renderFeed() {
  const wrap = $("#feed");
  updateKindCounts();
  const visible = filteredFeed();
  state.focusedIndex = -1;
  if (!visible.length) {
    const hasFilter = state.search.trim() || state.kindFilter !== "all";
    wrap.innerHTML = hasFilter
      ? `<div class="empty"><strong>No matching signals</strong><span>Try a different search or switch the signal type filter.</span></div>`
      : `<div class="empty"><strong>No signals yet</strong><span>Pull live sources or pick more genres to populate your feed.</span></div>`;
    return;
  }
  const query = state.search.trim().toLowerCase();
  wrap.innerHTML = "";
  visible.forEach((item, index) => {
    const event = item.event;
    const date = dateLabel(event);
    const domain = event.source_domain || extractDomain(event.url);
    const sourceUrl = event.url
      ? `<a class="open-link" href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">Open ↗</a>`
      : "";
    const venueDisplay = event.venue && event.venue !== event.source
      ? `<span>${highlightText(event.venue, query)}</span>`
      : "";
    const imgThumb = event.image_url
      ? `<div class="card-thumb"><img src="${escapeHtml(event.image_url)}" alt="" loading="lazy" onerror="this.parentElement.style.display='none'"></div>`
      : "";
    const kind = event.kind || "event";
    const card = document.createElement("article");
    card.className = `signal-card kind-${escapeHtml(kind)}`;
    card.dataset.feedIndex = index;
    card.innerHTML = `
      <div class="rank">${index + 1}</div>
      <div class="signal-body">
        <div class="signal-main">
          <div class="card-head">
            <div>
              <span class="kind ${escapeHtml(kind)}">${escapeHtml(kindLabel(kind))}</span>
              ${topicChip(event.topic, kind)}
              ${domain ? `<span class="source-domain small">${escapeHtml(domain)}</span>` : ""}
            </div>
            <strong class="score">${pct(item.score)}</strong>
          </div>
          <h3>${highlightText(event.title, query)}</h3>
          <p class="description">${highlightText(event.summary || event.description, query)}</p>
          <div class="meta-line">
            <span>${highlightText(event.city, query)}</span>
            ${venueDisplay}
            <span>${escapeHtml(date)}</span>
            ${sourceUrl}
          </div>
          <div class="why">${escapeHtml(item.explanation)}</div>
          <div class="signal-bars">
            ${bar("taste", item.semantic_score)}
            ${bar("content", item.content_score || 0)}
            ${bar("near", item.proximity_score)}
            ${bar("fresh", item.recency_score)}
          </div>
          <div class="actions">
            <button class="action save" data-action="save" data-id="${escapeHtml(event.id)}">Save</button>
            <button class="action" data-action="click" data-id="${escapeHtml(event.id)}">Track</button>
            <button class="action quiet" data-action="not_interested" data-id="${escapeHtml(event.id)}">Mute</button>
          </div>
        </div>
        ${imgThumb}
      </div>
    `;

    card.addEventListener("click", (e) => {
      if (e.target.closest("[data-action]") || e.target.closest("a")) return;
      openCardModal(item);
    });

    wrap.appendChild(card);
  });

  bindCardActions(wrap);
}

function bindCardActions(root) {
  root.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async (e) => {
      e.stopPropagation();
      const action = button.dataset.action;
      await api("/api/interact", {
        method: "POST",
        body: JSON.stringify({
          user_id: "demo",
          event_id: button.dataset.id,
          action: action,
        }),
      });
      showToast(
        action === "save" ? "Saved to your trail" :
        action === "not_interested" ? "Muted this topic" :
        "Interest tracked",
        action === "not_interested" ? "mute" : "success"
      );
      await refreshQuiet();
    });
  });
}

function filteredFeed() {
  const query = state.search.trim().toLowerCase();
  let items = state.feed.filter((item) => {
    const event = item.event;
    const kindMatch = state.kindFilter === "all" || event.kind === state.kindFilter;
    if (!kindMatch) return false;
    if (!query) return true;
    return `${event.title} ${event.summary} ${event.description} ${event.city} ${event.source} ${event.topic}`
      .toLowerCase()
      .includes(query);
  });

  if (state.sortMode === "newest") {
    items = [...items].sort((a, b) => {
      const ta = new Date(a.event.published_at || a.event.event_date).getTime();
      const tb = new Date(b.event.published_at || b.event.event_date).getTime();
      return tb - ta;
    });
  } else if (state.sortMode === "nearest") {
    items = [...items].sort((a, b) => b.proximity_score - a.proximity_score);
  }

  return items;
}

function bar(label, value) {
  const width = Math.max(3, Math.round(Number(value) * 100));
  return `
    <div class="bar">
      <span>${label}</span>
      <div><i style="width:${width}%"></i></div>
      <b>${width}</b>
    </div>
  `;
}

function dateLabel(event) {
  const published = event.published_at || event.fetched_at || event.event_date;
  if (event.kind === "news" || event.kind === "signal" || event.kind === "discussion") {
    return `Published ${relativeTime(published)}`;
  }
  const date = new Date(event.event_date);
  if (!Number.isFinite(date.getTime())) return relativeTime(published);
  return `Event ${date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

function relativeTime(iso) {
  if (!iso) return "recently";
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (!Number.isFinite(diff)) return "recently";
  const abs = Math.abs(diff);
  const suffix = diff >= 0 ? "ago" : "from now";
  const minutes = Math.round(abs / 60000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes}m ${suffix}`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours}h ${suffix}`;
  const days = Math.round(hours / 24);
  return `${days}d ${suffix}`;
}

function renderAdmin() {
  const stats = state.stats || {};
  const sourceRows = Object.entries(stats.sources || {})
    .map(([source, count]) => `<li><span>${escapeHtml(source)}</span><strong>${count}</strong></li>`)
    .join("");
  const topicRows = Object.entries(stats.topic_counts || {})
    .map(([topic, count]) => `<li><span>${escapeHtml(topic)}</span><strong>${count}</strong></li>`)
    .join("");
  const cityRows = Object.entries(stats.cities || {})
    .map(([city, count]) => `<li><span>${escapeHtml(city)}</span><strong>${count}</strong></li>`)
    .join("");
  const ingest = stats.ingest || {};
  const lastResult = ingest.last_result || {};
  $("#adminGrid").innerHTML = `
    <article class="ops-panel">
      <span>Runtime</span>
      <strong>${escapeHtml(stats.pipeline || "local")}</strong>
      <p>Cost: $${escapeHtml(stats.cost_usd || 0)}. Storage: SQLite. Ingest: public free feeds every 5 minutes.</p>
    </article>
    <article class="ops-panel">
      <span>Auto ingest</span>
      <strong>${ingest.running ? "running" : "ready"}</strong>
      <p>Last: ${escapeHtml(ingest.last_run ? new Date(ingest.last_run).toLocaleTimeString() : "pending")}</p>
      <p>Last pull: ${escapeHtml(lastResult.fetched || 0)} fetched, ${escapeHtml(lastResult.upserted || 0)} upserted.</p>
    </article>
    <article class="ops-panel list"><span>Sources</span><ul>${sourceRows || "<li><span>None yet</span></li>"}</ul></article>
    <article class="ops-panel list"><span>Genres</span><ul>${topicRows || "<li><span>None yet</span></li>"}</ul></article>
    <article class="ops-panel list"><span>Cities</span><ul>${cityRows || "<li><span>None yet</span></li>"}</ul></article>
  `;
}

function renderInsights() {
  const insights = state.insights || {};
  const briefing = insights.briefing || [];
  $("#briefing").innerHTML = `
    <article class="briefing-card">
      <span>Local AI briefing</span>
      <h3>${escapeHtml(insights.city || state.profile?.city || "Your city")} pulse</h3>
      <ul>${briefing.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
    </article>
  `;

  $("#opportunities").innerHTML = `
    <section class="opportunity-panel">
      <h3>Opportunity queue</h3>
      ${(insights.opportunities || [])
        .map(
          (item) => `
            <article class="opportunity">
              <strong>${escapeHtml(item.title)}</strong>
              <span>${escapeHtml(item.kind)} - ${escapeHtml(item.topic)}</span>
              <p>${escapeHtml(item.why)}</p>
            </article>
          `
        )
        .join("") || '<p style="color:var(--muted)">Pull live sources to discover opportunities.</p>'}
    </section>
  `;

  $("#trends").innerHTML = (insights.trends || [])
    .map(
      (trend) => `
        <div class="trend-row">
          <div>
            <strong>${escapeHtml(trend.topic)}</strong>
            <span>${escapeHtml(trend.label)} - ${escapeHtml(trend.recent)} recent / ${escapeHtml(trend.total)} total</span>
          </div>
          <b>${escapeHtml(trend.score)}</b>
        </div>
      `
    )
    .join("") || '<p style="color:var(--muted)">No trend data yet.</p>';

  $("#clusters").innerHTML = (insights.clusters || [])
    .map(
      (cluster) => `
        <article class="cluster-card">
          <div>
            <strong>${escapeHtml(cluster.name)}</strong>
            <span>${escapeHtml(cluster.size)} signals</span>
          </div>
          <p>${escapeHtml(cluster.lead)}</p>
          <small>${(cluster.keywords || []).map(escapeHtml).join(" - ")}</small>
        </article>
      `
    )
    .join("") || '<p style="color:var(--muted)">No clusters yet.</p>';

  renderModelCard();
}

function renderModelCard() {
  const card = state.insights?.model_card || {};
  $("#modelCard").innerHTML = `
    <article class="model-panel">
      <span>Model card</span>
      <h3>${escapeHtml(card.name || "ContextCast Local Hybrid Ranker")}</h3>
      <div class="model-grid">
        <div><strong>${escapeHtml(card.signals_indexed || 0)}</strong><small>signals</small></div>
        <div><strong>${escapeHtml(card.source_coverage || 0)}</strong><small>sources</small></div>
        <div><strong>$${escapeHtml(card.cost_usd || 0)}</strong><small>cost</small></div>
        <div><strong>${escapeHtml(card.median_freshness_hours || 0)}h</strong><small>median freshness</small></div>
      </div>
      <ul>${(card.cv_metrics || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
    </article>
  `;

  $("#diagnostics").innerHTML = `
    <section class="diagnostics-panel">
      <h3>Quality diagnostics</h3>
      ${(state.insights?.diagnostics || [])
        .map(
          (item) => `
            <div class="diagnostic ${escapeHtml(item.level)}">
              <strong>${escapeHtml(item.level)}</strong>
              <span>${escapeHtml(item.message)}</span>
            </div>
          `
        )
        .join("")}
    </section>
  `;
}

function renderSaved() {
  const wrap = $("#savedFeed");
  if (!state.saved.length) {
    wrap.innerHTML = `<div class="empty">Save a few high-signal items and they will appear here as your decision trail.</div>`;
    return;
  }
  wrap.innerHTML = "";
  state.saved.forEach((event, index) => {
    const kind = event.kind || "event";
    const card = document.createElement("article");
    card.className = `signal-card kind-${escapeHtml(kind)}`;
    card.innerHTML = `
      <div class="rank">${index + 1}</div>
      <div class="signal-main">
        <div class="card-head">
          <div>
            <span class="kind ${escapeHtml(kind)}">${escapeHtml(kindLabel(kind))}</span>
            ${topicChip(event.topic, kind)}
          </div>
        </div>
        <h3>${escapeHtml(event.title)}</h3>
        <p class="description">${escapeHtml(event.summary || event.description)}</p>
        <div class="meta-line">
          <span>${escapeHtml(event.city)}</span>
          <span>${escapeHtml(event.source)}</span>
          <span>${escapeHtml(dateLabel(event))}</span>
          ${event.url ? `<a class="open-link" href="${escapeHtml(event.url)}" target="_blank" rel="noreferrer">Open</a>` : ""}
        </div>
        <div class="actions">
          <button class="action remove" data-remove-id="${escapeHtml(event.id)}">Remove</button>
        </div>
      </div>
    `;
    wrap.appendChild(card);
  });

  wrap.querySelectorAll("[data-remove-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api("/api/interact/remove", {
        method: "POST",
        body: JSON.stringify({ user_id: "demo", event_id: button.dataset.removeId }),
      });
      showToast("Removed from saved", "mute");
      await refreshQuiet();
    });
  });
}

function renderGraph(payload) {
  const canvas = $("#graph");
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  canvas.width = rect.width * scale;
  canvas.height = rect.height * scale;
  ctx.scale(scale, scale);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const center = { x: rect.width / 2, y: rect.height / 2 };
  const nodes = payload.nodes.map((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(payload.nodes.length, 1);
    const radius = node.type === "user" ? 0 : node.type === "topic" ? 155 : 270;
    return {
      ...node,
      x: center.x + Math.cos(angle) * radius,
      y: center.y + Math.sin(angle) * radius,
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));

  const edgeColor = state.darkMode ? "rgba(129, 140, 248," : "rgba(79, 70, 229,";
  const textColor = state.darkMode ? "#b3b9c4" : "#3c424c";
  const topicColor = state.darkMode ? "#818cf8" : "#4f46e5";
  const eventColor = state.darkMode ? "#a78bfa" : "#7c3aed";

  payload.edges.forEach((edge) => {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) return;
    ctx.strokeStyle = `${edgeColor} ${Math.max(0.14, edge.weight * 0.5)})`;
    ctx.lineWidth = Math.max(1, edge.weight * 3);
    ctx.beginPath();
    ctx.moveTo(source.x, source.y);
    ctx.lineTo(target.x, target.y);
    ctx.stroke();
  });

  nodes.forEach((node) => {
    const radius = node.type === "user" ? 30 : node.type === "topic" ? 21 : 10;
    ctx.fillStyle = node.type === "user" ? (state.darkMode ? "#e7e9ee" : "#16181d") : node.type === "topic" ? topicColor : eventColor;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = textColor;
    ctx.textAlign = "center";
    ctx.font = node.type === "event" ? "12px system-ui" : "700 13px system-ui";
    const label = node.label.length > 24 ? `${node.label.slice(0, 22)}...` : node.label;
    ctx.fillText(label, node.x, node.y + radius + 17);
  });
}

// ─── Profile & Data ───────────────────────────────────────────
async function saveProfile() {
  const selected = [...state.activeTopics];
  const signalTypes = [...state.activeSignalTypes];
  await saveContextProfile({
    city: $("#city").value,
    radius_km: Number($("#radius").value),
    selected,
    signalTypes,
    domain: $("#domain").value,
    goal: $("#goal").value,
  });
  showToast("Profile updated", "success");
}

async function saveContextProfile({ city, radius_km, selected, signalTypes, domain, goal }) {
  const interests = {};
  selected.forEach((topic, index) => {
    interests[topic] = index === 0 ? 1 : 0.7;
  });
  await api("/api/onboarding", {
    method: "POST",
    body: JSON.stringify({
      user_id: "demo",
      city,
      radius_km,
      interests,
      context: {
        domain,
        goal,
        signal_types: signalTypes,
        freshness: "latest",
      },
    }),
  });
  localStorage.setItem("contextcast:onboarded", "1");
  await load();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pullLiveSources() {
  const button = $("#refreshLive");
  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Pulling…';
  try {
    // The server starts the ingest in the background and returns immediately;
    // poll the pipeline endpoint until it finishes so the UI never hangs.
    await api("/api/ingest/live", {
      method: "POST",
      body: JSON.stringify({ limit_per_source: 4 }),
    });
    let result = null;
    for (let i = 0; i < 45; i++) {
      await sleep(1500);
      const stats = await api("/api/admin/pipeline");
      state.stats = stats;
      if (!stats.ingest?.running && stats.ingest?.last_result) {
        result = stats.ingest.last_result;
        break;
      }
    }
    if (result && result.ok !== false) {
      state.sourceStatuses = result.statuses || [];
      const okCount = (result.statuses || []).filter((s) => s.ok).length;
      const secs = result.duration_ms ? ` in ${(result.duration_ms / 1000).toFixed(1)}s` : "";
      showToast(`Pulled ${result.fetched || 0} signals from ${okCount} sources${secs}`, "success");
    } else {
      showToast(result?.error ? `Pull failed: ${result.error}` : "Pull is taking longer than usual — the feed will update automatically.", "mute");
    }
    await load({ keepStatuses: true });
  } catch (err) {
    showToast("Pull failed: " + err.message, "mute");
  } finally {
    button.disabled = false;
    button.innerHTML = originalHtml;
  }
}

// After an interaction we only need the saved list + stats to update.
// A full load() re-ranks the feed and yanks the user's scroll position.
async function refreshQuiet() {
  try {
    const [saved, stats] = await Promise.all([api("/api/saved"), api("/api/admin/pipeline")]);
    state.saved = saved.events || [];
    state.stats = stats;
    renderSaved();
    renderQuickStats();
  } catch { /* next auto-refresh will catch up */ }
}

async function load(options = {}) {
  const sortParam = state.sortMode !== "relevance" ? `&sort=${state.sortMode}` : "";
  const [meta, feed, graph, stats, insights, saved] = await Promise.all([
    api("/api/meta"),
    api(`/api/feed?limit=30${sortParam}`),
    api("/api/graph"),
    api("/api/admin/pipeline"),
    api("/api/insights"),
    api("/api/saved"),
  ]);
  state.topics = meta.topics;
  state.cities = meta.cities;
  state.profile = feed.profile;
  state.feed = feed.events;
  state.stats = stats;
  state.insights = insights;
  state.saved = saved.events || [];
  if (!options.keepStatuses) state.sourceStatuses = [];
  state.activeTopics = new Set(Object.keys(feed.profile.interests));
  state.activeSignalTypes = new Set(feed.profile.context?.signal_types || state.signalTypes);

  renderCities();
  $("#city").value = feed.profile.city;
  $("#radius").value = feed.profile.radius_km;
  $("#radiusLabel").textContent = `${feed.profile.radius_km} km`;
  $("#domain").value = feed.profile.context?.domain || "builder";
  $("#goal").value = feed.profile.context?.goal || "";
  $("#lastUpdated").textContent = `Updated ${new Date(feed.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  updateAutoStatus();

  renderTopics();
  renderSignalTypes();
  renderQuickStats();
  renderSourceStrip();
  renderDailyDigest();
  renderFeed();
  renderSaved();
  renderAdmin();
  renderInsights();
  renderGraph(graph);
  maybeShowOnboarding();
}

// ─── Keyboard Shortcuts ───────────────────────────────────────
function updateFocusedCard() {
  const cards = document.querySelectorAll("#feed .signal-card");
  cards.forEach((card, i) => {
    card.classList.toggle("focused", i === state.focusedIndex);
  });
  if (state.focusedIndex >= 0 && cards[state.focusedIndex]) {
    cards[state.focusedIndex].scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function handleKeyboard(e) {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
  if (!$("#cardModal").classList.contains("hidden")) {
    if (e.key === "Escape") closeCardModal();
    return;
  }

  const visible = filteredFeed();

  switch (e.key) {
    case "j":
      e.preventDefault();
      state.focusedIndex = Math.min(state.focusedIndex + 1, visible.length - 1);
      updateFocusedCard();
      break;
    case "k":
      e.preventDefault();
      state.focusedIndex = Math.max(state.focusedIndex - 1, 0);
      updateFocusedCard();
      break;
    case "Enter":
      if (state.focusedIndex >= 0 && visible[state.focusedIndex]) {
        e.preventDefault();
        openCardModal(visible[state.focusedIndex]);
      }
      break;
    case "s":
      if (state.focusedIndex >= 0 && visible[state.focusedIndex]) {
        e.preventDefault();
        const event = visible[state.focusedIndex].event;
        api("/api/interact", {
          method: "POST",
          body: JSON.stringify({ user_id: "demo", event_id: event.id, action: "save" }),
        }).then(() => {
          showToast("Saved to your trail", "success");
          refreshQuiet();
        });
      }
      break;
    case "m":
      if (state.focusedIndex >= 0 && visible[state.focusedIndex]) {
        e.preventDefault();
        const event = visible[state.focusedIndex].event;
        api("/api/interact", {
          method: "POST",
          body: JSON.stringify({ user_id: "demo", event_id: event.id, action: "not_interested" }),
        }).then(() => {
          showToast("Muted this topic", "mute");
          refreshQuiet();
        });
      }
      break;
    case "/":
      e.preventDefault();
      $("#search").focus();
      break;
    case "?":
      e.preventDefault();
      state.keyboardHintsVisible = !state.keyboardHintsVisible;
      $("#keyboardHints").classList.toggle("hidden", !state.keyboardHintsVisible);
      break;
    case "Escape":
      state.keyboardHintsVisible = false;
      $("#keyboardHints").classList.add("hidden");
      break;
  }
}

// ─── UI Bindings ──────────────────────────────────────────────
function bindUi() {
  $("#radius").addEventListener("input", (event) => {
    $("#radiusLabel").textContent = `${event.target.value} km`;
  });
  $("#saveProfile").addEventListener("click", saveProfile);
  $("#refreshLive").addEventListener("click", pullLiveSources);
  $("#darkToggle").addEventListener("click", toggleDarkMode);
  $("#search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderFeed();
  });
  $("#kindSegment").addEventListener("click", (event) => {
    const seg = event.target.closest(".seg");
    if (!seg) return;
    state.kindFilter = seg.dataset.kind;
    document.querySelectorAll("#kindSegment .seg").forEach((el) => {
      el.classList.toggle("active", el.dataset.kind === state.kindFilter);
    });
    renderFeed();
  });
  $("#sortMode").addEventListener("change", (event) => {
    state.sortMode = event.target.value;
    renderFeed();
  });
  $("#exportReport").addEventListener("click", exportReport);
  $("#finishOnboarding").addEventListener("click", finishOnboarding);
  $("#skipOnboarding").addEventListener("click", () => {
    localStorage.setItem("contextcast:onboarded", "1");
    $("#onboarding").classList.add("hidden");
  });
  $("#clearTopics").addEventListener("click", () => {
    state.activeTopics.clear();
    renderTopics();
  });

  document.addEventListener("click", async (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    await activateView(tab.dataset.view);
  });

  window.addEventListener("resize", async () => {
    if (state.activeView === "graph") renderGraph(await api("/api/graph"));
  });

  document.addEventListener("keydown", handleKeyboard);

  $(".card-modal-backdrop").addEventListener("click", closeCardModal);
  $(".card-modal-close").addEventListener("click", closeCardModal);
}

async function activateView(view) {
  state.activeView = view;
  state.focusedIndex = -1;
  document.querySelectorAll(".tab").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  $("#viewTitle").textContent =
    view === "graph"
      ? "Taste graph"
      : view === "pulse"
        ? "City intelligence"
        : view === "saved"
          ? "Saved intelligence"
          : view === "admin"
            ? "Source operations"
            : "Recommended signals";
  if (view === "graph") renderGraph(await api("/api/graph"));
}

async function exportReport() {
  const result = await api("/api/report");
  const report = $("#report");
  report.textContent = result.markdown;
  report.classList.remove("hidden");
  // Also offer the report as a downloadable markdown file.
  const blob = new Blob([result.markdown], { type: "text/markdown" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "contextcast-report.md";
  link.click();
  URL.revokeObjectURL(link.href);
  try {
    await navigator.clipboard.writeText(result.markdown);
    $("#exportReport").textContent = "Copied Report";
    showToast("Report copied to clipboard", "success");
    window.setTimeout(() => {
      $("#exportReport").textContent = "Export Report";
    }, 1600);
  } catch {
    $("#exportReport").textContent = "Report Ready";
    showToast("Report generated", "success");
  }
}

function maybeShowOnboarding() {
  const forceIntro = new URLSearchParams(window.location.search).get("intro") === "1";
  if (!forceIntro && localStorage.getItem("contextcast:onboarded") === "1") return;
  $("#introCity").value = state.profile?.city || "Bangalore";
  $("#introDomain").value = state.profile?.context?.domain || "builder";
  $("#introGoal").value = state.profile?.context?.goal || $("#introGoal").value;
  $("#onboarding").classList.remove("hidden");
}

async function finishOnboarding() {
  await saveContextProfile({
    city: $("#introCity").value,
    radius_km: 35,
    selected: [...state.introTopics],
    signalTypes: [...state.introSignalTypes],
    domain: $("#introDomain").value,
    goal: $("#introGoal").value,
  });
  $("#onboarding").classList.add("hidden");
  showToast("Welcome to ContextCast!", "success");
}

function startAutoRefresh() {
  state.autoTimer = window.setInterval(() => {
    load({ keepStatuses: true }).catch(() => {});
  }, 300000);
  state.countdownTimer = window.setInterval(updateAutoStatus, 1000);
}

function updateAutoStatus() {
  if (state.stats?.ingest?.running) {
    $("#autoStatus").textContent = "Refreshing…";
    return;
  }
  const nextRun = state.stats?.ingest?.next_run;
  $("#autoStatus").textContent = nextRun ? `Auto ${timeUntil(nextRun)}` : "Auto 5m";
}

function timeUntil(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return "soon";
  const total = Math.ceil(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

// ─── Init ─────────────────────────────────────────────────────
function showSkeletons(count = 5) {
  $("#feed").innerHTML = Array.from({ length: count })
    .map(
      () => `
      <div class="skeleton">
        <div class="sk-line" style="width:34px;height:34px;border-radius:8px"></div>
        <div class="sk-block">
          <div class="sk-line" style="width:30%"></div>
          <div class="sk-line" style="width:80%;height:16px"></div>
          <div class="sk-line" style="width:95%"></div>
          <div class="sk-line" style="width:60%"></div>
        </div>
      </div>`
    )
    .join("");
}

initDarkMode();
bindUi();
showSkeletons();
startAutoRefresh();
load().catch((error) => {
  $("#feed").innerHTML = `<div class="empty"><strong>Failed to load</strong><span>${escapeHtml(error.message)}. Is the server running?</span></div>`;
});
