// Small Web Audio metronome. Clicks are scheduled ahead so they stay steady under UI work.
const LOOKAHEAD_SECONDS = 0.12;
const TICK_MS = 25;

export function createMetronome() {
  let context = null;
  let timer = null;
  let nextClick = 0;

  function tone(at, frequency, duration, volume) {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(volume, at);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + duration);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(at);
    oscillator.stop(at + duration);
  }

  async function ensureContext() {
    const AudioEngine = window.AudioContext || window.webkitAudioContext;
    if (!AudioEngine) return false;
    context ??= new AudioEngine();
    if (context.state === 'suspended') await context.resume();
    return true;
  }

  return {
    // Must first run from a user gesture, as browsers block audio otherwise.
    prepare: ensureContext,

    start(bpm) {
      this.stop();
      if (!context) return;
      const interval = 60 / bpm;
      nextClick = context.currentTime + 0.05;
      timer = setInterval(() => {
        while (nextClick < context.currentTime + LOOKAHEAD_SECONDS) {
          tone(nextClick, 1400, 0.04, 0.28);
          nextClick += interval;
        }
      }, TICK_MS);
    },

    stop() {
      clearInterval(timer);
      timer = null;
    },

    finishSignal() {
      if (!context) return;
      const now = context.currentTime;
      tone(now, 880, 0.16, 0.32);
      tone(now + 0.2, 1320, 0.26, 0.32);
    },

    close() {
      this.stop();
      context?.close().catch(() => {});
      context = null;
    },
  };
}
