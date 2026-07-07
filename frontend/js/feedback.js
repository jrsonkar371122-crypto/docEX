/* ============================================================
   DocuMind — feedback: thumbs up/down + optional comment per message.
   Rendered under each assistant message by chat.js.
   ============================================================ */
(function () {
    "use strict";

    const { apiFetch } = window.DocuMindAuth;

    // Thumbs up => rating 5, thumbs down => rating 1 (schema expects 1..5).
    const RATING_UP = 5;
    const RATING_DOWN = 1;

    function buildFeedbackRow(messageId) {
        const row = document.createElement("div");
        row.className = "feedback-row";

        const up = document.createElement("button");
        up.className = "fb-btn";
        up.type = "button";
        up.textContent = "👍";
        up.title = "Helpful";

        const down = document.createElement("button");
        down.className = "fb-btn";
        down.type = "button";
        down.textContent = "👎";
        down.title = "Not helpful";

        const thanks = document.createElement("span");
        thanks.className = "fb-thanks";
        thanks.hidden = true;
        thanks.textContent = "Thanks for the feedback";

        row.appendChild(up);
        row.appendChild(down);
        row.appendChild(thanks);

        const commentWrap = document.createElement("div");
        commentWrap.className = "fb-comment";
        const textarea = document.createElement("textarea");
        textarea.rows = 2;
        textarea.placeholder = "What went wrong? (optional)";
        const submit = document.createElement("button");
        submit.className = "btn btn-primary";
        submit.type = "button";
        submit.textContent = "Submit";
        commentWrap.appendChild(textarea);
        commentWrap.appendChild(submit);

        async function send(rating, comment) {
            try {
                const res = await apiFetch("/feedback", {
                    method: "POST",
                    body: { message_id: messageId, rating: rating, comment: comment || null },
                });
                if (res.ok) {
                    thanks.hidden = false;
                }
            } catch (e) {
                /* ignore network errors on feedback */
            }
        }

        up.addEventListener("click", function () {
            up.classList.add("selected");
            down.classList.remove("selected");
            commentWrap.classList.remove("show");
            send(RATING_UP, null);
        });

        down.addEventListener("click", function () {
            down.classList.add("selected");
            up.classList.remove("selected");
            commentWrap.classList.add("show");
        });

        submit.addEventListener("click", function () {
            send(RATING_DOWN, textarea.value.trim());
            commentWrap.classList.remove("show");
        });

        const container = document.createElement("div");
        container.appendChild(row);
        container.appendChild(commentWrap);
        return container;
    }

    window.DocuMindFeedback = { buildFeedbackRow };
})();
