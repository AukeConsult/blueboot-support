/* public/js/channels.js — shared channel nav for all board pages.
 *
 * A "channel" = one configured mailbox (settings/mail_accounts/accounts/{email}).
 * Support is the main channel; every other mailbox is a secondary channel that
 * can transfer cases into Support. This module fetches the channel list from
 * GET /api/support/channels and renders a pill nav, so every board page stays
 * in sync automatically as channels are added/renamed/removed in Firestore —
 * no per-channel HTML files needed.
 *
 * Usage (after `fetchJSON` — the page's own authenticated fetch helper —
 * is defined):
 *   const channels = await window.BBChannels.fetchChannels(fetchJSON);
 *   window.BBChannels.render("channel-nav", channels, currentAccount);
 *   const main = window.BBChannels.mainChannel(channels);
 */
window.BBChannels = (function () {
  let _channels = null;
  let _byAccount = {};

  async function fetchChannels(fetchJSON) {
    if (_channels) return _channels;
    const data = await fetchJSON("/api/support/channels");
    _channels = data.channels || [];
    _byAccount = {};
    _channels.forEach(c => { _byAccount[c.account] = c; });
    return _channels;
  }

  function mainChannel(channels) {
    const list = channels || _channels || [];
    return list.find(c => c.is_main) || list[0] || null;
  }

  function labelFor(account) {
    const c = _byAccount[account];
    return c ? c.label : (account || "").split("@")[0];
  }

  function isMain(account) {
    const c = _byAccount[account];
    return !!(c && c.is_main);
  }

  function escHtml(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
                     .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function render(containerId, channels, activeAccount) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (!channels || !channels.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = channels.map(c => {
      const active = c.account === activeAccount;
      const cls = ["bb-channel-pill"];
      if (active) cls.push("active");
      if (c.is_main) cls.push("main");
      const icon = c.is_main ? '<i class="ti ti-headset bb-channel-pill-icon"></i>' : "";
      const overdue = c.overdue_count || 0;
      const unread  = c.unread_count || 0;
      let badge = "";
      if (overdue > 0) {
        badge = `<span class="bb-channel-pill-badge overdue" title="${overdue} overdue">${overdue}</span>`;
      } else if (unread > 0) {
        badge = `<span class="bb-channel-pill-badge" title="${unread} awaiting reply">${unread}</span>`;
      }
      return `<a href="board.html?account=${encodeURIComponent(c.account)}"
                 class="${cls.join(" ")}" title="${escHtml(c.account)}">
                ${icon}${escHtml(c.label)}${badge}
              </a>`;
    }).join("");
  }

  return { fetchChannels, mainChannel, labelFor, isMain, render };
})();
