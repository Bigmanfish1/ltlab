/**
 * exercise_progress.js — reflect a graded answer in place.
 *
 * Solving an exercise used to trigger a full page reload so the COMPLETED and
 * SOLVED badges refreshed; that flashed the page and wiped whatever the student
 * had typed. The badges are now revealed where they stand.
 */
(function () {
  "use strict";

  function revealCompleted() {
    const badge = document.getElementById("exercise-completed-badge");
    if (badge) badge.classList.remove("hidden");
  }

  function markPartSolved(target) {
    const card = target.closest("[data-part-card]");
    if (!card) return;
    const badge = card.querySelector("[data-solved-badge]");
    if (badge) badge.classList.remove("hidden");
    card.classList.remove("border-border-primary");
    card.classList.add("border-accent-lime");
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.body.addEventListener("exerciseSolved", revealCompleted);

    document.body.addEventListener("htmx:afterSwap", (e) => {
      if (e.target && e.target.querySelector('[data-status="correct"]')) {
        markPartSolved(e.target);
      }
    });
  });
})();
