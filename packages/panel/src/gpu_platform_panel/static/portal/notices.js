(() => {
  const toasts = document.querySelector('[data-notice-toasts]');
  const banners = document.querySelector('[data-notice-banners]');
  const session = document.querySelector('[data-notice-session]');
  const count = document.querySelector('[data-notice-count]');
  const shownToasts = new Map();
  let banner = null;
  let fetching = false;

  function remove(popup) {
    popup.classList.add('notice-fading');
    window.setTimeout(() => popup.remove(), 200);
  }

  function controls(popup, seconds, dismiss) {
    let timer;
    let remaining = seconds * 1000;
    let resumedAt;
    let closed = false;
    function close() {
      if (closed) return;
      closed = true;
      window.clearTimeout(timer);
      dismiss();
      remove(popup);
    }
    function resume() {
      if (closed || !seconds || timer || popup.matches(':hover, :focus-within')) return;
      resumedAt = performance.now();
      timer = window.setTimeout(close, remaining);
    }
    function pause() {
      if (!timer) return;
      window.clearTimeout(timer);
      timer = undefined;
      remaining = Math.max(0, remaining - (performance.now() - resumedAt));
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'notice-close';
    button.setAttribute('aria-label', toasts.dataset.closeLabel);
    button.textContent = '×';
    button.addEventListener('click', close);
    popup.append(button);
    popup.addEventListener('mouseenter', pause);
    popup.addEventListener('mouseleave', resume);
    popup.addEventListener('focusin', pause);
    popup.addEventListener('focusout', () => window.setTimeout(resume, 0));
    resume();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      remove(popup);
    };
  }

  function popupFor(item, destination, dismiss) {
    const popup = document.createElement('div');
    popup.className = 'notice-popup';
    const text = document.createElement('div');
    text.className = 'notice-popup-content';
    const title = document.createElement('strong');
    title.textContent = item.title;
    text.append(title);
    if (item.content) {
      const preview = document.createElement('p');
      preview.textContent = item.content;
      text.append(preview);
    }
    popup.append(text);
    destination.append(popup);
    return controls(popup, item.seconds, dismiss);
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {'X-CSRFToken': session.elements.csrfmiddlewaretoken.value},
      body,
      redirect: 'error'
    });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  async function refresh() {
    if (fetching || document.visibilityState === 'hidden') return;
    fetching = true;
    try {
      const body = new URLSearchParams();
      for (const id of shownToasts.keys()) body.append('shown', id);
      const updates = await post(session.dataset.reminders, body);
      count.textContent = updates.unread || '';
      count.hidden = !updates.unread;
      for (const id of updates.remove) {
        shownToasts.get(id)?.();
        shownToasts.delete(id);
      }
      for (const item of updates.toasts) {
        shownToasts.set(item.id, popupFor(item, toasts, () => shownToasts.delete(item.id)));
      }
      const item = updates.banners[0];
      if (banner && banner.id !== item?.id) {
        banner.remove();
        banner = null;
      }
      if (item && !banner) {
        banner = {id: item.id, remove: popupFor(item, banners, () => {
          // Keep the current ID until the server confirms that the banner was dismissed.
          post(session.dataset.dismissUrl.replace('/0/', `/${item.id}/`)).catch(() => {}).finally(() => {
            if (banner?.id === item.id) banner = null;
          });
        })};
      }
    } catch {
      // Retry on the next refresh.
    } finally {
      fetching = false;
    }
  }

  if (toasts) {
    toasts.querySelectorAll('[data-flash]').forEach(popup => controls(popup, Number(popup.dataset.seconds), () => {}));
    document.addEventListener('panel-notice', ({detail}) => {
      const popup = document.createElement('div');
      popup.classList.add('notice-popup', detail.level);
      const message = document.createElement('p');
      message.textContent = detail.message;
      popup.append(message);
      toasts.append(popup);
      controls(popup, detail.level === 'error' ? 0 : 5, () => {});
    });
  }
  if (session) {
    refresh();
    window.setInterval(refresh, 15000);
    document.addEventListener('visibilitychange', refresh);
  }

  const composer = document.querySelector('[data-notice-compose]');
  if (composer) {
    const {audience, member, group, presentation, seconds, expires_at} = composer.elements;
    const send = composer.querySelector('[data-notice-send]');
    const summary = composer.querySelector('[data-notice-count-summary]');
    const recipientCount = composer.querySelector('[data-notice-recipient-count]');
    const recipients = JSON.parse(document.getElementById('notice-recipient-counts').textContent);
    composer.querySelectorAll('[data-notice-hint]').forEach(hint => {
      hint.addEventListener('click', () => hint.focus());
    });
    function showFields() {
      member.required = audience.value === 'member';
      group.required = audience.value === 'group';
      member.closest('.field').hidden = !member.required;
      group.closest('.field').hidden = !group.required;
      seconds.closest('[data-notice-duration]').hidden = presentation.value === 'inbox';
      expires_at.closest('[data-notice-expiry]').hidden = presentation.value === 'inbox';
      seconds.disabled = expires_at.disabled = presentation.value === 'inbox';
      const count = {
        member: member.value ? 1 : 0,
        group: recipients.groups[group.value] ?? 0,
        all: recipients.all
      }[audience.value];
      recipientCount.textContent = count ?? 0;
      summary.hidden = count === undefined;
    }
    composer.addEventListener('input', showFields);
    composer.addEventListener('submit', () => { send.disabled = true; });
    showFields();
  }
})();
