// Dashboard client. Live tab: MJPEG feed + polling of /api/status and /api/incidents.
// Settings tab: alert recipients and sign-in (passwords are sent once, never shown or returned).
const $ = (id) => document.getElementById(id);
const STATUS = { full: "FULL PPE", partial: "PARTIAL PPE", none: "NO PPE", pending: "VERIFYING" };
const MAIL = {
  sent: "Email sent", pending: "Email queued", failed: "Email retrying", disabled: "Email off",
  not_configured: "Email not signed in", no_recipients: "No recipients", rate_limited: "Hourly limit",
  far_only: "Far away - not emailed",
};

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([],
  { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-PPE-Dashboard": "1" },
                               body: JSON.stringify(body || {}) });
  let j = {};
  try { j = await r.json(); } catch (e) { /* empty */ }
  return { ok: r.ok && j.ok !== false, message: j.message || (r.ok ? "Done" : "Failed"), data: j };
}

function say(id, text, good) {
  const el = $(id);
  el.textContent = text || "";
  el.className = "msg " + (good ? "ok" : "bad");
}

/* ------------------------------------------------------------------ tabs */
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("tab-live").hidden = name !== "live";
  $("tab-settings").hidden = name !== "settings";
  if (name === "settings") loadSettings();
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
$("banner").addEventListener("click", () => showTab("settings"));

/* ------------------------------------------------------------------ live */
setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString(); }, 1000);

async function refreshStatus() {
  try {
    const s = await (await fetch("/api/status", { cache: "no-store" })).json();
    $("site").textContent = s.site;
    $("camera").textContent = s.camera;
    const live = $("live");
    live.className = "live " + (s.camera_connected ? "on" : "off");
    live.querySelector("b").textContent = s.camera_connected ? "Live" : "Offline";

    const inc = s.incidents_today || {}, vis = s.visits_today || {};
    $("t-none").textContent = inc.none || 0;
    $("t-partial").textContent = inc.partial || 0;
    $("t-full").textContent = vis.full || 0;

    $("s-camera").innerHTML = s.camera_connected ? '<span class="ok">Live</span>'
      : `<span class="bad">Offline</span> &middot; ${esc(s.camera_status)}`;
    const e = s.email;
    $("s-email").innerHTML = ({ ready: '<span class="ok">Active</span>',
      not_configured: '<span class="bad">Sender not signed in</span>',
      no_recipients: '<span class="bad">No recipients</span>',
      disabled: '<span class="warn">Off</span>' }[e.state] || esc(e.state))
      + (e.mode === "file" ? " (test mode)" : "")
      + (e.last_error ? `<br><span class="bad small">${esc(e.last_error)}</span>` : "");

    const need = [];
    if (/sign-in required/.test(s.camera_status || "")) need.push("camera");
    if (e.state === "not_configured") need.push("sender email");
    $("banner").hidden = !need.length;
    $("banner").textContent = need.length ? `Sign-in required: ${need.join(" and ")} - open Settings` : "";
  } catch (err) {
    $("live").className = "live off";
    $("live").querySelector("b").textContent = "Stopped";
  }
}

let lastIncidentKey = "";
async function refreshIncidents() {
  try {
    const rows = await (await fetch("/api/incidents?limit=48", { cache: "no-store" })).json();
    const key = rows.map((r) => r.id + r.email_status).join(",");
    if (key === lastIncidentKey) return;
    lastIncidentKey = key;
    $("incidents").innerHTML = rows.length ? rows.map((r) => `
      <div class="incident">
        <img loading="lazy" src="${r.image_url}" data-caption="${esc(fmtTime(r.ts))} · Person #${r.person} · ${STATUS[r.status]}">
        <div class="meta">
          <div class="row"><span class="pill ${r.status}">${STATUS[r.status]}</span>
            <span class="pill mail ${r.email_status}">${MAIL[r.email_status] || esc(r.email_status)}</span></div>
          <div class="row"><b>Person #${r.person}</b><span class="muted small">${fmtTime(r.ts)}</span></div>
          <div class="missing">Missing: ${esc(r.missing.join(", ") || "-")}</div>
        </div>
      </div>`).join("") : '<span class="muted">None yet</span>';
  } catch (err) { /* monitor restarting - keep the last list */ }
}

$("incidents").addEventListener("click", (ev) => {
  if (ev.target.tagName !== "IMG") return;
  $("modal-img").src = ev.target.src;
  $("modal-caption").textContent = ev.target.dataset.caption;
  $("modal").hidden = false;
});
$("modal").addEventListener("click", () => { $("modal").hidden = true; });
$("feed").addEventListener("error", () => setTimeout(() => { if (!document.hidden) $("feed").src = "/stream.mjpg?" + Date.now(); }, 2000));
// Pause the video while the tab is hidden / minimised (the monitor then stops drawing it - saves CPU)
document.addEventListener("visibilitychange", () => {
  $("feed").src = document.hidden ? "" : "/stream.mjpg?" + Date.now();
});

/* ------------------------------------------------------------------ settings */
let recipients = [];

function renderRecipients() {
  $("rcpt-list").innerHTML = recipients.map((a, i) =>
    `<li><span>${esc(a)}<small>${i === 0 ? "To" : "Cc"}</small></span><button data-i="${i}" title="Remove">&times;</button></li>`
  ).join("") || '<li class="muted">No recipients</li>';
}

function stateLine(rec, who) {
  if (!rec.signed_in) return '<span class="bad">Not signed in</span>';
  const until = rec.expires_at ? ` &middot; valid until ${fmtDate(rec.expires_at)}` : "";
  return `<span class="ok">Signed in</span> as ${esc(who)}${until}`;
}

async function loadSettings() {
  const s = await (await fetch("/api/settings", { cache: "no-store" })).json();
  recipients = s.recipients.slice();
  renderRecipients();
  $("smtp-state").innerHTML = stateLine(s.smtp, s.smtp.address);
  $("smtp-address").value = s.smtp.address || "";
  $("smtp-link").href = s.smtp.app_password_url;
  $("nvr-state").innerHTML = stateLine(s.nvr, `${s.nvr.username} @ ${s.nvr.ip}`);
  $("nvr-ip").value = s.nvr.ip || "";
  $("nvr-user").value = s.nvr.username || "";
  $("nvr-channel").value = s.nvr.channel || 1;
  $("nvr-link").href = s.nvr.login_url;
  $("smtp-password").value = "";
  $("nvr-password").value = "";
}

$("rcpt-list").addEventListener("click", (ev) => {
  if (ev.target.tagName !== "BUTTON") return;
  recipients.splice(+ev.target.dataset.i, 1);
  renderRecipients();
  say("rcpt-msg", "Not saved yet", false);
});
function addRecipient() {
  const a = $("rcpt-new").value.trim().toLowerCase();
  if (!/^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$/.test(a)) { say("rcpt-msg", "Not a valid email address", false); return; }
  if (!recipients.includes(a)) recipients.push(a);
  $("rcpt-new").value = "";
  renderRecipients();
  say("rcpt-msg", "Not saved yet", false);
}
$("rcpt-add").addEventListener("click", addRecipient);
$("rcpt-new").addEventListener("keydown", (e) => { if (e.key === "Enter") addRecipient(); });
$("rcpt-save").addEventListener("click", async () => {
  const r = await post("/api/settings/recipients", { recipients });
  say("rcpt-msg", r.ok ? "Saved" : r.message, r.ok);
  if (r.ok) { recipients = r.data.recipients; renderRecipients(); }
});

async function withBusy(btn, msgId, fn) {
  btn.disabled = true;
  say(msgId, "Checking...", true);
  try { await fn(); } finally { btn.disabled = false; }
}

$("smtp-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const btn = ev.submitter;
  withBusy(btn, "smtp-msg", async () => {
    const r = await post("/api/settings/smtp", { address: $("smtp-address").value, password: $("smtp-password").value });
    $("smtp-password").value = "";
    say("smtp-msg", r.message, r.ok);
    if (r.ok) loadSettings();
  });
});
$("smtp-test").addEventListener("click", (ev) => withBusy(ev.target, "smtp-msg", async () => {
  const r = await post("/api/settings/smtp/test");
  say("smtp-msg", r.message, r.ok);
}));
$("smtp-out").addEventListener("click", async () => {
  await post("/api/settings/smtp/signout");
  say("smtp-msg", "Signed out", true);
  loadSettings();
});

$("nvr-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  withBusy(ev.submitter, "nvr-msg", async () => {
    const r = await post("/api/settings/nvr", { ip: $("nvr-ip").value, username: $("nvr-user").value,
      password: $("nvr-password").value, channel: $("nvr-channel").value });
    $("nvr-password").value = "";
    say("nvr-msg", r.message, r.ok);
    if (r.ok) loadSettings();
  });
});
$("nvr-out").addEventListener("click", async () => {
  await post("/api/settings/nvr/signout");
  say("nvr-msg", "Signed out", true);
  loadSettings();
});

refreshStatus(); refreshIncidents();
setInterval(refreshStatus, 2000);
setInterval(refreshIncidents, 4000);
