// Background sync batch. Lives outside the views, so changing route never interrupts it.
import { api } from './api.js';
import { store } from './store.js';

const listeners = new Set();
const finishListeners = new Set();
let cooldownTimer = null;

export const trackKey = (track, index) => track.track_id || `${track.artist}:${track.name}:${index}`;

function notify() {
  listeners.forEach((listener) => listener(syncQueue));
}

export const syncQueue = {
  batch: null,
  active: false,
  cooldownUntil: 0,
  pausedWithoutTime: false,

  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  onFinish(listener) {
    finishListeners.add(listener);
    return () => finishListeners.delete(listener);
  },

  batchFor(playlistId) {
    return this.batch?.playlistId === playlistId ? this.batch : null;
  },

  statusFor(playlistId, key) {
    return this.batchFor(playlistId)?.items.find((item) => item.key === key)?.status;
  },

  isRunning(playlistId) {
    return this.active && this.batch?.playlistId === playlistId;
  },

  cooldownRemaining() {
    return Math.max(0, (this.cooldownUntil - Date.now()) / 1000);
  },

  progress() {
    if (!this.batch) return { done: 0, total: 0, current: null };
    const { items } = this.batch;
    const done = items.filter((item) => ['synced', 'cached', 'not_found', 'error'].includes(item.status)).length;
    return { done, total: items.length, current: items.find((item) => item.status === 'running') || null };
  },

  setCooldown(seconds) {
    clearTimeout(cooldownTimer);
    if (!seconds || seconds <= 0) {
      this.cooldownUntil = 0;
      notify();
      return;
    }
    this.pausedWithoutTime = false;
    this.cooldownUntil = Date.now() + seconds * 1000;
    cooldownTimer = setTimeout(() => {
      this.cooldownUntil = 0;
      notify();
    }, seconds * 1000);
    notify();
  },

  clearFinished(playlistId) {
    if (!this.active && this.batchFor(playlistId)) {
      this.batch = null;
      notify();
    }
  },

  start({ playlistId, playlistName, simplified, avoidBarre, selections }) {
    if (this.active || !selections.length || this.cooldownRemaining() > 0) return false;
    this.pausedWithoutTime = false;
    this.active = true;
    this.batch = {
      playlistId,
      playlistName,
      simplified,
      items: selections.map(({ index, track }) => ({
        key: trackKey(track, index),
        index,
        track,
        status: 'queued',
        result: null,
        retry: false,
      })),
    };
    notify();
    this.run(simplified, avoidBarre);
    return true;
  },

  async run(simplified, avoidBarre) {
    const { items } = this.batch;
    const counts = { saved: 0, failed: 0, rateLimited: false, errorMessage: '' };

    for (const [position, item] of items.entries()) {
      item.status = 'running';
      notify();

      let response;
      try {
        response = await api.syncTracks(
          [{ track_id: item.track.track_id || null, artist: item.track.artist, name: item.track.name }],
          { simplified, avoidBarre },
        );
      } catch (error) {
        item.status = 'error';
        item.retry = true;
        items.slice(position + 1).forEach((rest) => { rest.status = 'pending'; });
        counts.errorMessage = error.message;
        break;
      }

      const result = response.results?.[0];
      const status = result?.status || 'error';
      item.status = status;

      if (status === 'rate_limited') {
        items.slice(position + 1).forEach((rest) => { rest.status = 'pending'; });
        counts.rateLimited = true;
        const seconds = result.retry_after_seconds ?? response.retry_after_seconds;
        if (seconds > 0) this.setCooldown(seconds);
        else this.pausedWithoutTime = true;
        break;
      }

      if (status === 'synced' || status === 'cached') {
        item.result = { ...item.track, ...result, artist: item.track.artist, name: item.track.name };
        counts.saved += 1;
      } else {
        counts.failed += 1;
      }
      notify();
    }

    this.active = false;
    notify();
    store.refreshStatus().catch(() => {});
    finishListeners.forEach((listener) => listener(counts, this.batch));
  },
};
