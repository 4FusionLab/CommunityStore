// Run with Node.js, Playwright and installed Chromium: node --test tests/test_palette_browser.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {test} = require("node:test");
const {chromium} = require("playwright");

const product = path.join(__dirname, "..", "CommunityStore");
const origin = "http://community-store.test";
const addin = {
  key: "panel", folder: "/fixture/PanelTool", name: "Panel Tool", version: "1.0.0",
  author: "Installed Author",
  repo: "Example/PanelTool", releaseRepo: "Example/PanelTool", sourceType: "folder",
  target: "Latest", targets: ["v2.0.0"], historyLoaded: true, running: false,
  startup: false, canRemove: true, canUpdate: true, update: true, direction: "upgrade", tag: "v2.0.0",
};
const catalog = [{
  name: addin.name, repo: addin.repo, stars: 3, tag: addin.tag, matches: [addin.key],
  updates: [], available: true, assets: [{name: "PanelTool.zip", compatible: true}],
  description: "Create perforated panels", author: "Repository Author",
}, {
  name: "Gear Tool", repo: "Another/GearTool", stars: 9, tag: "v1.0.0", matches: [],
  updates: [], available: true, assets: [{name: "GearTool.zip", compatible: true}],
  description: "Create gears", author: "Release Author",
}, {
  name: "Mount Tool", repo: "Supplier/MountTool", stars: 5, tag: "v3.0.0", matches: [],
  updates: [], available: true, assets: [{name: "MountTool-mac.zip", compatible: true},
    {name: "MountTool-win.zip", compatible: false}],
  description: "Create mounts",
}];

async function translations(language) {
  const raw = JSON.parse(await fs.readFile(path.join(product, "locales", language + ".json"), "utf8"));
  return Object.fromEntries(Object.entries(raw).filter(([key]) => key.startsWith("ui."))
    .map(([key, value]) => [key.slice(3), value]));
}

async function openPalette(browser, options) {
  const {language, hangFirstState = false, ...media} = options;
  const words = {en: await translations("en"), ru: await translations("ru")};
  const page = await browser.newPage(media);
  page.setDefaultTimeout(3000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(origin + "/**", (route) => {
    const url = new URL(route.request().url());
    const file = url.pathname === "/" ? "/index.html" : url.pathname;
    return route.fulfill({path: path.join(product, "ui", file)});
  });
  await page.addInitScript(({language, words, addin, catalog, hangFirstState}) => {
    const model = {
      installed: [addin,
        {...addin, key: "linked", folder: "/fixture/Linked", name: "Linked Tool", repo: null, releaseRepo: null, external: true,
          update: false, canUpdate: false, direction: "", tag: ""},
        {...addin, key: "app-store", folder: "/fixture/AppStore", name: "App Store Tool", repo: null, releaseRepo: null, sourceType: "app_store",
          update: false, canUpdate: false, direction: "", tag: ""}],
      catalog, settings: {language, owners: ["Example"], trustedOwners: ["Example"], automatic: true, interval: 60},
      github: {configured: language === "ru", stars: {}}, discoveryErrors: {}, ownerCounts: {},
      newReleaseCount: 1, storeVersion: "0.1.0", progress: [], batchResults: [],
    };
    const snapshot = () => {
      const language = model.settings.language;
      return JSON.stringify({...model, language, translations: words[language]});
    };
    window.fixture = {
      model, calls: [], publish: () => window.fusionJavaScriptHandler.handle("state", snapshot()),
      artwork: (message) => window.fusionJavaScriptHandler.handle("artwork", JSON.stringify(message)),
    };
    let hangState = hangFirstState;
    window.adsk = {async fusionSendData(name, json) {
      const action = name.split(":").at(-1);
      const data = JSON.parse(json);
      window.fixture.calls.push({action, data});
      model.notice = "";
      if (action === "media") {
        if (data.kind !== "detail") queueMicrotask(() => window.fixture.artwork({ticket: data.ticket, done: true}));
        return "";
      }
      if (action === "state" && hangState) {
        hangState = false;
        const stale = JSON.stringify({...JSON.parse(snapshot()), installed: []});
        return new Promise((resolve) => { window.fixture.releaseFirstState = () => resolve(stale); });
      }
      if (action === "state" && window.fixture.holdState) {
        await new Promise((resolve) => { window.fixture.releaseState = resolve; });
      }
      if (action === "dismissGuide") model.settings.guideSeen = true;
      if (action === "resetGuide") model.settings.guideSeen = false;
      if (action === "language") model.settings.language = data.language;
      if (action === "preferences") {
        if (window.fixture.holdPreferences) {
          await new Promise((resolve) => { window.fixture.releasePreferences = resolve; });
        }
        Object.assign(model.settings, data);
      }
      if (action === "install" && window.fixture.holdInstall) {
        await new Promise((resolve) => { window.fixture.releaseInstall = resolve; });
      }
      if (action === "enable" || action === "disable") {
        model.installed.find((row) => row.key === data.key).running = action === "enable";
      }
      if (action === "startup") model.installed.find((row) => row.key === data.key).startup = data.enabled;
      if (action === "target") {
        if (window.fixture.rejectTarget) model.notice = "Target unavailable";
        else model.installed.find((row) => row.key === data.key).target = data.target;
      }
      if (action === "preview") {
        const rows = data.mode === "all" ? model.installed : model.installed.filter((row) => data.keys.includes(row.key));
        model.preview = {token: String(window.fixture.calls.length), rows: rows.map((row) => (
          row.direction ? {...row, assets: catalog[0].assets} : row
        ))};
      }
      if (action === "addOwner" && data.url === "example.com/acme") model.notice = language === "ru" ? "Укажите владельца GitHub." : "Enter a GitHub owner.";
      if (action === "githubSignIn") Object.assign(model.github, {connecting: true, code: "TEST-1234"});
      if (action === "githubCancel") Object.assign(model.github, {connecting: false, code: ""});
      if (action === "check" && window.fixture.failCheck) throw Error("Test bridge failure");
      if (action === "check" && window.fixture.malformedState) {
        return JSON.stringify({...model, installed: null, language: model.settings.language, translations: words[model.settings.language]});
      }
      return snapshot();
    }};
  }, {language, words, addin, catalog, hangFirstState});
  await page.goto(origin);
  await page.locator("body:not([data-loading])").waitFor();
  return {page, words, errors};
}

async function detailArtworkTicket(page) {
  const handle = await page.waitForFunction(() => window.fixture.calls.find(({action, data}) =>
    action === "media" && data.kind === "detail")?.data.ticket);
  return handle.jsonValue();
}

async function focused(page, selector) {
  assert(await page.evaluate((selector) => document.activeElement.matches(selector), selector), selector);
}

test("palette actions, keyboard focus and localization", async (suite) => {
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || undefined});
  suite.after(() => browser.close());

  await suite.test("portable collections export only the checked groups and restore only after approval", async (test) => {
    const {page, errors} = await openPalette(browser, {language: "ru"});
    test.after(() => page.close());
    await page.evaluate(() => {
      Object.assign(fixture.model.settings, {ownerSubscriptions: ["Example"], automatic: false});
      Object.assign(fixture.model.catalog[1], {id: "gear-id", profileTags: ["v1.0.0"]});
      fixture.publish();
    });
    await page.locator('[data-tab="settings"]').click();
    await page.locator("#profile-export").click();
    await page.locator("#profile-body > label select").selectOption("collection");
    const group = page.locator("#profile-body fieldset");
    const submit = page.locator("#profile-submit");
    assert(await submit.isDisabled());
    const entries = [group.nth(0).getByLabel("Another/GearTool", {exact:true}),
      group.nth(1).getByLabel("Example", {exact:true}), group.nth(2).locator("label").filter({hasText:"Выкл."}).locator("input")];
    for (let mask = 1; mask < 8; ++mask) {
      for (let i = 0; i < 3; ++i) await entries[i].setChecked(Boolean(mask & (1 << i)));
      await submit.click();
      await page.waitForFunction(n => fixture.calls.filter(c => c.action === "exportProfile").length === n, mask);
      const doc = await page.evaluate(() => fixture.calls.filter(c => c.action === "exportProfile").at(-1).data);
      assert.equal(Boolean(doc.addins), Boolean(mask & 1));
      assert.equal(Boolean(doc.ownerSubscriptions), Boolean(mask & 2));
      assert.equal(Boolean(doc.settings), Boolean(mask & 4));
      if (doc.settings) assert.deepEqual(doc.settings, {automatic:false});
      if (doc.addins) assert.deepEqual(doc.addins, [{repo:"Another/GearTool", manifestId:"gear-id",
        installTarget:"Latest", updateTarget:"Latest"}]);
    }
    await page.locator("#profile-close").click();
    await page.evaluate(() => {
      fixture.model.profilePreview = {token:"confirmed-preview", name:"Tools", settings:{}, owners:[], manual:[],
        rows:[{name:"Another/GearTool", version:"2.0.0", tag:"v1.0.0", target:"Latest",
          action:"downgrade", assets:["Gear.zip"]}]};
      fixture.publish();
    });
    assert(await submit.isDisabled());
    assert.equal(await page.evaluate(() => fixture.calls.filter(c => c.action === "applyProfile").length), 0);
    await page.locator("#profile-body input[type=checkbox]").check();
    await submit.click();
    await page.waitForFunction(() => fixture.calls.some(c => c.action === "applyProfile"));
    assert.deepEqual(await page.evaluate(() => fixture.calls.find(c => c.action === "applyProfile").data),
      {token:"confirmed-preview", selected:{"Another/GearTool":"Gear.zip"}});
    assert.deepEqual(errors, []);
  });

  await suite.test("developer links open the localized guide and trusted source form", async (test) => {
    const {page} = await openPalette(browser, {language: "en"});
    test.after(() => page.close());
    const site = await browser.newPage();
    test.after(() => site.close());
    await page.locator('[data-tab="settings"]').click();
    const repo = "https://github.com/4FusionLab/CommunityStore";
    for (const language of ["en", "ru"]) {
      await page.locator("#language").selectOption(language);
      const russian = language === "ru";
      const guide = "docs/" + (russian ? "PUBLISHER_GUIDE.ru.md" : "PUBLISHER_GUIDE.md");
      const form = russian ? "request-source-ru.yml" : "request-source.yml";
      const urls = [repo + "/blob/main/" + guide, repo + "/issues/new?template=" + form];
      await fs.access(path.join(product, "..", ".github", "ISSUE_TEMPLATE", form));
      assert((await fs.readFile(path.join(product, "..", guide), "utf8")).includes(urls[1]));
      await site.goto(require("node:url").pathToFileURL(path.join(product, "..", "docs/pages",
        russian ? "ru/index.html" : "index.html")).href);
      await site.locator('nav a[href="#developers"]').click();
      assert.deepEqual(await site.locator("#developers a").evaluateAll(links => links.map(a => a.getAttribute("href"))),
        ["publisher.html", urls[1]]);
      await site.locator('#developers a[href="publisher.html"]').click();
      assert.equal(await site.locator("html").getAttribute("lang"), language);
      assert(await site.locator(`a[href="${urls[1]}"]`).first().isVisible());
      for (const selector of ["#publisher-guide", "#request-source"]) await page.locator(selector).click();
      assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(c => c.action === "openUrl")
        .slice(-2).map(c => c.data.url)), urls);
    }
  });

  await suite.test("a hung state read is retried and its late response is ignored", async (test) => {
    const {page, errors} = await openPalette(browser, {language: "en", hangFirstState: true});
    test.after(() => page.close());
    assert.equal(await page.locator("#installed-body tr").count(), 3);
    assert.equal(await page.evaluate(() => window.fixture.calls.filter(({action}) => action === "state").length), 2);
    await page.evaluate(() => window.fixture.releaseFirstState());
    assert.equal(await page.locator("#installed-body tr").count(), 3);
    await page.locator("#check").press("Enter");
    await page.waitForFunction(() => window.fixture.calls.some(({action}) => action === "check"));
    assert.deepEqual(errors, []);
  });

  await suite.test("the Russian inline selector fits the installed row at all widths", async (test) => {
    const {page} = await openPalette(browser, {language: "ru"});
    test.after(() => page.close());
    const target = page.locator('[data-row="panel"] .target-control');
    for (const width of [300, 320, 410, 411, 470, 570, 571]) {
      await page.setViewportSize({width, height: 520});
      assert(await target.isVisible(), `target selector at ${width}px`);
      assert.equal(await target.locator("option:checked").textContent(), "Последняя");
      assert(await target.evaluate((control) =>
        control.getBoundingClientRect().right <= control.parentElement.getBoundingClientRect().right));
    }
  });

  await suite.test("Find reuses the current section's search and leaves dialog input alone", async (test) => {
    const {page} = await openPalette(browser, {language: "ru", viewport: {width: 300, height: 360}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    const search = page.locator("#search");
    await search.fill("Panel");
    await page.locator('[data-tab="catalog"]').press("Enter");
    const sources = page.locator("#sources");
    const refresh = page.locator("#check");
    const boxes = await Promise.all([search.boundingBox(), sources.boundingBox(), refresh.boundingBox()]);
    assert(boxes.every((box) => Math.abs(box.y - boxes[0].y) < 2));
    assert(await refresh.getAttribute("aria-label"));
    await search.fill("Gear");
    await refresh.press("Meta+f");
    await focused(page, "#search");
    assert.equal(await search.evaluate((input) => input.selectionEnd - input.selectionStart), 4);
    await sources.press("Enter");
    await page.locator("#source-url").press("Control+f");
    await focused(page, "#source-url");
    await page.keyboard.press("Escape");
    await page.locator('[data-tab="installed"]').press("Enter");
    await page.keyboard.press("Control+f");
    await focused(page, "#search");
    assert.equal(await search.inputValue(), "Panel");
  });

  await suite.test("store installations show the repository owner even when it matches the author", async (test) => {
    const {page, words} = await openPalette(browser, {language: "en", viewport: {width: 920, height: 580}});
    test.after(() => page.close());
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      Object.assign(model.installed[0], {sourceType: "community_store", author: "Example"});
      Object.assign(model.installed[1], {sourceType: "community_store", author: "Someone Else", releaseRepo: "Example/Linked"});
      Object.assign(model.installed[2], {sourceType: "community_store", releaseRepo: "", repo: ""});
      publish();
    });
    const sources = page.locator("#installed-body .source-owner");
    assert.deepEqual(await sources.allInnerTexts(), ["Example", "Example", words.en.github_source_unknown]);
    const linked = page.locator('[data-row="linked"]');
    assert.equal(await linked.locator(".author-cell").innerText(), "Someone Else");
    assert(await linked.locator(".author-cell").isVisible());
    await page.setViewportSize({width: 620, height: 580});
    assert(await linked.locator(".installed-credits").isVisible());
    assert.deepEqual(await linked.locator(".installed-credits span").allInnerTexts(),
      ["Publisher: Example", "Author: Someone Else"]);
    await page.locator('[data-tab="catalog"]').click();
    assert.match(await page.locator('[data-entry="catalog/Example/PanelTool"] .card-meta').innerText(),
      /Publisher: Example\s+Author: Repository Author/);
  });

  await suite.test("Sources displays every trusted owner and allows removal of personal sources", async (test) => {
    const {page} = await openPalette(browser, {language: "en"});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      model.settings.owners = ["Example", "Studio", "Personal"];
      model.settings.trustedOwners = ["Example", "Studio"];
      publish();
    });
    await page.locator('[data-tab="catalog"]').click();
    await page.locator("#sources").click();
    const sources = page.locator("#source-list li");
    assert.deepEqual(await sources.getByRole("link").allTextContents(), ["Example", "Studio", "Personal"]);
    const profile = sources.getByRole("link", {name: "Studio"});
    assert.equal(await profile.getAttribute("href"), "https://github.com/Studio");
    await profile.click();
    await page.waitForFunction(() => window.fixture.calls.some(({action, data}) =>
      action === "openUrl" && data.url === "https://github.com/Studio"));
    assert(await sources.nth(2).evaluate((row) => {
      const avatar = row.querySelector(".source-avatar").getBoundingClientRect();
      const actions = row.querySelector(".source-actions").getBoundingClientRect();
      return Math.abs(avatar.top + avatar.height / 2 - actions.top - actions.height / 2) < 1;
    }));
    assert.equal(await sources.getByRole("button", {name: /^Remove\b/}).count(), 1);
    assert.equal(await sources.nth(2).getByRole("button", {name: /^Remove\b/}).count(), 1);
  });

  await suite.test("source reporting opens the public draft only after confirmation", async (test) => {
    const {page, words} = await openPalette(browser, {language: "en"});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-tab="catalog"]').click();
    await page.locator("#sources").click();
    const report = page.locator("#source-list").getByRole("button", {name: /^Report\b/});
    assert.equal(await report.innerText(), "Report");
    await report.click();
    const dialog = page.locator("#report-dialog");
    await dialog.getByRole("button", {name: words.en.cancel}).click();
    assert.equal(await dialog.isVisible(), false);
    assert.equal(await report.evaluate((button) => button === document.activeElement), true);
    assert.equal(await page.evaluate(() => window.fixture.calls.some(({action}) => action === "report")), false);
    await report.click();
    await dialog.getByRole("button", {name: words.en.report_public}).click();
    await page.waitForFunction(() => window.fixture.calls.some(({action}) => action === "report"));
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(({action}) => action === "report")),
      [{action: "report", data: {kind: "source", owner: "Example"}}]);
    assert.equal(await page.locator("#source-dialog").isVisible(), true);
  });

  await suite.test("add-in reporting presents public and private choices and omits local identifiers", async (test) => {
    const {page, words} = await openPalette(browser, {language: "ru", viewport: {width: 300, height: 360}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-tab="catalog"]').click();
    await page.locator('[data-entry="catalog/Another/GearTool"]').click();
    const dialog = page.locator("#report-dialog");
    const report = page.locator('[data-detail-focus="report"]');
    assert.equal(await report.textContent(), words.ru.report_addin);
    await report.click();
    await focused(page, "#report-title");
    const copy = dialog.locator(".report-copy");
    assert.equal(await copy.evaluate((element) => element.scrollTop), 0);
    await copy.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    assert(await copy.evaluate((element) => element.scrollTop > 0));
    assert.equal(await dialog.locator("#report-visibility").textContent(), words.ru.report_visibility);
    assert.equal(await dialog.locator("#report-developer").textContent(), words.ru.report_developer);
    await dialog.getByRole("button", {name: words.ru.report_public}).click();
    await report.click();
    assert.equal(await copy.evaluate((element) => element.scrollTop), 0);
    await dialog.getByRole("button", {name: words.ru.report_security}).click();
    await page.locator('[data-tab="installed"]').click();
    await page.locator('[data-entry="installed/linked"]').click();
    await report.click();
    await dialog.getByRole("button", {name: words.ru.report_public}).click();
    await page.waitForFunction(() => window.fixture.calls.filter(({action}) => action === "report").length === 3);
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(({action}) => action === "report").map(({data}) => data)),
      [{kind: "addin", repo: "Another/GearTool"}, {kind: "security"}, {kind: "addin"}]);
  });

  await suite.test("row menu stays in the viewport and shows every action when there is room", async (test) => {
    const {page} = await openPalette(browser, {
      language: "ru", viewport: {width: 300, height: 500},
    });
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    const trigger = page.locator('[data-row="panel"] .row-menu-trigger');
    const menu = page.locator('[data-row="panel"] .row-menu');
    for (const height of [500, 240]) {
      await page.setViewportSize({width: 300, height});
      await trigger.scrollIntoViewIfNeeded();
      await trigger.press("ArrowDown");
      const bounds = await menu.evaluate((element) => {
        const {left, right, top, bottom} = element.getBoundingClientRect();
        return {left, right, top, bottom, visible: element.clientHeight, content: element.scrollHeight};
      });
      assert(bounds.left >= 7 && bounds.right <= 293 && bounds.top >= 7 && bounds.bottom <= height - 7);
      if (height === 500) assert.equal(bounds.visible, bounds.content);
      else assert(bounds.content > bounds.visible);
      await page.keyboard.press("End");
      assert(await menu.evaluate((element) => {
        const action = document.activeElement.getBoundingClientRect();
        const frame = element.getBoundingClientRect();
        return action.top >= frame.top && action.bottom <= frame.bottom;
      }));
      await page.keyboard.press("Escape");
      await focused(page, '[data-row="panel"] .row-menu-trigger');
    }
  });

  await suite.test("compact action groups and version measurements retain their alignment", async (test) => {
    const {page} = await openPalette(browser, {language: "ru"});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-row="panel"] [data-focus="select"]').check();
    for (const width of [300, 320, 411, 470, 570, 620]) {
      await page.setViewportSize({width, height: 520});
      const layout = await page.evaluate(() => {
        const box = (selector) => document.querySelector(selector).getBoundingClientRect().toJSON();
        return {
          selected: box("#update-selected"), all: box("#update-all"), link: box("#register-local"),
          currentLabel: box('[data-row="panel"] .current-cell .compact-caption'),
          targetLabel: box('[data-row="panel"] .target-cell .compact-caption'),
          currentValue: box('[data-row="panel"] .current-versions > .version'),
          targetValue: box('[data-row="panel"] .target-control'),
        };
      });
      assert.equal(layout.selected.top, layout.all.top, `update group at ${width}px`);
      if (layout.link.top > layout.selected.top) {
        assert.equal(layout.selected.left, layout.link.left, `wrapped link at ${width}px`);
      }
      if (width > 300 && width <= 570) assert.equal(layout.currentLabel.top, layout.targetLabel.top);
      if (width > 300) {
        const centerY = (box) => box.top + box.height / 2;
        assert(Math.abs(centerY(layout.currentValue) - centerY(layout.targetValue)) <= 1, `values at ${width}px`);
      }
    }
  });

  await suite.test("the inline version list updates in the background and saves cached versions", async (test) => {
    const {page} = await openPalette(browser, {language: "ru", viewport: {width: 300, height: 500}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    const target = page.locator('[data-row="panel"] .target-control');
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      Object.assign(model.installed[0], {targets: [], historyLoaded: false});
      model.checking = true;
      publish();
    });
    assert.deepEqual(await target.locator("option").allTextContents(), ["Последняя"]);
    await target.focus();
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      Object.assign(model.installed[0], {targets: ["v2.0.0", "v1.0.0"], historyLoaded: true});
      model.retryAt = Math.floor(Date.now() / 1000) + 3600;
      model.checking = false;
      publish();
    });
    await focused(page, '[data-row="panel"] .target-control');
    assert.deepEqual(await target.locator("option").allTextContents(), ["Последняя", "v2.0.0", "v1.0.0"]);
    await target.selectOption("v1.0.0");
    await page.waitForFunction(() => window.fixture.model.installed[0].target === "v1.0.0");
    await page.waitForFunction(() => document.activeElement.value === "v1.0.0");
    await page.evaluate(() => { window.fixture.rejectTarget = true; });
    await target.selectOption("v2.0.0");
    await page.waitForFunction(() => document.activeElement.value === "v1.0.0");
    assert.equal(await page.locator("dialog[open]").count(), 0);
    assert.deepEqual(await page.evaluate(() => window.fixture.calls.filter(({action}) =>
      ["target", "history"].includes(action)).map(({action, data}) => [action, data.target])),
    [["target", "v1.0.0"], ["target", "v2.0.0"]]);
  });

  await suite.test("the detail header stays visible above release notes and returns to the list", async (test) => {
    for (const width of [300, 680, 1000]) {
      const {page} = await openPalette(browser, {
        language: "en", reducedMotion: "reduce", viewport: {width, height: 440},
      });
      test.after(() => page.close());
      await page.locator("#guide-skip").press("Enter");
      await page.evaluate(() => {
        window.fixture.model.catalog[0].notes = "Detailed release notes.\n\n".repeat(60);
        window.fixture.publish();
      });
      const entry = page.locator('[data-row="panel"] .name');
      await entry.click();
      const header = page.locator(".detail-header");
      const back = header.locator(".back");
      assert.equal(await header.locator("h1").innerText(), "Panel Tool");
      assert.equal(await header.locator(".version").innerText(), "1.0.0");
      const hero = await header.locator(".detail-hero").boundingBox();
      const actions = await header.locator(".detail-actions").boundingBox();
      assert(actions.x + actions.width > hero.x + hero.width - 1);
      if (width === 300) assert(actions.y >= hero.y + hero.height);
      else assert(actions.x > hero.x + hero.width && actions.y < hero.y + hero.height);
      await page.locator("main").evaluate((main) => { main.scrollTop = 500; });
      const top = await page.locator("main").evaluate((main) => {
        return {scroll: main.scrollTop, top: main.getBoundingClientRect().top};
      });
      assert(top.scroll > 200);
      assert.equal((await header.boundingBox()).y, top.top);
      await back.click();
      assert(await entry.isVisible());
      await focused(page, '[data-row="panel"] .name');
    }
  });

  await suite.test("the sticky header manages installed add-ins opened from Marketplace", async (test) => {
    const {page, words, errors} = await openPalette(browser, {
      language: "en", reducedMotion: "reduce", viewport: {width: 300, height: 440},
    });
    test.after(() => page.close());
    const strings = words.en;
    await page.locator("#guide-skip").press("Enter");
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      model.installed[0].author = "Local Author";
      model.catalog[0].notes = "Release notes.\n\n".repeat(50);
      publish();
    });
    await page.locator('[data-tab="catalog"]').click();
    await page.locator('[data-entry="catalog/Example/PanelTool"]').click();
    const header = page.locator(".detail-header");
    const menu = header.locator(".row-menu-trigger");
    const action = (key) => header.getByRole("menuitem", {name: strings[key], exact: true});
    await page.locator("main").evaluate((main) => { main.scrollTop = 450; });
    assert.equal(await header.locator(".detail-author").innerText(), "Author: Local Author");
    assert.equal(await header.locator(".detail-byline > span").first().innerText(), "Example");
    const target = header.locator(".target-control");
    await target.selectOption("v2.0.0");
    await page.waitForFunction(() => window.fixture.model.installed[0].target === "v2.0.0");
    await header.getByRole("button", {name: "Update to v2.0.0"}).click();
    await page.locator("#batch-preview[open]").waitFor();
    await page.locator("#batch-cancel").click();
    await menu.press("ArrowDown");
    await action("start_addin").press("Enter");
    await page.waitForFunction(() => window.fixture.model.installed[0].running);
    await menu.press("ArrowDown");
    await action("enable_startup").press("Enter");
    await page.waitForFunction(() => window.fixture.model.installed[0].startup);
    await menu.press("ArrowDown");
    await action("reload_addin").press("Enter");
    await page.waitForFunction(() => window.fixture.calls.some(({action, data}) =>
      action === "reload" && data.key === "panel"));
    await menu.press("ArrowUp");
    await action("remove").press("Enter");
    await page.locator("#confirm[open]").waitFor();
    await page.locator("#confirm-action").click();
    await page.waitForFunction(() => window.fixture.calls.some(({action, data}) =>
      action === "remove" && data.key === "panel" && data.confirmed));
    assert.deepEqual(errors, []);
  });

  await suite.test("Marketplace pins Install and shows the discovered manifest author", async (test) => {
    const {page} = await openPalette(browser, {language: "en", viewport: {width: 300, height: 440}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-tab="catalog"]').click();
    await page.locator('[data-entry="catalog/Another/GearTool"]').click();
    const header = page.locator(".detail-header");
    assert.equal(await header.locator(".detail-author").textContent(), "Author: Release Author");
    await header.getByRole("button", {name: "Install", exact: true}).click();
    await page.waitForFunction(() => window.fixture.calls.some(({action, data}) =>
      action === "install" && data.repo === "Another/GearTool" && data.asset === "GearTool.zip"));
  });

  await suite.test("the add-in detail offers a localized GitHub star action for linked repositories", async (test) => {
    for (const language of ["en", "ru"]) {
      const {page, words} = await openPalette(browser, {language, viewport: {width: 294, height: 293}});
      test.after(() => page.close());
      await page.locator("#guide-skip").press("Enter");
      await page.locator('[data-tab="catalog"]').press("Enter");
      await page.locator('[data-entry="catalog/Another/GearTool"]').press("Enter");
      const star = page.locator(".detail-header").getByRole("link", {name: words[language].star_on_github, exact: true});
      const [starBox, backBox] = await Promise.all([star.boundingBox(), page.locator(".back").boundingBox()]);
      assert(Math.abs(starBox.y - backBox.y) < 2);
      assert(starBox.x + starBox.width <= 294);
      await star.press("Enter");
      await page.waitForFunction(() => window.fixture.calls.some(({action, data}) =>
        action === "openUrl" && data.url === "https://github.com/Another/GearTool"));
      await focused(page, '[data-detail-focus="star"]');
      await page.locator('[data-tab="installed"]').press("Enter");
      await page.evaluate(() => {
        window.fixture.model.catalog = [];
        window.fixture.publish();
      });
      await page.locator('[data-entry="installed/panel"]').press("Enter");
      assert.equal(await star.getAttribute("href"), "https://github.com/Example/PanelTool");
      await page.locator(".back").press("Enter");
      await page.locator('[data-entry="installed/linked"]').press("Enter");
      assert.equal(await star.count(), 0);
    }
  });

  await suite.test("ZIP picker keeps its title and actions in view at the native minimum", async (test) => {
    const {page} = await openPalette(browser, {language: "ru", viewport: {width: 294, height: 293}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-tab="catalog"]').press("Enter");
    await page.locator('[data-entry="catalog/Supplier/MountTool"]').press("Enter");
    const install = page.locator('[data-detail-focus="install"]');
    await install.press("Enter");
    const dialog = page.locator("#confirm");
    await dialog.waitFor();
    assert(await dialog.evaluate((frame) => {
      const box = frame.getBoundingClientRect();
      const title = frame.querySelector("h1").getBoundingClientRect();
      const actions = frame.querySelector(".dialog-actions").getBoundingClientRect();
      return title.top >= box.top && actions.bottom <= box.bottom;
    }));
    await focused(page, "#confirm-title");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await focused(page, '#packages input[value="MountTool-mac.zip"]');
    await page.keyboard.press("Escape");
    await focused(page, '[data-detail-focus="install"]');
  });

  await suite.test("Marketplace details can select the intended installation", async (test) => {
    const {page} = await openPalette(browser, {language: "en", viewport: {width: 620, height: 500}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      model.catalog[0].matches.push("linked");
      publish();
    });
    await page.locator('[data-tab="catalog"]').click();
    await page.locator('[data-entry="catalog/Example/PanelTool"]').click();
    const header = page.locator(".detail-header");
    await header.locator(".detail-installation").selectOption("linked");
    await header.locator(".target-control").selectOption("v2.0.0");
    await page.waitForFunction(() => window.fixture.model.installed[1].target === "v2.0.0");
    assert.equal(await header.locator(".detail-installation").inputValue(), "linked");
    await header.locator(".row-menu-trigger").press("ArrowUp");
    await header.getByRole("menuitem", {name: "Unlink", exact: true}).press("Enter");
    await page.locator("#confirm[open]").waitFor();
    await page.locator("#confirm-action").click();
    await page.waitForFunction(() => window.fixture.calls.some(({action, data}) =>
      action === "remove" && data.key === "linked" && data.confirmed));
  });

  await suite.test("README shows a loading state and renders text before artwork finishes", async (test) => {
    const {page, words} = await openPalette(browser, {language: "en", viewport: {width: 420, height: 540}});
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-row="panel"] .name').click();
    const loading = page.locator("#detail-readme-loading");
    await loading.waitFor();
    assert.equal(await page.locator(".detail-readme-status").textContent(), words.en.readme_loading);
    const ticket = await detailArtworkTicket(page);
    await page.evaluate((ticket) => {
      window.fixture.artwork({ticket, field: "about",
        value: "# Project\n\nIntroduction before any subheading.\n\n## Usage\n\nRun the command."});
      window.fixture.artwork({ticket, field: "readme", value: "README.md"});
    }, ticket);
    await loading.waitFor({state: "hidden"});
    await page.locator("#detail-readme summary").click();
    assert.equal(await page.locator("#detail-readme .markdown h1").innerText(), "Project");
    assert.match(await page.locator("#detail-readme").innerText(), /Introduction before any subheading/);
    assert.equal(await page.locator(".detail-documentation .external-link").getAttribute("href"),
      "https://github.com/Example/PanelTool/blob/v2.0.0/README.md");
  });

  await suite.test("the gallery grows with the open palette and restores focus", async (test) => {
    const {page} = await openPalette(browser, {
      language: "en", reducedMotion: "reduce", viewport: {width: 300, height: 360},
    });
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    await page.locator('[data-row="panel"] .name').click();
    const ticket = await detailArtworkTicket(page);
    await page.evaluate((ticket) => {
      window.fixture.artwork({ticket, field: "shot0",
        value: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1sAAAAASUVORK5CYII="});
      window.fixture.artwork({ticket, done: true});
    }, ticket);
    const thumbnail = '#detail button[data-shot="0"]';
    await page.locator(thumbnail).click();
    const gallery = page.locator("#gallery-dialog");
    const before = await page.locator("#gallery-image").boundingBox();
    await page.setViewportSize({width: 920, height: 680});
    const after = await page.locator("#gallery-image").boundingBox();
    const bounds = await gallery.boundingBox();
    assert(bounds.width >= 890 && bounds.height >= 650);
    assert(after.width > before.width && after.height > before.height);
    assert.equal(await page.locator("#gallery-image").evaluate((image) => getComputedStyle(image).objectFit), "contain");
    await page.keyboard.press("Escape");
    await gallery.waitFor({state: "hidden"});
    await focused(page, thumbnail);
  });

  await suite.test("downgrade and cancel fit on one row and work from the keyboard at 300px", async (test) => {
    for (const language of ["en", "ru"]) {
      const {page} = await openPalette(browser, {language, reducedMotion: "reduce", viewport: {width: 300, height: 500}});
      test.after(() => page.close());
      await page.locator("#guide-skip").press("Enter");
      await page.evaluate(() => {
        Object.assign(window.fixture.model.installed[0], {direction: "downgrade", tag: "v0.9.0"});
        window.fixture.publish();
      });
      const update = page.locator('[data-row="panel"] .version-update');
      await update.press("Enter");
      await page.locator("#batch-preview[open]").waitFor();
      assert.match(await page.locator("#batch-title").innerText(), /v0\.9\.0/);
      const cancel = page.locator("#batch-cancel");
      const apply = page.locator("#batch-apply");
      assert.equal(await apply.innerText(), language === "ru" ? "Понизить версию" : "Downgrade");
      assert.equal((await cancel.boundingBox()).y, (await apply.boundingBox()).y);
      await page.keyboard.press("Tab");
      await focused(page, "#batch-cancel");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => !activePreview);
      assert(!(await page.evaluate(() => window.fixture.calls.some(({action}) => action === "applyBatch"))));
      await update.press("Enter");
      await page.locator("#batch-preview[open]").waitFor();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      await focused(page, "#batch-apply");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => window.fixture.calls.some(({action}) => action === "applyBatch"));
    }
  });

  await suite.test("updates keep the visible installed rows in place through progress and completion", async (test) => {
    const {page} = await openPalette(browser, {
      language: "ru", viewport: {width: 411, height: 500},
    });
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    const anchor = '[data-row="fixture-3"]';
    await page.evaluate(() => {
      const {model, publish} = window.fixture;
      const source = model.installed[0];
      model.installed.push(...Array.from({length: 10}, (_, index) => ({
        ...source, key: "fixture-" + index, name: "Fixture Add-in " + index,
        direction: "", update: false, canUpdate: false, tag: "",
      })));
      publish();
    });
    const measure = () => page.locator(anchor).evaluate((row) => {
      const main = row.closest("main");
      const {top, left, width} = row.getBoundingClientRect();
      return {top, left, width, scroll: main.scrollTop};
    });
    await page.locator(anchor).evaluate((row) => {
      const main = row.closest("main");
      // Qt WebView does not retain the visual anchor across the row height change.
      main.style.overflowAnchor = "none";
      main.scrollTop += row.getBoundingClientRect().top - main.getBoundingClientRect().top - 24;
    });
    await page.evaluate((selector) => {
      window.fixture.anchor = document.querySelector(selector);
    }, anchor);
    const before = await measure();
    assert(before.scroll > 100);
    for (const phase of ["pending", "downloading", "extracting", "completed"]) {
      await page.evaluate((phase) => {
        const {model, publish} = window.fixture;
        model.pending = phase !== "completed";
        model.installed[0].busy = phase !== "completed";
        model.progress = ["downloading", "extracting"].includes(phase)
          ? [{name: "Panel Tool", tag: "v2.0.0", phase, downloaded: 1e6, total: 2e6}] : [];
        if (phase === "completed") {
          Object.assign(model.installed[0], {version: "2.0.0", direction: "", update: false, canUpdate: false});
          model.batchResults = [{name: "Panel Tool", version: "1.0.0", tag: "v2.0.0", status: "completed"}];
        }
        publish();
      }, phase);
      const current = await measure();
      assert(Math.abs(current.top - before.top) <= 1, phase + " " + JSON.stringify({before, current}));
      assert.equal(current.width, before.width, phase);
      assert(await page.locator(anchor).evaluate((row) => row === window.fixture.anchor), phase + " row retained");
      assert.equal(await page.locator(anchor + ' [data-focus="startup"]').isDisabled(), phase !== "completed");
    }
  });

  await suite.test("a focused row hint stays above an appearing operation dock and closes on scroll", async (test) => {
    const {page} = await openPalette(browser, {
      language: "ru", viewport: {width: 300, height: 500}, reducedMotion: "reduce",
    });
    test.after(() => page.close());
    await page.locator("#guide-skip").press("Enter");
    const anchor = '[data-row="fixture-3"] .name';
    await page.evaluate((selector) => {
      const {model, publish} = window.fixture;
      const source = model.installed[0];
      model.installed.push(...Array.from({length: 8}, (_, i) => ({
        ...source, key: "fixture-" + i, name: "Fixture Add-in " + i,
      })));
      model.pending = true;
      publish();
      const main = document.querySelector("main");
      const name = document.querySelector(selector);
      main.scrollTop += name.getBoundingClientRect().bottom - main.getBoundingClientRect().bottom + 86;
    }, anchor);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const name = page.locator(anchor);
    await name.focus();
    await focused(page, anchor);
    const hint = page.locator(".control-hint:visible");
    await hint.waitFor();
    const before = await hint.boundingBox();
    assert(before.y >= (await name.boundingBox()).y + 24);
    await page.evaluate(() => {
      window.fixture.model.progress = [{name: "Fixture Add-in 3", tag: "v1.0.0", phase: "extracting"}];
      window.fixture.publish();
    });
    await page.waitForFunction((selector) => {
      const rect = document.querySelector(".control-hint:not([hidden])")?.getBoundingClientRect();
      const dock = document.querySelector("#download-dock");
      const name = document.querySelector(selector);
      return !dock.hidden && rect && rect.bottom <= name.getBoundingClientRect().top
        && rect.bottom <= dock.getBoundingClientRect().top - 8;
    }, anchor);
    await focused(page, anchor);
    const description = await name.getAttribute("aria-describedby");
    await page.locator("main").evaluate((main) => { main.scrollTop += 32; });
    await hint.waitFor({state: "hidden"});
    await focused(page, anchor);
    assert.equal(await name.getAttribute("aria-describedby"), description);
  });

  for (const width of [300, 320, 620]) {
    for (const language of ["en", "ru"]) {
      for (const colorScheme of ["light", "dark"]) {
        await suite.test(`${width}px ${language} ${colorScheme}`, async (test) => {
          const {page, words, errors} = await openPalette(browser, {
            language, viewport: {width, height: 520}, colorScheme, reducedMotion: "reduce",
          });
          test.after(() => page.close());
          const strings = words[language];
          const control = (id) => page.locator("#" + id);
          const tab = (key) => page.locator(`[data-tab="${key}"]`);
          const menuItem = (key) => page.getByRole("menuitem", {name: strings[key], exact: true});
          const rowMenu = (key) => page.locator(`[data-row="${key}"] .row-menu-trigger`);

          assert.equal(await page.locator("html").getAttribute("lang"), language);
          assert.equal(await page.locator("html").evaluate((html) => getComputedStyle(html).colorScheme), colorScheme);
          assert(await page.evaluate(() => document.documentElement.scrollWidth === innerWidth &&
            document.querySelector("main").scrollWidth <= innerWidth));
          assert.equal(await control("update-selected").innerText(), strings.update_selected);
          assert.equal(await control("update-all").innerText(), strings.update_all);
          assert.equal(await control("register-local").innerText(), strings.register_folder);
          assert.equal(await control("update-label").innerText(), strings.updates_count.replace("{count}", "1"));
          assert(await page.getByRole("checkbox", {name: strings.updates_count.replace("{count}", "1")}).isVisible());
          assert(await control(width <= 410 ? "check-compact" : "check-label").isVisible());

          assert.equal(await control("guide-open-market").innerText(), strings.guide_open_market);
          await control("guide-skip").press("Tab");
          await focused(page, "#guide-open-market");
          await page.keyboard.press("Enter");
          await focused(page, '[data-tab="catalog"]');
          assert(await control("catalog").isVisible());
          assert(!(await control("store-guide").isVisible()));
          await page.waitForFunction(() => window.fixture.model.settings.guideSeen);

          await tab("settings").press("Enter");
          const [automaticLabel, intervalLabel] = await page.locator(".release-check-fields label").evaluateAll(
            (labels) => labels.map((label) => ({x: label.offsetLeft, y: label.offsetTop})));
          if (width >= 620) {
            assert.equal(automaticLabel.y, intervalLabel.y);
            assert(automaticLabel.x < intervalLabel.x);
          } else {
            assert.equal(automaticLabel.x, intervalLabel.x);
            assert(automaticLabel.y < intervalLabel.y);
          }
          await control("guide-replay").press("Enter");
          await focused(page, "#guide-title");
          assert.equal(await control("guide-progress").innerText(), strings.guide_progress.replace("{step}", "1").replace("{total}", "3"));
          assert.equal(await page.evaluate(() => window.fixture.model.settings.guideSeen), false);
          await tab("installed").press("Enter");
          await control("guide-next").press("Enter");
          await focused(page, "#guide-title");
          assert(!(await control("guide-open-market").isVisible()));
          await tab("catalog").press("Enter");
          await page.locator(".market-card").first().press("Enter");
          assert(!(await control("store-guide").isVisible()));
          await page.keyboard.press("Escape");
          assert.equal(await control("guide-progress").innerText(), strings.guide_progress.replace("{step}", "2").replace("{total}", "3"));
          await control("sources").press("Enter");
          assert(!(await control("store-guide").isVisible()));
          await page.keyboard.press("Escape");
          await control("store-guide").waitFor();
          await control("guide-next").press("Enter");
          assert.equal(await control("guide-account-note").innerText(),
            strings[language === "ru" ? "guide_github_available" : "guide_github_unavailable"]);
          await control("guide-next").press("Enter");
          await focused(page, '[data-tab="catalog"]');
          await page.waitForFunction(() => window.fixture.model.settings.guideSeen);
          const search = control("search");
          await search.fill("PERFORATED");
          assert.equal(await page.locator(".market-card").count(), 1);
          await search.fill("missing add-in");
          assert.equal(await page.locator(".market-card").count(), 0);
          await page.locator("#catalog .empty button").press("Enter");
          await focused(page, "#search");
          await page.locator(".market-card").first().press("Enter");
          await focused(page, "#detail-title");
          await control("detail-title").press("Escape");
          await focused(page, ".market-card");
          await control("sources").press("Enter");
          await focused(page, "#source-url");
          assert.equal(await control("source-url").getAttribute("placeholder"), "4FusionLab");
          assert.equal(await control("source-url").evaluate((input) => input.labels[0].htmlFor), "source-url");
          await control("source-url").fill("example.com/acme");
          await control("source-url").press("Enter");
          await page.locator('#source-url[aria-invalid="true"]').waitFor();
          await control("source-url").press("Escape");
          await focused(page, "#sources");

          await page.locator('[data-entry="catalog/Another/GearTool"]').press("Enter");
          const install = page.getByRole("button", {name: strings.install, exact: true});
          const fromPackage = strings.install_package.replace("{name}", "GearTool.zip").replace("{repo}", "Another/GearTool");
          assert.equal(await control("install-disclosure").innerText(), fromPackage + " " + strings.install_effects);
          assert.equal(await install.getAttribute("aria-describedby"), "install-disclosure");
          await page.evaluate(() => { window.fixture.holdInstall = true; });
          await install.press("Enter");
          await page.waitForFunction(() => window.fixture.releaseInstall);
          await install.press("Enter");
          await page.evaluate(() => {
            window.fixture.holdInstall = false;
            window.fixture.releaseInstall();
          });
          assert(!(await control("confirm").isVisible()));
          await page.keyboard.press("Escape");

          await page.locator('[data-entry="catalog/Supplier/MountTool"]').press("Enter");
          const fromRepo = strings.install_repository.replace("{repo}", "Supplier/MountTool");
          assert.equal(await control("install-disclosure").innerText(), fromRepo + " " + strings.install_effects);
          await install.press("Enter");
          assert.equal(await control("confirm-text").innerText(),
            [strings.select_release_file, fromRepo, strings.install_effects].join(" "));
          assert(!(await control("confirm-action").isEnabled()));
          await page.locator('#packages input[value="MountTool-mac.zip"]').check();
          await control("confirm-action").press("Enter");
          await page.waitForFunction(() => window.fixture.calls.filter(({action}) => action === "install").length === 2);
          const installs = await page.evaluate(() => window.fixture.calls.filter(({action}) => action === "install")
            .map(({data}) => data));
          assert.deepEqual(installs, [
            {repo: "Another/GearTool", asset: "GearTool.zip"}, {repo: "Supplier/MountTool", asset: "MountTool-mac.zip"},
          ]);
          await focused(page, '#detail [data-detail-focus="install"]');
          await page.keyboard.press("Escape");

          await tab("installed").press("Enter");
          const panelMenu = rowMenu("panel");
          assert(!(await control("removal-archive").isVisible()));
          await page.evaluate(() => {
            window.fixture.model.canOpenRemovalArchive = true;
            window.fixture.publish();
          });
          await page.locator("#removal-archive summary").press("Enter");
          assert.equal(await control("archive-help").innerText(), strings.removal_archive_help);
          await control("open-removal-archive").press("Enter");
          await page.waitForFunction(() => window.fixture.calls.some(({action}) => action === "openRemovalArchive"));
          await page.locator("#removal-archive summary").press("Enter");
          const selection = page.locator('[data-row="panel"] [data-focus="select"]');
          await selection.press("Space");
          assert.equal(await control("update-selected").innerText(), strings.update_selected + " (1)");
          await selection.press("Space");
          if (width === 300) {
            await panelMenu.press("ArrowDown");
            await menuItem("start_addin").press("Enter");
            await focused(page, '[data-row="panel"] .row-menu-trigger');
            await panelMenu.press("ArrowDown");
            assert(await menuItem("stop_addin").isVisible());
            await menuItem("enable_startup").press("Enter");
            await panelMenu.press("ArrowDown");
            assert(await menuItem("disable_startup").isVisible());
            await page.keyboard.press("Escape");
          }
          const target = page.locator('[data-row="panel"] .target-control');
          await target.focus();
          await target.selectOption("v2.0.0");
          await page.waitForFunction(() => document.activeElement.matches('[data-row="panel"] .target-control') &&
            document.activeElement.value === "v2.0.0");
          assert.equal(await page.locator("dialog[open]").count(), 0);

          for (const [key, action] of [["panel", "remove"], ["linked", "unlink"], ["app-store", "hide_addin"]]) {
            await rowMenu(key).press("ArrowUp");
            await focused(page, `[data-row="${key}"] .row-menu [role="menuitem"]:last-child`);
            assert(await page.locator('[role="menu"]:visible').evaluate((menu) => {
              const bounds = menu.getBoundingClientRect();
              const focused = document.activeElement.getBoundingClientRect();
              return bounds.left >= 7 && bounds.right <= innerWidth - 7 &&
                focused.top >= bounds.top && focused.bottom <= bounds.bottom;
            }), "The menu and focused item fit in the palette");
            await menuItem(action).press("Enter");
            assert.equal(await control("confirm-action").innerText(), strings[action]);
            assert.equal(await control("confirm-text").innerText(), strings[action + "_description"]);
            await focused(page, '#confirm button[value="cancel"]');
            await page.keyboard.press("Escape");
            await focused(page, `[data-row="${key}"] .row-menu-trigger`);
          }
          await control("update-all").press("Enter");
          await page.locator("#batch-preview[open]").waitFor();
          await focused(page, "#batch-title");
          assert.equal(await control("batch-title").innerText(), strings.update_all);
          assert.equal(await page.locator("#batch-rows li").count(), 3);
          assert.equal(await control("batch-preview").evaluate((dialog) => getComputedStyle(dialog).animationName), "none");
          await page.keyboard.press("Escape");
          await focused(page, "#update-all");

          await selection.press("Space");
          await page.evaluate(() => {
            window.fixture.holdState = true;
            window.dispatchEvent(new Event("focus"));
          });
          await page.waitForFunction(() => window.fixture.releaseState);
          await control("update-selected").press("Enter");
          await page.evaluate(() => {
            window.fixture.holdState = false;
            window.fixture.releaseState();
            window.fixture.releaseState = undefined;
          });
          await page.locator("#batch-preview[open]").waitFor();
          assert.equal(await control("batch-title").innerText(), strings.update_selected);
          assert.equal(await page.locator("#batch-rows li").count(), 1);
          await page.keyboard.press("Escape");
          await focused(page, "#update-selected");
          await selection.press("Space");

          await page.locator('[data-row="panel"] .version-update').press("Enter");
          await page.locator("#batch-preview[open]").waitFor();
          assert.equal(await control("batch-title").innerText(), strings.update_to.replace("{tag}", "v2.0.0"));
          assert.equal(await page.locator("#batch-rows li").count(), 1);
          const previews = await page.evaluate(() => window.fixture.calls.filter(({action}) => action === "preview")
            .map(({data}) => data));
          assert.deepEqual(previews, [
            {mode: "all", keys: []}, {mode: "selected", keys: ["panel"]}, {mode: "single", keys: ["panel"]},
          ]);
          await page.keyboard.press("Escape");

          await tab("settings").press("Enter");
          await page.evaluate(() => { window.fixture.holdPreferences = true; });
          await control("interval").selectOption("120");
          await page.waitForFunction(() => window.fixture.releasePreferences);
          await page.evaluate(() => window.fixture.publish());
          assert.equal(await control("interval").inputValue(), "120");
          await control("automatic").press("Space");
          await page.evaluate(() => {
            window.fixture.holdPreferences = false;
            window.fixture.releasePreferences();
          });
          await page.waitForFunction(() => !window.fixture.model.settings.automatic);
          assert(!(await control("automatic").isChecked()));
          assert(!(await control("interval").isEnabled()));
          const settings = await page.evaluate(() => window.fixture.calls.filter(({action}) => action === "preferences")
            .map(({data}) => data));
          assert.deepEqual(settings, [{automatic: true, interval: 120}, {automatic: false, interval: 120}]);
          if (language === "ru") {
            await control("github-sign-in").press("Enter");
            await control("github-device").waitFor();
            await focused(page, "#github-copy");
            assert.equal(await control("github-code").innerText(), "TEST-1234");
            await control("github-cancel").press("Enter");
            await focused(page, "#github-sign-in");
          } else {
            assert.equal(await control("github-status").innerText(), strings.github_unconfigured);
          }
          await control("language").focus();
          await control("language").selectOption(language === "ru" ? "en" : "ru");
          await focused(page, "#language");
          assert.equal(await control("guide-replay").innerText(), words[language === "ru" ? "en" : "ru"].guide_replay);
          await control("guide-replay").press("Enter");
          await focused(page, "#guide-title");
          await page.keyboard.press("Escape");
          await focused(page, "#guide-replay");

          await tab("installed").press("Enter");
          await page.evaluate(() => {
            window.fixture.model.checking = true;
            window.fixture.publish();
          });
          assert.equal(await control("check").getAttribute("aria-disabled"), "true");
          assert.equal(await control(width <= 410 ? "check-compact" : "check-label").innerText(),
            words[language === "ru" ? "en" : "ru"].checking);
          assert.equal(await page.locator("#check .icon").evaluate((icon) => getComputedStyle(icon).animationName), "none");
          await page.evaluate(() => {
            window.fixture.model.checking = false;
            window.fixture.publish();
          });
          await page.evaluate(() => {
            window.fixture.holdState = true;
            window.dispatchEvent(new Event("focus"));
          });
          await page.waitForFunction(() => window.fixture.releaseState);
          await control("check").press("Enter");
          await page.evaluate(() => window.fixture.releaseState());
          await page.waitForFunction(() => window.fixture.calls.some(({action}) => action === "check"));
          await page.evaluate(() => { window.fixture.failCheck = true; });
          await control("check").press("Enter");
          await control("notice-message").waitFor();
          assert.match(await control("notice-message").innerText(), /Test bridge failure/);
          await control("notice-dismiss").press("Enter");
          await focused(page, "#check");
          await page.evaluate(() => {
            window.fixture.failCheck = false;
            window.fixture.malformedState = true;
          });
          const renderError = page.waitForEvent("console", {
            predicate: (message) => message.type() === "error" && message.text().includes("could not render Fusion state"),
          });
          await control("check").press("Enter");
          await renderError;
          assert.equal(await control("notice-message").innerText(), words[language === "ru" ? "en" : "ru"].palette_error);
          assert.deepEqual(errors, []);
        });
      }
    }
  }
});
