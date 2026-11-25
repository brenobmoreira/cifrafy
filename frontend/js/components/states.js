import { h } from '../dom.js';
import { icon } from './icons.js';

export function Strings() {
  return h(
    'div',
    { class: 'strings', 'aria-hidden': 'true' },
    Array.from({ length: 6 }, (_, index) => h('span', { style: { '--s': index } })),
  );
}

export function Skeleton({ count = 4, variant = 'card' } = {}) {
  return h(
    'div',
    { class: `skeleton-group skeleton-group--${variant}`, role: 'status', 'aria-label': 'Carregando' },
    Array.from({ length: count }, (_, index) =>
      h('div', { class: 'skeleton', style: { '--i': index } }),
    ),
  );
}

export function EmptyState({ title, text, action }) {
  return h(
    'div',
    { class: 'state' },
    Strings(),
    h('h3', { class: 'state__title' }, title),
    text && h('p', { class: 'state__text' }, text),
    action,
  );
}

export function ErrorState({ error, onRetry }) {
  return h(
    'div',
    { class: 'state state--error', role: 'alert' },
    h('h3', { class: 'state__title' }, 'Algo desafinou'),
    h('p', { class: 'state__text' }, error?.message || 'Não foi possível carregar.'),
    onRetry && h('button', { class: 'btn', type: 'button', onclick: onRetry }, 'Tentar de novo'),
  );
}

export function LinkButton({ href, label, iconName, variant = 'primary' }) {
  return h('a', { class: `btn btn--${variant}`, href }, iconName && icon(iconName, 18), label);
}

let toastHost = null;

// An action (like Desfazer) keeps the toast up longer so there is time to reach it.
export function toast(message, tone = 'info', action = null) {
  if (!toastHost) {
    toastHost = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastHost);
  }
  const item = h('div', { class: `toast toast--${tone}` }, h('span', {}, message),
    action && h('button', {
      class: 'toast__action', type: 'button',
      onclick: () => {
        item.remove();
        action.onClick();
      },
    }, action.label));
  toastHost.append(item);
  setTimeout(() => item.remove(), action ? 8000 : 4800);
}
