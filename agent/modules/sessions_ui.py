"""
sessions_ui.py — Interactive Cross-Platform Sessions Router Web Console.

Served by gateway_server.py on GET / and GET /sessions so the user can
view, manage, route, export, and interact with sessions across Claude,
Arena.AI, ChatGPT, OpenRouter, Groq, and OmniRoute from a browser.
"""

SESSIONS_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Unified Sessions Router — Claude · Arena.AI · ChatGPT</title>
<style>
  :root {
    --bg: #0b0e14; --panel: #131823; --panel-2: #19202e; --border: #252f42;
    --text: #e6edf7; --muted: #93a1b8; --accent: #5b9cf6; --green: #38c172;
    --claude: #d97757; --arena: #8b5cf6; --chatgpt: #10a37f; --other: #3b82f6;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }
  header { padding: 12px 18px; border-bottom: 1px solid var(--border); background: var(--panel);
           display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
  .tabs { display: flex; gap: 6px; flex-wrap: wrap; }
  .tab { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--panel-2);
         color: var(--muted); cursor: pointer; font-size: 12px; font-weight: 600; }
  .tab.active { background: var(--accent); color: #08101c; border-color: var(--accent); }
  main { flex: 1; display: grid; grid-template-columns: 320px 1fr; min-height: 0; }
  aside { border-right: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
  .aside-top { padding: 10px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
  .row { display: flex; gap: 6px; }
  input, select, textarea, button { font-family: inherit; font-size: 13px; border-radius: 7px; border: 1px solid var(--border);
                                    background: var(--bg); color: var(--text); padding: 7px 10px; }
  button { cursor: pointer; background: var(--panel-2); font-weight: 600; }
  button.primary { background: var(--accent); color: #07101d; border-color: var(--accent); }
  button.danger { background: #3b1720; color: #ff8093; border-color: #6b2232; }
  .session-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
  .sess-card { padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel-2);
               cursor: pointer; transition: border-color .15s; }
  .sess-card:hover, .sess-card.active { border-color: var(--accent); }
  .sess-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sess-preview { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 6px; }
  .badges { display: flex; flex-wrap: wrap; gap: 4px; }
  .badge { font-size: 10px; padding: 2px 6px; border-radius: 999px; font-weight: 700; text-transform: uppercase; }
  .b-claude { background: rgba(217,119,87,.2); color: #f6ad93; border: 1px solid rgba(217,119,87,.4); }
  .b-arena { background: rgba(139,92,246,.2); color: #c4b5fd; border: 1px solid rgba(139,92,246,.4); }
  .b-chatgpt { background: rgba(16,163,127,.2); color: #6ee7b7; border: 1px solid rgba(16,163,127,.4); }
  .b-other { background: rgba(59,130,246,.18); color: #93c5fd; border: 1px solid rgba(59,130,246,.35); }
  section.chat-pane { display: flex; flex-direction: column; min-height: 0; }
  .chat-head { padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--panel);
               display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
  .messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .msg { padding: 10px 14px; border-radius: 10px; max-width: 85%; border: 1px solid var(--border); white-space: pre-wrap; line-height: 1.45; }
  .msg.user { align-self: flex-end; background: #18253a; border-color: #2c4368; }
  .msg.assistant { align-self: flex-start; background: var(--panel); }
  .msg.system { align-self: center; background: #161b22; font-size: 12px; color: var(--muted); max-width: 95%; }
  .msg-meta { font-size: 11px; color: var(--muted); margin-bottom: 4px; display: flex; gap: 6px; align-items: center; }
  .composer { padding: 12px 16px; border-top: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; gap: 8px; }
  .composer-controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .composer-input { display: flex; gap: 8px; }
  .composer-input textarea { flex: 1; resize: vertical; min-height: 44px; max-height: 160px; }
  .empty { color: var(--muted); text-align: center; margin-top: 40px; font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>🔀 Cross-Platform Sessions Router</h1>
  <div class="tabs" id="platformTabs">
    <button class="tab active" data-plat="">All Platforms</button>
    <button class="tab" data-plat="claude">Claude</button>
    <button class="tab" data-plat="arena">Arena.AI</button>
    <button class="tab" data-plat="chatgpt">ChatGPT</button>
    <button class="tab" data-plat="openrouter">OpenRouter</button>
    <button class="tab" data-plat="groq">Groq</button>
  </div>
</header>
<main>
  <aside>
    <div class="aside-top">
      <div class="row">
        <input id="searchBox" placeholder="Search sessions, models..." style="flex:1" />
        <button class="primary" onclick="newSessionPrompt()">+ New</button>
      </div>
      <div style="font-size:11px;color:var(--muted)" id="countLabel">Loading sessions...</div>
    </div>
    <div class="session-list" id="sessionList"></div>
  </aside>
  <section class="chat-pane">
    <div class="chat-head" id="chatHead">
      <div>
        <strong id="activeTitle">Select or create a session</strong>
        <div id="activeBadges" class="badges" style="margin-top:4px"></div>
      </div>
      <div class="row" id="headActions" style="display:none">
        <button onclick="exportCurrent('chatgpt')">Export ChatGPT</button>
        <button onclick="exportCurrent('claude')">Export Claude</button>
        <button onclick="exportCurrent('arena')">Export Arena</button>
        <button onclick="renameCurrent()">Rename</button>
        <button class="danger" onclick="deleteCurrent()">Delete</button>
      </div>
    </div>
    <div class="messages" id="messages">
      <div class="empty">Pick any session on the left to inspect or route it across Claude, Arena.AI, and ChatGPT.</div>
    </div>
    <div class="composer">
      <div class="composer-controls">
        <label style="font-size:12px;color:var(--muted)">Route via Platform:</label>
        <select id="targetPlatform">
          <option value="arena">Arena.AI</option>
          <option value="claude">Claude (Anthropic)</option>
          <option value="chatgpt">ChatGPT (OpenAI)</option>
          <option value="openrouter">OpenRouter</option>
          <option value="groq">Groq</option>
          <option value="pollinations">Pollinations</option>
        </select>
        <label style="font-size:12px;color:var(--muted)">Model:</label>
        <input id="targetModel" value="claude-3-5-sonnet" style="width:200px" />
        <button onclick="switchRouteOnly()">Switch Route</button>
      </div>
      <div class="composer-input">
        <textarea id="promptBox" placeholder="Continue this session on the selected platform/model..."></textarea>
        <button class="primary" onclick="sendInteract()" style="padding:0 18px">Send</button>
      </div>
    </div>
  </section>
</main>
<script>
let currentPlatform = "";
let currentSessionId = null;

function badgeClass(p) {
  if (p === "claude") return "b-claude";
  if (p === "arena") return "b-arena";
  if (p === "chatgpt") return "b-chatgpt";
  return "b-other";
}

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function loadSessions() {
  const q = document.getElementById("searchBox").value.trim();
  const params = new URLSearchParams();
  if (currentPlatform) params.set("platform", currentPlatform);
  if (q) params.set("q", q);
  const res = await fetch(`/v1/sessions?${params.toString()}`);
  const data = await res.json();
  const list = document.getElementById("sessionList");
  const sessions = data.sessions || [];
  document.getElementById("countLabel").textContent = `${sessions.length} session(s) visible${currentPlatform ? " on " + currentPlatform : ""}`;
  list.innerHTML = sessions.map(s => `
    <div class="sess-card ${s.session_id === currentSessionId ? "active" : ""}" onclick="openSession('${esc(s.session_id)}')">
      <div class="sess-title">${esc(s.title)}</div>
      <div class="sess-preview">${esc(s.last_message_preview || s.session_id)}</div>
      <div class="badges">
        ${(s.routed_platforms || []).map(p => `<span class="badge ${badgeClass(p)}">${esc(p)}</span>`).join("")}
        <span class="badge b-other">${s.message_count} msgs</span>
      </div>
    </div>
  `).join("");
}

async function openSession(sid) {
  currentSessionId = sid;
  const res = await fetch(`/v1/sessions/${encodeURIComponent(sid)}`);
  if (!res.ok) return;
  const s = await res.json();
  document.getElementById("activeTitle").textContent = `${s.title} (${s.session_id})`;
  document.getElementById("headActions").style.display = "flex";
  document.getElementById("targetPlatform").value = s.active_platform || "arena";
  document.getElementById("targetModel").value = s.active_model || "claude-3-5-sonnet";
  document.getElementById("activeBadges").innerHTML =
    `<span class="badge b-other">Origin: ${esc(s.origin_platform)}</span>` +
    (s.visible_on_platforms || []).map(p => `<span class="badge ${badgeClass(p)}">Visible: ${esc(p)}</span>`).join("");

  const box = document.getElementById("messages");
  const msgs = s.messages || [];
  box.innerHTML = msgs.length ? msgs.map(m => `
    <div class="msg ${esc(m.role)}">
      <div class="msg-meta">
        <strong>${esc(m.role).toUpperCase()}</strong>
        <span class="badge ${badgeClass(m.platform)}">${esc(m.platform)}</span>
        <span>${esc(m.model || "")}</span>
      </div>
      <div>${esc(m.content)}</div>
    </div>
  `).join("") : '<div class="empty">Empty session. Send a message below.</div>';
  box.scrollTop = box.scrollHeight;
  loadSessions();
}

async function newSessionPrompt() {
  const title = prompt("Session title:", "Cross-Platform Chat") || "Cross-Platform Chat";
  const plat = document.getElementById("targetPlatform").value;
  const model = document.getElementById("targetModel").value.trim();
  const res = await fetch("/v1/sessions", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ title, platform: plat, model })
  });
  const s = await res.json();
  await openSession(s.session_id);
}

async function switchRouteOnly() {
  if (!currentSessionId) return alert("Select a session first.");
  const platform = document.getElementById("targetPlatform").value;
  const model = document.getElementById("targetModel").value.trim();
  const res = await fetch(`/v1/sessions/${encodeURIComponent(currentSessionId)}/route`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ platform, model })
  });
  if (res.ok) await openSession(currentSessionId);
}

async function sendInteract() {
  const box = document.getElementById("promptBox");
  const text = box.value.trim();
  if (!text) return;
  if (!currentSessionId) await newSessionPrompt();
  const platform = document.getElementById("targetPlatform").value;
  const model = document.getElementById("targetModel").value.trim();
  box.value = "";
  const res = await fetch(`/v1/sessions/${encodeURIComponent(currentSessionId)}/interact`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ prompt: text, platform, model })
  });
  const data = await res.json();
  if (!res.ok) alert(data.error?.message || JSON.stringify(data));
  await openSession(currentSessionId);
}

async function exportCurrent(plat) {
  if (!currentSessionId) return;
  const res = await fetch(`/v1/sessions/${encodeURIComponent(currentSessionId)}/export?platform=${encodeURIComponent(plat)}`);
  const data = await res.json();
  alert(`${plat.toUpperCase()} Export:\\n` + JSON.stringify(data, null, 2).slice(0, 1500));
}

async function renameCurrent() {
  if (!currentSessionId) return;
  const title = prompt("New session title:");
  if (!title) return;
  await fetch(`/v1/sessions/${encodeURIComponent(currentSessionId)}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ title })
  });
  await openSession(currentSessionId);
}

async function deleteCurrent() {
  if (!currentSessionId || !confirm("Delete this session permanently?")) return;
  await fetch(`/v1/sessions/${encodeURIComponent(currentSessionId)}`, { method: "DELETE" });
  currentSessionId = null;
  document.getElementById("messages").innerHTML = '<div class="empty">Session deleted.</div>';
  document.getElementById("headActions").style.display = "none";
  loadSessions();
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentPlatform = btn.dataset.plat || "";
    loadSessions();
  });
});
document.getElementById("searchBox").addEventListener("input", loadSessions);
loadSessions();
</script>
</body>
</html>
"""
