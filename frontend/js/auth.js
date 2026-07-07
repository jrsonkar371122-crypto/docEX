/* ============================================================
   DocuMind — auth: login, token refresh, logout, fetch wrapper.
   Tokens live in httpOnly cookies; JS never reads them directly.
   ============================================================ */
(function () {
    "use strict";

    const API = "/api";

    async function apiFetch(path, options = {}, retry = true) {
        const opts = Object.assign({ credentials: "same-origin" }, options);
        opts.headers = Object.assign(
            { "Content-Type": "application/json" },
            options.headers || {}
        );
        if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
            opts.body = JSON.stringify(opts.body);
        }
        if (opts.body instanceof FormData) {
            delete opts.headers["Content-Type"];
        }

        const res = await fetch(API + path, opts);
        if (res.status === 401 && retry && path !== "/auth/refresh" && path !== "/auth/login") {
            const refreshed = await tryRefresh();
            if (refreshed) {
                return apiFetch(path, options, false);
            }
            redirectToLogin();
            throw new Error("Unauthorized");
        }
        return res;
    }

    async function tryRefresh() {
        try {
            const res = await fetch(API + "/auth/refresh", {
                method: "POST",
                credentials: "same-origin",
            });
            return res.ok;
        } catch (e) {
            return false;
        }
    }

    function redirectToLogin() {
        if (!location.pathname.endsWith("index.html") && location.pathname !== "/") {
            location.href = "/index.html";
        }
    }

    async function getCurrentUser() {
        const res = await apiFetch("/auth/me");
        if (!res.ok) return null;
        return res.json();
    }

    async function requireAuth(adminOnly) {
        const user = await getCurrentUser();
        if (!user) {
            redirectToLogin();
            return null;
        }
        if (adminOnly && user.role !== "admin") {
            location.href = "/chat.html";
            return null;
        }
        return user;
    }

    async function logout() {
        try {
            await apiFetch("/auth/logout", { method: "POST" }, false);
        } catch (e) {
            /* ignore */
        }
        location.href = "/index.html";
    }

    function initLoginPage() {
        // If already authenticated, skip straight to chat.
        getCurrentUser().then((user) => {
            if (user) location.href = "/chat.html";
        });

        const form = document.getElementById("login-form");
        const errorBox = document.getElementById("login-error");
        const btn = document.getElementById("login-btn");
        const spinner = btn.querySelector(".spinner");
        const label = btn.querySelector(".btn-label");
        const toggle = document.getElementById("toggle-password");
        const pwd = document.getElementById("password");

        toggle.addEventListener("click", function () {
            const showing = pwd.type === "text";
            pwd.type = showing ? "password" : "text";
            toggle.textContent = showing ? "Show" : "Hide";
        });

        form.addEventListener("submit", async function (e) {
            e.preventDefault();
            errorBox.hidden = true;
            btn.disabled = true;
            spinner.hidden = false;
            label.textContent = "Signing in…";

            try {
                const res = await fetch(API + "/auth/login", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        email: document.getElementById("email").value.trim(),
                        password: pwd.value,
                    }),
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    throw new Error(data.detail || "Sign in failed.");
                }
                location.href = "/chat.html";
            } catch (err) {
                errorBox.textContent = err.message || "Sign in failed.";
                errorBox.hidden = false;
                btn.disabled = false;
                spinner.hidden = true;
                label.textContent = "Sign In";
            }
        });
    }

    window.DocuMindAuth = {
        apiFetch,
        getCurrentUser,
        requireAuth,
        logout,
        initLoginPage,
        API,
    };
})();
