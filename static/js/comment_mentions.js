function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function normalizeCandidate(candidate) {
  if (!candidate || typeof candidate !== "object") return null;
  const id = Number(candidate.id);
  const email = String(candidate.email ?? "").trim().toLowerCase();
  if (!Number.isInteger(id) || !email) return null;
  const name = String(candidate.name ?? "").trim();
  const displayName =
    String(candidate.display_name ?? "").trim() || name || email;
  return {
    id,
    email,
    name,
    display_name: displayName,
    mention_text: String(candidate.mention_text ?? `@${email}`).trim() || `@${email}`,
  };
}

function normalizeMentions(mentions) {
  if (!Array.isArray(mentions)) return [];
  return mentions
    .map((mention) => {
      if (!mention || typeof mention !== "object") return null;
      const userId = Number(mention.user_id);
      const email = String(mention.email ?? "").trim().toLowerCase();
      const displayName = String(mention.display_name ?? "").trim() || email;
      const mentionText =
        String(mention.mention_text ?? `@${email}`).trim() || `@${email}`;
      const start = Number(mention.start);
      const end = Number(mention.end);
      if (!Number.isInteger(userId) || !email) return null;
      return {
        user_id: userId,
        email,
        display_name: displayName,
        mention_text: mentionText,
        start: Number.isInteger(start) ? start : -1,
        end: Number.isInteger(end) ? end : -1,
      };
    })
    .filter(Boolean)
    .sort((left, right) => {
      if (left.start !== right.start) return left.start - right.start;
      if (left.end !== right.end) return left.end - right.end;
      return left.user_id - right.user_id;
    });
}

function mentionDisplayText(mention) {
  const displayName = String(mention.display_name ?? "").trim();
  const email = String(mention.email ?? "").trim();
  if (displayName && displayName !== email) {
    return `@${displayName}`;
  }
  return String(mention.mention_text ?? `@${email}`).trim() || `@${email}`;
}

export function renderCommentHtml(commentText, mentions) {
  const normalizedComment = String(commentText ?? "");
  const normalizedMentions = normalizeMentions(mentions);
  let cursor = 0;
  const htmlParts = [];

  normalizedMentions.forEach((mention) => {
    const start = mention.start;
    const end = mention.end;
    if (start < cursor || end <= start || end > normalizedComment.length) {
      return;
    }
    if (normalizedComment.slice(start, end) !== mention.mention_text) {
      return;
    }

    htmlParts.push(escapeHtml(normalizedComment.slice(cursor, start)));
    htmlParts.push(
      `<span class="comment-mention" title="${escapeHtml(
        mention.email
      )}">${escapeHtml(mentionDisplayText(mention))}</span>`
    );
    cursor = end;
  });

  htmlParts.push(escapeHtml(normalizedComment.slice(cursor)));
  return htmlParts.join("").replaceAll("\n", "<br>");
}

function getQueryState(textarea) {
  if (!textarea) return null;
  const selectionStart = Number(textarea.selectionStart);
  const selectionEnd = Number(textarea.selectionEnd);
  if (!Number.isInteger(selectionStart) || selectionStart !== selectionEnd) {
    return null;
  }

  const beforeCaret = textarea.value.slice(0, selectionStart);
  const match = /(^|[\s(])@([^\s@]*)$/.exec(beforeCaret);
  if (!match) return null;

  const prefixLength = match[1] ? match[1].length : 0;
  const tokenStart = beforeCaret.length - match[0].length + prefixLength;
  return {
    start: tokenStart,
    end: selectionStart,
    query: String(match[2] ?? "").trim().toLowerCase(),
  };
}

function filterCandidates(candidates, query) {
  const normalizedCandidates = (Array.isArray(candidates) ? candidates : [])
    .map(normalizeCandidate)
    .filter(Boolean);
  if (!query) {
    return normalizedCandidates.slice(0, 6);
  }

  return normalizedCandidates
    .filter((candidate) => {
      const haystack = [
        candidate.display_name,
        candidate.name,
        candidate.email,
      ]
        .filter(Boolean)
        .join("\n")
        .toLowerCase();
      return haystack.includes(query);
    })
    .slice(0, 6);
}

function createMenu() {
  if (window.__visionForgeMentionMenu instanceof HTMLElement) {
    return window.__visionForgeMentionMenu;
  }

  const menu = document.createElement("div");
  menu.className = "mention-autocomplete-menu";
  menu.hidden = true;
  document.body.appendChild(menu);
  window.__visionForgeMentionMenu = menu;
  return menu;
}

function positionMenu(menu, textarea) {
  if (!menu || !textarea) return;
  const rect = textarea.getBoundingClientRect();
  menu.style.left = `${Math.round(rect.left + window.scrollX)}px`;
  menu.style.top = `${Math.round(rect.bottom + window.scrollY + 4)}px`;
  menu.style.width = `${Math.max(rect.width, 220)}px`;
}

function insertCandidate(textarea, queryState, candidate) {
  const before = textarea.value.slice(0, queryState.start);
  const after = textarea.value.slice(queryState.end);
  const separator =
    !after || /^\s/.test(after) ? "" : " ";
  const insertion = `${candidate.mention_text}${separator}`;
  textarea.value = `${before}${insertion}${after}`;
  const nextCursor = before.length + insertion.length;
  textarea.setSelectionRange(nextCursor, nextCursor);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

export function attachMentionAutocomplete(textarea, options = {}) {
  if (!textarea || textarea.dataset.mentionAutocompleteBooted === "true") {
    return;
  }
  textarea.dataset.mentionAutocompleteBooted = "true";

  const menu = createMenu();
  const state = {
    candidates: Array.isArray(options.candidates) ? options.candidates : [],
    activeIndex: 0,
    filteredCandidates: [],
    queryState: null,
  };

  function hideMenu() {
    menu.hidden = true;
    menu.innerHTML = "";
    state.filteredCandidates = [];
    state.queryState = null;
    state.activeIndex = 0;
  }

  function renderMenu() {
    if (!state.queryState || !state.filteredCandidates.length) {
      hideMenu();
      return;
    }

    positionMenu(menu, textarea);
    menu.hidden = false;
    menu.innerHTML = "";

    state.filteredCandidates.forEach((candidate, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className =
        "mention-autocomplete-item" +
        (index === state.activeIndex ? " is-active" : "");
      button.innerHTML = `
        <span class="mention-autocomplete-name">${escapeHtml(
          candidate.display_name
        )}</span>
        <span class="mention-autocomplete-email">${escapeHtml(candidate.email)}</span>
      `;
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
      });
      button.addEventListener("click", () => {
        insertCandidate(textarea, state.queryState, candidate);
        hideMenu();
        textarea.focus();
      });
      menu.appendChild(button);
    });
  }

  function refreshMenu() {
    state.queryState = getQueryState(textarea);
    if (!state.queryState) {
      hideMenu();
      return;
    }

    state.filteredCandidates = filterCandidates(
      state.candidates,
      state.queryState.query
    );
    if (!state.filteredCandidates.length) {
      hideMenu();
      return;
    }
    state.activeIndex = Math.max(
      0,
      Math.min(state.activeIndex, state.filteredCandidates.length - 1)
    );
    renderMenu();
  }

  function onDocumentPointerDown(event) {
    if (event.target === textarea || menu.contains(event.target)) {
      return;
    }
    hideMenu();
  }

  textarea.addEventListener("input", refreshMenu);
  textarea.addEventListener("click", refreshMenu);
  textarea.addEventListener("focus", refreshMenu);
  textarea.addEventListener("blur", () => {
    window.setTimeout(() => {
      if (document.activeElement !== textarea && !menu.contains(document.activeElement)) {
        hideMenu();
      }
    }, 80);
  });
  textarea.addEventListener("keydown", (event) => {
    if (!state.filteredCandidates.length || menu.hidden) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      event.stopImmediatePropagation();
      event.stopPropagation();
      state.activeIndex =
        (state.activeIndex + 1) % state.filteredCandidates.length;
      renderMenu();
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      event.stopImmediatePropagation();
      event.stopPropagation();
      state.activeIndex =
        (state.activeIndex - 1 + state.filteredCandidates.length) %
        state.filteredCandidates.length;
      renderMenu();
      return;
    }

    if (event.key === "Enter" || event.key === "Tab") {
      const candidate = state.filteredCandidates[state.activeIndex];
      if (!candidate || !state.queryState) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      event.stopPropagation();
      insertCandidate(textarea, state.queryState, candidate);
      hideMenu();
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();
      event.stopPropagation();
      hideMenu();
    }
  });

  window.addEventListener("resize", () => {
    if (!menu.hidden) {
      positionMenu(menu, textarea);
    }
  });
  document.addEventListener("mousedown", onDocumentPointerDown);
}
