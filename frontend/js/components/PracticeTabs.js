// Treino and Progresso share one nav slot; these tabs make both reachable from either page.
import { h } from '../dom.js';

const TABS = [['treino', 'Treino', '#/treino'], ['progresso', 'Progresso', '#/progresso']];

export function PracticeTabs(active) {
  return h('nav', { class: 'page-tabs', 'aria-label': 'Treino e progresso' },
    TABS.map(([key, label, href]) => h('a', {
      class: 'page-tabs__tab', href, 'aria-current': key === active ? 'page' : undefined,
    }, label)));
}

// Long lists show the first few items; the rest open on demand.
export function ShowMore(items, visible, label) {
  if (items.length <= visible) return null;
  const button = h('button', {
    class: 'btn btn--ghost btn--small show-more', type: 'button',
    onclick: () => {
      items.slice(visible).forEach((item) => { item.hidden = false; });
      button.remove();
    },
  }, `${label} (${items.length - visible})`);
  items.slice(visible).forEach((item) => { item.hidden = true; });
  return button;
}
