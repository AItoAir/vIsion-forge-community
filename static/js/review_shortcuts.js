import { attachMentionAutocomplete } from "./comment_mentions.js?v=20260330_comment_mentions";

function bootReviewShortcuts() {
  const panel = document.getElementById("review-action-panel");
  if (!panel) return;
  if (panel.dataset.reviewShortcutsBooted === "true") return;
  panel.dataset.reviewShortcutsBooted = "true";

  const approveForm = document.getElementById("review-approve-form");
  const resetForm = document.getElementById("review-reset-form");
  const rejectForm = document.getElementById("review-reject-form");
  const rejectTextarea = document.getElementById("reject-comment");
  const annotationList = document.getElementById("annotation-list");
  const prevChangedItemButton = document.getElementById("btn-prev-changed-item");
  const nextChangedItemButton = document.getElementById("btn-next-changed-item");
  const prevChangedAnnotationButton = document.getElementById(
    "btn-prev-changed-annotation"
  );
  const nextChangedAnnotationButton = document.getElementById(
    "btn-next-changed-annotation"
  );
  const showOnlyChangedToggle = document.getElementById(
    "review-show-only-changed"
  );
  const templateButtons = Array.from(
    document.querySelectorAll(".review-template-btn")
  );

  const currentUrl = window.location.pathname + window.location.search;
  const cfg = window.VISION_FORGE_CONFIG || {};
  const nextUrl = cfg.nextItemUrl || null;
  const prevChangedItemUrl = cfg.prevChangedItemUrl || null;
  const nextChangedItemUrl = cfg.nextChangedItemUrl || null;
  attachMentionAutocomplete(rejectTextarea, {
    candidates: Array.isArray(cfg.mentionCandidates) ? cfg.mentionCandidates : [],
  });

  function setRedirectTarget(form, targetUrl) {
    const redirectInput = form?.querySelector('input[name="redirect_to"]');
    if (!redirectInput) return;
    redirectInput.value = targetUrl || currentUrl;
  }

  function submitApprove(goNext = false) {
    if (!approveForm) return;
    setRedirectTarget(approveForm, goNext && nextUrl ? nextUrl : currentUrl);
    approveForm.requestSubmit();
  }

  function submitReset(goNext = false) {
    if (!resetForm) return;
    setRedirectTarget(resetForm, goNext && nextUrl ? nextUrl : currentUrl);
    resetForm.requestSubmit();
  }

  function submitReject(goNext = false) {
    if (!rejectForm || !rejectTextarea) return;
    if (!(rejectTextarea.value || "").trim()) {
      rejectTextarea.focus();
      return;
    }
    setRedirectTarget(rejectForm, goNext && nextUrl ? nextUrl : currentUrl);
    rejectForm.requestSubmit();
  }

  function goToUrl(targetUrl) {
    if (!targetUrl) return;
    window.location.href = targetUrl;
  }

  function getChangedRows() {
    if (!annotationList) return [];
    return Array.from(
      annotationList.querySelectorAll(
        ".annotation-row.change-new, .annotation-row.change-changed"
      )
    );
  }

  function focusChangedRow(direction) {
    const changedRows = getChangedRows();
    if (!changedRows.length) return;

    const activeIndex = changedRows.findIndex((row) =>
      row.classList.contains("active")
    );
    let nextIndex;

    if (activeIndex === -1) {
      nextIndex = direction > 0 ? 0 : changedRows.length - 1;
    } else {
      nextIndex = activeIndex + direction;
      if (nextIndex < 0) nextIndex = changedRows.length - 1;
      if (nextIndex >= changedRows.length) nextIndex = 0;
    }

    const targetRow = changedRows[nextIndex];
    targetRow.scrollIntoView({ block: "nearest" });
    targetRow.click();
  }

  function applyChangedOnlyFilter() {
    if (!annotationList || !showOnlyChangedToggle) return;
    const onlyChanged = !!showOnlyChangedToggle.checked;

    Array.from(annotationList.querySelectorAll(".annotation-row")).forEach((row) => {
      const isChanged =
        row.classList.contains("change-new") ||
        row.classList.contains("change-changed");
      row.style.display = !onlyChanged || isChanged ? "" : "none";
    });
  }

  templateButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (!rejectTextarea) return;
      const templateText = (button.dataset.template || "").trim();
      if (!templateText) return;

      const currentText = rejectTextarea.value.trim();
      if (!currentText) {
        rejectTextarea.value = templateText;
      } else if (!currentText.includes(templateText)) {
        rejectTextarea.value = `${rejectTextarea.value.trim()}\n${templateText}`;
      }

      rejectTextarea.focus();
      rejectTextarea.setSelectionRange(
        rejectTextarea.value.length,
        rejectTextarea.value.length
      );
    });
  });

  prevChangedItemButton?.addEventListener("click", (event) => {
    if (!prevChangedItemUrl) {
      event.preventDefault();
    }
  });

  nextChangedItemButton?.addEventListener("click", (event) => {
    if (!nextChangedItemUrl) {
      event.preventDefault();
    }
  });

  prevChangedAnnotationButton?.addEventListener("click", () => {
    focusChangedRow(-1);
  });

  nextChangedAnnotationButton?.addEventListener("click", () => {
    focusChangedRow(1);
  });

  showOnlyChangedToggle?.addEventListener("change", () => {
    applyChangedOnlyFilter();
  });

  if (annotationList) {
    const observer = new MutationObserver(() => {
      applyChangedOnlyFilter();
    });
    observer.observe(annotationList, { childList: true, subtree: true });
  }

  applyChangedOnlyFilter();

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;

    const activeElement = document.activeElement;
    const activeTag = activeElement?.tagName || "";

    if (
      event.key === "Enter" &&
      (event.ctrlKey || event.metaKey) &&
      activeElement === rejectTextarea
    ) {
      event.preventDefault();
      submitReject(!!event.shiftKey);
      return;
    }

    if (
      activeTag === "INPUT" ||
      activeTag === "SELECT" ||
      activeTag === "TEXTAREA"
    ) {
      return;
    }

    if (event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }

    const key = (event.key || "").toLowerCase();

    if (event.shiftKey && key === "a") {
      event.preventDefault();
      goToUrl(prevChangedItemUrl);
      return;
    }

    if (event.shiftKey && key === "d") {
      event.preventDefault();
      goToUrl(nextChangedItemUrl);
      return;
    }

    if (key === "y") {
      event.preventDefault();
      submitApprove(!!event.shiftKey);
      return;
    }

    if (key === "u") {
      event.preventDefault();
      submitReset(!!event.shiftKey);
      return;
    }

    if (key === "r") {
      event.preventDefault();
      rejectTextarea?.focus();
      return;
    }

    if (!event.shiftKey && key === "j") {
      event.preventDefault();
      focusChangedRow(1);
      return;
    }

    if (!event.shiftKey && key === "k") {
      event.preventDefault();
      focusChangedRow(-1);
    }
  });
}

if (typeof window !== "undefined") {
  const init = () => {
    try {
      bootReviewShortcuts();
    } catch (error) {
      console.error("Failed to initialize review shortcuts", error);
    }
  };

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  window.addEventListener("htmx:load", init);
}
