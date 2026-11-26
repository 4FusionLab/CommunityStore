const russian = document.documentElement.lang === "ru";
const projectLinks = [...document.querySelectorAll("[data-project]")];
function selectProject(id) {
    for (const link of projectLinks) {
        const selected = link.dataset.project === id;
        link.setAttribute("aria-current", String(selected));
        document.getElementById(link.dataset.project).hidden = !selected;
    }
}
for (const link of projectLinks) {
    link.addEventListener("click", (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)
            return;
        event.preventDefault();
        selectProject(link.dataset.project);
    });
}
selectProject(
    projectLinks.find((link) => link.hash === location.hash)?.dataset.project ||
        projectLinks[0].dataset.project,
);
const hero = document.querySelector(".hero");
const motion = matchMedia(
    "(hover:hover) and (prefers-reduced-motion:no-preference)",
);
hero.addEventListener("pointermove", (event) => {
    if (!motion.matches) return;
    const bounds = hero.getBoundingClientRect();
    hero.style.setProperty(
        "--px",
        ((event.clientX - bounds.left) / bounds.width - 0.5) * 14 + "px",
    );
    hero.style.setProperty(
        "--py",
        ((event.clientY - bounds.top) / bounds.height - 0.5) * 8 + "px",
    );
});
hero.addEventListener("pointerleave", () => {
    hero.style.removeProperty("--px");
    hero.style.removeProperty("--py");
});

async function linkReleaseAssets() {
    const response = await fetch(
        "https://api.github.com/repos/4FusionLab/CommunityStore/releases/latest",
        { referrerPolicy: "no-referrer" },
    );
    if (!response.ok) return;
    const release = await response.json();
    const version = /^v(\d+\.\d+\.\d+)$/.exec(release.tag_name)?.[1];
    if (!version || release.draft || release.prerelease) return;
    const assetNames = new Set(
        release.assets
            .filter((asset) => asset.state === "uploaded")
            .map((asset) => asset.name),
    );
    const zipName = "CommunityStore-" + version + ".zip";
    if (!assetNames.has(zipName)) return;
    const download =
        "https://github.com/4FusionLab/CommunityStore/releases/download/" +
        release.tag_name +
        "/";
    const zip = document.querySelector("[data-zip]");
    zip.href = download + zipName;
    zip.textContent =
        (russian ? "Скачать ZIP · " : "Download ZIP · ") + release.tag_name;
    for (const link of document.querySelectorAll("[data-installer]")) {
        if (!assetNames.has(link.dataset.installer)) continue;
        link.href = download + link.dataset.installer;
        link.textContent = link.dataset.download;
        link.closest(".platform").hidden = false;
        link.closest("[data-installers]").hidden = false;
    }
}
// The versioned ZIP link also works without the GitHub API.
linkReleaseAssets().catch(() => {});

const separation = document.querySelector("[data-separation]");
const canvas = document.querySelector(".ejixSketch canvas");
const ctx = canvas.getContext("2d");
const cubeSide = 68;
const pocketDepth = cubeSide / 2;
const cube = [
    [0, 0],
    [68, 0],
    [68, 68],
    [0, 68],
];
// One closed footprint defines both the blind pocket and the extracted solid.
const profile = [
    [12, 13],
    [42, 10],
    [56, 23],
    [53, 45],
    [35, 56],
    [13, 47],
];

function surface(outline, z, shift = 0) {
    const path = new Path2D();
    outline.forEach(([x, y], i) => {
        const u = 263 + 2.35 * x - 1.75 * y + shift;
        const v = 118 + 1.1 * x + 1.45 * y - 1.27 * z;
        if (i === 0) path.moveTo(u, v);
        else path.lineTo(u, v);
    });
    path.closePath();
    return path;
}

function paint(path, color) {
    ctx.fillStyle = color;
    ctx.fill(path);
    ctx.stroke(path);
}

function walls(outline, bottom, top, shift, inside) {
    outline.forEach(([x, y], i) => {
        const [nx, ny] = outline[(i + 1) % outline.length];
        const dx = nx - x;
        const dy = ny - y;
        // Screen-space winding determines which vertical faces face the viewer.
        if (2.35 * dx - 1.75 * dy < 0 === inside) return;
        const low = (a, b) => [
            263 + 2.35 * a - 1.75 * b + shift,
            118 + 1.1 * a + 1.45 * b - 1.27 * bottom,
        ];
        const path = new Path2D();
        const [a, b] = low(x, y);
        const [c, d] = low(nx, ny);
        const height = (top - bottom) * 1.27;
        path.moveTo(a, b - height);
        path.lineTo(c, d - height);
        path.lineTo(c, d);
        path.lineTo(a, b);
        path.closePath();
        const colors = inside
            ? ["#557a68", "#6d9681"]
            : outline === profile
              ? ["#c57943", "#e5a668"]
              : ["#658f7c", "#83af98"];
        paint(path, colors[Math.abs(dx) > Math.abs(dy) ? 1 : 0]);
    });
}

function drawEjix() {
    const progress = Number(separation.value) / 100;
    // Lift vertically until the core clears the rim, then move it aside.
    const lift = Math.min(progress / 0.48, 1) * (pocketDepth + 5);
    const shift = Math.max(0, (progress - 0.48) / 0.52) * 210;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.lineWidth = 1.4;
    ctx.strokeStyle = "#37483d";
    ctx.lineJoin = "round";
    walls(cube, 0, cubeSide, 0, false);
    paint(surface(cube, cubeSide), "#b5d7bd");
    const opening = surface(profile, cubeSide);
    ctx.save();
    ctx.clip(opening);
    paint(opening, "#537561");
    walls(profile, cubeSide - pocketDepth, cubeSide, 0, true);
    paint(surface(profile, cubeSide - pocketDepth), "#94b69b");
    ctx.restore();
    ctx.stroke(opening);

    if (lift > 0)
        walls(
            profile,
            Math.max(cubeSide, cubeSide - pocketDepth + lift),
            cubeSide + lift,
            shift,
            false,
        );
    paint(surface(profile, cubeSide + lift, shift), "#ffc285");
}
separation.addEventListener("input", drawEjix);
drawEjix();

// Reveal the linked project before the browser scrolls to its gallery entry.
for (const link of document.querySelectorAll(".directory a[href^='#']")) {
    link.addEventListener("click", () => selectProject(link.hash.slice(1)));
}

async function showDefaultSources() {
    const response = await fetch(
        "https://raw.githubusercontent.com/4FusionLab/CommunityStore/refs/heads/main/trusted-stores.json",
        { cache: "no-cache", referrerPolicy: "no-referrer" },
    );
    if (!response.ok) return;
    const { schemaVersion, owners } = await response.json();
    if (schemaVersion !== 1 || !Array.isArray(owners) || owners.length > 32)
        return;
    const list = document.querySelector("[data-owners]");
    for (const owner of new Set(owners)) {
        if (
            typeof owner !== "string" ||
            !/^[a-z\d][a-z\d-]{0,38}$/i.test(owner)
        )
            continue;
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = "https://github.com/" + owner;
        link.textContent = owner;
        item.append(link);
        list.append(item);
    }
}
showDefaultSources().catch(() => {});
