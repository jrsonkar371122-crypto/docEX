/* ============================================================
   DocuMind — chat UI: sessions, SSE streaming, source citations.
   ============================================================ */
(function () {
    "use strict";

    const { apiFetch, requireAuth, logout } = window.DocuMindAuth;
    const { buildFeedbackRow } = window.DocuMindFeedback;

    const state = {
        user: null,
        currentSessionId: null,
        streaming: false,
        lastSources: [],
    };

    const el = {};

    function cache() {
        el.sessionList = document.getElementById("session-list");
        el.thread = document.getElementById("message-thread");
        el.emptyState = document.getElementById("empty-state");
        el.input = document.getElementById("chat-input");
        el.sendBtn = document.getElementById("send-btn");
        el.newChatBtn = document.getElementById("new-chat-btn");
        el.title = document.getElementById("session-title");
        el.docCount = document.getElementById("doc-count");
        el.userName = document.getElementById("user-name");
        el.userRole = document.getElementById("user-role");
        el.adminLink = document.getElementById("admin-link");
        el.logoutBtn = document.getElementById("logout-btn");
        el.sidebar = document.getElementById("sidebar");
        el.sidebarToggle = document.getElementById("sidebar-toggle");
    }

    function escapeHtml(s) {
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    // Minimal, safe markdown: paragraphs, bold, inline code, pipe tables,
    // and [N] citation markers turned into clickable spans.
    function renderMarkdown(text) {
        let html = escapeHtml(text);
        html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
        html = renderTables(html);
        html = html.replace(/\[(\d+)\]/g, function (_m, n) {
            return '<span class="citation" data-cite="' + n + '">[' + n + "]</span>";
        });
        return html;
    }

    function renderTables(html) {
        const lines = html.split("\n");
        const out = [];
        let i = 0;
        while (i < lines.length) {
            const line = lines[i];
            const next = lines[i + 1] || "";
            const isTableHead = /^\s*\|.*\|\s*$/.test(line);
            const isDivider = /^\s*\|?[\s:-]+\|[\s:|-]*$/.test(next);
            if (isTableHead && isDivider) {
                const tbl = ["<table>"];
                const header = splitRow(line);
                tbl.push("<thead><tr>" + header.map((c) => "<th>" + c + "</th>").join("") + "</tr></thead>");
                i += 2;
                tbl.push("<tbody>");
                while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
                    const cells = splitRow(lines[i]);
                    tbl.push("<tr>" + cells.map((c) => "<td>" + c + "</td>").join("") + "</tr>");
                    i += 1;
                }
                tbl.push("</tbody></table>");
                out.push(tbl.join(""));
            } else {
                out.push(line);
                i += 1;
            }
        }
        return out.join("\n");
    }

    function splitRow(line) {
        return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    }

    function clearThread() {
        el.thread.innerHTML = "";
    }

    function addMessage(role, contentHtml) {
        if (el.emptyState) el.emptyState.remove();
        const msg = document.createElement("div");
        msg.className = "msg " + role;
        const rowWrap = document.createElement("div");
        rowWrap.className = "msg-row";
        const bubble = document.createElement("div");
        bubble.className = "bubble";
        if (contentHtml !== undefined) bubble.innerHTML = contentHtml;
        rowWrap.appendChild(bubble);
        msg.appendChild(rowWrap);
        el.thread.appendChild(msg);
        scrollToBottom();
        return { msg, bubble };
    }

    function scrollToBottom() {
        el.thread.scrollTop = el.thread.scrollHeight;
    }

    async function loadSessions() {
        const res = await apiFetch("/chat/sessions");
        if (!res.ok) return;
        const sessions = await res.json();
        el.sessionList.innerHTML = "";
        sessions.forEach((s) => el.sessionList.appendChild(sessionItem(s)));
    }

    function sessionItem(s) {
        const item = document.createElement("button");
        item.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
        item.dataset.id = s.id;

        const name = document.createElement("span");
        name.className = "session-name";
        name.textContent = s.title || "Untitled";

        const date = document.createElement("span");
        date.className = "session-date";
        date.textContent = new Date(s.updated_at).toLocaleDateString();

        const del = document.createElement("span");
        del.className = "session-delete";
        del.textContent = "Delete";
        del.addEventListener("click", async (e) => {
            e.stopPropagation();
            await apiFetch("/chat/sessions/" + s.id, { method: "DELETE" }, true);
            if (s.id === state.currentSessionId) startNewChat();
            loadSessions();
        });

        item.appendChild(name);
        item.appendChild(date);
        item.appendChild(del);
        item.addEventListener("click", () => openSession(s.id));
        return item;
    }

    async function openSession(sessionId) {
        state.currentSessionId = sessionId;
        clearThread();
        const res = await apiFetch("/chat/sessions/" + sessionId);
        if (!res.ok) return;
        const session = await res.json();
        el.title.textContent = session.title;
        (session.messages || []).forEach((m) => {
            if (m.role === "user") {
                addMessage("user", escapeHtml(m.content));
            } else {
                const { bubble, msg } = addMessage("assistant", renderMarkdown(m.content));
                if (m.source_chunks && m.source_chunks.length) {
                    msg.appendChild(buildSourcesPanel(m.source_chunks, bubble));
                }
                msg.appendChild(buildFeedbackRow(m.id));
            }
        });
        highlightActive();
        scrollToBottom();
    }

    function highlightActive() {
        document.querySelectorAll(".session-item").forEach((n) => {
            n.classList.toggle("active", n.dataset.id === state.currentSessionId);
        });
    }

    function startNewChat() {
        state.currentSessionId = null;
        clearThread();
        el.title.textContent = "New Chat";
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.innerHTML = "<h2>Ask about your manuals</h2><p>Every answer is grounded in your uploaded documents and cites its sources.</p>";
        el.thread.appendChild(empty);
        highlightActive();
    }

    function buildSourcesPanel(sources, bubble) {
        state.lastSources = sources;
        const details = document.createElement("details");
        details.className = "sources";
        const summary = document.createElement("summary");
        summary.textContent = "Sources (" + sources.length + ")";
        details.appendChild(summary);

        sources.forEach((s, idx) => {
            const item = document.createElement("div");
            item.className = "source-item";
            item.dataset.index = idx + 1;
            const page = s.page != null ? " · p." + s.page : "";
            item.innerHTML =
                '<div class="source-doc">[' + (idx + 1) + "] " + escapeHtml(s.doc_name) + "</div>" +
                '<div class="source-meta">' + escapeHtml(s.section_path || "") + page + "</div>" +
                '<div class="source-preview">' + escapeHtml(s.preview) + "</div>" +
                '<div class="source-full">' + escapeHtml(s.preview) + "</div>";
            item.addEventListener("click", () => item.classList.toggle("expanded"));
            details.appendChild(item);
        });

        // Clicking a [N] citation opens the panel and highlights the source.
        bubble.addEventListener("click", (e) => {
            const cite = e.target.closest(".citation");
            if (!cite) return;
            details.open = true;
            const target = details.querySelector('.source-item[data-index="' + cite.dataset.cite + '"]');
            if (target) {
                target.classList.add("expanded");
                target.scrollIntoView({ block: "nearest" });
            }
        });

        return details;
    }

    async function sendMessage() {
        const content = el.input.value.trim();
        if (!content || state.streaming) return;

        state.streaming = true;
        el.sendBtn.disabled = true;
        el.input.value = "";
        autoResize();

        addMessage("user", escapeHtml(content));

        const { bubble, msg } = addMessage("assistant", "");
        const typing = document.createElement("div");
        typing.className = "typing";
        typing.innerHTML = "<span></span><span></span><span></span>";
        bubble.appendChild(typing);

        let answer = "";
        let sources = [];
        let messageId = null;

        try {
            const res = await fetch("/api/chat/message", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ session_id: state.currentSessionId, content: content }),
            });
            if (!res.ok || !res.body) {
                throw new Error("Request failed");
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split("\n\n");
                buffer = parts.pop();
                for (const part of parts) {
                    const line = part.trim();
                    if (!line.startsWith("data:")) continue;
                    const payload = JSON.parse(line.slice(5).trim());
                    if (payload.type === "token") {
                        if (typing.parentNode) typing.remove();
                        answer += payload.content;
                        bubble.innerHTML = renderMarkdown(answer);
                        scrollToBottom();
                    } else if (payload.type === "sources") {
                        sources = payload.chunks || [];
                    } else if (payload.type === "done") {
                        messageId = payload.message_id;
                        if (payload.session_id) state.currentSessionId = payload.session_id;
                    } else if (payload.type === "error") {
                        throw new Error(payload.message || "Generation error");
                    }
                }
            }

            if (typing.parentNode) typing.remove();
            bubble.innerHTML = renderMarkdown(answer || "(no response)");
            if (sources.length) msg.appendChild(buildSourcesPanel(sources, bubble));
            if (messageId) msg.appendChild(buildFeedbackRow(messageId));
            await loadSessions();
            highlightActive();
        } catch (err) {
            if (typing.parentNode) typing.remove();
            bubble.innerHTML = '<span style="color:var(--error)">Something went wrong generating a response.</span>';
        } finally {
            state.streaming = false;
            el.sendBtn.disabled = false;
            el.input.focus();
        }
    }

    async function renameSession() {
        if (!state.currentSessionId) return;
        const title = el.title.textContent.trim();
        if (!title) return;
        await apiFetch("/chat/sessions/" + state.currentSessionId, {
            method: "PATCH",
            body: { title: title },
        });
        loadSessions();
    }

    function autoResize() {
        el.input.style.height = "auto";
        el.input.style.height = Math.min(el.input.scrollHeight, 180) + "px";
    }

    async function loadDocCount() {
        const res = await apiFetch("/documents");
        if (!res.ok) return;
        const docs = await res.json();
        const ready = docs.filter((d) => d.status === "ready").length;
        el.docCount.textContent = ready + (ready === 1 ? " document" : " documents");
    }

    function bindEvents() {
        el.sendBtn.addEventListener("click", sendMessage);
        el.newChatBtn.addEventListener("click", startNewChat);
        el.logoutBtn.addEventListener("click", logout);

        el.input.addEventListener("input", autoResize);
        el.input.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        el.title.addEventListener("click", () => {
            if (!state.currentSessionId) return;
            el.title.setAttribute("contenteditable", "true");
            el.title.focus();
        });
        el.title.addEventListener("blur", () => {
            el.title.setAttribute("contenteditable", "false");
            renameSession();
        });
        el.title.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                el.title.blur();
            }
        });

        if (el.sidebarToggle) {
            el.sidebarToggle.addEventListener("click", () =>
                el.sidebar.classList.toggle("open")
            );
        }
    }

    async function init() {
        cache();
        const user = await requireAuth(false);
        if (!user) return;
        state.user = user;
        el.userName.textContent = user.full_name;
        el.userRole.textContent = user.role;
        if (user.role === "admin") el.adminLink.hidden = false;

        bindEvents();
        await loadSessions();
        await loadDocCount();
        startNewChat();
        el.input.focus();
    }

    window.DocuMindChat = { init };
})();
