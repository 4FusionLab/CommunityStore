"use strict";

const find = (id) => document.getElementById(id);
const dialog = find("confirm");
let tab = "installed";
const searchTerms = {installed: "", catalog: ""};
let state;
let submitting = false;
let stateRefresh;
let lastCommand;
const destructiveCommands = new Set(["applyBatch", "install", "remove", "selfUpdate"]);
const queuedDestructive = new Map();
let confirmation;
const fingerprints = {};
const installedRowFingerprints = new WeakMap();
let installedPending;
const selected = new Set();
let activePreview;
let requestingPreview = false;
let pendingPreferences;
let retryTimer;
let noticeText = "";
let noticeTimer;
let noticeExitTimer;
let lastSourceNotice = "";
const guideSteps = ["discover", "manage", "github"];
let guideStep = 0;
let guideOpen = false;
let guideSavePending = false;

function t(key, values = {}) {
  return state.translations[key].replace(/\{(\w+)\}/g, (_, name) => values[name]);
}

function dismissNotice() {
  clearTimeout(noticeTimer);
  if (find("notice").hidden || find("notice").classList.contains("leaving")) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    find("notice").hidden = true;
    return;
  }
  find("notice").classList.add("leaving");
  noticeExitTimer = setTimeout(() => { find("notice").hidden = true; }, 180);
}

function showNotice(text) {
  if (text === noticeText) return;
  clearTimeout(noticeTimer);
  clearTimeout(noticeExitTimer);
  noticeText = text;
  const notice = find("notice");
  notice.classList.remove("leaving");
  find("notice-message").textContent = text;
  notice.hidden = !text;
  if (text) scheduleNoticeDismiss();
}

function scheduleNoticeDismiss() {
  const notice = find("notice");
  if (!notice.hidden && !notice.matches(":hover, :focus-within")) {
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(dismissNotice, 6500);
  }
}

function localize() {
  document.documentElement.lang = state.language;
  const russian = state.language === "ru";
  const repository = "https://github.com/4FusionLab/CommunityStore";
  find("publisher-guide").href = repository + "/blob/main/docs/" + (russian ? "PUBLISHER_GUIDE.ru.md" : "PUBLISHER_GUIDE.md");
  find("request-source").href = repository + "/issues/new?template=" + (russian ? "request-source-ru.yml" : "request-source.yml");
  for (const element of document.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const attribute of ["aria-label", "placeholder"]) {
    const marker = "data-i18n-" + attribute;
    for (const element of document.querySelectorAll(`[${marker}]`)) {
      element.setAttribute(attribute, t(element.getAttribute(marker)));
    }
  }
}

function renderGuide() {
  const step = guideSteps[guideStep];
  find("guide-title").textContent = t(`guide_${step}_title`);
  find("guide-copy").textContent = t(`guide_${step}_copy`);
  find("guide-progress").textContent = t("guide_progress", {step: guideStep + 1, total: guideSteps.length});
  const accountNote = find("guide-account-note");
  accountNote.textContent = step === "github"
    ? t(state.github?.configured ? "guide_github_available" : "guide_github_unavailable")
    : "";
  accountNote.hidden = !accountNote.textContent;
  find("guide-open-market").hidden = guideStep !== 0;
  find("guide-back").hidden = guideStep === 0;
  find("guide-next").textContent = t(guideStep + 1 === guideSteps.length ? "guide_open_market" : "guide_next");
}

function syncGuide() {
  find("store-guide").hidden = !guideOpen || Boolean(detail || document.querySelector("dialog[open]"));
  find("guide-replay").hidden = guideOpen;
}

function showPaletteDialog(dialog) {
  dialog.showModal();
  syncGuide();
}

for (const modal of document.querySelectorAll("dialog")) modal.addEventListener("close", syncGuide);

function closeGuide(openMarket = false) {
  guideOpen = false;
  syncGuide();
  if (openMarket) {
    const marketplace = document.querySelector('[data-tab="catalog"]');
    marketplace.click();
    marketplace.focus();
  } else {
    const replay = find("guide-replay");
    (find("settings").hidden ? document.querySelector('[data-tab][aria-pressed="true"]') : replay).focus();
  }
  if (state.settings.guideSeen !== true) {
    guideSavePending = true;
    saveGuide();
  }
}

async function saveGuide() {
  if (submitting) return;
  guideSavePending = false;
  if (state.settings.guideSeen === true) return;
  const received = await send("dismissGuide");
  if (state.settings.guideSeen !== true && (!received || !state.notice)) showNotice(t("guide_save_failed"));
}

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content) element.textContent = content;
  return element;
}

function button(label, className, handler, disabled = false) {
  const control = node("button", className, label);
  control.type = "button";
  control.disabled = disabled;
  control.addEventListener("click", handler);
  return control;
}

function icon(name) {
  const glyph = node("i", "icon " + name);
  glyph.setAttribute("aria-hidden", "true");
  return glyph;
}

function toggle(label, value, handler, disabled) {
  const input = node("input");
  input.type = "checkbox";
  input.checked = value === true;
  input.indeterminate = value === null;
  input.disabled = disabled;
  input.setAttribute("aria-label", label);
  input.addEventListener("change", () => handler(input.checked));
  return input;
}

function ask(title, text, label, action, payload, assets = []) {
  confirmation = {action, payload, detailEntry: detail?.entry, focusKey: document.activeElement.dataset.detailFocus};
  find("confirm-title").textContent = title;
  find("confirm-text").textContent = text;
  const apply = find("confirm-action");
  apply.textContent = label;
  apply.classList.toggle("destructive", action === "remove");
  apply.disabled = assets.length > 0;
  const field = find("packages");
  field.hidden = !assets.length;
  field.querySelectorAll("label").forEach((element) => element.remove());
  for (const asset of assets) {
    const option = node("label", asset.compatible ? "" : "unavailable");
    const radio = node("input");
    radio.type = "radio";
    radio.name = "asset";
    radio.value = asset.name;
    radio.disabled = !asset.compatible;
    radio.addEventListener("change", () => { apply.disabled = false; });
    option.append(radio, node("span", "", asset.compatible ? asset.name : t("asset_unavailable", {name: asset.name})));
    field.append(option);
  }
  dialog.returnValue = "cancel";
  showPaletteDialog(dialog);
  dialog.querySelector(".confirm-copy").scrollTop = 0;
  if (assets.length) find("confirm-title").focus({preventScroll: true});
}

dialog.addEventListener("close", () => {
  if (dialog.returnValue === "apply" && confirmation) {
    const selected = new FormData(find("confirm-form")).get("asset");
    send(confirmation.action, {...confirmation.payload, ...(selected ? {asset: selected} : {})});
  }
  if (confirmation?.focusKey && confirmation.detailEntry === detail?.entry) {
    focusDetailControl(confirmation.focusKey);
  }
  confirmation = undefined;
});

let openActions;

const paletteContent = document.querySelector("main");
let visibleHint;
let hintId = 0;

function closeHint() {
  visibleHint?.hide();
}

function describeControl(control, container, lines, supplemental = true) {
  const hint = node("span", "control-hint");
  hint.id = `control-hint-${++hintId}`;
  hint.setAttribute("role", "tooltip");
  hint.hidden = true;
  hint.append(...lines.filter(Boolean).map((line) => node("span", "", line)));
  container.append(hint);
  if (supplemental) control.setAttribute("aria-describedby", hint.id);
  else hint.setAttribute("aria-hidden", "true");

  let timer;
  let keyboardFocus = false;
  const popup = {hide, position};
  function hide() {
    clearTimeout(timer);
    hint.hidden = true;
    if (hint.parentNode !== container) container.append(hint);
    if (visibleHint === popup) visibleHint = undefined;
  }
  function position() {
    const anchor = control.getBoundingClientRect();
    const content = paletteContent.getBoundingClientRect();
    const {offsetWidth, offsetHeight} = hint;
    const edge = 8;
    const gap = 4;
    const below = anchor.bottom + gap;
    const above = anchor.top - gap - offsetHeight;
    // Keep the hint within the scrollport when progress or status appears below it.
    if (anchor.bottom <= content.top || anchor.top >= content.bottom) return hide();
    const top = Math.max(below, content.top + edge);
    const bottom = content.bottom - edge;
    let y = top;
    if (top + offsetHeight > bottom) {
      y = Math.min(above, bottom - offsetHeight);
      if (y < content.top + edge) return hide();
    }
    const {clientWidth} = document.documentElement;
    hint.style.left = Math.max(edge, Math.min(anchor.left, clientWidth - offsetWidth - edge)) + "px";
    hint.style.top = y + "px";
  }
  function show() {
    if (!control.isConnected || openActions || document.querySelector("dialog[open]")) return;
    if (visibleHint !== popup) closeHint();
    visibleHint = popup;
    if (hint.parentNode !== document.body) document.body.append(hint);
    hint.hidden = false;
    position();
  }
  function leave() {
    clearTimeout(timer);
    if (!keyboardFocus) timer = setTimeout(hide, 160);
  }
  control.addEventListener("pointerenter", (event) => {
    if (event.pointerType === "touch") return;
    clearTimeout(timer);
    timer = setTimeout(show, 360);
  });
  control.addEventListener("pointerleave", leave);
  hint.addEventListener("pointerenter", () => clearTimeout(timer));
  hint.addEventListener("pointerleave", leave);
  control.addEventListener("focus", () => {
    keyboardFocus = control.matches(":focus-visible");
    if (keyboardFocus) show();
  });
  control.addEventListener("blur", () => {
    keyboardFocus = false;
    hide();
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && visibleHint) closeHint();
});
document.addEventListener("pointerdown", closeHint);
window.addEventListener("resize", closeHint);
paletteContent.addEventListener("scroll", closeHint);
new ResizeObserver(() => visibleHint?.position()).observe(paletteContent);

function closeRowActions(restoreFocus = false) {
  if (!openActions) return;
  const {trigger, menu} = openActions;
  openActions = undefined;
  trigger.setAttribute("aria-expanded", "false");
  menu.hidden = true;
  if (restoreFocus) trigger.focus({preventScroll: true});
}

function applyVersion(row) {
  if (row.self) send("selfUpdate", {key: row.key, tag: row.tag});
  else preview("single", [row.key]);
}

function rowMenuActions(menu) {
  return [...menu.querySelectorAll("button:not(:disabled)")]
    .filter((item) => getComputedStyle(item).display !== "none");
}

function addinMenu(row, locked, container, inDetails = false) {
  const payload = {key: row.key};
  const menu = node("div", inDetails ? "row-menu detail-menu" : "row-menu");
  menu.hidden = true;
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", t("actions_for_addin", {name: row.name}));
  function addAction(label, handler, className = "", disabled) {
    const action = button(label, className, () => {
      closeRowActions(true);
      handler();
    }, disabled);
    action.setAttribute("role", "menuitem");
    action.tabIndex = -1;
    // Actions with no disabled argument remain available during an update.
    if (!inDetails && disabled === false) action.dataset.operationControl = "";
    menu.append(action);
    return action;
  }
  if (row.canUpdate && !inDetails) addAction(t("update_to", {tag: row.tag}), () => applyVersion(row), "", locked);
  if (!row.self) {
    addAction(t(row.running ? "stop_addin" : "start_addin"),
      () => send(row.running ? "disable" : "enable", payload), "compact-action", locked);
  }
  addAction(t(row.startup ? "disable_startup" : "enable_startup"),
    () => send("startup", {...payload, enabled: !row.startup}), "compact-action", locked);
  if (row.sourceType === "folder") addAction(t("open_folder"), () => send("openFolder", payload));
  if (row.releaseRepo) addAction(t("show_release"), () => send("release", payload));
  addAction(t("reload_addin"), () => send("reload", payload), "", locked);
  if (!row.self) {
    const action = row.external ? "unlink" : row.sourceType === "app_store" ? "hide_addin" : "remove";
    addAction(t(action), () => ask(t(`${action}_confirm`, {name: row.name}),
      t(`${action}_description`), t(action), "remove", {...payload, confirmed: true}),
      "destructive", locked || !row.canRemove
    ).title = !row.canRemove ? t("remove_native") : "";
  }
  const actions = () => rowMenuActions(menu);
  function focusAction(item) {
    if (!item) return;
    item.focus({preventScroll: true});
    const top = item.offsetTop;
    const bottom = top + item.offsetHeight;
    if (top < menu.scrollTop) menu.scrollTop = top;
    else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight;
  }
  function show(last = false) {
    closeHint();
    closeRowActions();
    openActions = {trigger, menu, scroll: document.querySelector("main").scrollTop};
    trigger.setAttribute("aria-expanded", "true");
    menu.hidden = false;
    const anchor = trigger.getBoundingClientRect();
    const {width, height} = menu.getBoundingClientRect();
    const viewport = document.documentElement;
    const edge = 8;
    const gap = 4;
    const below = anchor.bottom + gap;
    const above = anchor.top - gap - height;
    const bottom = viewport.clientHeight - height - edge;
    const overlapsAnchor = below > bottom && above < edge;
    const top = below <= bottom ? below : above >= edge ? above : bottom;
    const side = anchor.left - width - gap;
    const left = overlapsAnchor && side >= edge ? side : anchor.right - width;
    menu.style.left = Math.max(edge, Math.min(left, viewport.clientWidth - width - edge)) + "px";
    menu.style.top = Math.max(edge, top) + "px";
    const items = actions();
    focusAction(items[last ? items.length - 1 : 0]);
  }
  const trigger = button("⋯", "row-menu-trigger", () =>
    openActions?.trigger === trigger ? closeRowActions(true) : show(), !actions().length);
  trigger.dataset.focus = "actions";
  trigger.setAttribute("aria-label", menu.getAttribute("aria-label"));
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    show(event.key === "ArrowUp");
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      closeRowActions(true);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeRowActions(true);
      return;
    }
    const items = actions();
    const index = items.indexOf(document.activeElement);
    const next = {ArrowDown: (index + 1) % items.length, ArrowUp: (index + items.length - 1) % items.length,
      Home: 0, End: items.length - 1}[event.key];
    if (next === undefined) return;
    event.preventDefault();
    focusAction(items[next]);
  });
  container.append(trigger, menu);
  describeControl(trigger, container, [t("row_actions")], false);
  return trigger;
}

document.addEventListener("pointerdown", (event) => {
  if (openActions && !openActions.menu.contains(event.target) && !openActions.trigger.contains(event.target)) {
    closeRowActions();
  }
});
window.addEventListener("resize", () => closeRowActions(openActions?.menu.contains(document.activeElement)));
document.querySelector("main").addEventListener("scroll", (event) => {
  if (openActions && event.currentTarget.scrollTop !== openActions.scroll) {
    closeRowActions(openActions.menu.contains(document.activeElement));
  }
});

function targetSelector(row, locked, container) {
  const edit = node("select", "target-control");
  edit.disabled = locked;
  edit.setAttribute("aria-label", t("action_for_addin", {action: t("target_version"), name: row.name}));
  edit.dataset.focus = "target";
  const tags = new Set(["Latest", ...row.targets, row.target]);
  for (const tag of tags) {
    const unavailable = tag !== "Latest" && !row.targets.includes(tag);
    const option = node("option", "", tag === "Latest" ? t("latest") :
      unavailable ? t("target_unavailable", {tag}) : tag);
    option.value = tag;
    option.disabled = unavailable;
    edit.append(option);
  }
  edit.value = row.target;
  edit.addEventListener("change", async () => {
    const target = edit.value;
    await send("target", {key: row.key, target});
    if (edit.isConnected && edit.value === target) {
      edit.value = state.installed.find((item) => item.key === row.key)?.target ?? row.target;
    }
  });
  container.append(edit);
  const rateLimit = row.repo && rateLimitStatus();
  const status = row.releaseError || (!row.historyLoaded && (row.releaseRepo || row.repo) &&
    (state.checking ? t("checking_releases") : row.repo && !rateLimit && t("history_unavailable")));
  if (rateLimit || status || row.reason) {
    describeControl(edit, container, [rateLimit, rateLimit && row.targets.length ? t("cached_versions") : "",
      status || row.reason]);
  }
  return edit;
}

function installedRow(row) {
  const tr = node("tr");
  tr.setAttribute("role", "row");
  tr.dataset.row = row.key;
  const cell = (name, label, heading = false) => {
    const element = node(heading ? "th" : "td", name + "-cell");
    element.setAttribute("role", heading ? "rowheader" : "cell");
    if (heading) element.scope = "row";
    if (label) {
      const caption = node("span", "compact-caption", t(label));
      caption.setAttribute("aria-hidden", "true");
      element.append(caption);
    }
    return element;
  };
  const addToggle = (cell, control) => {
    const hit = node("label", "toggle-target");
    hit.append(...cell.children, control);
    cell.append(hit);
  };
  const identity = cell("identity", null, true);
  const name = node("button", "name", row.name);
  name.type = "button";
  name.dataset.focus = "name";
  detailButton(name, "installed", row.key, row.name);
  const entry = state.catalog.find((item) => item.repo === row.repo);
  const badge = imageSlot("addin-icon", "package", entry ? artworkFor(entry, "card") : null, ["icon"],
    row.self ? "assets/open-catalog.png" : "");
  const title = node("div", "installed-identity");
  const copy = node("div", "identity-copy");
  copy.append(name);
  const source = t(`source_${row.sourceType}`);
  const owner = row.releaseRepo?.split("/")[0];
  const sourceOwner = owner || (row.sourceType === "community_store" ? t("github_source_unknown") : "");
  const sourceText = owner ? t("addin_source_github", {source, owner}) : t("addin_source", {source});
  const author = row.author?.trim();
  const sameOwner = owner && author?.toLocaleLowerCase() === owner.toLocaleLowerCase();
  const authorText = author && !sameOwner ? t("addin_author", {author}) : "";
  const sourceCell = cell("source");
  sourceCell.append(node("span", "", source));
  if (sourceOwner) sourceCell.append(node("span", "source-owner", sourceOwner));
  const authorCell = cell("author");
  authorCell.textContent = author || "—";
  const credits = node("div", "installed-credits");
  if (sourceOwner) credits.append(node("span", "", t("addin_publisher", {publisher: sourceOwner})));
  if (author) credits.append(node("span", "", t("addin_author", {author})));
  if (credits.childElementCount) copy.append(credits);
  const duplicate = !row.direction && row.duplicate;
  describeControl(name, copy, [sourceText, !owner && sourceOwner, authorText, duplicate]);
  if (duplicate) copy.append(node("span", "row-warning", t("duplicate")));
  title.append(badge, copy);
  identity.append(title);

  const payload = {key: row.key};
  const locked = Boolean(row.busy);
  const selection = cell("selection");
  const select = toggle(t("select_addin", {name: row.name, folder: row.folder}), selected.has(row.key), (checked) => {
    if (checked) selected.add(row.key);
    else selected.delete(row.key);
    tr.classList.toggle("selected", checked);
    updateBatchButtons();
  }, locked || row.self);
  select.dataset.focus = "select";
  tr.classList.toggle("selected", selected.has(row.key));
  addToggle(selection, select);

  const current = cell("current", "installed_short");
  const versions = node("div", "current-versions");
  versions.append(node("span", "version", row.version || "—"));
  current.append(versions);
  let apply;
  if (row.direction) {
    const downgrade = row.direction === "downgrade";
    const label = t(downgrade ? "downgrade_to" : "update_to", {tag: row.tag});
    apply = button((downgrade ? "↓ " : "↑ ") + row.tag, "version-update", () => applyVersion(row), locked);
    apply.toggleAttribute("data-downgrade", downgrade);
    apply.setAttribute("aria-label", t("action_for_addin", {action: label, name: row.name}));
    apply.dataset.focus = "update";
    versions.append(apply);
    if (!locked) describeControl(apply, current, [label], false);
  }
  if (row.newerTag && (!row.update || row.newerTag !== row.tag)) {
    const latest = node("span", "new-release", t("new_release_tag", {tag: row.newerTag}));
    current.append(latest);
  }
  const target = cell("target", "target_short");
  const edit = row.self ? null : targetSelector(row, locked, target);
  if (!edit) target.append(node("span", "version", "—"));

  const run = cell("running", "running");
  const startup = cell("startup", "startup_short");
  const running = toggle(t("running_addin", {name: row.name}), row.running,
    (enabled) => send(enabled ? "enable" : "disable", payload), locked || row.self);
  running.className = "run-toggle";
  running.dataset.focus = "running";
  addToggle(run, running);
  const auto = toggle(t("startup_addin", {name: row.name}), row.startup,
    (enabled) => send("startup", {...payload, enabled}), locked);
  auto.dataset.focus = "startup";
  addToggle(startup, auto);
  const actions = cell("actions");
  addinMenu(row, locked, actions);
  for (const control of [select, apply, edit, running, auto]) {
    if (control && !control.disabled) control.dataset.operationControl = "";
  }
  tr.append(selection, identity, sourceCell, authorCell, current, target, run, startup, actions);
  return tr;
}

function requestPackage(row) {
  const payload = {repo: row.repo};
  if (row.assets.length === 1) {
    send("install", {...payload, asset: row.assets[0].name});
    return;
  }
  const description = [
    t("select_release_file"), t("install_repository", {repo: row.repo}), t("install_effects"),
  ].join(" ");
  ask(`${row.name} ${row.tag}`, description,
    t("install"), "install", payload, row.assets);
}

function marketRow(row) {
  const article = node("article", "market-row");
  const control = node("button", "market-card");
  control.type = "button";
  detailButton(control, "catalog", row.repo, row.name);
  const art = imageSlot("market-art", "image", artworkFor(row, "card"), ["shot0", "icon"]);
  const copy = node("span", "card-copy");
  const title = node("span", "card-title");
  title.append(node("strong", "", row.name), stars(row));
  copy.append(title);
  const description = summary(row.description);
  const blurb = description.length > 220 ? description.slice(0, 217).replace(/\s+\S*$/, "") + "…" : description;
  if (blurb) copy.append(node("span", "card-description", blurb));
  const status = row.updates.length ? t("update_available") : row.matches.length ? t("installed") : "";
  const release = row.prerelease ? t("prerelease", {tag: row.tag}) : row.tag;
  const meta = node("span", "card-meta");
  meta.append(node("span", "", t("addin_publisher", {publisher: row.repo.split("/")[0]})));
  const author = row.author?.trim();
  if (author) meta.append(node("span", "", t("addin_author", {author})));
  meta.append(node("span", "version", release));
  if (status) meta.append(node("span", row.updates.length ? "available" : "installed-label", status));
  copy.append(meta);
  control.append(art, copy, icon("chevron-right"));
  article.append(control);
  return article;
}

function updateBatchButtons() {
  const selection = find("update-selected");
  selection.disabled = state.pending || !selected.size;
  selection.toggleAttribute("data-has-selection", Boolean(selected.size));
  const suffix = selected.size ? ` (${selected.size.toLocaleString(state.language)})` : "";
  selection.textContent = t("update_selected") + suffix;
  find("update-all").disabled = state.pending || !state.installed.length;
  find("register-local").disabled = state.pending;
}

function rateLimitStatus() {
  const retry = new Date(state.retryAt * 1000);
  if (!(retry > Date.now())) return "";
  const time = retry.toLocaleString(state.language, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  return t("rate_limit_retry_at", {time});
}

async function preview(mode, keys = []) {
  if (requestingPreview || find("batch-preview").open) return;
  requestingPreview = true;
  const token = state.preview?.token;
  let received;
  try {
    received = await send("preview", {mode, keys});
  } finally {
    requestingPreview = false;
  }
  if (!received || !state.preview || state.preview.token === token || document.querySelector("dialog[open]")) return;
  activePreview = state.preview;
  const rows = find("batch-rows");
  rows.replaceChildren();
  for (const item of activePreview.rows) {
    const entry = node("li");
    const transition = item.tag ? ` → ${item.tag}` : "";
    const heading = node("div", "batch-item-title");
    heading.append(node("strong", "", item.name), node("span", "version", (item.version || "—") + transition));
    if (!item.direction || item.direction === "downgrade") {
      heading.append(node("span", "batch-item-status " + (item.direction || ""), t(item.direction || "skipped")));
    }
    entry.append(heading);
    if (item.reason) entry.append(node("span", "meta", item.reason));
    if (item.direction && item.fromFolder) entry.append(node("span", "meta", t("folder_update_preserves_files")));
    if (item.assets?.length > 1) {
      const label = node("label", "", "ZIP ");
      const select = node("select");
      select.dataset.key = item.key;
      select.setAttribute("aria-label", t("action_for_addin", {action: t("release_zip"), name: item.name}));
      select.required = true;
      const placeholder = node("option", "", t("choose_package"));
      placeholder.value = "";
      select.append(placeholder);
      for (const asset of item.assets) {
        const option = node("option", "", asset.compatible ? asset.name : t("asset_unavailable", {name: asset.name}));
        option.value = asset.name;
        option.disabled = !asset.compatible;
        select.append(option);
      }
      label.append(select);
      entry.append(label);
    } else if (item.assets?.length === 1) {
      entry.append(node("span", "meta", item.assets[0].name));
    }
    rows.append(entry);
  }
  const single = mode === "single" ? activePreview.rows[0] : null;
  find("batch-title").textContent = single?.direction && single.tag
    ? t(single.direction === "downgrade" ? "downgrade_to" : "update_to", {tag: single.tag})
    : t({all: "update_all", selected: "update_selected", single: "apply_target"}[mode]);
  const apply = find("batch-apply");
  const count = activePreview.rows.filter((item) => item.direction).length;
  const skipped = activePreview.rows.length - count;
  find("batch-summary").textContent = count
    ? [t("batch_ready_count", {count}), skipped ? t("batch_skipped_count", {count: skipped}) : ""].filter(Boolean).join(" · ")
    : t(activePreview.rows.length ? "batch_no_updates" : "batch_empty");
  find("batch-cancel").textContent = t(count ? "cancel" : "close");
  apply.hidden = !count;
  apply.textContent = activePreview.rows.some((item) => item.direction === "downgrade")
    ? t("confirm_downgrade") : t("update");
  showPaletteDialog(find("batch-preview"));
  rows.tabIndex = rows.scrollHeight > rows.clientHeight ? 0 : -1;
  find("batch-title").focus({preventScroll: true});
}

function renderOperationProgress() {
  const target = find("operation-progress");
  const tasks = state.progress || [];
  target.hidden = !tasks.length;
  while (target.childElementCount > tasks.length) target.lastElementChild.remove();
  const size = new Intl.NumberFormat(state.language, {style: "unit", unit: "megabyte", maximumFractionDigits: 1});
  return tasks.map((task, index) => {
    let group = target.children[index];
    if (!group) {
      group = node("div", "operation");
      const status = node("p", "operation-status");
      status.append(node("span"), node("span", "operation-size"));
      group.append(node("p", "operation-heading"), status, node("progress", "operation-meter"));
      target.append(group);
    }
    const name = `${task.name} · ${task.tag}`;
    const title = task.count > 1 ? t("operation_item", {index: task.index, count: task.count, name}) : name;
    const phase = t(`operation_${task.phase}`);
    const [heading, status, meter] = group.children;
    const [phaseText, bytes] = status.children;
    if (heading.textContent !== title) heading.textContent = title;
    if (phaseText.textContent !== phase) phaseText.textContent = phase;
    const label = `${title} · ${phase}`;
    if (meter.getAttribute("aria-label") !== label) meter.setAttribute("aria-label", label);
    const downloaded = task.downloaded || 0;
    const total = task.total || 0;
    if (task.phase === "downloading" && total) {
      if (meter.max !== total) meter.max = total;
      const value = Math.min(downloaded, total);
      if (!meter.hasAttribute("value") || meter.value !== value) meter.value = value;
    } else {
      meter.removeAttribute("value");
    }
    const current = size.format(downloaded / 1e6);
    const amount = task.phase !== "downloading" ? "" :
      total ? `${current} / ${size.format(total / 1e6)}` : downloaded ? current : "";
    if (bytes.textContent !== amount) bytes.textContent = amount;
    bytes.hidden = !amount;
    return label;
  });
}

function renderBatchResults() {
  const results = find("batch-results");
  const count = state.batchResults.length;
  results.hidden = !count;
  if (!count) {
    results.open = false;
    return "";
  }
  const failed = state.batchResults.filter((item) => item.status === "failed").length;
  const countLabel = failed ? t("batch_results_failed", {failed, count}) : String(count);
  const counter = find("batch-result-count");
  counter.textContent = countLabel;
  counter.toggleAttribute("data-has-errors", Boolean(failed));
  replaceList("batch-results", find("batch-result-list"), state.batchResults, (item) => {
    const transition = item.tag ? ` → ${item.tag}` : "";
    const row = node("li");
    row.append(node("span", item.status, `${item.name} · ${item.version || "—"}${transition} · ${t(item.status)}`),
      node("span", "meta", item.message));
    return row;
  });
  return `${t("batch_results")} · ${countLabel}`;
}

function installedScrollAnchor(target) {
  const {top, bottom} = paletteContent.getBoundingClientRect();
  const contentTop = top + target.parentElement.tHead.offsetHeight;
  let partial;
  for (const row of target.rows) {
    const rect = row.getBoundingClientRect();
    if (rect.top >= bottom) break;
    if (rect.top >= contentTop) return {key: row.dataset.row, top: rect.top};
    if (rect.bottom > contentTop) partial = {key: row.dataset.row, top: rect.top};
  }
  return partial;
}

function replaceList(key, target, data, build, empty) {
  const retryAt = state.retryAt * 1000 > Date.now() ? state.retryAt : 0;
  const fingerprint = JSON.stringify([data, state.language, state.pending, state.checking, retryAt, empty]);
  if (fingerprint === fingerprints[key]) return;
  fingerprints[key] = fingerprint;
  const active = document.activeElement;
  const previous = target.contains(active) ? active : null;
  const row = previous?.closest("[data-row]");
  const control = previous?.dataset.focus || (row && openActions?.menu.contains(previous) ? "actions" : "");
  const label = previous?.getAttribute("aria-label");
  const modal = document.querySelector("dialog[open]");
  const restore = previous && (!modal || modal.contains(target));
  if (key === "installed") {
    closeHint();
    const existing = new Map([...target.rows].map((row) => [row.dataset.row, row]));
    const pending = Boolean(state.pending);
    const anchor = (pending || installedPending) && paletteContent.scrollTop
      ? installedScrollAnchor(target) : null;
    const pendingChanged = installedPending !== pending;
    installedPending = pending;
    const rows = data.map((row) => {
      const before = existing.get(row.key);
      const signature = JSON.stringify([row, state.language,
        !row.historyLoaded && state.checking, row.repo && retryAt]);
      if (installedRowFingerprints.get(before) === signature) return before;
      const after = build(row);
      installedRowFingerprints.set(after, signature);
      return after;
    });
    const retained = new Set(rows);
    if (openActions && (pendingChanged || !retained.has(openActions.trigger.closest("[data-row]")))) closeRowActions();
    for (const row of existing.values()) if (!retained.has(row)) row.remove();
    rows.forEach((row, index) => {
      const atIndex = target.children[index];
      if (atIndex !== row) target.insertBefore(row, atIndex || null);
      if (pendingChanged || row !== existing.get(row.dataset.row)) {
        for (const control of row.querySelectorAll("[data-operation-control]")) control.disabled = pending;
        row.querySelector(".row-menu-trigger").disabled = !rowMenuActions(row.querySelector(".row-menu")).length;
      }
    });
    if (anchor) {
      const row = rows.find((entry) => entry.dataset.row === anchor.key);
      if (row) paletteContent.scrollTop += row.getBoundingClientRect().top - anchor.top;
    }
    if (previous?.isConnected && !previous.closest("[hidden], :disabled")) return;
  } else {
    target.replaceChildren(...data.map(build));
  }
  if (!data.length && empty) target.append(node("div", "empty"));
  if (!restore) return;
  const replacement = row && [...target.rows].find((entry) => entry.dataset.row === row.dataset.row);
  const entry = previous.dataset.entry;
  const next = replacement
    ? replacement.querySelector(`[data-focus="${control}"]:not(:disabled)`) || replacement.querySelector(".name")
    : [...target.querySelectorAll("[aria-label]")].find((control) =>
      entry ? control.dataset.entry === entry : control.getAttribute("aria-label") === label);
  (next || (key === "sources" ? find("source-url") : row || entry ? find("search") : null))
    ?.focus({preventScroll: true});
}

function settings() {
  const focused = document.activeElement;
  find("language").value = state.settings.language;
  const preferences = pendingPreferences || state.settings;
  find("automatic").checked = preferences.automatic;
  find("interval").value = preferences.interval;
  find("interval").disabled = !preferences.automatic;
  const github = state.github;
  const profile = github.profile;
  const connecting = github.connecting;
  const deviceCode = connecting ? github.code : "";
  const status = deviceCode ? "github_enter_code"
    : connecting ? "github_connecting"
    : github.connected ? (profile ? ""
      : github.profileLoading ? "github_profile_loading" : "github_profile_unavailable")
    : github.configured ? "github_anonymous" : "github_unconfigured";
  find("github-status").textContent = status ? t(status) : "";
  const account = find("github-account");
  const identity = JSON.stringify(profile ?? null);
  if (account.dataset.identity !== identity) {
    account.dataset.identity = identity;
    account.hidden = !profile;
    find("github-name").textContent = profile?.name || "";
    find("github-name").hidden = !profile?.name;
    find("github-login").textContent = profile ? '@' + profile.login : "";
    const avatar = profile ? imageSlot("github-avatar",
      node("span", "", profile.login[0].toUpperCase()),
      profile.avatar ? artworkFor(profile, "githubAvatar") : null, ["avatar"]) : null;
    find("github-avatar").replaceChildren(...(avatar ? [avatar] : []));
  }
  find("github-error").textContent = github.error;
  find("github-error").hidden = !github.error;
  find("github-code").textContent = deviceCode;
  find("github-device").hidden = !deviceCode;
  find("github-copy-feedback").textContent = deviceCode ? github.copyFeedback : "";
  find("github-copy-feedback").hidden = !deviceCode || !github.copyFeedback;
  find("github-copy").hidden = !deviceCode;
  find("github-sign-in").hidden = !github.configured || github.connected || connecting;
  find("github-open-device").hidden = !deviceCode;
  find("github-cancel").hidden = !connecting;
  find("github-profile").hidden = !github.connected || connecting || !!profile || github.profileLoading;
  const signOut = find("github-sign-out");
  signOut.hidden = !github.connected || connecting;
  const signOutContainer = profile ? account : find("github-actions");
  if (signOut.parentElement !== signOutContainer) {
    signOutContainer.append(signOut);
    if (focused === signOut && !signOut.hidden) signOut.focus({preventScroll: true});
  }
  if (!find("settings").hidden && focused?.closest(".github-settings") &&
      focused.matches("button") && focused.closest("[hidden]")) {
    const next = ["github-copy", "github-cancel", "github-sign-in", "github-profile", "github-sign-out"]
      .map(find).find((control) => !control.hidden && !control.disabled);
    next?.focus({preventScroll: true});
  }
}

function openSources() {
  showPaletteDialog(find("source-dialog"));
  find("source-notice").hidden = true;
}

function clearFilters() {
  find("search").value = searchTerms[tab] = "";
  if (tab === "installed") find("updates-only").checked = false;
  render();
  find("search").focus();
}

function renderEmpty(target, key, recovery, label) {
  if (target.dataset.empty === key && target.dataset.language === state.language) return;
  target.dataset.empty = key;
  target.dataset.language = state.language;
  target.replaceChildren(node("p", "", t(key)));
  if (recovery) target.append(button(t(label), "", recovery));
}

function sources() {
  const trusted = new Set(state.settings.trustedOwners.map((owner) => owner.toLowerCase()));
  const sources = state.settings.owners.map((name) => ({
    name, fixed: trusted.has(name.toLowerCase()), error: state.discoveryErrors[name],
    avatar: state.ownerAvatars?.[name], count: state.ownerCounts[name.toLowerCase()] ?? 0,
  }));
  replaceList("sources", find("source-list"), sources, (source) => {
    const item = node("li");
    const label = node("div", "source-identity");
    label.append(externalLink(source.name, `https://github.com/${encodeURIComponent(source.name)}`));
    const count = t("installed_count", {count: source.count});
    label.append(node("span", "meta", source.fixed ? `${t("primary_source")} · ${count}` : count));
    if (source.error) label.append(node("span", "meta source-error", source.error));
    const record = source.avatar ? artworkFor(source, "avatar") : null;
    item.append(imageSlot("source-avatar", "boxes", record, ["avatar"]), label);
    const actions = node("div", "source-actions");
    actions.append(reportButton("source", source.name));
    if (!source.fixed) {
      const remove = button(t("remove_source"), "link", () => send("removeOwner", {url: source.name}));
      remove.setAttribute("aria-label", t("action_for_addin", {action:t("remove_source"), name:source.name}));
      actions.append(remove);
    }
    item.append(actions);
    return item;
  });
}

function render() {
  if (!state) return;
  const keys = new Set(state.installed.map((row) => row.key));
  for (const key of selected) if (!keys.has(key)) selected.delete(key);
  updateBatchButtons();
  find("removal-archive").hidden = !state.canOpenRemovalArchive;
  const phases = renderOperationProgress();
  const results = renderBatchResults();
  find("download-dock").hidden = !phases.length && !results;
  const announcement = phases.length ? phases.join(". ") : results;
  const live = find("operation-announcement");
  if (live.textContent !== announcement) live.textContent = announcement;
  const details = detail && detailContent();
  if (details && !(details.local || details.entry)) {
    closeRowActions();
    detail = undefined;
  }
  syncGuide();
  find("detail").hidden = !detail;
  for (const id of ["installed", "catalog", "settings"]) find(id).hidden = Boolean(detail) || id !== tab;
  find("toolbar").hidden = Boolean(detail) || tab === "settings";
  find("sources").hidden = tab !== "catalog";
  find("update-filter").hidden = tab !== "installed";

  const search = find("search").value.trim().toLocaleLowerCase(state.language);
  const updatesOnly = find("updates-only").checked;
  const matches = (row) => [row.name, row.author, row.repo, row.description]
    .some((value) => value?.toLocaleLowerCase(state.language).includes(search));
  let filteredCount;
  if (detail) {
    renderDetails(details.local, details.entry, details.installations);
  } else if (tab === "installed") {
    const rows = state.installed.filter((row) => matches(row) &&
      (!updatesOnly || row.update || row.newerTag));
    if (search || updatesOnly) filteredCount = rows.length;
    replaceList(tab, find("installed-body"), rows, installedRow);
    const empty = find("installed-empty");
    empty.hidden = Boolean(rows.length);
    find("installed").querySelector("table").hidden = !rows.length;
    if (!rows.length) {
      if (search || updatesOnly) {
        renderEmpty(empty, search ? "search_empty" : "updates_empty", clearFilters, "clear_filters");
      } else {
        renderEmpty(empty, "addins_empty", () => {
          const marketplace = document.querySelector('[data-tab="catalog"]');
          marketplace.click();
          marketplace.focus();
        }, "marketplace");
      }
    }
  } else if (tab === "catalog") {
    const rows = state.catalog.filter(matches);
    if (search) filteredCount = rows.length;
    const catalog = find("catalog");
    const hadEmptyFocus = catalog.querySelector(".empty")?.contains(document.activeElement);
    const empty = search ? "search_empty" : state.checking ? "loading_releases" : "releases_empty";
    replaceList(tab, catalog, rows, marketRow, empty);
    const emptyContent = catalog.querySelector(".empty");
    if (emptyContent) {
      const recovery = search ? clearFilters : state.checking ? undefined : openSources;
      renderEmpty(emptyContent, empty, recovery, search ? "clear_search" : "sources");
    }
    if (hadEmptyFocus && !catalog.contains(document.activeElement)) {
      (emptyContent?.querySelector("button") || find("search")).focus();
    }
  }

  settings();
  renderProfilePreview();
  sources();
  const sourceDialogOpen = find("source-dialog").open;
  const notice = state.notice || "";
  const sourceNotice = find("source-notice");
  if (sourceDialogOpen) lastSourceNotice = notice;
  else if (lastSourceNotice !== notice) lastSourceNotice = "";
  sourceNotice.textContent = sourceDialogOpen ? notice : "";
  sourceNotice.hidden = !sourceNotice.textContent;
  showNotice(sourceDialogOpen || notice === lastSourceNotice ? "" : notice);
  const rateLimit = rateLimitStatus();
  find("check-status").textContent = rateLimit;
  find("check-status").hidden = !rateLimit;
  find("store-update-notice").textContent = state.storeUpdateNotice || "";
  find("store-update-notice").hidden = !state.storeUpdateNotice;
  find("palette-status").hidden = !rateLimit && !state.storeUpdateNotice;
  find("store-version").textContent = `Community Store ${state.storeVersion}`;
  find("store-update-disabled").textContent = state.storeUpdateDisabled
    ? t("self_update_disabled", {reason: state.storeUpdateDisabled}) : "";
  find("store-update-disabled").hidden = !state.storeUpdateDisabled;
  find("check").setAttribute("aria-disabled", String(Boolean(state.checking || rateLimit)));
  find("check").toggleAttribute("data-checking", Boolean(state.checking));
  find("check-label").textContent = state.checking ? t("checking") : t(tab === "installed" ? "check_updates" : "check_releases");
  find("check-compact").textContent = t(state.checking ? "checking" : "check_short");
  find("check").setAttribute("aria-label", find("check-label").textContent);
  find("check").title = rateLimit || find("check-label").textContent;
  find("checked").textContent = state.checked
    ? t("checked_at", {time: new Date(state.checked * 1000).toLocaleTimeString(state.language, {hour: "2-digit", minute: "2-digit"})})
    : tab === "settings" ? "" : t("not_checked");
  const total = tab === "installed" ? state.installed.length : state.catalog.length;
  const footerLabel = tab === "settings" ? "" : filteredCount !== undefined
    ? t("search_results", {count: filteredCount, total})
    : t(tab === "installed" ? "installed_count" : "catalog_count", {count: total});
  if (find("footer-label").textContent !== footerLabel) find("footer-label").textContent = footerLabel;
  find("palette-footer").hidden = tab === "settings" || Boolean(detail);
  const count = state.newReleaseCount;
  const filter = count ? t("updates_count", {count}) : t("updates_only");
  find("update-label").textContent = filter;
}

function accept(json) {
  const previousLanguage = state?.language;
  const previousGitHubConfigured = state?.github?.configured;
  const firstSnapshot = !state;
  state = JSON.parse(json);
  if (firstSnapshot) guideOpen = state.settings.guideSeen !== true;
  if (state.language !== previousLanguage) localize();
  if (guideOpen && (state.language !== previousLanguage || state.github?.configured !== previousGitHubConfigured)) {
    renderGuide();
  }
  clearTimeout(retryTimer);
  const delay = state.retryAt * 1000 - Date.now();
  if (delay > 0) retryTimer = setTimeout(render, delay);
  render();
  find("startup-status").hidden = true;
  delete document.body.dataset.loading;
}

window.fusionJavaScriptHandler = {
  handle(name, json) {
    if (name === "state" && !receiveState(json)) return "ERROR";
    if (name === "artwork") acceptArtwork(JSON.parse(json));
    return "OK";
  }
};

function receiveState(json) {
  const previous = state;
  try {
    accept(json);
    return true;
  } catch (error) {
    console.error("Community Store could not render Fusion state.", error);
    const message = (state?.translations || previous?.translations)?.palette_error || (russianStartup
      ? "Не удалось отобразить Community Store. Закройте и снова откройте палитру."
      : "Could not display Community Store. Close and reopen the palette.");
    state = previous;
    if (startup.hidden) showNotice(message);
    else {
      clearInterval(bridge);
      clearTimeout(startupTimeout);
      startup.textContent = message;
    }
    return false;
  }
}

function refreshState() {
  if (!lastCommand && !stateRefresh) {
    const {availLeft = 0, availTop = 0, availWidth, availHeight} = window.screen;
    const payload = JSON.stringify({display: {left: availLeft, top: availTop, width: availWidth, height: availHeight}});
    stateRefresh = request("state", payload).finally(() => { stateRefresh = undefined; });
  }
  return stateRefresh;
}

function send(name, data = {}) {
  const payload = JSON.stringify(data);
  const key = name + ":" + payload;
  if (lastCommand?.key === key) return lastCommand.result;
  const destructive = destructiveCommands.has(name);
  if (destructive && queuedDestructive.has(key)) return queuedDestructive.get(key);
  const previous = lastCommand?.result;
  const current = {key};
  current.result = (previous || Promise.resolve()).catch(() => {}).then(async () => {
    if (stateRefresh) await stateRefresh;
    return request(name, payload);
  }).finally(() => {
    if (lastCommand === current) lastCommand = undefined;
    if (destructive) queuedDestructive.delete(key);
  });
  lastCommand = current;
  if (destructive) queuedDestructive.set(key, current.result);
  return current.result;
}

async function readState(response) {
  let deadline;
  const expired = new Promise((resolve) => { deadline = setTimeout(resolve, 1500); });
  try {
    return await Promise.race([response, expired]);
  } finally {
    clearTimeout(deadline);
  }
}

async function request(name, payload) {
  if (!window.adsk?.fusionSendData) return false;
  submitting = true;
  if (name !== "state" && name !== "dismissGuide") showNotice("");
  try {
    let result;
    try {
      const response = window.adsk.fusionSendData(`communityStore:${name}`, payload);
      result = await (name === "state" ? readState(response) : response);
    } catch (error) {
      if (startup.hidden) {
        const message = t("bridge_error", {error});
        if (find("source-dialog").open) {
          find("source-notice").textContent = message;
          find("source-notice").hidden = false;
        } else {
          showNotice(message);
        }
      }
      return false;
    }
    return Boolean(result) && receiveState(result);
  } finally {
    submitting = false;
    if (guideSavePending) saveGuide();
  }
}

for (const control of document.querySelectorAll("[data-tab]")) {
  control.addEventListener("click", () => {
    closeRowActions();
    if (detail && tab === control.dataset.tab) {
      closeDetails();
      return;
    }
    if (tab === control.dataset.tab) return;
    detail = undefined;
    tab = control.dataset.tab;
    for (const other of document.querySelectorAll("[data-tab]")) {
      other.setAttribute("aria-pressed", String(other === control));
    }
    find("search").value = searchTerms[tab] || "";
    document.querySelector("main").scrollTop = 0;
    render();
  });
}
find("update-selected").addEventListener("click", () => preview("selected", [...selected]));
find("update-all").addEventListener("click", () => preview("all"));
find("register-local").addEventListener("click", () => send("registerLocal"));
find("open-removal-archive").addEventListener("click", () => send("openRemovalArchive"));
const batchPreview = find("batch-preview");
batchPreview.addEventListener("close", () => {
  if (!batchPreview.open) activePreview = undefined;
});
find("batch-cancel").addEventListener("click", () => batchPreview.close());
find("batch-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!activePreview?.rows.some((item) => item.direction)) return;
  const token = activePreview.token;
  const assets = Object.fromEntries([...event.currentTarget.querySelectorAll("select")]
    .map((select) => [select.dataset.key, select.value]));
  activePreview = undefined;
  batchPreview.close();
  send("applyBatch", {token, assets});
});
document.addEventListener("keydown", (event) => {
  if (event.defaultPrevented || event.isComposing || document.querySelector("dialog[open]")) return;
  if (event.key.toLowerCase() === "f" && (event.metaKey || event.ctrlKey)
      && !event.altKey && !event.shiftKey && state && !find("toolbar").hidden) {
    event.preventDefault();
    find("search").focus();
    find("search").select();
    return;
  }
  if (event.key === "Escape" && detail) closeDetails();
});
find("search").addEventListener("input", (event) => {
  searchTerms[tab] = event.target.value;
  render();
});
find("updates-only").addEventListener("change", render);
find("check").addEventListener("click", (event) => {
  if (event.currentTarget.getAttribute("aria-disabled") !== "true") send("check");
});
find("notice-dismiss").addEventListener("click", () => {
  dismissNotice();
  const control = find("toolbar").hidden
    ? document.querySelector('#tabs [aria-pressed="true"]') : find("check");
  control.focus({preventScroll: true});
});
for (const event of ["focusin", "pointerenter"]) {
  find("notice").addEventListener(event, () => clearTimeout(noticeTimer));
}
for (const event of ["focusout", "pointerleave"]) {
  find("notice").addEventListener(event, () => setTimeout(scheduleNoticeDismiss, 0));
}
find("sources").addEventListener("click", openSources);
find("source-dialog").addEventListener("close", () => {
  find("source-url").removeAttribute("aria-invalid");
  find("source-url").setAttribute("aria-describedby", "source-help");
});
find("source-url").addEventListener("input", (event) => {
  event.currentTarget.removeAttribute("aria-invalid");
  event.currentTarget.setAttribute("aria-describedby", "source-help");
});
find("guide-replay").addEventListener("click", async () => {
  const reset = find("guide-replay");
  reset.disabled = true;
  const received = await send("resetGuide");
  reset.disabled = false;
  if (!received || state.settings.guideSeen !== false) return;
  guideStep = 0;
  guideOpen = true;
  syncGuide();
  renderGuide();
  find("guide-title").focus();
});
find("guide-skip").addEventListener("click", () => closeGuide());
find("guide-open-market").addEventListener("click", () => closeGuide(true));
find("guide-back").addEventListener("click", () => {
  guideStep -= 1;
  renderGuide();
  find("guide-title").focus();
});
find("guide-next").addEventListener("click", () => {
  if (guideStep + 1 === guideSteps.length) {
    closeGuide(true);
    return;
  }
  guideStep += 1;
  renderGuide();
  find("guide-title").focus();
});
find("store-guide").addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeGuide();
  }
});
find("native").addEventListener("click", () => send("native"));
find("export-logs").addEventListener("click", () => send("exportLogs"));
find("github-sign-in").addEventListener("click", () => send("githubSignIn"));
find("github-copy").addEventListener("click", () => send("githubCopyCode"));
find("github-open-device").addEventListener("click", () => send("githubOpenDevice"));
find("github-cancel").addEventListener("click", () => send("githubCancel"));
find("github-profile").addEventListener("click", () => send("githubProfile"));
find("github-sign-out").addEventListener("click", () => send("githubSignOut"));
find("language").addEventListener("change", async (event) => {
  const select = event.currentTarget;
  const wasFocused = document.activeElement === select;
  select.disabled = true;
  try {
    await send("language", {language: select.value});
  } finally {
    select.value = state.settings.language;
    select.disabled = false;
    if (wasFocused && document.activeElement === document.body && !find("settings").hidden) {
      select.focus({preventScroll: true});
    }
  }
});
for (const id of ["automatic", "interval"]) {
  find(id).addEventListener("change", async () => {
    const preferences = {
      automatic: find("automatic").checked, interval: Number(find("interval").value),
    };
    pendingPreferences = preferences;
    find("interval").disabled = !preferences.automatic;
    try {
      await send("preferences", preferences);
    } finally {
      if (pendingPreferences === preferences) {
        pendingPreferences = undefined;
        settings();
      }
    }
  });
}
find("add-owner").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const applied = await send("addOwner", Object.fromEntries(new FormData(form)));
  const input = find("source-url");
  if (!find("source-notice").hidden) {
    if (applied) input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", "source-help source-notice");
    input.focus({preventScroll: true});
  } else if (applied) {
    input.removeAttribute("aria-invalid");
    input.setAttribute("aria-describedby", "source-help");
    form.reset();
    input.focus({preventScroll: true});
  }
});

const startup = find("startup-status");
const russianStartup = navigator.language.toLowerCase().startsWith("ru");
if (russianStartup) startup.textContent = "Подключение к Fusion…";
const bridge = setInterval(async () => {
  if (!startup.hidden) await refreshState();
  if (startup.hidden) clearInterval(bridge);
}, 250);
const startupTimeout = setTimeout(() => {
  clearInterval(bridge);
  if (!startup.hidden) startup.textContent = russianStartup
    ? "Не удалось подключиться к Fusion. Закройте и снова откройте Community Store."
    : "Could not connect to Fusion. Close and reopen Community Store.";
}, 5000);
window.addEventListener("focus", refreshState);
