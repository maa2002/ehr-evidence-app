// The page of the EHR evidence application (Phase 8, work items 49 to 52).
//
// The sidebar selects the cohort and the patient; the conversation asks
// questions about the selected case in the selected scope and explains its
// predicted diagnosis; the panel shows the evidence. A question or an
// explanation runs as a job that the page asks after, so that the page never
// waits on the model. Changing the patient starts a new conversation;
// changing the scope keeps the earlier responses, greyed and labelled with
// the scope they answered (decision D4). Everything shown is built as text
// through the DOM, never parsed as markup; the words about values come from
// the interface's operations.

"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
// Seconds between looks at the model's state, by state.
const STATE_SECONDS = {ready: 30, loading: 4, unavailable: 30, off: 30, unreachable: 5};
// Evidence views kept in memory, and failed looks at a job before it fails.
const EVIDENCE_KEPT = 24;
const JOB_FAILURES = 6;
// The evidence panel's width: its bounds, a keyboard step and where it is
// remembered in this browser.
const PANEL = {least: 360, most: 920, step: 24, standard: 480, key: "ehr.panel-width"};
const CHECK_TEMPLATE = "Is  recorded?";
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const overlaid = window.matchMedia("(max-width: 1240px)");

// ---------------------------------------------------------------------------
// Elements
// ---------------------------------------------------------------------------

const byId = (id) => document.getElementById(id);

const ui = {
  app: byId("app"),
  sidebar: byId("sidebar"),
  scrim: byId("scrim"),
  menu: byId("menu-button"),
  sidebarClose: byId("sidebar-close"),
  newConversation: byId("new-conversation"),
  cohortMenu: byId("cohort-menu"),
  filter: byId("case-filter"),
  caseList: byId("case-list"),
  casesCount: byId("cases-count"),
  version: byId("version"),
  main: byId("main"),
  caseTitle: byId("case-title"),
  caseDetail: byId("case-detail"),
  pseudonym: byId("pseudonym-tag"),
  evidenceButton: byId("evidence-button"),
  banner: byId("banner"),
  bannerText: byId("banner-text"),
  bannerRetry: byId("banner-retry"),
  welcomeTitle: byId("welcome-title"),
  welcomeTags: byId("welcome-tags"),
  thread: byId("thread"),
  announce: byId("announce"),
  composer: byId("composer"),
  question: byId("question"),
  scopeMenu: byId("scope-menu"),
  explain: byId("explain"),
  model: byId("model-state"),
  modelText: byId("model-text"),
  send: byId("send"),
  suggestions: byId("suggestions"),
  suggestCheck: byId("suggest-check"),
  suggestExplain: byId("suggest-explain"),
  suggestEvidence: byId("suggest-evidence"),
  panel: byId("panel"),
  resizer: byId("panel-resizer"),
  evidenceMenu: byId("evidence-menu"),
  panelClose: byId("panel-close"),
  panelBody: byId("panel-body"),
  menuLayer: byId("menu-layer"),
};

// Build an element: properties set attributes, classes, data and
// listeners; children are nodes or text.
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "className") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function icon(name, className = "icon") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

const sleep = (milliseconds) => new Promise((done) => setTimeout(done, milliseconds));
const behaviour = () => (reducedMotion.matches ? "auto" : "smooth");

// This tab remembers the cohort and patient; this browser, the panel's
// width. Either store may be refused; the page works without them.
function remember(name, value) {
  try {
    sessionStorage.setItem("ehr." + name, String(value));
  } catch {
    // Refused: nothing is remembered.
  }
}

function recall(name) {
  try {
    return sessionStorage.getItem("ehr." + name);
  } catch {
    return null;
  }
}

function keep(name, value) {
  try {
    localStorage.setItem(name, String(value));
  } catch {
    // Refused: the width is not kept.
  }
}

function kept(name) {
  try {
    return localStorage.getItem(name);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

class Refusal extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request(path, options = {}) {
  const headers = {Accept: "application/json"};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let reply;
  try {
    reply = await fetch(path, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
    });
  } catch {
    throw new Refusal("interface_unreachable",
      "The interface isn't answering. It may have stopped.", 0);
  }
  let body = null;
  try {
    body = await reply.json();
  } catch {
    body = null;
  }
  if (!reply.ok) {
    if (body && typeof body.error === "string") {
      throw new Refusal(body.error, body.message || body.error, reply.status);
    }
    if (reply.status === 404) {
      throw new Refusal("session_ended", "Your session has ended. Open the link again.", 404);
    }
    throw new Refusal("interface_refused", "The interface refused the request.", reply.status);
  }
  return body;
}

const get = (path) => request(path);
const post = (path, body) => request(path, {method: "POST", body});
const casePath = (caseSet, number) =>
  `/api/case-sets/${encodeURIComponent(caseSet)}/cases/${number}`;

// ---------------------------------------------------------------------------
// Menus: a button that opens a list of choices, in groups, each with what
// it holds. The list is drawn above the page, so that no panel clips it.
// ---------------------------------------------------------------------------

let openMenu = null;
let menuCount = 0;

class Menu {
  constructor(host, options) {
    menuCount += 1;
    this.options = options;
    this.items = [];
    this.value = null;
    this.id = `menu-${menuCount}`;
    this.button = el("button", {
      type: "button",
      className: "menu-button",
      "aria-haspopup": "listbox",
      "aria-expanded": "false",
      "aria-controls": this.id,
      "aria-label": options.label,
    });
    this.shown = el("span", {className: "menu-value"}, options.empty || "");
    if (options.icon) this.button.append(icon(options.icon));
    this.button.append(this.shown, icon("chevron", "icon menu-chevron"));
    host.replaceChildren(this.button);
    this.list = el("div", {
      className: "menu-popover glass",
      role: "listbox",
      id: this.id,
      tabindex: "-1",
      "aria-label": options.label,
      hidden: true,
    });
    ui.menuLayer.append(this.list);
    this.button.addEventListener("click", () => (this.isOpen() ? this.close(true) : this.open()));
    this.button.addEventListener("keydown", (event) => {
      if (["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        this.open();
      }
    });
    this.list.addEventListener("keydown", (event) => this.key(event));
  }

  set(items, value) {
    this.items = items;
    this.value = value;
    this.render();
  }

  enable(on) {
    this.button.disabled = !on;
    if (!on) this.close(false);
  }

  isOpen() {
    return !this.list.hidden;
  }

  render() {
    const current = this.items.find((item) => item.key === this.value);
    this.shown.textContent = current ? current.label : this.options.empty || "";
    this.button.title = current && current.detail ? `${current.label}: ${current.detail}` : "";
    const groups = [];
    for (const item of this.items) {
      let group = groups.find((found) => found.name === (item.group || ""));
      if (!group) groups.push(group = {name: item.group || "", items: []});
      group.items.push(item);
    }
    this.list.replaceChildren(...groups.map((group) => el("div", {
      role: "group",
      className: "menu-group",
      "aria-label": group.name || null,
    },
    group.name ? el("div", {className: "menu-group-label", "aria-hidden": "true"}, group.name) : null,
    ...group.items.map((item) => el("div", {
      role: "option",
      className: "menu-option",
      tabindex: "-1",
      "aria-selected": String(item.key === this.value),
      dataset: {key: item.key},
      onclick: () => this.choose(item.key),
    },
    el("span", {className: "option-text"},
      el("span", {className: "option-label"}, item.label),
      item.detail ? el("span", {className: "option-detail"}, item.detail) : null),
    icon("check", "icon option-check"))))));
  }

  open() {
    if (this.button.disabled || !this.items.length) return;
    if (openMenu && openMenu !== this) openMenu.close(false);
    openMenu = this;
    this.list.hidden = false;
    this.button.setAttribute("aria-expanded", "true");
    this.place();
    const chosen = this.list.querySelector('[aria-selected="true"]')
      || this.list.querySelector('[role="option"]');
    if (chosen) chosen.focus();
  }

  place() {
    const box = this.button.getBoundingClientRect();
    this.list.style.minWidth = `${Math.max(box.width, 230)}px`;
    const height = this.list.offsetHeight;
    const width = this.list.offsetWidth;
    const above = this.options.above && box.top - height - 8 > 8;
    const top = above ? box.top - height - 8 : box.bottom + 8;
    this.list.style.top = `${Math.max(8, Math.min(top, window.innerHeight - height - 8))}px`;
    this.list.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`;
  }

  close(focus) {
    if (!this.isOpen()) return;
    this.list.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
    if (openMenu === this) openMenu = null;
    if (focus) this.button.focus();
  }

  choose(key) {
    this.close(true);
    if (key === this.value) return;
    this.value = key;
    this.render();
    this.options.onSelect(key);
  }

  key(event) {
    const options = [...this.list.querySelectorAll('[role="option"]')];
    const index = options.indexOf(document.activeElement);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      (options[index + 1] || options[0]).focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      (options[index - 1] || options[options.length - 1]).focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      options[event.key === "Home" ? 0 : options.length - 1].focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (index >= 0) this.choose(options[index].dataset.key);
    } else if (event.key === "Escape") {
      event.preventDefault();
      this.close(true);
    } else if (event.key === "Tab") {
      this.close(false);
    }
  }
}

document.addEventListener("pointerdown", (event) => {
  if (openMenu && !openMenu.list.contains(event.target)
      && !openMenu.button.contains(event.target)) openMenu.close(false);
});
document.addEventListener("scroll", (event) => {
  if (openMenu && !openMenu.list.contains(event.target)) openMenu.close(false);
}, true);
window.addEventListener("resize", () => openMenu && openMenu.close(false));

const menus = {
  cohort: new Menu(ui.cohortMenu, {
    label: "Cohort",
    empty: "Loading",
    onSelect: (key) => selectCaseSet(key),
  }),
  scope: new Menu(ui.scopeMenu, {
    label: "Scope",
    icon: "layers",
    empty: "Scope",
    above: true,
    onSelect: (key) => {
      state.scopeKey = key;
      renderThread();
    },
  }),
  evidence: new Menu(ui.evidenceMenu, {
    label: "Show the Evidence Of",
    empty: "Evidence",
    onSelect: (key) => openPanel(key, null),
  }),
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  caseSets: [],
  caseSet: null,
  cases: [],
  filter: "",
  caseNumber: null,
  current: null,
  scopeKey: null,
  thread: [],
  conversation: 0,
  turns: 0,
  panel: {open: false, key: null, ref: null, request: 0},
  evidence: new Map(),
  timer: null,
  banner: null,
};

const pending = () => state.thread.some((turn) => turn.status === "pending");

function currentScope() {
  const scopes = state.current ? state.current.scopes : [];
  return scopes.find((scope) => scope.key === state.scopeKey) || scopes[0];
}

// ---------------------------------------------------------------------------
// The banner and the model's state
// ---------------------------------------------------------------------------

// The banner says what failed; kind names what it is about (the state or
// the case), so that the success of the same thing takes it away.
function showBanner(message, kind, retry) {
  ui.bannerText.textContent = message;
  state.banner = {kind, retry};
  ui.bannerRetry.hidden = !retry;
  ui.banner.hidden = false;
}

function hideBanner(kind) {
  if (!state.banner || (kind && state.banner.kind !== kind)) return;
  ui.banner.hidden = true;
  state.banner = null;
}

function showModel(model) {
  ui.model.dataset.state = model.state;
  ui.modelText.textContent = model.label;
  ui.model.title = model.detail || "";
}

async function refreshState() {
  clearTimeout(state.timer);
  let seconds = STATE_SECONDS.unreachable;
  try {
    const found = await get("/api/state");
    showModel(found.model);
    seconds = STATE_SECONDS[found.model.state] || 30;
    ui.question.maxLength = found.question_chars;
    ui.version.textContent = `EHR Evidence ${found.version}`;
    const labels = found.case_sets.map((item) => item.id).join(" ");
    if (labels !== state.caseSets.map((item) => item.id).join(" ")) {
      state.caseSets = found.case_sets;
      menus.cohort.set(state.caseSets.map((item) => ({
        key: item.id, label: item.label, detail: item.detail,
      })), state.caseSet);
    }
    hideBanner("state");
    if (!state.caseSet && state.caseSets.length) {
      const saved = recall("caseSet");
      const known = state.caseSets.some((item) => item.id === saved);
      await selectCaseSet(known ? saved : state.caseSets[0].id);
    }
  } catch (error) {
    showModel({state: "unreachable", label: "Not Connected", detail: error.message});
    showBanner(error.message, "state", refreshState);
  }
  state.timer = setTimeout(refreshState, seconds * 1000);
}

// ---------------------------------------------------------------------------
// Cohorts and patients (work item 49)
// ---------------------------------------------------------------------------

// A patient is found by the pseudonym, or by the case's number.
function matches(item, term) {
  if (!term) return true;
  const number = term.replace(/^case\s*/, "");
  return item.name.toLowerCase().includes(term) || (/^\d+$/.test(number)
    && String(item.number).startsWith(number));
}

function renderCases() {
  const term = state.filter.trim().toLowerCase();
  const shown = state.cases.filter((item) => matches(item, term));
  ui.casesCount.textContent = state.cases.length ? String(state.cases.length) : "";
  ui.caseList.replaceChildren(...shown.map((item) => el("button", {
    type: "button",
    className: "case-item",
    "aria-current": item.number === state.caseNumber ? "true" : null,
    onclick: () => {
      closeSidebar();
      selectCase(item.number);
    },
  }, el("span", {className: "case-name"}, item.name), el("span", {className: "case-meta"}, item.detail))));
  if (!shown.length) {
    ui.caseList.append(el("p", {className: "list-note"},
      state.cases.length ? "No Matching Patients" : "Loading Patients"));
  }
}

async function selectCaseSet(id) {
  state.caseSet = id;
  menus.cohort.set(menus.cohort.items, id);
  remember("caseSet", id);
  state.cases = [];
  state.caseNumber = null;
  renderCases();
  try {
    const found = await get(`/api/case-sets/${encodeURIComponent(id)}/cases`);
    if (state.caseSet !== id) return;
    state.cases = found.cases;
    renderCases();
    const saved = Number(recall("case." + id));
    const known = state.cases.some((item) => item.number === saved);
    if (state.cases.length) await selectCase(known ? saved : state.cases[0].number);
    else showBanner("This cohort has no admitted patients.", "case", null);
  } catch (error) {
    showBanner(error.message, "case", () => selectCaseSet(id));
  }
}

async function selectCase(number) {
  if (number === state.caseNumber && state.current) return;
  // A new patient starts a new conversation: nothing carries over (D4).
  state.conversation += 1;
  const conversation = state.conversation;
  state.caseNumber = number;
  state.current = null;
  state.thread = [];
  remember("case." + state.caseSet, number);
  renderCases();
  renderCase();
  renderThread();
  try {
    const found = await get(casePath(state.caseSet, number));
    if (conversation !== state.conversation) return;
    state.current = found;
    state.scopeKey = found.scopes.length ? found.scopes[0].key : null;
    renderCase();
    hideBanner("case");
    if (state.panel.open) openPanel(state.scopeKey, null);
  } catch (error) {
    if (conversation === state.conversation) {
      showBanner(error.message, "case", () => {
        state.caseNumber = null;
        selectCase(number);
      });
    }
  }
}

function renderCase() {
  const current = state.current;
  const listed = state.cases.find((item) => item.number === state.caseNumber);
  ui.pseudonym.hidden = !current;
  if (current) {
    ui.caseTitle.textContent = current.name;
    ui.caseDetail.textContent = current.subtitle;
    ui.welcomeTitle.textContent = current.greeting;
    ui.welcomeTags.replaceChildren(...current.tags.map((tag) => el("span", {className: "tag"}, tag)));
    document.title = `${current.name} · EHR Evidence`;
  } else {
    ui.caseTitle.textContent = listed ? listed.name : "Loading";
    ui.caseDetail.textContent = listed ? listed.detail : "";
    ui.welcomeTitle.textContent = "Opening the Record";
    ui.welcomeTags.replaceChildren();
  }
  menus.scope.set(current ? current.scopes : [], state.scopeKey);
  menus.evidence.set(current ? current.evidence : [], state.panel.key);
  updateControls();
}

function updateControls() {
  const current = state.current;
  const busy = pending();
  const explainable = Boolean(current && current.prediction);
  ui.question.disabled = !current;
  menus.scope.enable(Boolean(current));
  ui.send.disabled = !current || busy || !ui.question.value.trim();
  ui.send.classList.toggle("is-busy", busy);
  ui.send.setAttribute("aria-label", busy ? "Waiting for the Response" : "Ask");
  ui.explain.hidden = Boolean(current) && !explainable;
  ui.explain.disabled = !explainable || busy;
  ui.suggestExplain.hidden = !explainable;
  ui.suggestExplain.disabled = busy;
  for (const chip of ui.suggestions.querySelectorAll("[data-question]")) chip.disabled = !current || busy;
  ui.suggestCheck.disabled = !current;
  ui.suggestEvidence.disabled = !current;
  ui.evidenceButton.disabled = !current;
  ui.newConversation.disabled = !state.thread.length;
  ui.main.classList.toggle("is-empty", state.thread.length === 0);
  ui.question.placeholder = !current ? "Ask About the Record"
    : state.thread.length ? "Ask Another Question" : current.placeholder;
  resize();
}

function newConversation() {
  if (!state.thread.length) return;
  state.conversation += 1;
  state.thread = [];
  renderThread();
  ui.question.focus();
}

// ---------------------------------------------------------------------------
// Questions and the explanation (work items 50 and 51)
// ---------------------------------------------------------------------------

function ask(text) {
  const question = text.trim();
  const scope = currentScope();
  if (!question || !scope || pending()) return;
  begin({
    kind: "question",
    asked: question,
    scopeKey: scope.key,
    scopeLabel: scope.label,
    evidenceKey: scope.key,
    path: "/api/questions",
    body: {
      case_set: state.current.case_set,
      case_number: state.current.number,
      scope: scope.scope,
      question,
    },
  });
  ui.question.value = "";
  updateControls();
}

function explain() {
  if (!state.current || !state.current.prediction || pending()) return;
  begin({
    kind: "explanation",
    asked: "Generate the Predicted Diagnosis",
    scopeKey: null,
    scopeLabel: null,
    evidenceKey: "prediction",
    path: "/api/explanations",
    body: {case_set: state.current.case_set, case_number: state.current.number},
  });
}

// Put the frame of a condition question in the box, the cursor where the
// condition goes.
function checkCondition() {
  if (!state.current) return;
  ui.question.value = CHECK_TEMPLATE;
  updateControls();
  ui.question.focus();
  ui.question.setSelectionRange(3, 3);
}

function begin(fields) {
  state.turns += 1;
  const turn = Object.assign({
    id: state.turns,
    conversation: state.conversation,
    status: "pending",
    started: Date.now(),
    job: null,
    result: null,
    error: null,
    node: null,
  }, fields);
  state.thread.push(turn);
  renderThread();
  turn.node.scrollIntoView({block: "end", behavior: behaviour()});
  send(turn);
}

const live = (turn) => turn.conversation === state.conversation;

async function send(turn) {
  try {
    const found = await post(turn.path, turn.body);
    if (!live(turn)) return;
    turn.job = found.job;
    await follow(turn);
  } catch (error) {
    if (live(turn)) fail(turn, error.message);
  }
}

// Ask after a job until it is done; a few failed looks in a row fail it.
async function follow(turn) {
  let delay = 600;
  let failures = 0;
  while (live(turn)) {
    await sleep(delay);
    delay = Math.min(delay + 300, 1500);
    if (!live(turn)) return;
    let found;
    try {
      found = await get(`/api/jobs/${encodeURIComponent(turn.job)}`);
      failures = 0;
    } catch (error) {
      failures += 1;
      const lasting = error.status && error.status < 500 && error.status !== 429;
      if (lasting || failures >= JOB_FAILURES) {
        fail(turn, error.message);
        return;
      }
      continue;
    }
    if (!live(turn)) return;
    if (found.state === "running") continue;
    if (found.state === "done") {
      finish(turn, found.result);
      return;
    }
    fail(turn, found.error ? found.error.message : "The request failed.");
    return;
  }
}

function finish(turn, result) {
  turn.status = "done";
  turn.result = result;
  renderTurn(turn);
  updateControls();
  ui.announce.textContent = `${result.outcome.label}: the response is ready.`;
  turn.node.scrollIntoView({block: "start", behavior: behaviour()});
}

function fail(turn, message) {
  turn.status = "failed";
  turn.error = message;
  renderTurn(turn);
  updateControls();
  ui.announce.textContent = message;
}

function retry(turn) {
  if (!live(turn) || pending()) return;
  turn.status = "pending";
  turn.started = Date.now();
  turn.error = null;
  turn.job = null;
  renderTurn(turn);
  updateControls();
  send(turn);
}

// Keep the seconds of every waiting turn current.
function tick() {
  for (const turn of state.thread) {
    if (turn.status !== "pending" || !turn.node) continue;
    const shown = turn.node.querySelector(".elapsed");
    if (shown) shown.textContent = `${Math.floor((Date.now() - turn.started) / 1000)} s`;
  }
}

// ---------------------------------------------------------------------------
// The thread
// ---------------------------------------------------------------------------

function renderThread() {
  for (const turn of state.thread) turn.node = turnNode(turn);
  ui.thread.replaceChildren(...state.thread.map((turn) => turn.node));
  updateControls();
}

function renderTurn(turn) {
  const node = turnNode(turn);
  if (turn.node && turn.node.isConnected) turn.node.replaceWith(node);
  turn.node = node;
}

function turnNode(turn) {
  const earlier = turn.kind === "question" && turn.scopeKey !== state.scopeKey;
  const item = el("li", {className: earlier ? "turn is-earlier" : "turn"});
  if (earlier) {
    item.append(el("p", {className: "earlier-label"}, `Earlier Scope · ${turn.scopeLabel}`));
  }
  item.append(el("div", {className: "ask"}, turn.asked));
  if (turn.status === "pending") item.append(thinking(turn));
  else if (turn.status === "failed") item.append(failure(turn));
  else item.append(reply(turn));
  return item;
}

function thinking(turn) {
  const seconds = Math.floor((Date.now() - turn.started) / 1000);
  return el("div", {className: "thinking", role: "status"},
    icon("mark", "mark"),
    el("span", {className: "thinking-text"}, "Thinking"),
    el("span", {className: "elapsed"}, `${seconds} s`));
}

function failure(turn) {
  return el("div", {className: "failure", role: "alert"},
    icon("alert"),
    el("p", {}, turn.error),
    el("button", {type: "button", className: "text-button", onclick: () => retry(turn)}, "Try Again"));
}

function badge(outcome) {
  return el("span", {className: `badge tone-${outcome.tone}`}, outcome.label);
}

function cite(ref, turn) {
  return el("button", {
    type: "button",
    className: "cite",
    title: `Open ${ref} in the Evidence`,
    "aria-label": `Open Reference ${ref} in the Evidence`,
    onclick: () => openPanel(turn.evidenceKey, ref),
  }, ref);
}

// The text line by line: paragraphs, and lists nested by depth, each line
// followed by the references of the claim it states.
function prose(lines, turn) {
  const body = el("div", {className: "prose"});
  let lists = [];
  for (const line of lines) {
    const content = [line.text, line.refs.length
      ? el("span", {className: "refs"}, ...line.refs.map((ref) => cite(ref, turn)))
      : null];
    if (line.depth === null) {
      lists = [];
      body.append(el("p", {}, ...content));
      continue;
    }
    while (lists.length > line.depth + 1) lists.pop();
    while (lists.length < line.depth + 1) {
      const list = el("ul");
      if (!lists.length) body.append(list);
      else {
        const parent = lists[lists.length - 1];
        const last = parent.lastElementChild || parent.appendChild(el("li"));
        last.append(list);
      }
      lists.push(list);
    }
    // An item numbered in its text needs no bullet as well.
    const numbered = /^\d+\.\s/.test(line.text);
    if (numbered) lists[lists.length - 1].classList.add("numbered");
    lists[lists.length - 1].append(el("li", {}, ...content));
  }
  return body;
}

function predictionCard(prediction) {
  return el("div", {className: "prediction glass"},
    el("div", {className: "prediction-head"},
      el("span", {className: "kicker"}, "Predicted Diagnosis"),
      el("dl", {className: "numbers"}, ...prediction.facts.map((fact) =>
        el("div", {}, el("dt", {}, fact.label), el("dd", {}, fact.value))))),
    el("div", {className: "prediction-label"}, prediction.label),
    prediction.timeline ? timelineRow(prediction.timeline) : null,
    prediction.target ? el("p", {className: "plain"}, prediction.target) : null,
    prediction.record ? el("div", {className: `record-check tone-${prediction.record.tone}`},
      el("span", {className: "record-label"}, "In the Record"),
      el("span", {className: "record-text"}, prediction.record.text)) : null,
    el("div", {className: "fine-group"},
      prediction.plain ? el("p", {className: "fine"}, prediction.plain) : null,
      el("p", {className: "fine"}, `${prediction.meaning} ${prediction.note}`)));
}

// Where a prediction applies: after the earlier visits, at the final
// admission.
function timelineRow(timeline) {
  const stop = (kind, text, target) => el("span", {className: target ? "stop is-target" : "stop"},
    el("span", {className: "stop-kind"}, kind), el("span", {}, text));
  return el("div", {className: "timeline"},
    timeline.earlier ? stop("Earlier Visits", timeline.earlier, false) : null,
    timeline.earlier ? el("span", {className: "arrow", "aria-hidden": "true"}, "→") : null,
    stop("Final Admission · Predicted Here", timeline.final, true));
}

// What an explanation is, in plain words, before it.
function introBlock(text) {
  return el("div", {className: "intro"},
    el("span", {className: "kicker"}, "What This Is"),
    el("p", {}, text));
}

// The fixed notes of an explanation, apart from what it states.
function notesBlock(notes) {
  return el("div", {className: "notes"},
    el("span", {className: "kicker"}, "Notes"),
    ...notes.map((text) => el("p", {className: "fine"}, text)));
}

// How to read an explanation's parts, folded.
function glossaryBlock(glossary) {
  return el("details", {className: "block glass"},
    el("summary", {}, "How to Read This", icon("chevron")),
    el("dl", {className: "block-body glossary"}, ...glossary.map((item) =>
      el("div", {}, el("dt", {}, item.term), el("dd", {}, item.meaning)))));
}

function note(tone, text) {
  return el("p", {className: `note tone-${tone}`}, text);
}

function conditionsBlock(conditions) {
  const body = el("div", {className: "block-body"}, el("p", {className: "fine"}, conditions.note));
  if (conditions.table) body.append(tableNode(conditions.table));
  return el("details", {className: "block glass"},
    el("summary", {}, conditions.title, icon("chevron")),
    body);
}

function copyButton(text) {
  if (!text || !navigator.clipboard) return null;
  const button = el("button", {
    type: "button",
    className: "icon-button",
    title: "Copy",
    "aria-label": "Copy the Response",
  }, icon("copy"));
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      button.replaceChildren(icon("check"));
      setTimeout(() => button.replaceChildren(icon("copy")), 1500);
    } catch {
      // The browser refused the clipboard; nothing to do.
    }
  });
  return button;
}

function reply(turn) {
  const view = turn.result;
  const node = el("div", {className: "reply"}, badge(view.outcome));
  const explained = turn.kind === "explanation";
  if (explained && view.intro) node.append(introBlock(view.intro));
  if (explained && view.prediction) node.append(predictionCard(view.prediction));
  if (view.lines.length) node.append(prose(view.lines, turn));
  if (view.withheld) node.append(note("muted", view.withheld));
  if (explained && view.notes && view.notes.length) node.append(notesBlock(view.notes));
  if (explained && view.unsigned) node.append(note("info", view.unsigned));
  if (view.references.length) {
    node.append(el("div", {className: "sources"}, "Sources",
      ...view.references.map((ref) => cite(ref, turn))));
  }
  node.append(el("div", {className: "meta"},
    copyButton(view.text),
    el("span", {className: "meta-text"}, `${view.scope.label} · ${view.author} · ${view.seconds}`)));
  // The folded blocks close the reply, after its sources and who wrote it.
  if (explained && view.glossary) node.append(glossaryBlock(view.glossary));
  if (explained && view.conditions) node.append(conditionsBlock(view.conditions));
  return node;
}

// ---------------------------------------------------------------------------
// The evidence (work item 52)
// ---------------------------------------------------------------------------

function evidenceOf(caseSet, number, key) {
  const id = `${caseSet}/${number}/${key}`;
  if (!state.evidence.has(id)) {
    const [kind, visit] = key.split(":");
    const query = new URLSearchParams({kind});
    if (visit) query.set("visit", visit);
    const loading = get(`${casePath(caseSet, number)}/evidence?${query}`);
    state.evidence.set(id, loading);
    loading.catch(() => state.evidence.delete(id));
    while (state.evidence.size > EVIDENCE_KEPT) {
      state.evidence.delete(state.evidence.keys().next().value);
    }
  }
  return state.evidence.get(id);
}

async function openPanel(key, ref) {
  if (!state.current) return;
  const known = state.current.evidence.some((item) => item.key === key);
  state.panel = {open: true, key: known ? key : state.current.evidence[0].key, ref,
    request: state.panel.request + 1};
  const token = state.panel.request;
  ui.panel.hidden = false;
  ui.evidenceButton.setAttribute("aria-expanded", "true");
  menus.evidence.set(state.current.evidence, state.panel.key);
  ui.panelBody.replaceChildren(el("div", {className: "skeleton", "aria-label": "Loading the Evidence"},
    ...Array.from({length: 9}, () => el("span"))));
  try {
    const view = await evidenceOf(state.current.case_set, state.current.number, state.panel.key);
    if (token !== state.panel.request) return;
    ui.panelBody.replaceChildren(...view.sections.map(sectionNode));
    ui.panelBody.scrollTop = 0;
    if (ref) focusReference(ref);
  } catch (error) {
    if (token !== state.panel.request) return;
    ui.panelBody.replaceChildren(el("div", {className: "failure", role: "alert"},
      icon("alert"), el("p", {}, error.message),
      el("button", {type: "button", className: "text-button", onclick: () => openPanel(key, ref)},
        "Try Again")));
  }
}

function closePanel() {
  state.panel = {open: false, key: null, ref: null, request: state.panel.request + 1};
  ui.panel.hidden = true;
  ui.evidenceButton.setAttribute("aria-expanded", "false");
}

function sectionNode(section) {
  const title = [section.title];
  if (section.ref) title.push(el("span", {className: "ref-tag"}, section.ref));
  const blocks = section.blocks.map(blockNode);
  const node = section.collapsed
    ? el("details", {className: "ev-section"}, el("summary", {}, ...title, icon("chevron")), ...blocks)
    : el("section", {className: "ev-section"}, el("h3", {}, ...title), ...blocks);
  if (section.ref) node.dataset.ref = section.ref;
  return node;
}

function blockNode(block) {
  if (block.type === "facts") {
    return el("dl", {className: "facts"}, ...block.items.map((item) =>
      el("div", {className: "fact"}, el("dt", {}, item.label), el("dd", {}, item.value))));
  }
  if (block.type === "table") return tableNode(block);
  return note(block.tone, block.text);
}

function tableNode(block) {
  if (!block.rows.length) return el("p", {className: "empty"}, block.empty);
  const kinds = block.columns.map((column) => `kind-${column.kind}`);
  return el("div", {className: "table-wrap"}, el("table", {},
    el("thead", {}, el("tr", {}, ...block.columns.map((column, index) =>
      el("th", {className: kinds[index], scope: "col"}, column.label)))),
    el("tbody", {}, ...block.rows.map((row) => el("tr", {dataset: row.ref ? {ref: row.ref} : null},
      ...row.cells.map((cell, index) => el("td", {className: kinds[index]}, cell)))))));
}

function focusReference(ref) {
  for (const found of ui.panelBody.querySelectorAll(".is-focus")) found.classList.remove("is-focus");
  const target = [...ui.panelBody.querySelectorAll("[data-ref]")]
    .find((node) => node.dataset.ref === ref);
  if (!target) {
    ui.panelBody.prepend(note("warn", `Reference ${ref} is not in this evidence.`));
    return;
  }
  const folded = target.closest("details");
  if (folded) folded.open = true;
  target.classList.add("is-focus");
  target.scrollIntoView({block: "center", behavior: behaviour()});
}

// The panel's width follows its handle, within bounds that leave the
// conversation room; the arrow keys move it too.
function setPanelWidth(width, save) {
  const room = overlaid.matches
    ? window.innerWidth - 20
    : Math.min(PANEL.most, window.innerWidth - ui.sidebar.offsetWidth - 480);
  const found = Math.round(Math.max(PANEL.least, Math.min(width, Math.max(PANEL.least, room))));
  ui.app.style.setProperty("--panel-width", `${found}px`);
  ui.resizer.setAttribute("aria-valuenow", String(found));
  if (save) keep(PANEL.key, found);
  return found;
}

function bindResizer() {
  setPanelWidth(Number(kept(PANEL.key)) || PANEL.standard, false);
  ui.resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    ui.resizer.setPointerCapture(event.pointerId);
    document.body.classList.add("is-resizing");
    const right = ui.panel.getBoundingClientRect().right;
    const move = (moved) => setPanelWidth(right - moved.clientX, false);
    const end = () => {
      ui.resizer.removeEventListener("pointermove", move);
      ui.resizer.removeEventListener("pointerup", end);
      ui.resizer.removeEventListener("pointercancel", end);
      document.body.classList.remove("is-resizing");
      setPanelWidth(ui.panel.getBoundingClientRect().width, true);
    };
    ui.resizer.addEventListener("pointermove", move);
    ui.resizer.addEventListener("pointerup", end);
    ui.resizer.addEventListener("pointercancel", end);
  });
  ui.resizer.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const change = event.key === "ArrowLeft" ? PANEL.step : -PANEL.step;
    setPanelWidth(ui.panel.getBoundingClientRect().width + change, true);
  });
  ui.resizer.addEventListener("dblclick", () => setPanelWidth(PANEL.standard, true));
}

// ---------------------------------------------------------------------------
// The composer and the sidebar on small screens
// ---------------------------------------------------------------------------

// Fit the question box to its text, or to its placeholder when empty; it
// scrolls only past its largest height.
function resize() {
  const largest = window.innerHeight * 0.4;
  ui.question.style.height = "auto";
  const wanted = ui.question.scrollHeight;
  ui.question.style.height = `${Math.min(wanted, largest)}px`;
  ui.question.style.overflowY = wanted > largest ? "auto" : "hidden";
}

function openSidebar() {
  ui.app.classList.add("sidebar-open");
  ui.scrim.hidden = false;
  ui.sidebarClose.focus();
}

function closeSidebar() {
  if (!ui.app.classList.contains("sidebar-open")) return;
  ui.app.classList.remove("sidebar-open");
  ui.scrim.hidden = true;
  ui.menu.focus();
}

function bind() {
  ui.filter.addEventListener("input", () => {
    state.filter = ui.filter.value;
    renderCases();
  });
  ui.newConversation.addEventListener("click", newConversation);
  ui.menu.addEventListener("click", openSidebar);
  ui.sidebarClose.addEventListener("click", closeSidebar);
  ui.scrim.addEventListener("click", closeSidebar);
  ui.evidenceButton.addEventListener("click", () =>
    (state.panel.open ? closePanel() : openPanel(state.scopeKey, null)));
  ui.panelClose.addEventListener("click", closePanel);
  ui.question.addEventListener("input", updateControls);
  window.addEventListener("resize", resize);
  ui.question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      ask(ui.question.value);
    }
  });
  ui.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    ask(ui.question.value);
  });
  ui.explain.addEventListener("click", explain);
  ui.suggestExplain.addEventListener("click", explain);
  ui.suggestCheck.addEventListener("click", checkCondition);
  ui.suggestEvidence.addEventListener("click", () => openPanel(state.scopeKey, null));
  ui.suggestions.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-question]");
    if (chip && !chip.disabled) ask(chip.dataset.question);
  });
  ui.bannerRetry.addEventListener("click", () => {
    const retryWith = state.banner && state.banner.retry;
    hideBanner();
    if (retryWith) retryWith();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || openMenu) return;
    if (ui.app.classList.contains("sidebar-open")) closeSidebar();
    else if (state.panel.open) closePanel();
  });
  bindResizer();
  setInterval(tick, 1000);
}

bind();
renderCases();
refreshState();
