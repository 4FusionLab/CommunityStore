function reportButton(kind, subject) {
  const label = t(kind === "source" ? "report_source" : "report_addin");
  const title = subject ? t("action_for_addin", {action: label, name: subject}) : label;
  const control = button(label, "link", () => {
    const dialog = find("report-dialog");
    find("report-title").textContent = title;
    find("report-scope").textContent = t(kind === "source" ? "report_source_scope" : "report_addin_scope");
    find("report-developer").hidden = kind !== "addin";
    find("report-public").onclick = () => {
      dialog.close();
      send("report", kind === "source" ? {kind, owner: subject} : {kind, repo: subject});
    };
    showPaletteDialog(dialog);
    dialog.querySelector(".report-copy").scrollTop = 0;
  });
  if (subject) control.setAttribute("aria-label", title);
  return control;
}

document.getElementById("report-security").addEventListener("click", () => {
  find("report-dialog").close();
  send("report", {kind: "security"});
});
