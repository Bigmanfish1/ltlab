/**
 * feedback_sound.js — a short tone when an answer is graded.
 *
 * Listens for the server's `answerGraded` HTMX trigger. Tones are synthesised
 * with WebAudio, so there are no audio files to ship or license, and the
 * context is only created inside a submission the student initiated, which
 * keeps browser autoplay policy happy.
 *
 * The mute toggle lives in the header and persists in localStorage.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "ltlab:sound-muted";

  let audioCtx = null;

  function muted() {
    return localStorage.getItem(STORAGE_KEY) === "1";
  }

  function setMuted(value) {
    localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
    paintToggle();
  }

  const SPEAKER = '<polygon points="4 9 8 9 13 5 13 19 8 15 4 15"/>';
  const ICON_ON = SPEAKER + '<path d="M16.5 9.5a3.5 3.5 0 0 1 0 5"/><path d="M19 7a7 7 0 0 1 0 10"/>';
  const ICON_OFF = SPEAKER + '<line x1="17" y1="9.5" x2="21.5" y2="14.5"/><line x1="21.5" y1="9.5" x2="17" y2="14.5"/>';

  function paintToggle() {
    const btn = document.getElementById("sound-toggle");
    if (!btn) return;
    const off = muted();
    btn.innerHTML =
      '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
      (off ? ICON_OFF : ICON_ON) + "</svg>";
    btn.setAttribute("aria-pressed", off ? "true" : "false");
    btn.title = off ? "Sound off — click to enable" : "Sound on — click to mute";
  }

  function context() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) audioCtx = new Ctx();
    return audioCtx;
  }

  function tone(ctx, freq, startAt, duration, gainPeak) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(gainPeak, startAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
    osc.connect(gain).connect(ctx.destination);
    osc.start(startAt);
    osc.stop(startAt + duration + 0.02);
  }

  // A graded result arrives well after the click that caused it, so the context
  // can still be suspended here. Scheduling against a suspended context's
  // currentTime (0) puts every note in the past — resume first, then schedule.
  function schedule(fn) {
    const ctx = context();
    if (!ctx) return;
    const run = () => fn(ctx, ctx.currentTime + 0.05);
    if (ctx.state === "suspended") ctx.resume().then(run, () => {});
    else run();
  }

  function play(correct) {
    if (muted()) return;
    schedule((ctx, now) => {
      if (correct) {
        tone(ctx, 660, now, 0.16, 0.35);
        tone(ctx, 990, now + 0.15, 0.24, 0.35);
      } else {
        tone(ctx, 200, now, 0.34, 0.3);
      }
    });
  }

  function unlock() {
    const ctx = context();
    if (ctx && ctx.state === "suspended") ctx.resume();
  }

  document.addEventListener("DOMContentLoaded", () => {
    paintToggle();

    // warm the context on the first real interaction, so the first graded
    // answer of a session is not the one that gets swallowed
    ["pointerdown", "keydown"].forEach((evt) => {
      document.addEventListener(evt, unlock, { once: true });
    });

    const btn = document.getElementById("sound-toggle");
    if (btn) {
      btn.addEventListener("click", () => {
        const nowMuted = !muted();
        setMuted(nowMuted);
        if (!nowMuted) {
          schedule((ctx, now) => tone(ctx, 880, now, 0.16, 0.35));
        }
      });
    }

    document.body.addEventListener("answerGraded", (e) => {
      play(!!(e.detail && e.detail.correct));
    });
  });
})();
