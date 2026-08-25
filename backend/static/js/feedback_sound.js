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

  // Triad notes overlap, so the bus carries a limiter rather than trusting
  // three envelopes to sum under full scale.
  function bus(ctx) {
    if (!ctx._ltlabBus) {
      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -6;
      limiter.knee.value = 0;
      limiter.ratio.value = 20;
      limiter.attack.value = 0.002;
      limiter.release.value = 0.12;
      limiter.connect(ctx.destination);
      ctx._ltlabBus = limiter;
    }
    return ctx._ltlabBus;
  }

  function tone(ctx, freq, startAt, duration, gainPeak) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(gainPeak, startAt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);
    osc.connect(gain).connect(bus(ctx));
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

  // C major arpeggiated up for a pass, two notes stepping down for a fail
  const PASS = [
    { freq: 523.25, at: 0.00, dur: 0.28 },
    { freq: 659.25, at: 0.06, dur: 0.28 },
    { freq: 783.99, at: 0.12, dur: 0.28 },
  ];
  const FAIL = [
    { freq: 392, at: 0.00, dur: 0.15 },
    { freq: 294, at: 0.14, dur: 0.26 },
  ];

  function play(correct) {
    if (muted()) return;
    const notes = correct ? PASS : FAIL;
    const peak = correct ? 0.7 : 0.75;
    schedule((ctx, now) => {
      notes.forEach((n) => tone(ctx, n.freq, now + n.at, n.dur, peak));
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
          schedule((ctx, now) => {
            PASS.forEach((n) => tone(ctx, n.freq, now + n.at, n.dur, 0.7));
          });
        }
      });
    }

    document.body.addEventListener("answerGraded", (e) => {
      play(!!(e.detail && e.detail.correct));
    });
  });
})();
