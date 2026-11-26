"use strict";

const markdown = window.markdownit({html: true, linkify: false});
const defaultLinkValidation = markdown.validateLink.bind(markdown);
markdown.validateLink = (url) => defaultLinkValidation(url) && !url.startsWith("//") && !url.includes("\\");
const markdownTags = new Set(("a abbr b blockquote br caption code dd del details div dl dt em figcaption figure " +
  "h1 h2 h3 h4 h5 h6 hr i img kbd li ol p pre s span strong sub summary sup table tbody td th thead tfoot tr ul").split(" "));
const blockedMarkdownTags = new Set(["embed", "iframe", "noscript", "object", "script", "style", "template"]);

function summary(text) {
  const parts = markdown.parseInline(text || "", {})[0].children;
  return parts.map((part) => {
    if (part.type === "text" || part.type === "code_inline") return part.content;
    return part.type === "softbreak" || part.type === "hardbreak" ? " " : "";
  }).join("").trim();
}

function markdownURL(value, base) {
  if (!value || !markdown.validateLink(value) || /[\u0000-\u0020]/.test(value)) return "";
  try {
    const link = new URL(value, base);
    return link.protocol === "https:" && !link.username && !link.password ? link.href : "";
  } catch {
    return "";
  }
}

function artworkImage(path, alt, artwork) {
  const field = artwork?.readmePaths?.[path];
  const data = typeof field === "string" && /^(?:icon|shot[0-2]|readmeShot[0-3])$/.test(field) && artwork[field];
  if (typeof data !== "string" || !/^data:image\/(?:png|jpeg|webp);base64,/.test(data)) return null;
  const image = node("img", "markdown-image");
  image.alt = alt;
  image.src = data;
  return image;
}

function markdownImage(source, artwork) {
  const alt = source.getAttribute("alt") || "";
  try {
    const url = source.getAttribute("src") || "";
    if (/^[a-z][a-z\d+.-]*:|^\/|\\/i.test(url)) return document.createTextNode(alt);
    const path = decodeURIComponent(url.split(/[?#]/, 1)[0]).split("/").filter((part) => part !== ".").join("/");
    if (artwork && path) {
      const image = artworkImage(path, alt, artwork);
      if (image) return image;
      const fallback = node("span", "", alt);
      fallback.dataset.markdownImage = path;
      return fallback;
    }
  } catch {
    // Malformed local paths can still display their alternative text.
  }
  return document.createTextNode(alt);
}

function renderMarkdown(text, base, artwork) {
  const content = node("div", "markdown");
  const parsed = document.createElement("template");
  parsed.innerHTML = markdown.render(text);
  function copy(source, target, depth = 0) {
    if (depth >= 64) return;
    for (const child of source.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        target.append(document.createTextNode(child.textContent));
        continue;
      }
      if (child.nodeType !== Node.ELEMENT_NODE || child.namespaceURI !== "http://www.w3.org/1999/xhtml" ||
          blockedMarkdownTags.has(child.localName)) continue;
      const tag = child.localName;
      if (tag === "img") {
        target.append(markdownImage(child, artwork));
        continue;
      }
      const href = tag === "a" && markdownURL(child.getAttribute("href"), base);
      if (!markdownTags.has(tag) || (tag === "a" && !href)) {
        copy(child, target, depth + 1);
        continue;
      }
      const element = node(tag);
      if (href) {
        element.href = href;
        element.dataset.external = "";
      }
      for (const attribute of tag === "ol" ? ["start"] : ["td", "th"].includes(tag) ? ["colspan", "rowspan"] : []) {
        const span = child.getAttribute(attribute);
        if (/^[1-9]\d?$/.test(span || "")) element.setAttribute(attribute, span);
      }
      copy(child, element, depth + 1);
      if (tag === "table") {
        const wrapper = node("div", "markdown-table");
        wrapper.tabIndex = 0;
        wrapper.setAttribute("role", "region");
        wrapper.setAttribute("aria-label", t("readme_table"));
        wrapper.append(element);
        target.append(wrapper);
      } else {
        target.append(element);
      }
    }
  }
  copy(parsed.content, content);
  return content;
}

function externalLink(label, href) {
  const link = node("a", "external-link", label);
  link.href = href;
  link.dataset.external = "";
  link.append(icon("external"));
  return link;
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-external]");
  if (!link) return;
  event.preventDefault();
  send("openUrl", {url: link.href});
});

const artworkCache = new Map();
const artworkRequests = new Map();
const artworkQueue = [];
let nextTicket = Math.floor(Math.random() * 1e8) * 1000;

function updateArtwork(record) {
  for (const [target, paint] of record.listeners) {
    if (target.isConnected) paint(record.data);
    else record.listeners.delete(target);
  }
}

function finishArtwork(ticket) {
  const record = artworkRequests.get(ticket);
  if (!record) return;
  clearTimeout(record.timeout);
  record.done = true;
  artworkRequests.delete(ticket);
  updateArtwork(record);
  startArtwork();
}

function acceptArtwork(message) {
  const record = artworkRequests.get(message.ticket);
  if (record && message.field) {
    record.data[message.field] = message.value;
    updateArtwork(record);
  }
  if (message.done) finishArtwork(message.ticket);
}

function startArtwork() {
  while (artworkRequests.size < 2 && artworkQueue.length) {
    const record = artworkQueue.shift();
    if ([...record.listeners.keys()].every((element) => !element.isConnected)) {
      record.started = false;
      continue;
    }
    const ticket = String(++nextTicket);
    artworkRequests.set(ticket, record);
    record.timeout = setTimeout(() => finishArtwork(ticket), 90000);
    const data = JSON.stringify({ticket, key: record.key, kind: record.kind});
    requestArtwork(ticket, data);
  }
}

async function requestArtwork(ticket, data) {
  try {
    await window.adsk.fusionSendData("communityStore:media", data);
  } catch {
    finishArtwork(ticket);
  }
}

const visibility = new WeakMap();
const artworkObserver = new IntersectionObserver((entries) => {
  for (const {target, isIntersecting} of entries) {
    if (!target.isConnected || isIntersecting) {
      artworkObserver.unobserve(target);
      const record = visibility.get(target);
      if (target.isConnected && record && !record.started && window.adsk?.fusionSendData) {
        record.started = true;
        if (record.kind === "detail") artworkQueue.unshift(record);
        else artworkQueue.push(record);
      }
    }
  }
  startArtwork();
});

function artworkFor(row, kind) {
  const key = kind === "avatar" ? row.name : kind === "githubAvatar" ? row.login : row.repo;
  const revision = row.avatar ?? (row.tag + "|" + state.language);
  const id = JSON.stringify([kind, key, revision]);
  if (!artworkCache.has(id)) {
    artworkCache.set(id, {kind, key, data: {}, listeners: new Map()});
    for (const [cachedId, cached] of artworkCache) {
      if (artworkCache.size <= 32) break;
      if (cached.done && [...cached.listeners.keys()].every((element) => !element.isConnected)) {
        artworkCache.delete(cachedId);
      }
    }
  }
  return artworkCache.get(id);
}

function watchArtwork(target, record, paint) {
  for (const element of record.listeners.keys()) {
    if (!element.isConnected) record.listeners.delete(element);
  }
  record.listeners.set(target, paint);
  paint(record.data);
  if (!record.started) {
    visibility.set(target, record);
    artworkObserver.observe(target);
  }
}

function imageSlot(className, placeholder, record, fields, localImage = "") {
  const slot = node("span", "image-slot " + className);
  const fallback = typeof placeholder === "string" ? icon(placeholder) : placeholder;
  const image = node("img");
  image.alt = "";
  image.hidden = true;
  image.addEventListener("load", () => {
    image.hidden = false;
    fallback.hidden = true;
  });
  image.addEventListener("error", () => {
    image.hidden = true;
    fallback.hidden = false;
  });
  slot.append(fallback, image);
  let initialPaint = true;
  const paint = (data) => {
    const field = fields.find((name) => data[name]);
    const url = (field && data[field]) || localImage;
    if (url && image.getAttribute("src") !== url) {
      slot.classList.toggle("icon-art", field === "icon" || !field);
      slot.classList.toggle("store-mark", !field && Boolean(localImage));
      image.classList.toggle("artwork-arrival", !initialPaint);
      image.src = url;
    }
  };
  if (record) watchArtwork(slot, record, paint);
  else paint({});
  initialPaint = false;
  return slot;
}

function stars(row) {
  const count = node("span", "stars");
  const formatted = row.stars.toLocaleString(state.language);
  count.title = t("github_stars");
  count.setAttribute("aria-label", count.title + " · " + formatted);
  count.append(icon("star"), document.createTextNode(formatted));
  return count;
}

let detail;

function detailButton(control, kind, key, name) {
  control.dataset.entry = kind + "/" + key;
  control.setAttribute("aria-label", t("open_details", {name}));
  control.addEventListener("click", () => {
    const main = document.querySelector("main");
    detail = {kind, key, scroll: main.scrollTop, entry: control.dataset.entry};
    render();
    main.scrollTop = 0;
    find("detail-title")?.focus({preventScroll: true});
  });
}

function closeDetails() {
  const previous = detail;
  if (!previous) return;
  closeRowActions();
  detail = undefined;
  render();
  document.querySelector("main").scrollTop = previous.scroll;
  const control = [...document.querySelectorAll("[data-entry]")].find((item) =>
    item.dataset.entry === previous.entry && !item.closest("[hidden]"));
  (control || find("search")).focus({preventScroll: true});
}

function detailContent() {
  if (detail.kind === "installed") {
    const local = state.installed.find((row) => row.key === detail.key);
    return {local, entry: state.catalog.find((row) => row.repo === local?.repo), installations: []};
  }
  const entry = state.catalog.find((row) => row.repo === detail.key);
  const installations = entry ? state.installed.filter((row) => row.repo === entry.repo || entry.matches.includes(row.key)) : [];
  const local = installations.find((row) => row.key === detail.installation) || installations[0];
  return {local, entry, installations};
}

function detailActions(local, entry, installations) {
  const actions = node("div", "detail-actions");
  if (installations.length > 1) {
    const select = node("select", "detail-installation");
    select.setAttribute("aria-label", t("choose_installation"));
    select.dataset.detailFocus = "installation";
    for (const row of installations) {
      const option = node("option", "", row.folder);
      option.value = row.key;
      select.append(option);
    }
    select.value = local.key;
    select.addEventListener("change", () => {
      detail.installation = select.value;
      render();
    });
    actions.append(select);
  }
  if (local) {
    const locked = state.pending || local.busy;
    if (local.direction) {
      const apply = button(t(local.direction === "downgrade" ? "downgrade_to" : "update_to", {tag: local.tag}),
        "primary", () => applyVersion(local), locked);
      apply.dataset.detailFocus = "apply";
      actions.append(apply);
    }
    if (!local.self) {
      const target = node("label", "detail-target");
      target.append(node("span", "", t("target_short")));
      const selector = targetSelector(local, locked, target);
      selector.dataset.detailFocus = "target";
      actions.append(target);
    }
    const menu = node("div", "detail-more");
    const trigger = addinMenu(local, locked, menu, true);
    trigger.dataset.detailFocus = "actions";
    actions.append(menu);
  } else if (entry.available && !entry.matches.length) {
    const install = button(t("install"), "primary", () => requestPackage(entry), state.pending);
    install.dataset.detailFocus = "install";
    install.setAttribute("aria-describedby", "install-disclosure");
    actions.append(install);
  }
  return actions;
}

function installDisclosure(entry) {
  const source = entry.assets.length === 1
    ? t("install_package", {name: entry.assets[0].name, repo: entry.repo})
    : t("install_repository", {repo: entry.repo});
  const disclosure = node("p", "install-disclosure", source + " " + t("install_effects"));
  disclosure.id = "install-disclosure";
  return disclosure;
}

function detailMedia(record, target, row, gallery) {
  const shots = gallery.lastElementChild;
  let initialPaint = true;
  watchArtwork(target, record, (data) => {
    for (let index = 0; index < 3; index++) {
      const shot = data["shot" + index];
      if (!shot || shots.querySelector('[data-shot="' + index + '"]')) continue;
      const label = t("gallery_index", {name: row.name, index: index + 1});
      const open = button("", "", () => {
        const image = find("gallery-image");
        image.src = shot;
        image.alt = label;
        find("gallery-title").textContent = label;
        const dialog = find("gallery-dialog");
        dialog.dataset.entry = detail.entry;
        dialog.dataset.shot = index;
        showPaletteDialog(dialog);
      });
      open.dataset.shot = index;
      open.classList.toggle("artwork-arrival", !initialPaint);
      open.setAttribute("aria-label", label);
      const thumb = node("img");
      thumb.src = shot;
      thumb.alt = "";
      open.append(thumb);
      shots.append(open);
      gallery.hidden = false;
    }
  });
  initialPaint = false;
}

function detailDocumentation(about, record, root, open) {
  const documentation = node("div", "detail-documentation");
  const loading = node("div");
  loading.id = "detail-readme-loading";
  const status = node("p", "detail-readme-status", t("readme_loading"));
  status.setAttribute("role", "status");
  const skeleton = node("div", "readme-skeleton");
  skeleton.setAttribute("aria-hidden", "true");
  skeleton.append(node("span"), node("span"));
  const readme = node("details");
  readme.id = "detail-readme";
  readme.hidden = true;
  readme.open = open;
  readme.append(node("summary", "", t("read_more")));
  documentation.append(status, loading, readme);
  loading.append(skeleton);
  about.append(documentation);
  let mappedImages;
  watchArtwork(documentation, record, (data) => {
    if (data.about && data.readme) {
      if (readme.hidden) {
        const url = root + data.readme;
        readme.append(renderMarkdown(data.about, url, data));
        documentation.append(externalLink(t("readme_github", {name: data.readme}), url));
        loading.hidden = true;
        status.hidden = true;
        readme.hidden = false;
      }
      if (data.readmePaths && mappedImages !== data.readmePaths) {
        mappedImages = data.readmePaths;
        for (const fallback of readme.querySelectorAll("[data-markdown-image]")) {
          const image = artworkImage(fallback.dataset.markdownImage, fallback.textContent, data);
          if (image) fallback.replaceWith(image);
        }
      }
    } else if (data.readmeUnavailable || record.done || !window.adsk?.fusionSendData) {
      loading.hidden = true;
      status.textContent = t("readme_unavailable");
    }
  });
  return readme;
}

document.getElementById("gallery-dialog").addEventListener("close", (event) => {
  const dialog = event.currentTarget;
  if (detail?.entry !== dialog.dataset.entry) return;
  (find("detail").querySelector(`[data-shot="${dialog.dataset.shot}"]`) || find("detail-title"))
    ?.focus({preventScroll: true});
});

function renderDetails(local, entry, installations) {
  const fingerprint = JSON.stringify([detail.entry, local, entry, state.language, state.pending,
    installations.map(({key, folder}) => [key, folder]),
    local && !local.historyLoaded && state.checking, local && rateLimitStatus()]);
  if (fingerprint === fingerprints.detail) return;
  fingerprints.detail = fingerprint;
  const row = local || entry;
  const target = find("detail");
  const sameEntry = target.dataset.entry === detail.entry;
  const previousReadme = sameEntry && find("detail-readme");
  const focused = document.activeElement;
  const focusKey = sameEntry && target.contains(focused) &&
    (focused.dataset.detailFocus || (openActions?.menu.contains(focused) && openActions.trigger.dataset.detailFocus));
  if (openActions && target.contains(openActions.trigger)) closeRowActions();
  const focusSelector = sameEntry && target.contains(focused) &&
    [".back", "#detail-title", "#detail-readme > summary", "[data-shot]"].find((selector) => focused.matches(selector));
  const focusedShot = focused.dataset.shot;
  const href = sameEntry && target.contains(focused) && focused.getAttribute("href");
  target.dataset.entry = detail.entry;
  const record = entry ? artworkFor(entry, "detail") : null;
  const back = button(t(tab === "installed" ? "back_to_addins" : "back_to_market"), "back", closeDetails);
  back.prepend(icon("arrow-left"));
  const header = node("header", "detail-header");
  const navigation = node("div", "detail-navigation");
  navigation.append(back);
  const repository = entry?.repo || local?.releaseRepo;
  const repo = repository ? "https://github.com/" + repository : "";
  if (repo) {
    const star = externalLink(t("star_on_github"), repo);
    star.classList.add("detail-star");
    star.dataset.detailFocus = "star";
    star.prepend(icon("star"));
    navigation.append(star);
  }
  const title = node("h1", "", row.name);
  title.id = "detail-title";
  title.tabIndex = -1;
  const hero = node("div", "detail-hero");
  const identity = node("div");
  const installedVersion = local?.version;
  const releaseVersion = entry?.prerelease ? t("prerelease", {tag: entry.tag}) : entry?.tag;
  const byline = node("div", "detail-byline");
  byline.append(node("span", "", (entry?.repo || local?.releaseRepo)?.split("/")[0] || t("local_details")));
  if (installedVersion || releaseVersion) byline.append(node("span", "version", installedVersion || releaseVersion));
  if (entry) byline.append(stars(entry));
  const author = local?.author?.trim() || entry?.author?.trim();
  if (author) byline.append(node("span", "detail-author", t("addin_author", {author})));
  identity.append(title, byline);
  hero.append(imageSlot("addin-icon", "package", record, ["icon"], local?.self ? "assets/open-catalog.png" : ""),
    identity);
  const heading = node("div", "detail-heading");
  heading.append(hero, detailActions(local, entry, installations));
  header.append(navigation, heading);

  const gallery = node("section", "detail-section");
  gallery.hidden = true;
  gallery.append(node("h2", "", t("screenshots")), node("div", "screenshots"));
  const about = node("section", "detail-section");
  const blurb = entry && summary(entry.description);
  if (entry) about.append(node("h2", "", t("about_addin")));
  if (blurb) about.append(node("p", "", blurb));
  about.hidden = !entry;

  const facts = node("dl", "facts");
  const published = entry?.publishedAt && new Date(entry.publishedAt);
  for (const [label, value] of [
    [t("installation_source"), local && t(`source_${local.sourceType}`)],
    [t("installed_version"), installedVersion],
    [t("repository_version"), releaseVersion],
    [t("published"), published && Number.isFinite(+published) && published.toLocaleDateString(state.language)],
  ]) {
    if (!value) continue;
    const fact = node("div");
    fact.append(node("dt", "", label), node("dd", "", value));
    facts.append(fact);
  }

  const notes = node("section", "detail-section");
  const root = entry && repo + "/blob/" + encodeURIComponent(entry.tag) + "/";
  if (entry?.notes) notes.append(node("h2", "", t("whats_new")), renderMarkdown(entry.notes, root + "README.md"));
  const links = node("div", "detail-links");
  if (local?.checkout || local?.external) {
    links.append(button(t("open_folder"), "link", () => send("openFolder", {key: local.key})));
  }
  if (entry) {
    links.append(externalLink(t("show_release"), repo + "/releases/tag/" + encodeURIComponent(entry.tag)),
      externalLink(t("repository"), repo));
    if (!entry.available && !entry.matches.length) about.append(node("p", "detail-note", t("compatibility_unavailable")));
  } else if (repo) {
    links.append(button(t("show_release"), "link", () => send("release", {key: local.key})),
      externalLink(t("repository"), repo));
  }
  const report = reportButton("addin", entry?.repo);
  report.dataset.detailFocus = "report";
  links.append(report);
  const disclosure = !local && entry.available && !entry.matches.length ? [installDisclosure(entry)] : [];
  target.replaceChildren(header, ...disclosure, about, gallery, facts, notes, links);
  const readme = record && detailDocumentation(about, record, root, Boolean(previousReadme?.open));
  if (record) detailMedia(record, hero, row, gallery);
  if (focusKey) focusDetailControl(focusKey);
  else if (focusSelector) {
    (focusedShot === undefined ? target.querySelector(focusSelector)
      : target.querySelector(`[data-shot="${focusedShot}"]`))?.focus({preventScroll: true});
  } else if (href) {
    ([...target.querySelectorAll("a")].find((link) => link.getAttribute("href") === href)
      || (previousReadme?.contains(focused) && readme ? readme.firstElementChild : title)).focus({preventScroll: true});
  }
}

function focusDetailControl(key) {
  const target = find("detail");
  const control = [...target.querySelectorAll("[data-detail-focus]")].find((item) =>
    item.dataset.detailFocus === key && !item.disabled);
  (control || find("detail-title"))?.focus({preventScroll: true});
}
