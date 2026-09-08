"use strict";

/* Front minimal : pas de framework, pas de build. Trois écrans qui tapent
   sur la même API — itinéraire, prochains passages, lignes du réseau. */

const statusLine = document.getElementById("status");

// stop_id retenu pour chaque champ d'arrêt ; le texte saisi ne suffit pas,
// l'API travaille sur des identifiants GTFS.
const selected = { origin: null, destination: null, stop: null };

/* Onglets ----------------------------------------------------------- */

const tabs = [...document.querySelectorAll(".tabs button")];

function openPanel(name) {
  tabs.forEach((tab) => {
    const active = tab.dataset.panel === name;
    tab.setAttribute("aria-selected", String(active));
    document.getElementById(tab.dataset.panel).hidden = !active;
  });
  if (name === "lines") loadRoutes();
}

tabs.forEach((tab) => tab.addEventListener("click", () => openPanel(tab.dataset.panel)));

/* Autocomplétion ---------------------------------------------------- */

function autocomplete(inputId, listId) {
  const input = document.getElementById(inputId);
  const list = document.getElementById(listId);
  let timer;

  const close = () => { list.hidden = true; list.replaceChildren(); };

  input.addEventListener("input", () => {
    selected[inputId] = null;           // la saisie invalide le choix
    clearTimeout(timer);
    if (input.value.trim().length < 2) return close();
    // On attend une pause de frappe : une requête par lettre serait
    // inutilement bavarde pour un résultat identique.
    timer = setTimeout(() => suggest(input, list), 200);
  });

  input.addEventListener("blur", () => setTimeout(close, 150));
}

async function suggest(input, list) {
  let stops;
  try {
    const response = await fetch(`/api/stops?q=${encodeURIComponent(input.value.trim())}`);
    if (!response.ok) return;
    stops = await response.json();
  } catch {
    return;                              // hors ligne : on n'affiche rien
  }

  list.replaceChildren(...stops.map((stop) => {
    const item = document.createElement("li");
    item.textContent = stop.name;
    // mousedown, pas click : le blur du champ arriverait avant.
    item.addEventListener("mousedown", () => {
      input.value = stop.name;
      selected[input.id] = stop.id;
      list.hidden = true;
    });
    return item;
  }));
  list.hidden = stops.length === 0;
}

autocomplete("origin", "origin-list");
autocomplete("destination", "destination-list");
autocomplete("stop", "stop-list");

/* Itinéraire -------------------------------------------------------- */

const journeyForm = document.getElementById("search");
const journeyOut = document.getElementById("result");

journeyForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!selected.origin || !selected.destination) {
    return fill(journeyOut, `<p class="error">Choisissez un arrêt dans la liste proposée.</p>`);
  }

  const params = new URLSearchParams({
    origin: selected.origin,
    destination: selected.destination,
    departure: document.getElementById("time").value,
    day: document.getElementById("day").value,
    realtime: document.getElementById("realtime").checked,
  });

  await run(journeyForm, journeyOut, `/api/route?${params}`, renderJourney);
});

function renderJourney(journey) {
  const changes = journey.transfers === 0
    ? "direct"
    : `${journey.transfers} changement${journey.transfers > 1 ? "s" : ""}`;

  const legs = journey.legs.map((leg) => {
    const label = leg.mode === "walk"
      ? `<span class="badge">à pied</span>${esc(leg.to_stop)}`
      : `${badge(leg.route, leg.color)}${esc(leg.to_stop)}`;
    const detail = leg.mode === "walk"
      ? `${leg.duration_min} min de marche`
      : `direction ${esc(leg.headsign)} · ${leg.duration_min} min`;
    const delay = leg.delay_min > 0
      ? ` <span class="delay">+${leg.delay_min} min</span>` : "";

    return `<li class="leg ${leg.mode}">
              <time>${esc(leg.departure)}</time>
              <div><div>${label}${delay}</div>
                   <div class="detail">depuis ${esc(leg.from_stop)} · ${detail}</div></div>
            </li>`;
  }).join("");

  return `<div class="summary">
            <span class="times">${esc(journey.departure)} → ${esc(journey.arrival)}</span>
            <span class="meta">${journey.duration_min} min · ${changes}${journey.realtime ? " · temps réel" : ""}</span>
          </div>
          <ul class="legs">${legs}</ul>`;
}

/* Prochains passages ------------------------------------------------ */

const boardForm = document.getElementById("board-form");
const boardOut = document.getElementById("board-result");

boardForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!selected.stop) {
    return fill(boardOut, `<p class="error">Choisissez un arrêt dans la liste proposée.</p>`);
  }

  const params = new URLSearchParams({
    after: document.getElementById("board-time").value,
    day: document.getElementById("board-day").value,
    realtime: document.getElementById("board-realtime").checked,
    limit: 15,
  });

  await run(boardForm, boardOut,
            `/api/stops/${encodeURIComponent(selected.stop)}/departures?${params}`,
            renderBoard);
});

function renderBoard(departures) {
  if (departures.length === 0) {
    return `<p class="hint">Aucun passage à cette heure-là. Le réseau ne
            circule peut-être pas ce jour, ou le service est terminé.</p>`;
  }

  const rows = departures.map((d) => {
    const late = d.delay_min > 0
      ? `<span class="delay">+${d.delay_min} min</span>` : "";
    return `<li class="departure">
              <time>${esc(d.expected)}</time>
              <div><div>${badge(d.route, d.color)}${esc(d.headsign)} ${late}</div>
                   <div class="detail">quai ${esc(d.stop)}${
                     d.delay_min > 0 ? ` · théorique ${esc(d.time)}` : ""}</div></div>
            </li>`;
  }).join("");

  return `<ul class="legs">${rows}</ul>`;
}

/* Lignes ------------------------------------------------------------ */

const routeList = document.getElementById("route-list");
const routeDetail = document.getElementById("route-detail");
let routesLoaded = false;

async function loadRoutes() {
  if (routesLoaded) return;
  try {
    const response = await fetch("/api/routes");
    const routes = await response.json();
    routeList.replaceChildren(...routes.map(routeButton));
    routesLoaded = true;
  } catch {
    routeList.textContent = "Lignes indisponibles.";
  }
}

function routeButton(route) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "route-chip";
  button.innerHTML = badge(route.name, route.color);
  button.title = route.long_name || route.name;
  button.addEventListener("click", () => showRoute(route.id));
  return button;
}

async function showRoute(routeId) {
  fill(routeDetail, "<p>Chargement…</p>");
  try {
    const response = await fetch(`/api/routes/${encodeURIComponent(routeId)}`);
    const route = await response.json();
    if (!response.ok) return fill(routeDetail, `<p class="error">${esc(route.detail)}</p>`);
    fill(routeDetail, renderRoute(route));
    // Depuis le parcours d'une ligne, aller voir les passages d'un arrêt.
    routeDetail.querySelectorAll("[data-stop]").forEach((node) => {
      node.addEventListener("click", () => {
        selected.stop = node.dataset.stop;
        document.getElementById("stop").value = node.textContent;
        openPanel("board");
        boardForm.requestSubmit();
      });
    });
  } catch {
    fill(routeDetail, `<p class="error">Serveur injoignable.</p>`);
  }
}

function renderRoute(route) {
  const stops = route.stops.map((stop) =>
    `<li><button type="button" class="link" data-stop="${esc(stop.id)}">${esc(stop.name)}</button></li>`
  ).join("");

  return `<div class="summary">
            <span class="times">${badge(route.name, route.color)}</span>
            <span class="meta">${esc(route.long_name)}</span>
          </div>
          <p class="detail">${esc(route.mode)} · ${route.stops.length} arrêts ·
             ${route.service_days} jours de circulation<br>
             Destinations : ${route.destinations.map(esc).join(" · ") || "—"}</p>
          <ol class="stops">${stops}</ol>`;
}

/* Utilitaires ------------------------------------------------------- */

/** Enchaîne appel, états d'attente et rendu — le même pour chaque écran. */
async function run(form, out, url, render) {
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  fill(out, "<p>Recherche…</p>");
  try {
    const response = await fetch(url);
    const body = await response.json();
    fill(out, response.ok ? render(body)
                          : `<p class="error">${esc(body.detail)}</p>`);
  } catch {
    fill(out, `<p class="error">Serveur injoignable.</p>`);
  } finally {
    button.disabled = false;
  }
}

function fill(node, html) { node.innerHTML = html; }

function esc(text) {
  const node = document.createElement("span");
  node.textContent = text ?? "";
  return node.innerHTML;
}

/** Pastille de ligne aux couleurs du GTFS, quand elles sont fournies. */
function badge(name, color) {
  if (!/^#[0-9a-f]{6}$/i.test(color ?? "")) {
    return `<span class="badge">${esc(name)}</span>`;
  }
  // Luminance perçue : l'orange du METTIS veut du texte noir, le violet
  // des lignes urbaines du blanc. Le GTFS le dit, mais un calcul évite de
  // trimballer une couleur de plus dans chaque réponse.
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.substr(i, 2), 16));
  const ink = (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#111" : "#fff";
  return `<span class="badge" style="background:${color};color:${ink}">${esc(name)}</span>`;
}

/* Démarrage --------------------------------------------------------- */

(function init() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  // Heure locale, pas UTC : à 23h30 à Metz, toISOString donnerait demain.
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}`;

  for (const id of ["day", "board-day"]) document.getElementById(id).value = today;
  for (const id of ["time", "board-time"]) document.getElementById(id).value = time;

  fetch("/api/network")
    .then((r) => r.json())
    .then((n) => {
      const period = n.first_day
        ? ` · horaires du ${fr(n.first_day)} au ${fr(n.last_day)}` : "";
      statusLine.textContent =
        `${n.stations} arrêts · ${n.routes} lignes · ${n.trips} courses${period}`
        + (n.realtime ? " · temps réel actif" : "");
    })
    .catch(() => { statusLine.textContent = "Service indisponible."; });
})();

/** '2026-09-08' -> '08/09'. */
function fr(day) {
  const [, month, date] = day.split("-");
  return `${date}/${month}`;
}
