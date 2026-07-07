/* ============================================================
   DocuMind — admin dashboard: upload, jobs, documents, users, feedback.
   ============================================================ */
(function () {
    "use strict";

    const { apiFetch, requireAuth, logout } = window.DocuMindAuth;

    const state = { user: null, jobsTimer: null, feedback: [], fbSortDesc: true };
    const el = {};

    function cache() {
        el.tabs = document.querySelectorAll(".admin-tab");
        el.panels = document.querySelectorAll(".tab-panel");
        el.userName = document.getElementById("user-name");
        el.logoutBtn = document.getElementById("logout-btn");
        el.overlay = document.getElementById("modal-overlay");
        el.modal = document.getElementById("modal");

        // Upload
        el.uploadZone = document.getElementById("upload-zone");
        el.fileInput = document.getElementById("file-input");
        el.uploadSelected = document.getElementById("upload-selected");
        el.uploadFilename = document.getElementById("upload-filename");
        el.uploadFilesize = document.getElementById("upload-filesize");
        el.uploadBtn = document.getElementById("upload-btn");
        el.progressWrap = document.getElementById("upload-progress-wrap");
        el.progressFill = document.getElementById("upload-progress-fill");
        el.progressLabel = document.getElementById("upload-progress-label");
        el.uploadResult = document.getElementById("upload-result");

        // Tables
        el.jobsTbody = document.getElementById("jobs-tbody");
        el.documentsTbody = document.getElementById("documents-tbody");
        el.usersTbody = document.getElementById("users-tbody");
        el.feedbackTbody = document.getElementById("feedback-tbody");
        el.createUserBtn = document.getElementById("create-user-btn");
        el.fbFrom = document.getElementById("fb-from");
        el.fbTo = document.getElementById("fb-to");
        el.fbSort = document.getElementById("fb-sort-rating");
    }

    function escapeHtml(s) {
        const d = document.createElement("div");
        d.textContent = s == null ? "" : s;
        return d.innerHTML;
    }

    function fmtBytes(n) {
        if (n < 1024) return n + " B";
        if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
        return (n / 1024 / 1024).toFixed(1) + " MB";
    }

    function fmtDate(s) {
        return s ? new Date(s).toLocaleString() : "—";
    }

    function badge(status) {
        return '<span class="badge badge-' + status + '">' + status + "</span>";
    }

    // ---- Tabs ----
    function switchTab(name) {
        el.tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
        el.panels.forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
        if (name === "jobs") startJobsPolling();
        else stopJobsPolling();
        if (name === "documents") loadDocuments();
        if (name === "users") loadUsers();
        if (name === "feedback") loadFeedback();
    }

    // ---- Modal ----
    function openModal(html) {
        el.modal.innerHTML = html;
        el.overlay.hidden = false;
    }
    function closeModal() {
        el.overlay.hidden = true;
        el.modal.innerHTML = "";
    }

    // ---- Upload ----
    let selectedFile = null;
    const MAX_BYTES = 100 * 1024 * 1024;

    function selectFile(file) {
        if (!file) return;
        if (file.type !== "application/pdf") {
            showUploadResult("Only PDF files are allowed.", true);
            return;
        }
        if (file.size > MAX_BYTES) {
            showUploadResult("File exceeds the 100 MB limit.", true);
            return;
        }
        selectedFile = file;
        el.uploadFilename.textContent = file.name;
        el.uploadFilesize.textContent = fmtBytes(file.size);
        el.uploadSelected.hidden = false;
        el.uploadBtn.disabled = false;
        el.uploadResult.hidden = true;
    }

    function showUploadResult(msg, isError) {
        el.uploadResult.hidden = false;
        el.uploadResult.textContent = msg;
        el.uploadResult.style.background = isError ? "#fdecec" : "#e7f8ee";
        el.uploadResult.style.borderColor = isError ? "#f7c9c9" : "#bfe9cf";
    }

    function uploadFile() {
        if (!selectedFile) return;
        const form = new FormData();
        form.append("file", selectedFile);

        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/admin/upload");
        xhr.withCredentials = true;

        el.progressWrap.hidden = false;
        el.uploadBtn.disabled = true;

        xhr.upload.onprogress = function (e) {
            if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                el.progressFill.style.width = pct + "%";
                el.progressLabel.textContent = pct + "%";
            }
        };
        xhr.onload = function () {
            el.progressWrap.hidden = true;
            el.progressFill.style.width = "0%";
            if (xhr.status >= 200 && xhr.status < 300) {
                const data = JSON.parse(xhr.responseText);
                showUploadResult("Queued for ingestion (job " + data.job_id + ").", false);
                selectedFile = null;
                el.uploadSelected.hidden = true;
                el.fileInput.value = "";
                switchTab("jobs");
            } else {
                let detail = "Upload failed.";
                try { detail = JSON.parse(xhr.responseText).detail || detail; } catch (e) {}
                showUploadResult(detail, true);
                el.uploadBtn.disabled = false;
            }
        };
        xhr.onerror = function () {
            el.progressWrap.hidden = true;
            showUploadResult("Network error during upload.", true);
            el.uploadBtn.disabled = false;
        };
        xhr.send(form);
    }

    function bindUpload() {
        el.uploadZone.addEventListener("click", () => el.fileInput.click());
        el.fileInput.addEventListener("change", (e) => selectFile(e.target.files[0]));
        ["dragover", "dragenter"].forEach((ev) =>
            el.uploadZone.addEventListener(ev, (e) => {
                e.preventDefault();
                el.uploadZone.classList.add("dragover");
            })
        );
        ["dragleave", "drop"].forEach((ev) =>
            el.uploadZone.addEventListener(ev, (e) => {
                e.preventDefault();
                el.uploadZone.classList.remove("dragover");
            })
        );
        el.uploadZone.addEventListener("drop", (e) => {
            if (e.dataTransfer.files.length) selectFile(e.dataTransfer.files[0]);
        });
        el.uploadBtn.addEventListener("click", uploadFile);
    }

    // ---- Jobs ----
    function startJobsPolling() {
        loadJobs();
        stopJobsPolling();
        state.jobsTimer = setInterval(loadJobs, 5000);
    }
    function stopJobsPolling() {
        if (state.jobsTimer) clearInterval(state.jobsTimer);
        state.jobsTimer = null;
    }

    async function loadJobs() {
        const res = await apiFetch("/admin/jobs");
        if (!res.ok) return;
        const jobs = await res.json();
        el.jobsTbody.innerHTML = "";
        jobs.forEach((j) => {
            const tr = document.createElement("tr");
            tr.className = "row-clickable";
            const elapsed = j.finished_at
                ? Math.round((new Date(j.finished_at) - new Date(j.created_at)) / 1000) + "s"
                : "—";
            tr.innerHTML =
                "<td>" + escapeHtml(j.doc_name) + "</td>" +
                "<td>" + badge(j.status) + "</td>" +
                '<td class="table-progress"><div class="progress-bar"><div class="progress-fill" style="width:' +
                    j.progress_pct + '%"></div></div></td>' +
                "<td>" + fmtDate(j.created_at) + "</td>" +
                "<td>" + elapsed + "</td>";
            tr.addEventListener("click", () => showJobDetail(j.id));
            el.jobsTbody.appendChild(tr);
        });
    }

    async function showJobDetail(jobId) {
        const res = await apiFetch("/admin/jobs/" + jobId);
        if (!res.ok) return;
        const j = await res.json();
        openModal(
            "<h2>" + escapeHtml(j.doc_name) + "</h2>" +
            "<p>Status: " + badge(j.status) + " · " + j.progress_pct + "%</p>" +
            '<div class="log-tail">' + escapeHtml(j.log_tail || "(no log output)") + "</div>" +
            '<div class="modal-actions"><button class="btn btn-ghost" id="modal-close">Close</button></div>'
        );
        document.getElementById("modal-close").addEventListener("click", closeModal);
    }

    // ---- Documents ----
    async function loadDocuments() {
        const res = await apiFetch("/admin/documents");
        if (!res.ok) return;
        const docs = await res.json();
        el.documentsTbody.innerHTML = "";
        docs.forEach((d) => {
            const tr = document.createElement("tr");
            tr.innerHTML =
                "<td>" + escapeHtml(d.original_name) + "</td>" +
                "<td>" + d.page_count + "</td>" +
                "<td>" + d.chunk_count + "</td>" +
                "<td>" + escapeHtml(d.doc_type) + "</td>" +
                "<td>" + badge(d.status) + "</td>" +
                "<td>" + escapeHtml(d.uploaded_by_name || "—") + "</td>" +
                "<td>" + fmtDate(d.uploaded_at) + "</td>" +
                '<td><button class="btn btn-danger">Delete</button></td>';
            tr.querySelector("button").addEventListener("click", () =>
                confirmDeleteDoc(d.id, d.original_name)
            );
            el.documentsTbody.appendChild(tr);
        });
    }

    function confirmDeleteDoc(id, name) {
        openModal(
            "<h2>Delete document?</h2>" +
            "<p>This permanently removes <strong>" + escapeHtml(name) +
            "</strong> and all of its pages and chunks.</p>" +
            '<div class="modal-actions">' +
            '<button class="btn btn-ghost" id="cancel">Cancel</button>' +
            '<button class="btn btn-danger" id="confirm">Delete</button></div>'
        );
        document.getElementById("cancel").addEventListener("click", closeModal);
        document.getElementById("confirm").addEventListener("click", async () => {
            await apiFetch("/admin/documents/" + id, { method: "DELETE" });
            closeModal();
            loadDocuments();
        });
    }

    // ---- Users ----
    async function loadUsers() {
        const res = await apiFetch("/admin/users");
        if (!res.ok) return;
        const users = await res.json();
        el.usersTbody.innerHTML = "";
        users.forEach((u) => {
            const tr = document.createElement("tr");
            const otherRole = u.role === "admin" ? "technician" : "admin";
            tr.innerHTML =
                "<td>" + escapeHtml(u.full_name) + "</td>" +
                "<td>" + escapeHtml(u.email) + "</td>" +
                "<td>" + escapeHtml(u.role) + "</td>" +
                '<td><label class="switch"><input type="checkbox" ' +
                    (u.is_active ? "checked" : "") + "><span class=\"slider\"></span></label></td>" +
                "<td>" + fmtDate(u.last_login) + "</td>" +
                '<td><button class="link-btn">Make ' + otherRole + "</button></td>";

            tr.querySelector("input").addEventListener("change", (e) =>
                updateUser(u.id, { is_active: e.target.checked })
            );
            tr.querySelector(".link-btn").addEventListener("click", () =>
                updateUser(u.id, { role: otherRole }).then(loadUsers)
            );
            el.usersTbody.appendChild(tr);
        });
    }

    async function updateUser(id, payload) {
        const res = await apiFetch("/admin/users/" + id, { method: "PATCH", body: payload });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            alert(data.detail || "Update failed.");
            loadUsers();
        }
        return res;
    }

    function openCreateUser() {
        openModal(
            "<h2>Create User</h2>" +
            "<label>Full name</label><input id=\"cu-name\" />" +
            "<label>Email</label><input id=\"cu-email\" type=\"email\" />" +
            "<label>Password</label><input id=\"cu-password\" type=\"password\" />" +
            "<label>Role</label><select id=\"cu-role\"><option value=\"technician\">Technician</option>" +
            "<option value=\"admin\">Admin</option></select>" +
            '<div class="modal-error" id="cu-error" hidden></div>' +
            '<div class="modal-actions"><button class="btn btn-ghost" id="cu-cancel">Cancel</button>' +
            '<button class="btn btn-primary" id="cu-submit">Create</button></div>'
        );
        document.getElementById("cu-cancel").addEventListener("click", closeModal);
        document.getElementById("cu-submit").addEventListener("click", async () => {
            const err = document.getElementById("cu-error");
            err.hidden = true;
            const body = {
                full_name: document.getElementById("cu-name").value.trim(),
                email: document.getElementById("cu-email").value.trim(),
                password: document.getElementById("cu-password").value,
                role: document.getElementById("cu-role").value,
            };
            const res = await apiFetch("/auth/register", { method: "POST", body: body });
            if (res.ok) {
                closeModal();
                loadUsers();
            } else {
                const data = await res.json().catch(() => ({}));
                err.textContent = data.detail || "Could not create user.";
                err.hidden = false;
            }
        });
    }

    // ---- Feedback ----
    async function loadFeedback() {
        const res = await apiFetch("/admin/feedback");
        if (!res.ok) return;
        state.feedback = await res.json();
        renderFeedback();
    }

    function renderFeedback() {
        let rows = state.feedback.slice();
        const from = el.fbFrom.value ? new Date(el.fbFrom.value) : null;
        const to = el.fbTo.value ? new Date(el.fbTo.value + "T23:59:59") : null;
        rows = rows.filter((f) => {
            const t = new Date(f.created_at);
            if (from && t < from) return false;
            if (to && t > to) return false;
            return true;
        });
        rows.sort((a, b) =>
            state.fbSortDesc ? b.rating - a.rating : a.rating - b.rating
        );

        el.feedbackTbody.innerHTML = "";
        rows.forEach((f) => {
            const stars = "★".repeat(f.rating) + "☆".repeat(5 - f.rating);
            const tr = document.createElement("tr");
            tr.innerHTML =
                "<td>" + escapeHtml(f.message_preview) + "</td>" +
                '<td class="stars">' + stars + "</td>" +
                "<td>" + escapeHtml(f.comment || "—") + "</td>" +
                "<td>" + escapeHtml(f.username) + "</td>" +
                "<td>" + fmtDate(f.created_at) + "</td>";
            el.feedbackTbody.appendChild(tr);
        });
    }

    function bindEvents() {
        el.tabs.forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
        el.logoutBtn.addEventListener("click", logout);
        el.createUserBtn.addEventListener("click", openCreateUser);
        el.fbFrom.addEventListener("change", renderFeedback);
        el.fbTo.addEventListener("change", renderFeedback);
        el.fbSort.addEventListener("click", () => {
            state.fbSortDesc = !state.fbSortDesc;
            renderFeedback();
        });
        el.overlay.addEventListener("click", (e) => {
            if (e.target === el.overlay) closeModal();
        });
        bindUpload();
    }

    async function init() {
        cache();
        const user = await requireAuth(true);
        if (!user) return;
        state.user = user;
        el.userName.textContent = user.full_name;
        bindEvents();
        switchTab("upload");
    }

    window.DocuMindAdmin = { init };
})();
