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

async function linkLatestZip() {
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
}
// The versioned ZIP link also works without the GitHub API.
linkLatestZip().catch(() => {});

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

function animateAuthors(list) {
    const cloud = list.parentElement;
    const canvas = cloud.querySelector("canvas");
    const pen = canvas.getContext("2d");
    if (!pen || !list.childElementCount) return;
    const mode = document.querySelector(".cloud-mode");
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    let asList = reduced.matches;
    let visible = false;
    let frame = 0;
    let previous = 0;
    let angle = 0;
    let width = 0, height = 0, radius = 0;
    const rows = Math.min(4, Math.ceil(list.children.length / 4));
    const ranked = [...list.children].sort((a, b) =>
        Number(b.dataset.stars || 0) - Number(a.dataset.stars || 0));
    const highest = Math.max(1, Number(ranked[0].dataset.stars || 0));
    const rowOrder = Array.from({length: rows}, (_, i) => i)
        .sort((a, b) => Math.abs(a + .5 - rows / 2) - Math.abs(b + .5 - rows / 2));
    const items = ranked.map((item, i) => {
        const group = Math.floor(i * rows / ranked.length);
        const first = Math.ceil(group * ranked.length / rows);
        const size = Math.ceil((group + 1) * ranked.length / rows) - first;
        const weight = Math.log1p(Number(item.dataset.stars || 0)) / Math.log1p(highest);
        return {item, weight, row: rowOrder[group],
            phase: (i - first) * 2 * Math.PI / size + Math.PI / 2 + group * 1.37};
    });
    function track(row) {
        const level = (row + .5) / rows;
        return {
            rx: radius * (.68 + .32 * Math.sin(level * Math.PI)),
            ry: Math.min(26, height / rows * .25),
            cy: height * (.08 + .84 * level)
        };
    }
    // Project the avatar and its trail through the same tilted orbital plane.
    function project(ring, t) {
        const dx = Math.cos(t) * ring.rx;
        return { x: width / 2 + dx, y: ring.cy + Math.sin(t) * ring.ry - dx * .12 };
    }
    function trail(node) {
        const {ring, t, x, y, depth} = node;
        const strength = Math.max(0, (depth - .49) / .51);
        if (!strength) return;
        const start = t - (.24 + .32 * node.weight);
        const tail = project(ring, start);
        const ink = pen.createLinearGradient(tail.x, tail.y, x, y);
        ink.addColorStop(0, "rgba(221, 112, 42, 0)");
        ink.addColorStop(1, `rgba(221, 112, 42, ${strength * .86})`);
        pen.strokeStyle = ink;
        pen.lineWidth = .9 + strength;
        pen.beginPath();
        pen.moveTo(tail.x, tail.y);
        for (let step = 1; step <= 10; step++) {
            const point = project(ring, start + (t - start) * step / 10);
            pen.lineTo(point.x, point.y);
        }
        pen.stroke();
    }
    function position() {
        pen.clearRect(0, 0, width, height);
        pen.strokeStyle = "#d2cec4";
        pen.lineWidth = .85;
        pen.setLineDash([1, 5]);
        for (const path of paths) pen.stroke(path);
        pen.setLineDash([]);
        for (const node of items) {
            node.t = node.phase + angle;
            node.depth = (Math.sin(node.t) + 1) / 2;
            Object.assign(node, project(node.ring, node.t));
            node.scale = (.63 + node.depth * .43) * (.85 + node.weight * .36);
            // The avatar is centered horizontally, with its caption below.
            node.item.style.transform = `translate(${node.x}px, ${node.y}px) translateX(-50%) scale(${node.scale}) translateY(-17px)`;
            node.item.style.opacity = .35 + node.depth * .65;
            node.item.style.zIndex = Math.round(node.depth * 90);
            trail(node);
        }
        const occupied = [];
        for (const node of [...items].sort((a, b) => b.depth - a.depth)) {
            const half = node.captionWidth * node.scale / 2 + 4;
            const box = { left: node.x - half, right: node.x + half,
                top: node.y + 17 * node.scale, bottom: node.y + 39 * node.scale };
            const clear = node.depth > .34 && !occupied.some(other =>
                box.left < other.right && box.right > other.left &&
                box.top < other.bottom && box.bottom > other.top) &&
                !items.some(other => other.depth > node.depth &&
                    Math.abs(node.x - other.x) < half + 13 * other.scale &&
                    other.y + 15 * other.scale > box.top && other.y - 15 * other.scale < box.bottom);
            if (clear) occupied.push(box);
            node.item.classList.toggle("unlabelled", !clear);
        }
    }
    const paths = [];
    function measure() {
        if (asList) return;
        width = list.clientWidth;
        height = list.clientHeight;
        const longest = Math.max(...items.map(n => n.item.offsetWidth));
        radius = Math.max(1, (width - longest * 1.29) / 2 - 5);
        const dpr = Math.min(devicePixelRatio, 2);
        canvas.width = Math.ceil(width * dpr);
        canvas.height = Math.ceil(height * dpr);
        pen.setTransform(dpr, 0, 0, dpr, 0, 0);
        paths.length = 0;
        for (let row = 0; row < rows; row++) {
            const path = new Path2D();
            const ring = track(row);
            for (let i = 0; i <= 96; i++) {
                const point = project(ring, i / 96 * Math.PI * 2);
                if (i === 0) path.moveTo(point.x, point.y);
                else path.lineTo(point.x, point.y);
            }
            paths.push(path);
        }
        for (const node of items) {
            node.ring = track(node.row);
            node.captionWidth = node.item.offsetWidth;
        }
        position();
    }
    function tick(now) {
        if (previous) angle += Math.min(now - previous, 60) * .00017;
        previous = now;
        position();
        frame = requestAnimationFrame(tick);
    }
    function play() {
        cancelAnimationFrame(frame);
        previous = 0;
        if (visible && !asList && !document.hidden &&
            !list.querySelector("a:hover, a:focus-visible")) frame = requestAnimationFrame(tick);
    }
    function setMode() {
        cloud.classList.toggle("orbiting", !asList);
        canvas.hidden = asList;
        mode.textContent = asList ? (russian ? "Показать облаком" : "Show as cloud")
            : (russian ? "Показать списком" : "Show as list");
        if (asList) for (const {item} of items) item.removeAttribute("style");
        measure();
        play();
    }
    mode.hidden = false;
    mode.addEventListener("click", () => { asList = !asList; setMode(); });
    reduced.addEventListener("change", () => { asList = reduced.matches; setMode(); });
    for (const event of ["pointerover", "pointerout", "focusin", "focusout"])
        list.addEventListener(event, () => requestAnimationFrame(play));
    document.addEventListener("visibilitychange", play);
    new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; play(); }).observe(list);
    new ResizeObserver(measure).observe(list);
    document.fonts.ready.then(measure);
    setMode();
}

async function showDefaultSources() {
    const stars = typeof catalogAuthorStars === "object" ? catalogAuthorStars : {};
    let owners = Object.keys(stars);
    try {
        const response = await fetch(
            "https://raw.githubusercontent.com/4FusionLab/CommunityStore/refs/heads/main/trusted-stores.json",
            { cache: "no-cache", referrerPolicy: "no-referrer" },
        );
        if (response.ok) {
            const config = await response.json();
            if (config.schemaVersion === 1 && Array.isArray(config.owners) && config.owners.length <= 32)
                owners = config.owners;
        }
    } catch {
        // The build snapshot is also available offline.
    }
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
        const avatar = document.createElement("span");
        avatar.className = "author-avatar";
        avatar.setAttribute("aria-hidden", "true");
        avatar.textContent = owner.slice(0, 2).toUpperCase();
        const photo = document.createElement("img");
        photo.alt = "";
        photo.loading = "lazy";
        photo.referrerPolicy = "no-referrer";
        photo.addEventListener("error", () => photo.remove(), { once: true });
        photo.src = link.href + ".png?size=80";
        avatar.append(photo);
        const name = document.createElement("span");
        name.className = "author-name";
        name.textContent = owner;
        link.append(avatar, name);
        if (Number.isSafeInteger(stars[owner])) {
            item.dataset.stars = stars[owner];
            const score = document.createElement("span");
            score.className = "author-score";
            score.textContent = "★ " + stars[owner].toLocaleString(document.documentElement.lang);
            link.append(score);
        }
        item.append(link);
        list.append(item);
    }
    animateAuthors(list);
}
showDefaultSources().catch(() => {});
