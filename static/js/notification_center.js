(() => {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatRelativeTime(isoValue) {
    if (!isoValue) return "";
    const timestamp = Date.parse(isoValue);
    if (!Number.isFinite(timestamp)) return "";

    const diffMs = Date.now() - timestamp;
    const diffSeconds = Math.max(0, Math.round(diffMs / 1000));
    if (diffSeconds < 60) return "Just now";

    const diffMinutes = Math.round(diffSeconds / 60);
    if (diffMinutes < 60) {
      return `${diffMinutes} min${diffMinutes === 1 ? "" : "s"} ago`;
    }

    const diffHours = Math.round(diffMinutes / 60);
    if (diffHours < 24) {
      return `${diffHours} hour${diffHours === 1 ? "" : "s"} ago`;
    }

    const diffDays = Math.round(diffHours / 24);
    if (diffDays < 7) {
      return `${diffDays} day${diffDays === 1 ? "" : "s"} ago`;
    }

    try {
      return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(new Date(timestamp));
    } catch (_error) {
      return new Date(timestamp).toLocaleString();
    }
  }

  function initNotificationCenter() {
    const root = document.getElementById("notification-center");
    if (!root || root.dataset.notificationCenterReady === "1") {
      return;
    }
    root.dataset.notificationCenterReady = "1";

    const button = root.querySelector("[data-notification-toggle]");
    const badgeEl = root.querySelector("[data-notification-badge]");
    const statusEl = root.querySelector("[data-notification-status]");
    const listEl = root.querySelector("[data-notification-list]");
    const menuEl = root.querySelector(".notification-center-menu");
    const config = window.VISION_FORGE_NOTIFICATIONS || {};
    const pollIntervalMs = Math.max(
      5000,
      Number(config.pollIntervalMs) || 30000,
    );

    if (!button || !badgeEl || !statusEl || !listEl || !menuEl || !config.listUrl || !config.readUrl) {
      return;
    }

    let unreadCount = 0;
    let notifications = [];
    let fetchPromise = null;
    let markReadPromise = null;

    function isMenuOpen() {
      return (
        button.getAttribute("aria-expanded") === "true" ||
        menuEl.classList.contains("show")
      );
    }

    function updateStatus(message = "") {
      if (message) {
        statusEl.textContent = message;
        return;
      }
      if (unreadCount > 0) {
        statusEl.textContent = `${unreadCount} unread`;
        return;
      }
      statusEl.textContent = notifications.length ? "All caught up" : "No notifications yet";
    }

    function updateBadge() {
      if (unreadCount > 0) {
        badgeEl.hidden = false;
        badgeEl.textContent = unreadCount > 99 ? "99+" : String(unreadCount);
        return;
      }
      badgeEl.hidden = true;
      badgeEl.textContent = "0";
    }

    function renderNotifications() {
      if (!notifications.length) {
        listEl.innerHTML = '<div class="notification-center-empty">No notifications yet.</div>';
        return;
      }

      listEl.innerHTML = notifications
        .map((notification) => {
          const tagName = notification.link_path ? "a" : "div";
          const hrefAttr = notification.link_path
            ? ` href="${escapeHtml(notification.link_path)}"`
            : "";
          const unreadClass = notification.is_unread ? " is-unread" : "";
          const timeLabel = formatRelativeTime(notification.created_at);
          return `
            <${tagName} class="notification-center-item${unreadClass}"${hrefAttr}>
              <div class="notification-center-item-title">
                <span>${escapeHtml(notification.title)}</span>
                <span class="notification-center-item-time">${escapeHtml(timeLabel)}</span>
              </div>
              <div class="notification-center-item-body">${escapeHtml(notification.body)}</div>
            </${tagName}>
          `;
        })
        .join("");
    }

    async function markVisibleNotificationsRead() {
      if (!isMenuOpen() || markReadPromise) {
        return markReadPromise;
      }

      const unreadIds = notifications
        .filter((notification) => notification.is_unread)
        .map((notification) => Number(notification.id))
        .filter((notificationId) => Number.isInteger(notificationId) && notificationId > 0);

      if (!unreadIds.length) {
        return null;
      }

      markReadPromise = fetch(config.readUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ids: unreadIds }),
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error(`Notification read request failed (${response.status})`);
          }
          return response.json();
        })
        .then((payload) => {
          unreadCount = Number(payload?.unread_count) || 0;
          const unreadIdSet = new Set(unreadIds);
          notifications = notifications.map((notification) =>
            unreadIdSet.has(Number(notification.id))
              ? { ...notification, is_unread: false, read_at: notification.read_at || new Date().toISOString() }
              : notification
          );
          updateBadge();
          renderNotifications();
          updateStatus();
        })
        .catch((_error) => {
          updateStatus("Unable to update read state");
        })
        .finally(() => {
          markReadPromise = null;
        });

      return markReadPromise;
    }

    async function fetchNotifications({ markVisibleRead = false } = {}) {
      if (fetchPromise) {
        return fetchPromise;
      }

      updateStatus("Loading...");
      fetchPromise = fetch(config.listUrl, {
        headers: {
          Accept: "application/json",
        },
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new Error(`Notification request failed (${response.status})`);
          }
          return response.json();
        })
        .then(async (payload) => {
          unreadCount = Number(payload?.unread_count) || 0;
          notifications = Array.isArray(payload?.notifications)
            ? payload.notifications
            : [];
          updateBadge();
          renderNotifications();
          updateStatus();
          if (markVisibleRead && isMenuOpen()) {
            await markVisibleNotificationsRead();
          }
        })
        .catch((_error) => {
          if (!notifications.length) {
            listEl.innerHTML =
              '<div class="notification-center-empty">Unable to load notifications right now.</div>';
          }
          updateStatus("Unable to load notifications");
        })
        .finally(() => {
          fetchPromise = null;
        });

      return fetchPromise;
    }

    button.addEventListener("click", () => {
      window.setTimeout(() => {
        if (isMenuOpen()) {
          void fetchNotifications({ markVisibleRead: true });
        }
      }, 0);
    });

    root.addEventListener("shown.bs.dropdown", () => {
      void fetchNotifications({ markVisibleRead: true });
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        void fetchNotifications({ markVisibleRead: isMenuOpen() });
      }
    });

    window.setInterval(() => {
      if (document.visibilityState !== "visible") {
        return;
      }
      void fetchNotifications({ markVisibleRead: isMenuOpen() });
    }, pollIntervalMs);

    void fetchNotifications();
  }

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", initNotificationCenter, { once: true });
  } else {
    initNotificationCenter();
  }
})();
