/*
 * Per-part hint reveal for student exercise pages.
 *
 * Each [data-part-hints] block (components/student_part_hints.html) carries its
 * hints in an inline application/json script and a hidden hints_used input that
 * the part's submit button hx-includes. Blocks without hints hide themselves.
 */
(function () {
    document.querySelectorAll("[data-part-hints]").forEach(function (root) {
        const script = root.querySelector('script[type="application/json"]');
        let hints = [];
        try {
            hints = JSON.parse(script ? script.textContent : "[]") || [];
        } catch (e) {
            hints = [];
        }
        hints = hints.filter(function (h) { return typeof h === "string" && h.trim(); });

        const btn = root.querySelector("[data-hint-btn]");
        const list = root.querySelector("[data-hints-list]");
        const used = root.querySelector('input[name="hints_used"]');
        if (!hints.length) {
            btn.classList.add("hidden");
            return;
        }

        let revealed = 0;
        btn.addEventListener("click", function () {
            if (revealed >= hints.length) return;
            const div = document.createElement("div");
            div.className = "bg-bg-primary border border-border-primary rounded p-3 text-sm text-text-primary";
            const label = "HINT " + (revealed + 1) + " OF " + hints.length;
            div.innerHTML = '<span class="font-mono text-xs text-accent-orange">' + label + ":</span> ";
            div.appendChild(document.createTextNode(hints[revealed]));
            list.appendChild(div);
            revealed += 1;
            used.value = revealed;
            if (revealed >= hints.length) {
                btn.disabled = true;
                btn.classList.add("opacity-50", "cursor-not-allowed");
            } else {
                btn.textContent = "Get Hint (" + (revealed + 1) + "/" + hints.length + ")";
            }
        });
    });
})();
