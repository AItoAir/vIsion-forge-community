let mediaConversionPollTimer = null;

function applyMediaConversionPayload(root, payload) {
  if (!root || !payload) return;

  root.dataset.mediaConversionStatus = payload.status || "";

  const badge = root.querySelector("[data-media-conversion-badge]");
  if (badge) {
    badge.className = `badge ${payload.badge_class || "text-bg-secondary"}`;
    badge.textContent = payload.label || "Unknown";
  }

  const message = root.querySelector("[data-media-conversion-message]");
  if (message) {
    message.textContent = payload.message || "";
  }

  const detail = root.querySelector("[data-media-conversion-detail]");
  if (detail) {
    const text = (payload.detail || "").trim();
    detail.textContent = text;
    detail.classList.toggle("d-none", !text);
    detail.classList.toggle("text-danger", !!payload.failed);
    detail.classList.toggle("text-secondary", !payload.failed);
  }
}

function isPendingStatus(status) {
  return status === "pending" || status === "processing";
}

function mediaConversionRoots() {
  return Array.from(
    document.querySelectorAll("[data-media-conversion-root][data-status-url]"),
  );
}

async function pollMediaConversionOnce() {
  const roots = mediaConversionRoots();
  let hasPending = false;

  for (const root of roots) {
    const status = (root.dataset.mediaConversionStatus || "").trim();
    const statusUrl = root.dataset.statusUrl;
    if (!statusUrl || !isPendingStatus(status)) {
      continue;
    }

    hasPending = true;
    try {
      const response = await fetch(statusUrl, {
        headers: {
          Accept: "application/json",
        },
      });
      if (!response.ok) {
        continue;
      }

      const payload = await response.json();
      applyMediaConversionPayload(root, payload);

      if (root.dataset.reloadOnReady === "true" && payload.ready) {
        window.location.reload();
        return false;
      }
    } catch (error) {
      console.error("Failed to poll media conversion status", error);
    }
  }

  return hasPending;
}

function scheduleMediaConversionPoll(delayMs = 0) {
  if (mediaConversionPollTimer != null) {
    return;
  }

  mediaConversionPollTimer = window.setTimeout(async () => {
    mediaConversionPollTimer = null;
    const hasPending = await pollMediaConversionOnce();
    if (hasPending) {
      scheduleMediaConversionPoll(1500);
    }
  }, delayMs);
}

function bootMediaConversionStatus() {
  if (!mediaConversionRoots().length) {
    return;
  }
  scheduleMediaConversionPoll(0);
}

if (typeof window !== "undefined") {
  const init = () => {
    try {
      bootMediaConversionStatus();
    } catch (error) {
      console.error("Failed to initialize media conversion polling", error);
    }
  };

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  window.addEventListener("htmx:load", init);
}
