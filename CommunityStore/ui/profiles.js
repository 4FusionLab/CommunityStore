"use strict";

let shownProfileToken;
const profileDialog = document.getElementById("profile-dialog");
const profileBody = document.getElementById("profile-body");
const profileSubmit = document.getElementById("profile-submit");

function profileChoice(parent, label, checked) {
  const wrapper = node("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  wrapper.append(input, document.createTextNode(label));
  parent.append(wrapper);
  return input;
}

function profileGroup(title) {
  const group = node("fieldset");
  group.append(node("legend", "", title));
  profileBody.append(group);
  return group;
}

function profileGroupToggle(group) {
  const entries = [...group.querySelectorAll('input[type="checkbox"]')];
  if (!entries.length) { group.hidden = true; return; }
  const bulk = profileChoice(group.firstChild, t("profile_all"), false);
  function sync() {
    bulk.checked = entries.every(box => box.checked);
    bulk.indeterminate = !bulk.checked && entries.some(box => box.checked);
  }
  bulk.onchange = () => { entries.forEach(box => { box.checked = bulk.checked; }); sync(); };
  entries.forEach(box => box.addEventListener("change", sync));
  sync();
}

function profileField(parent, title, input) {
  const label = node("label", "profile-field", title);
  label.append(input);
  parent.append(label);
}

function selectProfileValue(options, value) {
  const select = node("select");
  options.forEach(([key, text]) => select.add(new Option(text, key)));
  select.value = value;
  return select;
}

function prefLabel(key, value) {
  if (key === "language") return t(value === "system" ? "system_language" : "language_" + value);
  if (key === "interval") return t("interval_" + value);
  return t(value ? "profile_on" : "profile_off");
}

function openProfileExport() {
  find("profile-feedback").textContent = "";
  profileBody.replaceChildren();
  find("profile-title").textContent = t("profile_export");
  profileSubmit.textContent = t("save_target");
  const kind = selectProfileValue([["transfer", t("profile_transfer")], ["collection", t("profile_collection")]], "transfer");
  profileField(profileBody, t("profile_mode"), kind);
  const name = node("input");
  name.maxLength = 160;
  name.value = t("profile_transfer");
  profileField(profileBody, t("profile_name"), name);
  const groups = node("div");
  profileBody.append(groups);
  function populate() {
    groups.replaceChildren();
    const transfer = kind.value === "transfer";
    const selections = [];
    const group = title => {
      const set = profileGroup(t(title));
      groups.append(set);
      return set;
    };
    const addins = group("addins_short");
    const installedRepos = new Set();
    function addRow(row, installed) {
      const repo = row.releaseRepo || row.repo;
      if (repo && installed) installedRepos.add(repo.toLowerCase());
      const id = row.id;
      const portable = repo && id && (installed ? row.sourceVerified || row.boundSource : row.available);
      const external = installed && row.sourceType !== "community_store";
      const checkbox = profileChoice(addins, (repo || row.name) + (external && portable ? " · " + t("profile_release_copy") : ""),
        transfer && installed && !(external && portable));
      let tag, exact;
      if (portable) {
        const tags = new Set(row.targets || row.profileTags || []);
        if (row.installedTag) tags.add(row.installedTag);
        const current = row.installedTag || "";
        const choices = [["Latest", t("latest")], ...[...tags].map(v => [v,v])];
        if (transfer && !current) choices.unshift(["", t("profile_manual")]);
        choices.push(["custom", t("profile_exact_tag")]);
        tag = selectProfileValue(choices, transfer && installed ? current : "Latest");
        tag.setAttribute("aria-label", repo + " · " + t("target_version"));
        exact = node("input");
        exact.hidden = true;
        exact.maxLength = 160;
        exact.placeholder = "v1.2.0";
        exact.setAttribute("aria-label", repo + " · " + t("profile_exact_tag"));
        tag.onchange = () => {
          exact.hidden = tag.value !== "custom";
          if (!exact.hidden) exact.focus();
        };
        addins.append(tag, exact);
      } else checkbox.parentNode.append(" · " + t("profile_manual"));
      selections.push(doc => {
        if (!checkbox.checked) return;
        if (!portable || !tag.value) {
          (doc.manual ||= []).push({name: row.name, reason: "noPublishedSource"});
          return;
        }
        const installTarget = tag.value === "custom" ? exact.value.trim() : tag.value;
        const item = {repo, manifestId: id, installTarget,
          updateTarget: transfer && installed ? row.target : installTarget};
        if (transfer && installed) item.runOnStartup = row.startup;
        (doc.addins ||= []).push(item);
      });
    }
    state.installed.filter(row => !row.self).forEach(row => addRow(row,true));
    if (!transfer) state.catalog.filter(row => row.available && !installedRepos.has(row.repo.toLowerCase()))
      .forEach(row => addRow(row,false));
    profileGroupToggle(addins);
    const sources = group("sources");
    (state.settings.ownerSubscriptions || []).forEach(owner => {
      const box = profileChoice(sources, owner, transfer);
      selections.push(doc => { if (box.checked) (doc.ownerSubscriptions ||= []).push(owner); });
    });
    profileGroupToggle(sources);
    const prefs = group("settings");
    for (const key of ["language", "automatic", "interval"]) {
      const value = state.settings[key];
      const box = profileChoice(prefs, t(key) + " · " + prefLabel(key,value), transfer);
      selections.push(doc => { if (box.checked) (doc.settings ||= {})[key] = value; });
    }
    profileGroupToggle(prefs);
    const update = () => {
      profileSubmit.disabled = !name.value.trim() || !groups.querySelector('fieldset > label > input:checked');
    };
    groups.onchange = name.oninput = update;
    update();
    profileSubmit.onclick = async () => {
      const doc = {format: "fusion-community-store-profile", schemaVersion: 1, name: name.value.trim(), kind: kind.value};
      selections.forEach(read => read(doc));
      profileSubmit.disabled = true;
      const received = await send("exportProfile", doc);
      if (received && state.profileSaved) profileDialog.close();
      else find("profile-feedback").textContent = state.notice || "";
      update();
    };
  }
  kind.onchange = () => {
    if ([t("profile_transfer"), t("profile_collection")].includes(name.value)) name.value = kind.selectedOptions[0].text;
    populate();
  };
  populate();
  showPaletteDialog(profileDialog);
}

function renderProfilePreview() {
  const plan = state.profilePreview;
  if (!plan || plan.token === shownProfileToken) return;
  shownProfileToken = plan.token;
  find("profile-feedback").textContent = "";
  profileBody.replaceChildren();
  find("profile-title").textContent = t("profile_review") + " · " + plan.name;
  for (const [key, change] of Object.entries(plan.settings)) {
    profileBody.append(node("p", "", t(key) + " · " + prefLabel(key,change.from) + " → " + prefLabel(key,change.to)));
  }
  if (plan.owners.length) profileBody.append(node("p", "", t("sources") + " · + " + plan.owners.join(", ")));
  const choices = [];
  for (const row of plan.rows) {
    const group = profileGroup(row.name);
    const text = t("profile_" + row.action) + " · " + (row.version ? row.version + " → " : "") + row.tag;
    if (row.action === "skip") { group.append(node("p","",text + " · " + row.reason)); continue; }
    const box = profileChoice(group,text,row.action === "install" || row.action === "configure");
    const policy = row.currentTarget && row.currentTarget !== row.target ? row.currentTarget + " → " + row.target : row.target;
    const startup = row.startup ?? row.currentStartup ?? true;
    const previous = row.currentStartup !== undefined && row.currentStartup !== startup ?
      prefLabel("automatic", row.currentStartup) + " → " : "";
    group.append(node("p", "", t("target_version") + " · " + policy + " · " + t("startup") + " · "
      + previous + prefLabel("automatic", startup)));
    let asset;
    if (row.assets.length) {
      const choices = row.assets.map(v => [v,v]);
      if (choices.length > 1) choices.unshift(["",t("profile_select_zip")]);
      asset = selectProfileValue(choices,choices[0][0]);
      profileField(group,t("release_zip"),asset);
    }
    choices.push({box,asset,repo:row.name});
  }
  for (const entry of plan.manual) profileBody.append(node("p","",entry.name + " · " + t("profile_manual")));
  const update = () => {
    const selected = choices.filter(v => v.box.checked);
    profileSubmit.disabled = !(plan.owners.length || Object.keys(plan.settings).length || selected.length)
      || selected.some(v => v.asset && !v.asset.value);
  };
  if (!plan.rows.length && !plan.manual.length && !plan.owners.length && !Object.keys(plan.settings).length)
    profileBody.append(node("p","",t("profile_satisfied")));
  profileBody.onchange = update;
  profileSubmit.textContent = t("profile_apply");
  profileSubmit.onclick = async () => {
    const selected = Object.fromEntries(choices.filter(v => v.box.checked).map(v => [v.repo,v.asset?.value || ""]));
    profileSubmit.disabled = true;
    await send("applyProfile",{token:plan.token,selected});
    if (!state.profilePreview) profileDialog.close();
    else find("profile-feedback").textContent = state.notice || "";
    update();
  };
  update();
  if (!profileDialog.open) showPaletteDialog(profileDialog);
}

document.getElementById("profile-close").onclick = () => profileDialog.close();
document.getElementById("profile-export").onclick = () => { profileBody.onchange = null; openProfileExport(); };
document.getElementById("profile-import").onclick = () => send("importProfile");
