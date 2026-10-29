/*!
 * Agent Presence website widget: shows what you're coding right now.
 * https://github.com/abelxmendoza/discord-agent-presence (MIT)
 *
 *   <div data-agent-presence="your-user/your-user"></div>
 *   <script src="https://abelxmendoza.github.io/discord-agent-presence/embed.js" async></script>
 *
 * Reads the agent-presence-status branch of that repo, which
 * `agent-presence web setup` keeps up to date: each machine publishes
 * its own status.d/<machine-id>.json there, and this widget merges them
 * (mergeStatuses) so several machines can be live at once, each on its
 * own evidence. The legacy single status.json is the fallback when no
 * per-machine files exist. Optional attributes:
 *   data-branch="agent-presence-status"   data-compact   data-theme="light"
 * data-hide="tokens,machines,projects" leaves those parts out on this page.
 * A parent with data-agent-presence-section is unhidden once data loads, so a
 * whole page section can stay hidden until there's something to show.
 * Restyle with CSS variables on the element: --ap-bg, --ap-panel, --ap-text,
 * --ap-muted, --ap-line, --ap-accent, --ap-live, --ap-font, --ap-mono, --ap-radius.
 *
 * Presence truth (docs/presence-truth-model.md): this widget fails closed.
 * A status only renders as live when its heartbeat is fresh (TTL below) AND
 * it carries verified activity — an executing agent or an active human. A
 * payload that claims "coding" for a merely waiting agent, a stale payload,
 * and a malformed payload all render as waiting / offline / "no live status
 * reported", never as live. Sample data must carry demo:true and is badged
 * DEMO, never "live".
 */
(function () {
  "use strict";
  var TTL = 150;           // heartbeat freshness budget, seconds (truth model)
  var SKEW = 60;           // a heartbeat further ahead than this is untrusted
  var POLL = 120 * 1000;
  var STATES = ["vibe-coding", "coding", "waiting", "idle", "offline", "summary"];

  var CSS = [
    ":host{all:initial;display:block;font-family:var(--ap-font,system-ui,-apple-system,'Segoe UI',sans-serif);color:var(--ap-text,#e6e8ee)}",
    ".c{background:var(--ap-bg,#111214);border:1px solid var(--ap-line,#2b2d31);border-radius:var(--ap-radius,14px);overflow:hidden;max-width:560px}",
    ".bar{height:4px;background:linear-gradient(90deg,#f97316,#db2777 50%,#14b8a6)}",
    ".top{display:flex;gap:14px;padding:16px 18px 12px;align-items:center}",
    ".pic{position:relative;flex:none;width:64px;height:64px}",
    ".pic img{width:64px;height:64px;border-radius:12px;display:block;object-fit:cover;background:var(--ap-panel,#1e1f22)}",
    ".dot{position:absolute;right:-4px;bottom:-4px;width:16px;height:16px;border-radius:50%;border:4px solid var(--ap-bg,#111214);background:#80848e}",
    ".dot.on{background:var(--ap-live,#23a55a);box-shadow:0 0 10px var(--ap-live,#23a55a)}",
    ".dot.idle{background:#f0b232}",
    "h3{margin:0;font-size:17px;line-height:1.25;font-weight:700}",
    ".sub{margin-top:3px;color:var(--ap-muted,#949ba4);font-size:13px}",
    ".sub b{color:var(--ap-text,#e6e8ee);font-weight:600}",
    ".sec{padding:4px 18px 12px}",
    ".lbl{font:600 11px var(--ap-mono,ui-monospace,Menlo,monospace);letter-spacing:.6px;text-transform:uppercase;color:var(--ap-accent,#fb923c);margin:8px 0 6px}",
    "ul{list-style:none;margin:0;padding:0;display:grid;gap:6px}",
    "li{background:var(--ap-panel,#1a1b1e);border:1px solid var(--ap-line,#2b2d31);border-radius:10px;padding:8px 10px;font-size:13.5px;line-height:1.35;min-width:0}",
    "li .m{color:var(--ap-muted,#949ba4);font-size:12.5px}",
    ".tag{display:inline-block;font:600 11px var(--ap-mono,ui-monospace,Menlo,monospace);padding:1px 6px;border-radius:6px;background:rgba(255,255,255,.06);margin-right:6px}",
    ".tag.busy{color:var(--ap-live,#23a55a)}.tag.wait{color:#f0b232}",
    "a{color:inherit;text-decoration:none;border-bottom:1px dotted currentColor}",
    ".stats{display:flex;flex-wrap:wrap;gap:8px}",
    ".stat{flex:1 1 90px;background:var(--ap-panel,#1a1b1e);border:1px solid var(--ap-line,#2b2d31);border-radius:10px;padding:8px 10px}",
    ".stat b{display:block;font-size:17px}.stat span{color:var(--ap-muted,#949ba4);font-size:12px}",
    ".foot{display:flex;justify-content:space-between;gap:10px;padding:10px 18px 14px;color:var(--ap-muted,#949ba4);font-size:12px}",
    ".foot a{border:0}",
    ":host([data-compact]) .sec{display:none}",
    ":host([data-theme=light]){--ap-bg:#fff;--ap-panel:#f5f6f8;--ap-text:#14161a;--ap-muted:#5c6370;--ap-line:#e1e4ea}",
    "@media (prefers-reduced-motion:no-preference){.dot.on{animation:p 2s ease-in-out infinite}@keyframes p{50%{opacity:.45}}}"
  ].join("");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function dur(s) {
    s = Math.max(0, Math.round(s));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? h + "h " + m + "m" : m ? m + "m" : s + "s";
  }
  function ago(t) {
    var s = Date.now() / 1000 - t;
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  // Epoch seconds for a number or an ISO-8601 string; anything else —
  // including the legacy string "now" — is not a time and returns null.
  function parseTime(v) {
    if (typeof v === "number" && isFinite(v)) return v;
    if (typeof v === "string") {
      var t = Date.parse(v);
      return isNaN(t) ? null : t / 1000;
    }
    return null;
  }
  function arr(x) {
    return Array.isArray(x) ? x.filter(function (e) { return e && typeof e === "object"; }) : [];
  }
  function isCoding(state) { return state === "vibe-coding" || state === "coding"; }

  // The one place a payload is judged. Pure, so tests can drive it directly.
  // Returns { ok, state, stale, ref, demo }: `state` is the state that may be
  // SHOWN, which is the published state only after the truth checks pass.
  function validateStatus(st, now) {
    if (!st || typeof st !== "object" || Array.isArray(st)) return { ok: false };
    if (STATES.indexOf(st.state) === -1) return { ok: false };
    if (st.demo) return { ok: true, state: st.state, stale: false, ref: null, demo: true };
    var ref = parseTime(st.heartbeat_at);
    if (ref === null) ref = parseTime(st.published_at);
    if (ref === null && typeof st.updated === "number") ref = st.updated;
    if (ref === null) ref = parseTime(st.updated);
    var stale = ref === null || now - ref > TTL || ref - now > SKEW;
    var state = st.state;
    if (stale) {
      state = "offline";  // a stale snapshot proves nothing about right now
    } else if (isCoding(state)) {
      // "Coding" needs verified activity, however the payload was produced:
      // an executing agent or an active human. Waiting agents are not that.
      var agents = arr(st.agents);
      var executing = st.agent_executing === true ||
        agents.some(function (a) { return a.status === "busy"; });
      var waiting = st.agent_waiting === true ||
        agents.some(function (a) { return a.status === "waiting"; });
      if (!executing && st.human_active !== true)
        state = waiting ? "waiting" : "idle";
    }
    return { ok: true, state: state, stale: stale, ref: ref, demo: false };
  }
  function tokens(n) {
    return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n);
  }

  function fetchJson(repo, branch, path) {
    var api = "https://api.github.com/repos/" + repo + "/contents/" + path + "?ref=" + encodeURIComponent(branch);
    var raw = "https://raw.githubusercontent.com/" + repo + "/" + branch + "/" + path;
    // The API is fresh but limited to 60 calls an hour per visitor; raw is cached ~5 min.
    return fetch(api, { headers: { Accept: "application/vnd.github.raw+json" }, cache: "no-store" })
      .then(function (r) { if (!r.ok) throw r; return r.json(); })
      .catch(function () {
        return fetch(raw + "?t=" + Math.floor(Date.now() / 60000)).then(function (r) {
          if (!r.ok) throw new Error("status " + r.status);
          return r.json();
        });
      });
  }
  function load(repo, branch) { return fetchJson(repo, branch, "status.json"); }

  var STATE_RANK = { "vibe-coding": 5, coding: 4, waiting: 3, idle: 2, summary: 1, offline: 0 };
  function machineOf(st) {
    var m = arr(st.machines)[0] || {};
    return {
      id: st.machine_id || m.id || m.label || "machine",
      label: m.label || st.machine_id || "Machine",
      icon: m.icon || "🖥",
      os: m.os || "",
      os_full: m.os_full || m.os || "",
      agents: typeof m.agents === "number" ? m.agents : arr(st.agents).length,
      last_seen_at: m.last_seen_at || null
    };
  }

  // Merge per-machine payloads (status.d/*.json) into one status object
  // render() can consume. Every payload is judged on its own first, with
  // the same truth rules as a single status: a machine whose heartbeat
  // is stale drops out of the merged view entirely — it can neither
  // hide nor fake the others — and if ALL are stale the result is the
  // newest snapshot, which validateStatus renders as offline. Only
  // genuine activity is listed: busy agents from any fresh machine, and
  // waiting agents only from machines actually in a coding/waiting
  // state — never a tool merely because it is installed or detected.
  function mergeStatuses(payloads, now) {
    var judged = [];
    (payloads || []).forEach(function (st) {
      var v = validateStatus(st, now);
      if (v.ok && !v.demo) judged.push({ st: st, v: v });
    });
    if (!judged.length) return null;
    var fresh = judged.filter(function (j) { return !j.v.stale; });
    if (!fresh.length) {
      judged.sort(function (a, b) { return (b.v.ref || 0) - (a.v.ref || 0); });
      return judged[0].st;
    }
    fresh.sort(function (a, b) { return (b.v.ref || 0) - (a.v.ref || 0); });
    var state = "offline";
    fresh.forEach(function (j) { if (STATE_RANK[j.v.state] > STATE_RANK[state]) state = j.v.state; });
    var lead = fresh.filter(function (j) { return j.v.state === state; })[0] || fresh[0];
    var agents = [], machines = [], seenMachines = {}, tools = [], seenTools = {},
        projects = [], seenProjects = {}, highlights = [], seenHl = {},
        anyExecuting = false, anyWaiting = false, human = false, humanUnknown = false,
        lastActive = null, today = null, streakFrom = null;
    fresh.forEach(function (j) {
      var st = j.st, vs = j.v.state;
      var mach = machineOf(st);
      if (!seenMachines[mach.id]) { seenMachines[mach.id] = true; machines.push(mach); }
      if (st.human_active === true) human = true;
      else if (st.human_active === "unknown") humanUnknown = true;
      var la = parseTime(st.last_active);
      if (la !== null && (lastActive === null || la > lastActive)) lastActive = la;
      arr(st.highlights).forEach(function (h) {
        if (h && h.key && !seenHl[h.key]) { seenHl[h.key] = true; highlights.push(h); }
      });
      if (st.today && typeof st.today === "object") {
        today = today || { agent_seconds: 0, commits: 0, tokens: 0 };
        ["agent_seconds", "commits", "tokens"].forEach(function (k) {
          if (typeof st.today[k] === "number") today[k] += st.today[k];
        });
      }
      var tot = typeof st.streak_days_total === "number" ? st.streak_days_total : -1;
      if (!streakFrom || tot > streakFrom.tot) streakFrom = { tot: tot, st: st };
      if (vs !== "vibe-coding" && vs !== "coding" && vs !== "waiting") return;
      arr(st.agents).forEach(function (a) {
        if (a.status !== "busy" && a.status !== "waiting") return;
        var copy = {};
        for (var k in a) copy[k] = a[k];
        if (!copy.machine) copy.machine = mach.label;
        agents.push(copy);
        if (a.status === "busy") anyExecuting = true; else anyWaiting = true;
      });
      arr(st.tools).forEach(function (t) {
        if (t && t.id && !seenTools[t.id]) { seenTools[t.id] = true; tools.push(t); }
      });
      arr(st.projects).forEach(function (p) {
        if (p && p.name && !seenProjects[p.name]) { seenProjects[p.name] = true; projects.push(p); }
      });
    });
    var nBusy = agents.filter(function (a) { return a.status === "busy"; }).length;
    var nWait = agents.length - nBusy;
    var headline = nBusy ? "⚡ " + nBusy + " agent" + (nBusy > 1 ? "s" : "") + " working" +
                    (nWait ? " · " + nWait + " waiting" : "")
      : nWait ? "⏳ " + nWait + " agent" + (nWait > 1 ? "s" : "") + " waiting"
      : "● hands on keyboard";
    var ref = fresh[0].v.ref;
    var merged = {
      v: 2, schema_version: 2,
      name: lead.st.name || fresh[0].st.name,
      image: lead.st.image || null,
      state: state,
      verb: lead.st.verb, title: lead.st.title, model: lead.st.model,
      headline: headline,
      tools: tools, agents: agents.slice(0, 8), projects: projects.slice(0, 4),
      machines: machines, today: today || {}, highlights: highlights,
      streak: streakFrom ? (streakFrom.st.streak || 0) : 0,
      last_active: lastActive !== null ? new Date(lastActive * 1000).toISOString() : null,
      heartbeat_at: new Date(ref * 1000).toISOString(),
      published_at: new Date(ref * 1000).toISOString(),
      updated: ref,
      machine_reachable: true,
      agent_executing: anyExecuting, agent_waiting: anyWaiting,
      human_active: human ? true : humanUnknown ? "unknown" : false,
      landing: lead.st.landing || fresh[0].st.landing,
      tracking_since: fresh.map(function (j) { return j.st.tracking_since; })
        .filter(Boolean).sort()[0] || null
    };
    if (streakFrom && streakFrom.tot >= 0) {
      merged.streak_days_total = streakFrom.st.streak_days_total;
      if (streakFrom.st.streak_provenance) merged.streak_provenance = streakFrom.st.streak_provenance;
      if (streakFrom.st.streak_source) merged.streak_source = streakFrom.st.streak_source;
      if (streakFrom.st.streak_cached) merged.streak_cached = true;
    }
    return merged;
  }

  // The per-machine view: list status.d/ on the branch (one extra API
  // call), fetch each file, merge. Any failure — no such directory, an
  // empty one, a rate limit — returns null and the caller falls back to
  // the legacy single status.json, so the widget never regresses.
  function loadMachines(repo, branch) {
    var api = "https://api.github.com/repos/" + repo + "/contents/status.d?ref=" + encodeURIComponent(branch);
    return fetch(api, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    }).then(function (entries) {
      var names = (Array.isArray(entries) ? entries : []).filter(function (e) {
        return e && e.type === "file" && /\.json$/.test(e.name || "");
      }).slice(0, 8).map(function (e) { return e.name; });
      if (!names.length) return null;
      return Promise.all(names.map(function (n) {
        return fetchJson(repo, branch, "status.d/" + n).catch(function () { return null; });
      })).then(function (list) {
        var ok = list.filter(Boolean);
        return ok.length ? mergeStatuses(ok, Date.now() / 1000) : null;
      });
    }).catch(function () { return null; });
  }

  function renderUnavailable(root) {
    root.innerHTML = "<style>" + CSS + "</style>" +
      "<div class='c'><div class='bar'></div><div class='top'><div><h3>No live status reported</h3>" +
      "<div class='sub'>Nothing current is being published right now.</div></div></div>" +
      "<div class='foot'><span>updates while I code</span>" +
      "<a href='https://abelxmendoza.github.io/discord-agent-presence/' target='_blank' rel='noopener'>🤖 Agent Presence</a></div></div>";
  }

  function render(root, st, hide) {
    hide = hide || {};
    var now = Date.now() / 1000;
    var v = validateStatus(st, now);
    if (!v.ok) { renderUnavailable(root); return; }
    var state = v.state;
    var demo = v.demo;
    var live = !demo && isCoding(state);
    var showcase = live || demo;  // demo data may illustrate, but is badged DEMO
    var agents = arr(st.agents), projects = arr(st.projects), machines = arr(st.machines);
    var words = { "vibe-coding": ["is vibe coding", "Vibe coding"], coding: ["is coding", "Coding"],
                  waiting: ["has an agent waiting", "An agent is waiting"],
                  idle: ["isn't coding right now", "Not coding right now"], offline: ["is offline", "Offline"],
                  summary: ["is building", "Building"] };
    var head = (st.name ? esc(st.name) + " " + words[state][0] : words[state][1]) +
               (demo ? " <span class='tag'>DEMO</span>" : "");
    var sub;
    if (showcase) {
      var tools = arr(st.tools).map(function (t) { return t.name; }).filter(Boolean).join(", ");
      sub = "<b>" + esc(st.verb || "Playing") + " " + esc(st.title || "Coding") + "</b>" +
            (tools ? " · " + esc(tools) : "") + (st.model ? " · " + esc(st.model) : "");
    } else {
      var la = parseTime(st.last_active);
      sub = la !== null ? "Last coding " + esc(ago(la)) : "Agent Presence";
    }
    var html = "<div class='c'><div class='bar'></div><div class='top'>";
    if (st.image) html += "<div class='pic'><img alt='' src='" + esc(st.image) + "'><span class='dot " +
      (live ? "on" : state === "idle" || state === "waiting" ? "idle" : "") + "'></span></div>";
    html += "<div><h3>" + head + "</h3><div class='sub'>" + sub + "</div></div></div>";

    if ((showcase || state === "waiting") && agents.length) {
      html += "<div class='sec'><div class='lbl'>Agents · " + esc(st.headline || "") + "</div><ul>";
      agents.slice(0, 6).forEach(function (a) {
        // A phone origin is a label on an executing job only: the moment
        // the agent stops executing, the marker goes with the evidence.
        var meta = [a.mobile_origin && a.status === "busy" ? "📱 " + a.mobile_origin + " →" : null,
                    a.tool_name, a.project, a.model, a.machine].filter(Boolean).map(esc).join(" · ");
        html += "<li><span class='tag " + (a.status === "busy" ? "busy'>working" : "wait'>waiting") +
                "</span>" + esc(a.action) + "<div class='m'>" + meta + "</div></li>";
      });
      html += "</ul></div>";
    }
    if (showcase && projects.length && !hide.projects) {
      html += "<div class='sec'><div class='lbl'>Projects</div><ul>";
      projects.slice(0, 4).forEach(function (p) {
        var name = p.url ? "<a href='" + esc(p.url) + "' target='_blank' rel='noopener'>" + esc(p.name) + "</a>" : esc(p.name);
        var bits = [];
        if (p.branch) bits.push("🌿 " + esc(p.branch));
        if (p.added || p.removed) bits.push("+" + p.added + " −" + p.removed);
        if (p.commits_today) bits.push("📦 " + p.commits_today + " today");
        if (p.ci) bits.push(esc(p.ci));
        html += "<li>📁 " + name + (bits.length ? "<div class='m'>" + bits.join(" · ") + "</div>" : "") + "</li>";
      });
      html += "</ul></div>";
    }
    if (showcase && machines.length && !hide.machines) {
      html += "<div class='sec'><div class='lbl'>" + (machines.length > 1 ? machines.length + " machines" : "Machine") + "</div><ul>";
      machines.forEach(function (m) {
        var what = m.agents ? m.agents + " agent" + (m.agents > 1 ? "s" : "") : "online";
        html += "<li>" + esc(m.icon) + " " + esc(m.label) + "<div class='m'>" + esc(m.os_full || m.os) + " · " + what + "</div></li>";
      });
      html += "</ul></div>";
    }
    var t = st.today && typeof st.today === "object" ? st.today : {}, stats = [];
    if (showcase && t.agent_seconds >= 60) stats.push([dur(t.agent_seconds), "agent time today"]);
    if (showcase && t.tokens && !hide.tokens) stats.push([tokens(t.tokens), "tokens today"]);
    if (showcase && t.commits) stats.push([t.commits, "commits today"]);
    arr(st.highlights).forEach(function (h) {
      if (!(hide.tokens && h.key === "tokens") && !(h.key === "streak" && stats.some(function (x) { return x[1] === "coding streak"; })))
        stats.push([h.value, h.label]);
    });
    if (!st.highlights && st.streak >= 2) stats.push([st.streak + " days", "coding streak"]);
    if (stats.length) {
      html += "<div class='sec'><div class='stats'>" + stats.map(function (s) {
        return "<div class='stat'><b>" + esc(s[0]) + "</b><span>" + esc(s[1]) + "</span></div>";
      }).join("") + "</div></div>";
    }
    var foot = demo ? "sample data · not a live status"
      : live ? "● live · updated " + esc(ago(typeof st.updated === "number" ? st.updated : v.ref))
      : v.stale && v.ref ? "⚠ stale · data from " + esc(new Date(v.ref * 1000).toLocaleString())
      : "updates while I code";
    html += "<div class='foot'><span>" + foot + "</span><a href='" +
            esc(st.landing || "https://abelxmendoza.github.io/discord-agent-presence/") +
            "' target='_blank' rel='noopener'>🤖 Agent Presence</a></div></div>";
    root.innerHTML = "<style>" + CSS + "</style>" + html;
  }

  function mount(el) {
    if (el.__ap) return;
    el.__ap = true;
    var repo = el.getAttribute("data-agent-presence");
    var branch = el.getAttribute("data-branch") || "agent-presence-status";
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo || "")) return;
    var root = el.attachShadow ? el.attachShadow({ mode: "open" }) : el;
    var hide = {};
    (el.getAttribute("data-hide") || "").split(",").forEach(function (k) { if (k.trim()) hide[k.trim()] = true; });
    var last = null;
    function tick() {
      var sec = el.closest && el.closest("[data-agent-presence-section]");
      function done(st) {
        last = st; render(root, st, hide); el.hidden = false;
        if (sec) sec.hidden = false;  // a page section that waits for real data
      }
      function fail() {
        // A failed fetch is itself the truth: no live status is reported.
        // Show the neutral card; if older data exists, the re-render timer
        // keeps showing it with its (now stale) timestamp until it downgrades.
        if (!last) { renderUnavailable(root); el.hidden = false; if (sec) sec.hidden = false; }
      }
      // Per-machine files first (several machines live at once); the
      // legacy single status.json remains the fallback.
      loadMachines(repo, branch).then(function (merged) {
        if (merged) { done(merged); return; }
        load(repo, branch).then(done).catch(fail);
      }).catch(function () { load(repo, branch).then(done).catch(fail); });
    }
    tick();
    setInterval(function () { if (!document.hidden) tick(); }, POLL);
    setInterval(function () {  // keep the session timer and "updated" text current
      if (last) render(root, last, hide);
    }, 30000);
  }

  function scan() {
    var els = document.querySelectorAll("[data-agent-presence]");
    for (var i = 0; i < els.length; i++) mount(els[i]);
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
    else scan();
  }
  var api = { scan: scan, render: render, validateStatus: validateStatus, parseTime: parseTime, TTL: TTL,
              mergeStatuses: mergeStatuses, loadMachines: loadMachines };
  if (typeof window !== "undefined") window.AgentPresence = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;  // unit tests under Node
})();
