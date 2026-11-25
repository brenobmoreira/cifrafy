// HTTP client for the Cifrafy API. No DOM access here.
export const API_BASE =
  new URLSearchParams(window.location.search).get('api') ||
  `${window.location.protocol}//${window.location.hostname}:8000`;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function detailMessage(payload, fallback) {
  const detail = payload?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  return fallback;
}

async function request(path, { method = 'GET', query, body, signal } = {}) {
  const url = new URL(`${API_BASE}${path}`);
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== null && value !== undefined && value !== '') url.searchParams.set(key, value);
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new ApiError('Não foi possível falar com o servidor. Confira se o backend está rodando.', 0);
  }

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(detailMessage(payload, `Erro ${response.status} no servidor.`), response.status);
  }
  return payload;
}

const id = encodeURIComponent;

export const api = {
  status: (signal) => request('/api/status', { signal }),
  spotifyAuthUrl: () => request('/api/spotify/auth-url'),
  updateProfile: (patch) => request('/api/profile', { method: 'PUT', body: patch }),

  playlists: (signal) => request('/api/playlists', { query: { limit: 50 }, signal }),
  addPlaylist: (urlOrId) => request('/api/playlists', { method: 'POST', body: { url_or_id: urlOrId } }),
  deletePlaylist: (playlistId) => request(`/api/playlists/${id(playlistId)}`, { method: 'DELETE' }),
  playlistTracks: (playlistId, { simplified = false, limit = 1000, signal } = {}) =>
    request(`/api/playlists/${id(playlistId)}/tracks`, { query: { limit, simplified }, signal }),
  syncTracks: (tracks, { simplified, avoidBarre }) =>
    request('/api/playlists/sync-selected', {
      method: 'POST',
      body: { tracks, simplified, avoid_barre: avoidBarre },
    }),

  catalog: (query, signal) => request('/api/catalog', { query: { limit: 200, ...query }, signal }),
  chords: ({ artist, trackName, semitones = 0, simplified = false }, signal) =>
    request('/api/chords', {
      query: { artist, track_name: trackName, semitones, simplified },
      signal,
    }),
  searchCifraClub: (q, signal) => request('/api/search', { query: { q }, signal }),
  nextChords: ({ playlistId, limit = 5 } = {}, signal) =>
    request('/api/learning/next-chords', { query: { playlist_id: playlistId, limit }, signal }),

  deleteSong: (songId) => request(`/api/songs/${id(songId)}`, { method: 'DELETE' }),
  repertoire: (status, signal) => request('/api/repertoire', { query: { status }, signal }),
  saveRepertoire: (songId, body) => request(`/api/repertoire/${id(songId)}`, { method: 'PUT', body }),
  removeRepertoire: (songId) => request(`/api/repertoire/${id(songId)}`, { method: 'DELETE' }),

  practiceTrail: ({ playlistId, limit = 5 } = {}, signal) =>
    request('/api/practice/trail', { query: { playlist_id: playlistId, limit }, signal }),
  practiceChanges: ({ playlistId, songId, limit = 8 } = {}, signal) =>
    request('/api/practice/changes', { query: { playlist_id: playlistId, song_id: songId, limit }, signal }),
  logPracticeChange: (body) => request('/api/practice/changes', { method: 'POST', body }),
  practiceHistory: (from, to, limit = 30, signal) =>
    request('/api/practice/changes/history', { query: { from, to, limit }, signal }),
  practiceSession: ({ minutes, playlistId }, signal) =>
    request('/api/practice/session', { query: { minutes, playlist_id: playlistId }, signal }),

  saveStudySession: (body) => request('/api/study/sessions', { method: 'POST', body }),
  studySummary: ({ today, days = 30 }, signal) =>
    request('/api/study/summary', { query: { today, days }, signal }),
  saveStudyGoal: (minutes) => request('/api/study/goal', { method: 'PUT', body: { minutes } }),

  arrangement: ({ artist, trackName, simplified = false }, signal) =>
    request('/api/arrangement', { query: { artist, track_name: trackName, simplified }, signal }),
  listening: ({ source = 'top', timeRange, limit = 12 }, signal) =>
    request('/api/recommendations/listening', { query: { source, time_range: timeRange, limit }, signal }),
};
