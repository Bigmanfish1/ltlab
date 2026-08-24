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

  function paintToggle() {
    const btn = document.getElementById("sound-toggle");
    if (!btn) return;
    const off = muted();
    btn.textContent = off ? "🔇" : "🔊";
    btn.setAttribute("aria-pressed", off ? "true" : "false");
    btn.title = off ? "Sound off — click to enable" : "Sound on — click to mute";
  }

  function tone(freq, startAt, duration, gainPeak) {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(gainPeak, startAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(startAt);
    osc.stop(startAt + duration + 0.02);
  }

  function play(correct) {
    if (muted()) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const now = audioCtx.currentTime;
    if (correct) {
      tone(660, now, 0.12, 0.18);
      tone(990, now + 0.11, 0.18, 0.18);
    } else {
      tone(200, now, 0.28, 0.14);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    paintToggle();
    const btn = document.getElementById("sound-toggle");
    if (btn) btn.addEventListener("click", () => setMuted(!muted()));

    document.body.addEventListener("answerGraded", (e) => {
      play(!!(e.detail && e.detail.correct));
    });
  });
})();
