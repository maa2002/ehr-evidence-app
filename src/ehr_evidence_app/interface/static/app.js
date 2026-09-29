// The page of the EHR evidence application (Phase 8, work items 49 to 52).
//
// The sidebar selects the case set and the case; the conversation asks
// questions about the selected case in the selected scope and explains its
// prepared prediction; the panel shows the evidence. A question or an
// explanation runs as a job that the page asks after, so that the page never
// waits on the model. Changing the case starts a new conversation; changing
// the scope keeps the earlier responses, greyed and labelled with the scope
// they answered (decision D4). Everything shown is built as text through the
// DOM, never parsed as markup; the words about values come from the
// interface's operations.

"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
// Seconds between looks at the model's state, by state.
const STATE_SECONDS = {ready: 30, loading: 4, unavailable: 30, off: 30, unreachable: 5};
// Evidence views kept in memory, and failed looks at a job before it fails.
const EVIDENCE_KEPT = 24;
const JOB_FAILURES = 6;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

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
  caseSet: byId("case-set"),
  filter: byId("case-filter"),
  caseList: byId("case-list"),
  casesLabel: byId("cases-label"),
  version: byId("version"),
  main: byId("main"),
  caseTitle: byId("case-title"),
  caseDetail: byId("case-detail"),
  evidenceButton: byId("evidence-button"),
  conversation: byId("conversation"),
  banner: byId("banner"),
  bannerText: byId("banner-text"),
  bannerRetry: byId("banner-retry"),
  welcomeTitle: byId("welcome-title"),
  welcomeDetail: byId("welcome-detail"),
  thread: byId("thread"),
  announce: byId("announce"),
  composer: byId("composer"),
  question: byId("question"),
  scope: byId("scope"),
  explain: byId("explain"),
  model: byId("model-state"),
  modelText: byId("model-text"),
  send: byId("send"),
  suggestions: byId("suggestions"),
  suggestExplain: byId("suggest-explain"),
  suggestEvidence: byId("suggest-evidence"),
  panel: byId("panel"),
  evidenceKey: byId("evidence-key"),
  panelClose: byId("panel-close"),
  panelBody: byId("panel-body"),
};

// Build an element: properties set attributes, text, classes, data and
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

function remember(name, value) {
  try {
    sessionStorage.setItem("ehr." + name, String(value));
  } catch {
    // Storage may be refused; the page works without it.
  }
}

function recall(name) {
  try {
    return sessionStorage.getItem("ehr." + name);
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
      "The interface is not answering. It may have stopped.", 0);
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
      throw new Refusal("session_ended", "The session has ended. Open the link again.", 404);
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
      renderCaseSets();
    }
    hideBanner("state");
    if (!state.caseSet && state.caseSets.length) {
      const saved = recall("caseSet");
      const known = state.caseSets.some((item) => item.id === saved);
      await selectCaseSet(known ? saved : state.caseSets[0].id);
    }
  } catch (error) {
    showModel({state: "unreachable", label: "Not connected", detail: error.message});
    showBanner(error.message, "state", refreshState);
  }
  state.timer = setTimeout(refreshState, seconds * 1000);
}

// ---------------------------------------------------------------------------
// Case sets and cases (work item 49)
// ---------------------------------------------------------------------------

function renderCaseSets() {
  ui.caseSet.replaceChildren(...state.caseSets.map((item) =>
    el("option", {value: item.id, title: item.detail}, item.label)));
  if (state.caseSet) ui.caseSet.value = state.caseSet;
}

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
  ui.casesLabel.textContent = state.cases.length ? `Patients · ${state.cases.length}` : "Patients";
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
      state.cases.length ? "No patient matches." : "Loading the patients…"));
  }
}

async function selectCaseSet(id) {
  state.caseSet = id;
  ui.caseSet.value = id;
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
    else showBanner("This cohort has no admitted patient.", "case", null);
  } catch (error) {
    showBanner(error.message, "case", () => selectCaseSet(id));
  }
}

async function selectCase(number) {
  if (number === state.caseNumber && state.current) return;
  // A new case starts a new conversation: nothing carries over (D4).
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
  if (current) {
    ui.caseTitle.textContent = current.name;
    ui.caseDetail.textContent = current.subtitle;
    ui.welcomeTitle.textContent = current.greeting;
    ui.welcomeDetail.textContent = current.introduction;
    document.title = `${current.name} · EHR Evidence`;
  } else {
    ui.caseTitle.textContent = listed ? listed.name : "Loading";
    ui.caseDetail.textContent = listed ? listed.detail : "";
    ui.welcomeTitle.textContent = "Opening the record";
    ui.welcomeDetail.textContent = "";
  }
  const scopes = current ? current.scopes : [];
  ui.scope.replaceChildren(...scopes.map((scope) => el("option", {value: scope.key}, scope.label)));
  if (state.scopeKey) ui.scope.value = state.scopeKey;
  const evidence = current ? current.evidence : [];
  ui.evidenceKey.replaceChildren(...evidence.map((item) =>
    el("option", {value: item.key}, item.label)));
  updateControls();
}

function updateControls() {
  const current = state.current;
  const busy = pending();
  const explainable = Boolean(current && current.prediction);
  ui.question.disabled = !current;
  ui.scope.disabled = !current;
  ui.send.disabled = !current || busy || !ui.question.value.trim();
  ui.send.classList.toggle("is-busy", busy);
  ui.send.setAttribute("aria-label", busy ? "Waiting for the response" : "Ask");
  ui.explain.hidden = Boolean(current) && !explainable;
  ui.explain.disabled = !explainable || busy;
  ui.suggestExplain.hidden = !explainable;
  ui.suggestExplain.disabled = busy;
  for (const chip of ui.suggestions.querySelectorAll("[data-question]")) chip.disabled = !current || busy;
  ui.suggestEvidence.disabled = !current;
  ui.evidenceButton.disabled = !current;
  ui.newConversation.disabled = !state.thread.length;
  ui.main.classList.toggle("is-empty", state.thread.length === 0);
  ui.question.placeholder = state.thread.length
    ? "Ask another question about this case"
    : "Ask which conditions are recorded, or whether one is";
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
    scopeName: scope.name,
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
    asked: "Explain the prediction",
    scopeKey: null,
    scopeName: null,
    evidenceKey: "prediction",
    path: "/api/explanations",
    body: {case_set: state.current.case_set, case_number: state.current.number},
  });
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
  ui.announce.textContent = `${result.outcome.label}. The response is ready.`;
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
    item.append(el("p", {className: "earlier-label"},
      `Asked about ${turn.scopeName}: an earlier scope, not the current one`));
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
    el("span", {className: "thinking-text"},
      turn.kind === "explanation" ? "Writing the explanation" : "Writing the response"),
    el("span", {className: "elapsed"}, `${seconds} s`));
}

function failure(turn) {
  return el("div", {className: "failure", role: "alert"},
    icon("alert"),
    el("p", {}, turn.error),
    el("button", {type: "button", className: "text-button", onclick: () => retry(turn)}, "Try again"));
}

function badge(outcome) {
  return el("span", {className: `badge tone-${outcome.tone}`}, outcome.label);
}

function cite(ref, turn) {
  return el("button", {
    type: "button",
    className: "cite",
    title: `Open ${ref} in the evidence`,
    "aria-label": `Open reference ${ref} in the evidence`,
    onclick: () => openPanel(turn.evidenceKey, ref),
  }, ref);
}

// The text line by line: paragraphs, and lists nested by depth, each line
// followed by the references of the claim it states.
function prose(lines, turn) {
  const body = el("div", {className: "prose"});
  let lists = [];
  for (const line of lines) {
    const content = [line.text, ...line.refs.map((ref) => cite(ref, turn))];
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
    lists[lists.length - 1].append(el("li", {}, ...content));
  }
  return body;
}

function predictionCard(prediction) {
  return el("div", {className: "prediction"},
    el("span", {className: "kicker"}, "Prepared prediction"),
    el("div", {className: "prediction-label"}, prediction.label),
    el("dl", {className: "numbers"}, ...prediction.facts.map((fact) =>
      el("div", {}, el("dt", {}, fact.label), el("dd", {}, fact.value)))),
    el("p", {className: "fine"}, `${prediction.meaning} ${prediction.note}`));
}

function note(tone, text) {
  return el("p", {className: `note tone-${tone}`}, text);
}

function conditionsBlock(conditions) {
  const body = el("div", {className: "block-body"}, el("p", {className: "fine"}, conditions.note));
  if (conditions.table) body.append(tableNode(conditions.table));
  return el("details", {className: "block"},
    el("summary", {}, conditions.title, icon("chevron")),
    body);
}

function copyButton(text) {
  if (!text || !navigator.clipboard) return null;
  const button = el("button", {
    type: "button",
    className: "icon-button",
    title: "Copy the response",
    "aria-label": "Copy the response",
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
  if (turn.kind === "explanation" && view.prediction) node.append(predictionCard(view.prediction));
  if (view.lines.length) node.append(prose(view.lines, turn));
  if (view.withheld) node.append(note("muted", view.withheld));
  if (turn.kind === "explanation" && view.unsigned) node.append(note("info", view.unsigned));
  if (view.references.length) {
    node.append(el("div", {className: "sources"}, "References",
      ...view.references.map((ref) => cite(ref, turn))));
  }
  node.append(el("div", {className: "meta"},
    copyButton(view.text),
    el("span", {className: "meta-text"},
      `${view.scope.label} · ${view.author} · ${view.seconds}`)));
  if (turn.kind === "explanation" && view.conditions) node.append(conditionsBlock(view.conditions));
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
  ui.evidenceKey.value = state.panel.key;
  ui.panelBody.replaceChildren(el("div", {className: "skeleton", "aria-label": "Loading the evidence"},
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
        "Try again")));
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
  ui.caseSet.addEventListener("change", () => selectCaseSet(ui.caseSet.value));
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
  ui.evidenceKey.addEventListener("change", () => openPanel(ui.evidenceKey.value, null));
  ui.scope.addEventListener("change", () => {
    state.scopeKey = ui.scope.value;
    renderThread();
  });
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
    if (event.key !== "Escape") return;
    if (ui.app.classList.contains("sidebar-open")) closeSidebar();
    else if (state.panel.open) closePanel();
  });
  setInterval(tick, 1000);
}

bind();
renderCases();
refreshState();
