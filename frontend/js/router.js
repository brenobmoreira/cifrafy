// Hash router. Each view receives { params, query, signal } and returns an element.
import { replaceContent, withViewTransition } from './dom.js';

function parseHash() {
  const raw = window.location.hash.replace(/^#/, '') || '/hoje';
  const [path, search = ''] = raw.split('?');
  return { segments: path.split('/').filter(Boolean), query: new URLSearchParams(search) };
}

function matchRoute(routes, segments) {
  for (const route of routes) {
    const pattern = route.path.split('/').filter(Boolean);
    if (pattern.length !== segments.length) continue;
    const params = {};
    const matches = pattern.every((part, index) => {
      if (part.startsWith(':')) {
        try {
          params[part.slice(1)] = decodeURIComponent(segments[index]);
        } catch {
          return false;
        }
        return true;
      }
      return part === segments[index];
    });
    if (matches) return { route, params };
  }
  return null;
}

export function createRouter({ routes, outlet, onRoute, notFound }) {
  let controller = null;

  function render() {
    const { segments, query } = parseHash();
    const match = matchRoute(routes, segments);
    if (!match) {
      window.location.hash = notFound;
      return;
    }

    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;

    withViewTransition(() => {
      const view = match.route.view({ params: match.params, query, signal });
      replaceContent(outlet, view);
      onRoute?.(match.route);
      window.scrollTo(0, 0);
    });
  }

  window.addEventListener('hashchange', render);
  return { start: render };
}

export const songPath = (artist, trackName) =>
  `#/tocar/${encodeURIComponent(artist)}/${encodeURIComponent(trackName)}`;
