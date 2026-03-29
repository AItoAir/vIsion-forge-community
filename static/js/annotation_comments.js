function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function generateClientUid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `lf${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

function cloneAuditUser(user) {
  if (!user || typeof user !== "object") return null;
  const cloned = {};
  if (Number.isInteger(user.id)) {
    cloned.id = user.id;
  }
  if (typeof user.email === "string" && user.email.trim()) {
    cloned.email = user.email;
  }
  return Object.keys(cloned).length ? cloned : null;
}

function normalizeCommentText(value) {
  return String(value ?? "").trim();
}

const timestampFormatter =
  typeof Intl !== "undefined"
    ? new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      })
    : null;

function formatTimestamp(value) {
  if (typeof value !== "string" || !value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  if (timestampFormatter) return timestampFormatter.format(parsed);
  return parsed.toISOString().replace("T", " ").slice(0, 19);
}

function getAuditUserLabel(user, userId) {
  if (user?.email) return user.email;
  if (Number.isInteger(user?.id)) return `User #${user.id}`;
  if (Number.isInteger(userId)) return `User #${userId}`;
  return "Unknown user";
}

function getCommentSortValue(comment) {
  const source = comment?.updated_at || comment?.created_at || null;
  if (typeof source !== "string" || !source) return 0;
  const parsed = new Date(source);
  if (Number.isNaN(parsed.getTime())) return 0;
  return parsed.getTime();
}

const REGION_COMMENT_QUERY_PARAM = "region_comment";
const REGION_COMMENT_FRAME_QUERY_PARAM = "frame";

function cloneRegionComment(comment) {
  return {
    id: Number.isInteger(comment?.id) ? comment.id : null,
    client_uid:
      typeof comment?.client_uid === "string" && comment.client_uid.trim()
        ? comment.client_uid.trim()
        : generateClientUid(),
    frame_index:
      comment?.frame_index == null || !Number.isFinite(Number(comment.frame_index))
        ? null
        : Math.max(0, Math.trunc(Number(comment.frame_index))),
    x1: Number(comment?.x1),
    y1: Number(comment?.y1),
    x2: Number(comment?.x2),
    y2: Number(comment?.y2),
    comment: normalizeCommentText(comment?.comment),
    created_by: Number.isInteger(comment?.created_by) ? comment.created_by : null,
    updated_by: Number.isInteger(comment?.updated_by) ? comment.updated_by : null,
    created_at:
      typeof comment?.created_at === "string" && comment.created_at
        ? comment.created_at
        : null,
    updated_at:
      typeof comment?.updated_at === "string" && comment.updated_at
        ? comment.updated_at
        : null,
    created_by_user: cloneAuditUser(comment?.created_by_user),
    updated_by_user: cloneAuditUser(comment?.updated_by_user),
  };
}

function parseRegionCommentFrameParam(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.trunc(parsed) - 1);
}

function getRequestedRegionCommentTarget() {
  if (typeof window === "undefined") {
    return { clientUid: null, frameIndex: null };
  }

  let url;
  try {
    url = new URL(window.location.href);
  } catch (_error) {
    return { clientUid: null, frameIndex: null };
  }

  const requestedClientUid = url.searchParams.get(REGION_COMMENT_QUERY_PARAM);
  return {
    clientUid:
      typeof requestedClientUid === "string" && requestedClientUid.trim()
        ? requestedClientUid.trim()
        : null,
    frameIndex: parseRegionCommentFrameParam(
      url.searchParams.get(REGION_COMMENT_FRAME_QUERY_PARAM)
    ),
  };
}

function commentsEqual(left, right) {
  return (
    left.client_uid === right.client_uid &&
    left.frame_index === right.frame_index &&
    left.x1 === right.x1 &&
    left.y1 === right.y1 &&
    left.x2 === right.x2 &&
    left.y2 === right.y2 &&
    left.comment === right.comment
  );
}

function getCommentState(canvas) {
  return canvas.regionCommentState || null;
}

function getFrameKey(canvas, frameIndex) {
  if (canvas.kind !== "video") return null;
  if (!Number.isFinite(Number(frameIndex))) return null;
  return Math.max(0, Math.trunc(Number(frameIndex)));
}

function ensureCommentClientUid(comment) {
  if (!comment.client_uid) {
    comment.client_uid = generateClientUid();
  }
  return comment.client_uid;
}

function normalizeRegionCommentForStore(canvas, comment) {
  const normalized = cloneRegionComment(comment);
  normalized.comment = normalizeCommentText(normalized.comment);
  if (canvas.kind !== "video") {
    normalized.frame_index = null;
  } else if (!Number.isInteger(normalized.frame_index)) {
    normalized.frame_index = canvas.currentFrameIndex | 0;
  }

  if (typeof canvas.normalizeAnnotationCoords === "function") {
    canvas.normalizeAnnotationCoords(normalized);
  }
  ensureCommentClientUid(normalized);
  return normalized;
}

function getStoredRegionComments(canvas) {
  const state = getCommentState(canvas);
  if (!state) return [];
  const all = [];
  state.frameComments.forEach((bucket) => {
    if (Array.isArray(bucket) && bucket.length) {
      all.push(...bucket);
    }
  });
  return all;
}

function getStoredRegionCommentByClientUid(canvas, clientUid) {
  if (!clientUid) return null;
  return (
    getStoredRegionComments(canvas).find((comment) => comment.client_uid === clientUid) ||
    null
  );
}

function setStoredRegionComments(canvas, comments) {
  const state = getCommentState(canvas);
  if (!state) return;

  const frameComments = new Map();
  (Array.isArray(comments) ? comments : []).forEach((comment) => {
    const normalized = normalizeRegionCommentForStore(canvas, comment);
    if (!normalized.comment) return;
    if (
      typeof canvas.isDegenerateAnnotation === "function" &&
      canvas.isDegenerateAnnotation(normalized)
    ) {
      return;
    }

    const key = getFrameKey(canvas, normalized.frame_index);
    const bucket = frameComments.get(key) || [];
    bucket.push(normalized);
    frameComments.set(key, bucket);
  });

  frameComments.forEach((bucket, key) => {
    bucket.sort((left, right) => {
      const leftSort = getCommentSortValue(left);
      const rightSort = getCommentSortValue(right);
      if (leftSort !== rightSort) return rightSort - leftSort;
      return (left.client_uid || "").localeCompare(right.client_uid || "");
    });
    frameComments.set(key, bucket);
  });

  state.frameComments = frameComments;
}

function buildVisibleCommentsForCurrentFrame(canvas) {
  const state = getCommentState(canvas);
  if (!state) return [];
  const key = getFrameKey(canvas, canvas.currentFrameIndex);
  const bucket = state.frameComments.get(key) || [];
  return bucket.map((comment) => cloneRegionComment(comment));
}

function makeCommentSnapshotMap(canvas) {
  const snapshot = new Map();
  getStoredRegionComments(canvas).forEach((comment) => {
    const normalized = normalizeRegionCommentForStore(canvas, comment);
    snapshot.set(normalized.client_uid, normalized);
  });
  return snapshot;
}

function buildCommentPatch(canvas) {
  const state = getCommentState(canvas);
  if (!state) return { upserts: [], deletes: [] };

  const currentState = makeCommentSnapshotMap(canvas);
  const upserts = [];
  const deletes = [];

  currentState.forEach((comment, clientUid) => {
    const savedComment = state.lastSavedState.get(clientUid);
    if (!savedComment || !commentsEqual(savedComment, comment)) {
      upserts.push(comment);
    }
  });

  state.lastSavedState.forEach((_comment, clientUid) => {
    if (!currentState.has(clientUid)) {
      deletes.push(clientUid);
    }
  });

  return { upserts, deletes };
}

function replaceSavedCommentState(canvas) {
  const state = getCommentState(canvas);
  if (!state) return;
  state.lastSavedState = makeCommentSnapshotMap(canvas);
}

function setActiveCommentClientUid(canvas, clientUid) {
  const state = getCommentState(canvas);
  if (!state) return;

  state.activeCommentClientUid = clientUid || null;
  state.activeComment =
    state.visibleComments.find((comment) => comment.client_uid === state.activeCommentClientUid) ||
    null;
}

function syncVisibleComments(canvas) {
  const state = getCommentState(canvas);
  if (!state) return;

  state.visibleComments = buildVisibleCommentsForCurrentFrame(canvas);
  setActiveCommentClientUid(canvas, state.activeCommentClientUid);
}

function getCommentFrameLabel(canvas, comment) {
  if (canvas.kind !== "video") return "Image comment";
  const frameIndex = Number.isInteger(comment?.frame_index) ? comment.frame_index : 0;
  return `Frame ${frameIndex + 1}`;
}

function getCommentSummary(comment) {
  const text = normalizeCommentText(comment?.comment).replace(/\s+/g, " ");
  if (!text) return "(No comment)";
  return text.length > 72 ? `${text.slice(0, 69)}...` : text;
}

function drawRoundedRect(ctx, x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function drawCommentBadge(ctx, rect, active = false) {
  const fillColor = active ? "rgba(255, 193, 7, 0.95)" : "rgba(255, 193, 7, 0.85)";
  const strokeColor = active ? "rgba(255, 255, 255, 0.92)" : "rgba(0, 0, 0, 0.45)";

  drawRoundedRect(ctx, rect.x, rect.y, rect.w, rect.h, 6);
  ctx.fillStyle = fillColor;
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = strokeColor;
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(rect.x + 7, rect.y + rect.h);
  ctx.lineTo(rect.x + 11, rect.y + rect.h);
  ctx.lineTo(rect.x + 8.5, rect.y + rect.h + 4);
  ctx.closePath();
  ctx.fillStyle = fillColor;
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = "#111111";
  const centerY = rect.y + rect.h / 2;
  [rect.x + 7, rect.x + 12, rect.x + 17].forEach((cx) => {
    ctx.beginPath();
    ctx.arc(cx, centerY, 1.3, 0, Math.PI * 2);
    ctx.fill();
  });
}

function computeCommentBadgeRect(canvas, comment) {
  const minX = Math.min(comment.x1, comment.x2);
  const minY = Math.min(comment.y1, comment.y2);
  const point = canvas.fromImageToCanvasCoords(minX, minY);
  const viewportWidth =
    canvas.viewportWidth || canvas.canvas.width / (canvas.pixelRatio || 1) || 0;
  const viewportHeight =
    canvas.viewportHeight || canvas.canvas.height / (canvas.pixelRatio || 1) || 0;

  const rect = {
    x: (point.x || 0) + 6,
    y: (point.y || 0) + 6,
    w: 24,
    h: 18,
  };

  if (viewportWidth) {
    rect.x = Math.max(4, Math.min(viewportWidth - rect.w - 4, rect.x));
  }
  if (viewportHeight) {
    rect.y = Math.max(4, Math.min(viewportHeight - rect.h - 8, rect.y));
  }
  return rect;
}

function isPointInsideRect(x, y, rect) {
  return x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h + 4;
}

function findCommentBadgeHit(canvas, xCanvas, yCanvas) {
  const state = getCommentState(canvas);
  if (!state || !state.visible) return null;

  const comments = [...state.visibleComments];
  comments.sort((left, right) => {
    if (left.client_uid === state.activeCommentClientUid) return -1;
    if (right.client_uid === state.activeCommentClientUid) return 1;
    return 0;
  });

  for (const comment of comments) {
    const rect = computeCommentBadgeRect(canvas, comment);
    if (isPointInsideRect(xCanvas, yCanvas, rect)) {
      return { comment, rect };
    }
  }

  return null;
}

function getCommentArea(comment) {
  return Math.abs((comment?.x2 ?? 0) - (comment?.x1 ?? 0)) *
    Math.abs((comment?.y2 ?? 0) - (comment?.y1 ?? 0));
}

function compareCommentHits(left, right, activeClientUid) {
  const leftPriority = left?.handle ? 2 : 1;
  const rightPriority = right?.handle ? 2 : 1;
  if (leftPriority !== rightPriority) {
    return rightPriority - leftPriority;
  }

  if (leftPriority > 1) {
    const leftDistance = Number.isFinite(left?.hitDistanceSq)
      ? left.hitDistanceSq
      : Number.POSITIVE_INFINITY;
    const rightDistance = Number.isFinite(right?.hitDistanceSq)
      ? right.hitDistanceSq
      : Number.POSITIVE_INFINITY;
    if (Math.abs(leftDistance - rightDistance) > 1e-6) {
      return leftDistance - rightDistance;
    }
  }

  const leftArea = getCommentArea(left?.comment);
  const rightArea = getCommentArea(right?.comment);
  if (Math.abs(leftArea - rightArea) > 1e-6) {
    return leftArea - rightArea;
  }

  const leftIsActive = left?.comment?.client_uid === activeClientUid ? 1 : 0;
  const rightIsActive = right?.comment?.client_uid === activeClientUid ? 1 : 0;
  if (leftIsActive !== rightIsActive) {
    return rightIsActive - leftIsActive;
  }

  return 0;
}

function findCommentRegionHit(canvas, xCanvas, yCanvas) {
  const state = getCommentState(canvas);
  if (!state || !state.visible) return null;

  const handleRadius = canvas.handleRadius || 6;
  const handleRadiusSq = handleRadius * handleRadius;
  let bestHit = null;

  for (const comment of state.visibleComments) {
    const p1 = canvas.fromImageToCanvasCoords(comment.x1, comment.y1);
    const p2 = canvas.fromImageToCanvasCoords(comment.x2, comment.y2);
    const minX = Math.min(p1.x, p2.x);
    const maxX = Math.max(p1.x, p2.x);
    const minY = Math.min(p1.y, p2.y);
    const maxY = Math.max(p1.y, p2.y);

    const corners = [
      { name: "nw", cx: minX, cy: minY },
      { name: "ne", cx: maxX, cy: minY },
      { name: "se", cx: maxX, cy: maxY },
      { name: "sw", cx: minX, cy: maxY },
    ];

    let hit = null;
    for (const corner of corners) {
      const dx = xCanvas - corner.cx;
      const dy = yCanvas - corner.cy;
      if (dx * dx + dy * dy <= handleRadiusSq) {
        hit = {
          comment,
          handle: corner.name,
          hitDistanceSq: dx * dx + dy * dy,
        };
        break;
      }
    }

    if (!hit &&
      xCanvas >= minX &&
      xCanvas <= maxX &&
      yCanvas >= minY &&
      yCanvas <= maxY) {
      hit = {
        comment,
        handle: null,
        hitDistanceSq: 0,
      };
    }

    if (hit) {
      if (
        !bestHit ||
        compareCommentHits(hit, bestHit, state.activeCommentClientUid) < 0
      ) {
        bestHit = hit;
      }
    }
  }

  return bestHit;
}

function getCommentCursor(canvas, hit) {
  if (!hit) {
    return canvas.readOnly ? "default" : "crosshair";
  }
  if (canvas.readOnly) {
    return "pointer";
  }
  if (hit.handle === "nw" || hit.handle === "se") {
    return "nwse-resize";
  }
  if (hit.handle === "ne" || hit.handle === "sw") {
    return "nesw-resize";
  }
  return "move";
}

function drawCommentHandles(ctx, comment, scale, strokeColor) {
  const worldHandleRadius = (6 / scale);
  const worldHandleBorderWidth = (2 / scale);
  const corners = [
    { cx: Math.min(comment.x1, comment.x2), cy: Math.min(comment.y1, comment.y2) },
    { cx: Math.max(comment.x1, comment.x2), cy: Math.min(comment.y1, comment.y2) },
    { cx: Math.max(comment.x1, comment.x2), cy: Math.max(comment.y1, comment.y2) },
    { cx: Math.min(comment.x1, comment.x2), cy: Math.max(comment.y1, comment.y2) },
  ];

  corners.forEach((corner) => {
    ctx.beginPath();
    ctx.arc(corner.cx, corner.cy, worldHandleRadius, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.lineWidth = worldHandleBorderWidth;
    ctx.strokeStyle = strokeColor;
    ctx.stroke();
  });
}

function drawRegionComments(canvas) {
  const state = getCommentState(canvas);
  if (!state || !state.visible) return;

  const comments = [...state.visibleComments];
  if (state.draftComment) {
    comments.push(state.draftComment);
  }
  if (!comments.length) return;

  const ctx = canvas.ctx;
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);
  const translateX = canvas.translateX || 0;
  const translateY = canvas.translateY || 0;
  const pixelRatio = canvas.pixelRatio || 1;

  ctx.save();
  ctx.setTransform(
    pixelRatio * scale,
    0,
    0,
    pixelRatio * scale,
    pixelRatio * translateX,
    pixelRatio * translateY
  );

  comments.forEach((comment) => {
    const active =
      comment.client_uid === state.activeCommentClientUid || comment === state.draftComment;
    const minX = Math.min(comment.x1, comment.x2);
    const minY = Math.min(comment.y1, comment.y2);
    const width = Math.abs(comment.x2 - comment.x1);
    const height = Math.abs(comment.y2 - comment.y1);
    const strokeColor = active ? "#ffd166" : "#ffb347";

    ctx.save();
    ctx.setLineDash([8 / scale, 5 / scale]);
    ctx.lineWidth = active ? 3 / scale : 2 / scale;
    ctx.strokeStyle = strokeColor;
    ctx.fillStyle = active ? "rgba(255, 179, 71, 0.18)" : "rgba(255, 179, 71, 0.08)";
    ctx.fillRect(minX, minY, width, height);
    ctx.strokeRect(minX, minY, width, height);
    ctx.setLineDash([]);
    if (active && comment !== state.draftComment) {
      drawCommentHandles(ctx, comment, scale, strokeColor);
    }
    ctx.restore();
  });

  ctx.restore();

  ctx.save();
  ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  comments.forEach((comment) => {
    drawCommentBadge(
      ctx,
      computeCommentBadgeRect(canvas, comment),
      comment.client_uid === state.activeCommentClientUid || comment === state.draftComment
    );
  });
  ctx.restore();
}

function clearActiveAnnotationSelection(canvas) {
  if (typeof canvas.clearActiveAnnotationSelection === "function") {
    canvas.clearActiveAnnotationSelection({ requestRedraw: false });
    return;
  }

  canvas.activeAnnotation = null;
  canvas.activeVertexIndex = null;
  if (typeof canvas.setActiveTrackId === "function") {
    canvas.setActiveTrackId(null);
  }
  if (typeof canvas.updateFastActionButtons === "function") {
    canvas.updateFastActionButtons();
  }
  if (typeof canvas.syncTrackVisibilityControls === "function") {
    canvas.syncTrackVisibilityControls();
  }
  if (typeof canvas.syncAnnotationStateControls === "function") {
    canvas.syncAnnotationStateControls();
  }
}

function clearActiveComment(canvas, options = {}) {
  const state = getCommentState(canvas);
  if (!state) return;
  state.activeCommentClientUid = null;
  state.activeComment = null;
  state.editingCommentClientUid = null;
  if (!options.keepDraft) {
    state.draftComment = null;
  }
  if (!options.skipPermalinkSync) {
    syncRegionCommentPermalink(canvas, null);
  }
}

function getTooltipComment(canvas) {
  const state = getCommentState(canvas);
  if (!state) return null;
  if (state.isDrawing) return null;
  return state.draftComment || state.activeComment || null;
}

function getRegionCommentPermalink(canvas, commentOrClientUid) {
  if (typeof window === "undefined") return null;

  const comment =
    typeof commentOrClientUid === "string"
      ? getStoredRegionCommentByClientUid(canvas, commentOrClientUid)
      : commentOrClientUid;
  if (!comment?.client_uid) return null;

  const url = new URL(window.location.href);
  url.searchParams.set(REGION_COMMENT_QUERY_PARAM, comment.client_uid);
  if (canvas.kind === "video" && Number.isInteger(comment.frame_index)) {
    url.searchParams.set(
      REGION_COMMENT_FRAME_QUERY_PARAM,
      String(comment.frame_index + 1)
    );
  } else {
    url.searchParams.delete(REGION_COMMENT_FRAME_QUERY_PARAM);
  }
  return url.toString();
}

function syncRegionCommentPermalink(canvas, clientUid) {
  if (typeof window === "undefined" || !window.history?.replaceState) return;

  const url = new URL(window.location.href);
  if (clientUid) {
    const comment = getStoredRegionCommentByClientUid(canvas, clientUid);
    if (comment?.client_uid) {
      url.searchParams.set(REGION_COMMENT_QUERY_PARAM, comment.client_uid);
      if (canvas.kind === "video" && Number.isInteger(comment.frame_index)) {
        url.searchParams.set(
          REGION_COMMENT_FRAME_QUERY_PARAM,
          String(comment.frame_index + 1)
        );
      } else {
        url.searchParams.delete(REGION_COMMENT_FRAME_QUERY_PARAM);
      }
    } else {
      url.searchParams.set(REGION_COMMENT_QUERY_PARAM, clientUid);
      url.searchParams.delete(REGION_COMMENT_FRAME_QUERY_PARAM);
    }
  } else {
    url.searchParams.delete(REGION_COMMENT_QUERY_PARAM);
    url.searchParams.delete(REGION_COMMENT_FRAME_QUERY_PARAM);
  }

  window.history.replaceState(
    window.history.state,
    "",
    `${url.pathname}${url.search}${url.hash}`
  );
}

async function copyTextToClipboard(text) {
  if (!text) return false;

  if (
    typeof navigator !== "undefined" &&
    navigator.clipboard &&
    typeof navigator.clipboard.writeText === "function"
  ) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_error) {
      // Fall back to execCommand below.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_error) {
    copied = false;
  } finally {
    textarea.remove();
  }

  return copied;
}

function getRegionCommentCopyLabel(canvas, clientUid) {
  const state = getCommentState(canvas);
  return state?.copiedPermalinkClientUid === clientUid ? "Copied" : "Copy link";
}

function showRegionCommentCopyFeedback(canvas, clientUid) {
  const state = getCommentState(canvas);
  if (!state) return;

  state.copiedPermalinkClientUid = clientUid;
  if (state.copyFeedbackTimer) {
    window.clearTimeout(state.copyFeedbackTimer);
  }
  state.copyFeedbackTimer = window.setTimeout(() => {
    const latestState = getCommentState(canvas);
    if (!latestState) return;
    latestState.copiedPermalinkClientUid = null;
    latestState.copyFeedbackTimer = null;
    renderRegionCommentPanel(canvas);
  }, 1800);
}

async function copyRegionCommentPermalink(canvas, clientUid) {
  const permalink = getRegionCommentPermalink(canvas, clientUid);
  if (!permalink) return;

  const copied = await copyTextToClipboard(permalink);
  if (!copied) {
    alert("Failed to copy comment link");
    return;
  }

  showRegionCommentCopyFeedback(canvas, clientUid);
  renderRegionCommentPanel(canvas);
}

function restoreLinkedRegionCommentFromLocation(canvas) {
  const state = getCommentState(canvas);
  if (!state || state.hasAppliedLinkedComment) return;
  state.hasAppliedLinkedComment = true;

  if (
    state.linkedCommentClientUid &&
    getStoredRegionCommentByClientUid(canvas, state.linkedCommentClientUid)
  ) {
    selectRegionComment(canvas, state.linkedCommentClientUid, {
      updatePermalink: false,
    });
    return;
  }

  if (
    canvas.kind === "video" &&
    Number.isInteger(state.linkedFrameIndex) &&
    state.linkedFrameIndex !== canvas.currentFrameIndex
  ) {
    canvas.seekToFrame(state.linkedFrameIndex);
  }
}

function positionRegionCommentTooltip(canvas, comment) {
  const state = getCommentState(canvas);
  const tooltipEl = state?.tooltipEl;
  if (!tooltipEl || !comment) return;

  const parentWidth = tooltipEl.parentElement?.clientWidth || canvas.viewportWidth || 0;
  const parentHeight = tooltipEl.parentElement?.clientHeight || canvas.viewportHeight || 0;
  const badgeRect = computeCommentBadgeRect(canvas, comment);
  const gap = 14;

  tooltipEl.classList.remove("is-left", "is-right");
  tooltipEl.hidden = false;

  const tooltipWidth = tooltipEl.offsetWidth || 320;
  const tooltipHeight = tooltipEl.offsetHeight || 220;

  let left = badgeRect.x + badgeRect.w + gap;
  let top = Math.max(8, badgeRect.y - 8);
  let orientation = "is-right";

  if (left + tooltipWidth > parentWidth - 8) {
    left = badgeRect.x - tooltipWidth - gap;
    orientation = "is-left";
  }
  if (left < 8) {
    left = Math.max(8, Math.min(parentWidth - tooltipWidth - 8, badgeRect.x + 8));
    orientation = "is-right";
  }
  if (top + tooltipHeight > parentHeight - 8) {
    top = Math.max(8, parentHeight - tooltipHeight - 8);
  }

  tooltipEl.style.left = `${Math.round(left)}px`;
  tooltipEl.style.top = `${Math.round(top)}px`;
  tooltipEl.classList.add(orientation);
}

function renderRegionCommentDetail(canvas) {
  const state = getCommentState(canvas);
  if (!state?.detailEl) return;

  if (!state.activeComment) {
    state.detailEl.innerHTML =
      '<div class="small text-secondary">Select a comment badge to view its details.</div>';
    return;
  }

  const comment = state.activeComment;
  const createdLabel = formatTimestamp(comment.created_at);
  const updatedLabel = formatTimestamp(comment.updated_at);
  const createdBy = getAuditUserLabel(comment.created_by_user, comment.created_by);
  const updatedBy = getAuditUserLabel(comment.updated_by_user, comment.updated_by);
  const permalink = getRegionCommentPermalink(canvas, comment);
  const copyLabel = getRegionCommentCopyLabel(canvas, comment.client_uid);
  const coords = [
    Number(comment.x1).toFixed(1),
    Number(comment.y1).toFixed(1),
    Number(comment.x2).toFixed(1),
    Number(comment.y2).toFixed(1),
  ].join(", ");

  state.detailEl.innerHTML = `
    <div class="region-comment-card">
      <div class="d-flex justify-content-between align-items-start gap-2 mb-2">
        <div>
          <div class="region-comment-frame">${escapeHtml(getCommentFrameLabel(canvas, comment))}</div>
          <div class="small text-secondary">BBox ${escapeHtml(coords)}</div>
        </div>
        ${!canvas.readOnly ? `
        <div class="d-flex gap-2">
          <button id="btn-region-comment-edit" type="button" class="btn btn-sm btn-outline-warning">Edit</button>
          <button id="btn-region-comment-delete" type="button" class="btn btn-sm btn-outline-danger">Delete</button>
        </div>
        ` : ""}
      </div>
      <div class="region-comment-body">${escapeHtml(comment.comment).replaceAll("\n", "<br>")}</div>
      ${
        permalink
          ? `<div class="region-comment-share-row mt-3">
              <a
                href="${escapeHtml(permalink)}"
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-sm btn-outline-info"
              >Permalink</a>
              <button type="button" class="btn btn-sm btn-outline-secondary" data-comment-detail-action="copy-link">${escapeHtml(copyLabel)}</button>
            </div>`
          : ""
      }
      <div class="region-comment-audit mt-3">
        <div><span class="annotation-audit-label">Created</span> <span class="annotation-audit-value">${escapeHtml(createdLabel ? `${createdLabel} | ${createdBy}` : "Pending save")}</span></div>
        <div><span class="annotation-audit-label">Updated</span> <span class="annotation-audit-value">${escapeHtml(updatedLabel ? `${updatedLabel} | ${updatedBy}` : createdLabel ? "Same as created" : "Pending save")}</span></div>
      </div>
    </div>
  `;

  if (!canvas.readOnly) {
    state.detailEl
      .querySelector("#btn-region-comment-edit")
      ?.addEventListener("click", () => openRegionCommentEditor(canvas, comment));
    state.detailEl
      .querySelector("#btn-region-comment-delete")
      ?.addEventListener("click", () => deleteRegionComment(canvas, comment.client_uid));
  }
  state.detailEl
    .querySelector('[data-comment-detail-action="copy-link"]')
    ?.addEventListener("click", () => copyRegionCommentPermalink(canvas, comment.client_uid));
}

function renderRegionCommentList(canvas) {
  const state = getCommentState(canvas);
  if (!state?.listEl) return;

  const comments = getStoredRegionComments(canvas).sort((left, right) => {
    const leftIsCurrentFrame =
      getFrameKey(canvas, left.frame_index) === getFrameKey(canvas, canvas.currentFrameIndex)
        ? 1
        : 0;
    const rightIsCurrentFrame =
      getFrameKey(canvas, right.frame_index) === getFrameKey(canvas, canvas.currentFrameIndex)
        ? 1
        : 0;
    if (leftIsCurrentFrame !== rightIsCurrentFrame) {
      return rightIsCurrentFrame - leftIsCurrentFrame;
    }

    const leftFrame = left.frame_index ?? -1;
    const rightFrame = right.frame_index ?? -1;
    if (leftFrame !== rightFrame) return leftFrame - rightFrame;

    return getCommentSortValue(right) - getCommentSortValue(left);
  });

  state.listEl.innerHTML = "";
  if (!comments.length) {
    state.listEl.innerHTML =
      '<div class="px-2 py-3 text-muted text-center small">No region comments yet.</div>';
    return;
  }

  comments.forEach((comment) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className =
      "region-comment-row" +
      (comment.client_uid === state.activeCommentClientUid ? " is-active" : "");
    row.innerHTML = `
      <span class="region-comment-row-icon"><i class="bi bi-chat-left-text"></i></span>
      <span class="region-comment-row-main">
        <span class="region-comment-row-title">${escapeHtml(getCommentFrameLabel(canvas, comment))}</span>
        <span class="region-comment-row-summary">${escapeHtml(getCommentSummary(comment))}</span>
      </span>
    `;
    row.addEventListener("click", () => selectRegionComment(canvas, comment.client_uid));
    state.listEl.appendChild(row);
  });
}

function renderRegionCommentTooltip(canvas) {
  const state = getCommentState(canvas);
  if (!state?.tooltipEl) return;

  const comment = getTooltipComment(canvas);
  if (!comment || !state.visible) {
    state.tooltipEl.hidden = true;
    state.tooltipEl.innerHTML = "";
    return;
  }

  const isDraft = !!state.draftComment;
  const isEditing = isDraft || state.editingCommentClientUid === comment.client_uid;
  const createdLabel = formatTimestamp(comment.created_at);
  const updatedLabel = formatTimestamp(comment.updated_at);
  const createdBy = getAuditUserLabel(comment.created_by_user, comment.created_by);
  const updatedBy = getAuditUserLabel(comment.updated_by_user, comment.updated_by);
  const permalink = !isDraft ? getRegionCommentPermalink(canvas, comment) : null;
  const copyLabel = getRegionCommentCopyLabel(canvas, comment.client_uid);
  const auditText = [
    createdLabel ? `Created ${createdLabel} | ${createdBy}` : "Pending save",
    updatedLabel ? `Updated ${updatedLabel} | ${updatedBy}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  state.tooltipEl.innerHTML = `
    <div class="region-comment-tooltip-header">
      <div class="region-comment-tooltip-title">
        <div class="region-comment-frame">${escapeHtml(getCommentFrameLabel(canvas, comment))}</div>
        <div class="region-comment-tooltip-meta">${escapeHtml(getCommentSummary(comment))}</div>
      </div>
      <div class="region-comment-tooltip-actions">
        ${!canvas.readOnly && !isEditing ? '<button type="button" class="btn btn-sm btn-outline-warning" data-comment-action="edit">Edit</button>' : ""}
        ${!canvas.readOnly && !isDraft ? '<button type="button" class="btn btn-sm btn-outline-danger" data-comment-action="delete">Delete</button>' : ""}
        ${!isDraft ? `<button type="button" class="btn btn-sm btn-outline-secondary" data-comment-action="copy-link">${escapeHtml(copyLabel)}</button>` : ""}
        <button type="button" class="btn btn-sm btn-outline-secondary" data-comment-action="close">Close</button>
      </div>
    </div>
    ${
      isEditing
        ? `<textarea class="form-control form-control-sm region-comment-tooltip-textarea" data-comment-input placeholder="Explain what should be checked in this area">${escapeHtml(comment.comment)}</textarea>`
        : `<div class="region-comment-tooltip-body">${escapeHtml(comment.comment).replaceAll("\n", "<br>")}</div>`
    }
    <div class="region-comment-tooltip-footer">
      <div class="region-comment-tooltip-audit">${escapeHtml(auditText).replaceAll("\n", "<br>")}</div>
      ${
        isEditing
          ? `<div class="d-flex gap-2">
              <button type="button" class="btn btn-sm btn-outline-secondary" data-comment-action="cancel">Cancel</button>
              <button type="button" class="btn btn-sm btn-warning" data-comment-action="save">Save</button>
            </div>`
          : ""
      }
    </div>
    ${
      permalink
        ? `<div class="region-comment-tooltip-share-row">
            <a
              href="${escapeHtml(permalink)}"
              target="_blank"
              rel="noopener noreferrer"
              class="region-comment-tooltip-link"
            >Open shared link</a>
          </div>`
        : ""
    }
  `;

  state.tooltipEl.querySelectorAll("[data-comment-action]").forEach((button) => {
    const action = button.getAttribute("data-comment-action");
    if (action === "edit") {
      button.addEventListener("click", () => openRegionCommentEditor(canvas, comment));
    } else if (action === "delete") {
      button.addEventListener("click", () => deleteRegionComment(canvas, comment.client_uid));
    } else if (action === "copy-link") {
      button.addEventListener("click", () => copyRegionCommentPermalink(canvas, comment.client_uid));
    } else if (action === "close") {
      button.addEventListener("click", () => {
        const stateRef = getCommentState(canvas);
        if (stateRef?.draftComment) {
          stateRef.drawMode = false;
        }
        clearActiveComment(canvas);
        renderRegionCommentPanel(canvas);
        canvas.requestRedraw(false);
      });
    } else if (action === "cancel") {
      button.addEventListener("click", () => {
        const stateRef = getCommentState(canvas);
        if (stateRef?.draftComment) {
          clearActiveComment(canvas);
          stateRef.drawMode = false;
        } else if (stateRef) {
          stateRef.editingCommentClientUid = null;
        }
        renderRegionCommentPanel(canvas);
        canvas.requestRedraw(false);
      });
    } else if (action === "save") {
      button.addEventListener("click", () => saveRegionCommentFromTooltip(canvas));
    }
  });

  const input = state.tooltipEl.querySelector("[data-comment-input]");
  if (input) {
    input.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        saveRegionCommentFromTooltip(canvas);
      } else if (event.key === "Escape") {
        event.preventDefault();
        const stateRef = getCommentState(canvas);
        if (stateRef?.draftComment) {
          clearActiveComment(canvas);
          stateRef.drawMode = false;
        } else if (stateRef) {
          stateRef.editingCommentClientUid = null;
        }
        renderRegionCommentPanel(canvas);
        canvas.requestRedraw(false);
      }
    });
    window.setTimeout(() => input.focus(), 0);
  }

  state.tooltipEl.hidden = false;
  positionRegionCommentTooltip(canvas, comment);
}

function renderRegionCommentPanel(canvas) {
  const state = getCommentState(canvas);
  if (!state) return;

  if (state.countEl) {
    state.countEl.textContent = String(getStoredRegionComments(canvas).length);
  }
  if (state.toggleBtn) {
    state.toggleBtn.textContent = state.visible ? "Hide overlay" : "Show overlay";
    state.toggleBtn.classList.toggle("btn-outline-secondary", state.visible);
    state.toggleBtn.classList.toggle("btn-outline-warning", !state.visible);
  }
  if (state.drawBtn) {
    state.drawBtn.classList.toggle("btn-outline-warning", !state.drawMode);
    state.drawBtn.classList.toggle("btn-warning", state.drawMode);
    state.drawBtn.title = state.drawMode
      ? "Cancel region comment (Esc)"
      : "Add region comment (C)";
    state.drawBtn.setAttribute(
      "aria-label",
      state.drawMode ? "Cancel region comment (Esc)" : "Add region comment (C)"
    );
  }

  renderRegionCommentDetail(canvas);
  renderRegionCommentList(canvas);
  renderRegionCommentTooltip(canvas);
}

function toggleRegionCommentVisibility(canvas) {
  const state = getCommentState(canvas);
  if (!state) return;
  state.visible = !state.visible;
  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
}

function toggleRegionCommentDrawMode(canvas, forceValue = null) {
  const state = getCommentState(canvas);
  if (!state || canvas.readOnly) return;

  const nextValue =
    typeof forceValue === "boolean" ? forceValue : !state.drawMode;
  state.drawMode = nextValue;
  state.visible = true;
  state.isDrawing = false;
  state.draggedCommentClientUid = null;
  state.dragMode = null;
  state.dragHandle = null;
  if (!nextValue) {
    state.draftComment = null;
  }
  if (nextValue) {
    clearActiveComment(canvas);
  }
  clearActiveAnnotationSelection(canvas);
  if (typeof canvas.hideObjectContextMenu === "function") {
    canvas.hideObjectContextMenu();
  }
  if (typeof canvas.blurActiveFormControl === "function") {
    canvas.blurActiveFormControl();
  }
  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
}

function openRegionCommentEditor(canvas, comment = null) {
  const state = getCommentState(canvas);
  if (!state || canvas.readOnly) return;

  state.visible = true;
  if (comment) {
    state.editingCommentClientUid = comment.client_uid;
    setActiveCommentClientUid(canvas, comment.client_uid);
    state.draftComment = null;
  } else if (state.draftComment) {
    state.editingCommentClientUid = null;
  }

  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
}

function closeRegionCommentEditor(canvas) {
  const state = getCommentState(canvas);
  if (!state) return;
  state.editingCommentClientUid = null;
  state.draftComment = null;
  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
}

function applyOptimisticAudit(canvas, normalized, existing) {
  const state = getCommentState(canvas);
  const nowIso = new Date().toISOString();
  const user = state?.currentUser || null;
  if (existing) {
    normalized.created_at = existing.created_at;
    normalized.created_by = existing.created_by;
    normalized.created_by_user = cloneAuditUser(existing.created_by_user);
    normalized.updated_at = nowIso;
    normalized.updated_by = Number.isInteger(user?.id) ? user.id : existing.updated_by;
    normalized.updated_by_user = cloneAuditUser(user) || cloneAuditUser(existing.updated_by_user);
    return normalized;
  }

  normalized.created_at = nowIso;
  normalized.updated_at = nowIso;
  normalized.created_by = Number.isInteger(user?.id) ? user.id : null;
  normalized.updated_by = Number.isInteger(user?.id) ? user.id : null;
  normalized.created_by_user = cloneAuditUser(user);
  normalized.updated_by_user = cloneAuditUser(user);
  return normalized;
}

function upsertRegionComment(canvas, comment) {
  const state = getCommentState(canvas);
  if (!state) return;

  const existing = getStoredRegionCommentByClientUid(canvas, comment.client_uid);
  const normalized = applyOptimisticAudit(
    canvas,
    normalizeRegionCommentForStore(canvas, comment),
    existing
  );
  const key = getFrameKey(canvas, normalized.frame_index);
  const bucket = (state.frameComments.get(key) || []).filter(
    (candidate) => candidate.client_uid !== normalized.client_uid
  );
  bucket.unshift(normalized);
  state.frameComments.set(key, bucket);
  syncVisibleComments(canvas);
  setActiveCommentClientUid(canvas, normalized.client_uid);
}

function removeRegionComment(canvas, clientUid) {
  const state = getCommentState(canvas);
  if (!state || !clientUid) return;

  const nextMap = new Map();
  state.frameComments.forEach((bucket, key) => {
    const filtered = (bucket || []).filter((comment) => comment.client_uid !== clientUid);
    if (filtered.length) {
      nextMap.set(key, filtered);
    }
  });
  state.frameComments = nextMap;
  if (state.activeCommentClientUid === clientUid) {
    clearActiveComment(canvas, { keepDraft: false });
  }
  syncVisibleComments(canvas);
}

async function saveRegionComments(canvas) {
  const state = getCommentState(canvas);
  if (!state || canvas.readOnly) return;

  if (state.isSaving) {
    state.pendingSaveRequested = true;
    return;
  }

  const patch = buildCommentPatch(canvas);
  if (!patch.upserts.length && !patch.deletes.length) {
    state.isDirty = false;
    replaceSavedCommentState(canvas);
    renderRegionCommentPanel(canvas);
    return;
  }

  state.isSaving = true;
  try {
    const response = await fetch(`${canvas.apiBase}/items/${canvas.itemId}/region-comments`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upserts: patch.upserts.map((comment) => ({
          client_uid: comment.client_uid,
          frame_index: comment.frame_index,
          x1: comment.x1,
          y1: comment.y1,
          x2: comment.x2,
          y2: comment.y2,
          comment: comment.comment,
        })),
        deletes: patch.deletes,
      }),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_error) {
      data = {};
    }

    if (!response.ok) {
      state.isDirty = true;
      console.error("Failed to save region comments", data);
      alert("Failed to save region comments");
      return;
    }

    setStoredRegionComments(canvas, Array.isArray(data.comments) ? data.comments : []);
    replaceSavedCommentState(canvas);
    syncVisibleComments(canvas);
    setActiveCommentClientUid(canvas, state.activeCommentClientUid);
    syncRegionCommentPermalink(canvas, state.activeCommentClientUid);
    state.isDirty = false;
    renderRegionCommentPanel(canvas);
    canvas.requestRedraw(false);
  } catch (error) {
    state.isDirty = true;
    console.error("Error while saving region comments", error);
    alert("Error while saving region comments");
  } finally {
    state.isSaving = false;
    if (state.pendingSaveRequested) {
      state.pendingSaveRequested = false;
      saveRegionComments(canvas);
    }
  }
}

function saveRegionCommentFromTooltip(canvas) {
  const state = getCommentState(canvas);
  const input = state?.tooltipEl?.querySelector("[data-comment-input]");
  if (!state || !input) return;

  const commentText = normalizeCommentText(input.value);
  if (!commentText) {
    input.focus();
    return;
  }

  if (state.draftComment) {
    upsertRegionComment(canvas, {
      ...state.draftComment,
      comment: commentText,
    });
    state.draftComment = null;
    state.drawMode = false;
    state.isDirty = true;
    renderRegionCommentPanel(canvas);
    canvas.requestRedraw(false);
    saveRegionComments(canvas);
    return;
  }

  const existing = getStoredRegionCommentByClientUid(canvas, state.editingCommentClientUid);
  if (!existing) return;
  if (commentText === normalizeCommentText(existing.comment)) {
    state.editingCommentClientUid = null;
    renderRegionCommentPanel(canvas);
    canvas.requestRedraw(false);
    return;
  }

  upsertRegionComment(canvas, {
    ...existing,
    comment: commentText,
  });
  state.editingCommentClientUid = null;
  state.isDirty = true;
  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
  saveRegionComments(canvas);
}

function deleteRegionComment(canvas, clientUid) {
  const state = getCommentState(canvas);
  if (!state || canvas.readOnly || !clientUid) return;

  const target = getStoredRegionCommentByClientUid(canvas, clientUid);
  if (!target) return;

  const ok = window.confirm("Delete this region comment?");
  if (!ok) return;

  removeRegionComment(canvas, clientUid);
  state.draftComment = null;
  state.drawMode = false;
  state.editingCommentClientUid = null;
  state.isDirty = true;
  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
  saveRegionComments(canvas);
}

function selectRegionComment(canvas, clientUid, options = {}) {
  const state = getCommentState(canvas);
  if (!state || !clientUid) return;
  const { updatePermalink = true } = options;

  const storedComment = getStoredRegionCommentByClientUid(canvas, clientUid);
  if (!storedComment) return;

  if (
    canvas.kind === "video" &&
    Number.isInteger(storedComment.frame_index) &&
    storedComment.frame_index !== canvas.currentFrameIndex
  ) {
    canvas.seekToFrame(storedComment.frame_index);
  }

  state.visible = true;
  state.draftComment = null;
  state.editingCommentClientUid = null;
  clearActiveAnnotationSelection(canvas);
  if (typeof canvas.hideObjectContextMenu === "function") {
    canvas.hideObjectContextMenu();
  }
  if (typeof canvas.blurActiveFormControl === "function") {
    canvas.blurActiveFormControl();
  }
  syncVisibleComments(canvas);
  setActiveCommentClientUid(canvas, clientUid);
  if (updatePermalink) {
    syncRegionCommentPermalink(canvas, clientUid);
  }
  renderRegionCommentPanel(canvas);
  canvas.requestRedraw(false);
}

function installRegionCommentUi(canvas) {
  const state = getCommentState(canvas);
  if (!state) return;

  state.toggleBtn?.addEventListener("click", () => toggleRegionCommentVisibility(canvas));
  state.drawBtn?.addEventListener("click", () => toggleRegionCommentDrawMode(canvas));
  state.tooltipEl?.addEventListener("mousedown", (event) => {
    event.stopPropagation();
  });
  state.tooltipEl?.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  document.addEventListener("keydown", (event) => {
    if (canvas.readOnly) return;
    const activeTag = document.activeElement?.tagName || "";
    if (activeTag === "INPUT" || activeTag === "TEXTAREA" || activeTag === "SELECT") {
      return;
    }

    if (event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }

    const key = (event.key || "").toLowerCase();
    if (!event.shiftKey && key === "c") {
      event.preventDefault();
      toggleRegionCommentDrawMode(canvas, true);
      return;
    }

    if (event.key === "Escape") {
      if (state.isDrawing || state.drawMode) {
        toggleRegionCommentDrawMode(canvas, false);
      } else if (
        state.editingCommentClientUid ||
        state.draftComment ||
        state.activeCommentClientUid
      ) {
        clearActiveComment(canvas);
        renderRegionCommentPanel(canvas);
        canvas.requestRedraw(false);
      }
    }
  });
}

function initializeRegionComments(canvas, initialComments, currentUser) {
  const linkedTarget = getRequestedRegionCommentTarget();
  canvas.regionCommentState = {
    currentUser: currentUser || null,
    visible: true,
    drawMode: false,
    isDrawing: false,
    isDragging: false,
    draggedCommentClientUid: null,
    dragMode: null,
    dragHandle: null,
    dragOffset: { x: 0, y: 0 },
    dragOriginal: null,
    resizeStart: null,
    draftComment: null,
    editingCommentClientUid: null,
    isSaving: false,
    pendingSaveRequested: false,
    isDirty: false,
    frameComments: new Map(),
    visibleComments: [],
    lastSavedState: new Map(),
    activeCommentClientUid: null,
    activeComment: null,
    linkedCommentClientUid: linkedTarget.clientUid,
    linkedFrameIndex: linkedTarget.frameIndex,
    hasAppliedLinkedComment: false,
    copiedPermalinkClientUid: null,
    copyFeedbackTimer: null,
    detailEl: document.getElementById("region-comment-detail"),
    listEl: document.getElementById("region-comment-list"),
    countEl: document.getElementById("region-comment-count"),
    toggleBtn: document.getElementById("btn-toggle-region-comments"),
    drawBtn: document.getElementById("btn-region-comment-draw"),
    tooltipEl: document.getElementById("region-comment-tooltip"),
  };

  setStoredRegionComments(canvas, initialComments);
  replaceSavedCommentState(canvas);
  syncVisibleComments(canvas);
  installRegionCommentUi(canvas);
  renderRegionCommentPanel(canvas);
}

export function enhanceAnnotationCanvasWithComments(canvas, options = {}) {
  const initialComments = Array.isArray(options.initialComments)
    ? options.initialComments
    : Array.isArray(canvas.regionComments)
      ? canvas.regionComments
      : [];

  const originalInit = canvas.init.bind(canvas);
  const originalLoadFrame = canvas.loadFrame.bind(canvas);
  const originalSetCurrentFrame = canvas.setCurrentFrame.bind(canvas);
  const originalRedraw = canvas.redraw.bind(canvas);
  const originalOnMouseDown = canvas.onMouseDown.bind(canvas);
  const originalOnMouseMove = canvas.onMouseMove.bind(canvas);
  const originalOnMouseUp = canvas.onMouseUp.bind(canvas);
  const originalUpdateHoverCursor = canvas.updateHoverCursor.bind(canvas);

  canvas.init = function initWithComments(initialAnnotations) {
    initializeRegionComments(this, initialComments, options.currentUser || null);
    originalInit(initialAnnotations);
    syncVisibleComments(this);
    renderRegionCommentPanel(this);
    restoreLinkedRegionCommentFromLocation(this);
  };

  canvas.loadFrame = function loadFrameWithComments(frameIndex, copyFromPrev) {
    originalLoadFrame(frameIndex, copyFromPrev);
    syncVisibleComments(this);
    renderRegionCommentPanel(this);
  };

  canvas.setCurrentFrame = function setCurrentFrameWithComments(frameIndex, optionsForFrame = {}) {
    originalSetCurrentFrame(frameIndex, optionsForFrame);
    syncVisibleComments(this);
    if (!this.renderLoopActive || optionsForFrame.source !== "playback") {
      renderRegionCommentPanel(this);
    }
  };

  canvas.updateHoverCursor = function updateHoverCursorWithComments(clientX, clientY) {
    const state = getCommentState(this);
    if (state?.drawMode && !state.isDrawing && !this.isPanning) {
      this.canvas.style.cursor = "crosshair";
      return;
    }

    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const xCanvas = clientX - rect.left;
    const yCanvas = clientY - rect.top;
    const badgeHit = findCommentBadgeHit(this, xCanvas, yCanvas);
    if (badgeHit) {
      this.canvas.style.cursor = "pointer";
      return;
    }

    const commentHit = findCommentRegionHit(this, xCanvas, yCanvas);
    if (commentHit) {
      this.canvas.style.cursor = getCommentCursor(this, commentHit);
      return;
    }

    originalUpdateHoverCursor(clientX, clientY);
  };

  canvas.onMouseDown = function onMouseDownWithComments(evt) {
    const state = getCommentState(this);
    if (!state) {
      originalOnMouseDown(evt);
      return;
    }

    const rect = this.canvas.getBoundingClientRect();
    const xCanvas = evt.clientX - rect.left;
    const yCanvas = evt.clientY - rect.top;
    const badgeHit = !state.drawMode
      ? findCommentBadgeHit(this, xCanvas, yCanvas)
      : null;
    const commentHit = !state.drawMode
      ? findCommentRegionHit(this, xCanvas, yCanvas)
      : null;

    if (badgeHit && evt.button === 0 && !evt.ctrlKey) {
      evt.preventDefault();
      evt.stopPropagation();
      selectRegionComment(this, badgeHit.comment.client_uid);
      return;
    }

    if (commentHit && evt.button === 0 && !evt.ctrlKey) {
      evt.preventDefault();
      evt.stopPropagation();
      clearActiveAnnotationSelection(this);
      selectRegionComment(this, commentHit.comment.client_uid);
      if (!this.readOnly) {
        const stored = getStoredRegionCommentByClientUid(
          this,
          commentHit.comment.client_uid
        );
        if (stored) {
          state.isDragging = true;
          state.draggedCommentClientUid = stored.client_uid;
          state.dragMode = commentHit.handle ? "resize" : "move";
          state.dragHandle = commentHit.handle;
          state.dragOriginal = cloneRegionComment(stored);
          state.resizeStart = commentHit.handle
            ? {
                x1: stored.x1,
                y1: stored.y1,
                x2: stored.x2,
                y2: stored.y2,
              }
            : null;
          if (state.dragMode === "move") {
            const p1 = this.fromImageToCanvasCoords(stored.x1, stored.y1);
            state.dragOffset = {
              x: xCanvas - p1.x,
              y: yCanvas - p1.y,
            };
          } else {
            state.dragOffset = { x: 0, y: 0 };
          }
          this.canvas.style.cursor = getCommentCursor(this, commentHit);
        }
      }
      return;
    }

    if (state.drawMode && !this.readOnly) {
      if (evt.button === 0 && !evt.ctrlKey) {
        evt.preventDefault();
        evt.stopPropagation();
        clearActiveAnnotationSelection(this);
        clearActiveComment(this);
        const startPoint = this.toImageCoordinates(evt);
        state.isDrawing = true;
        state.visible = true;
        state.draftComment = normalizeRegionCommentForStore(this, {
          client_uid: generateClientUid(),
          frame_index: this.kind === "video" ? this.currentFrameIndex : null,
          x1: startPoint.x,
          y1: startPoint.y,
          x2: startPoint.x,
          y2: startPoint.y,
          comment: "",
        });
        this.hideObjectContextMenu();
        this.blurActiveFormControl();
        this.requestRedraw(false);
        return;
      }
    }

    if (evt.button === 0 && !evt.ctrlKey) {
      clearActiveComment(this);
      renderRegionCommentPanel(this);
    }

    originalOnMouseDown(evt);
  };

  canvas.onMouseMove = function onMouseMoveWithComments(evt) {
    const state = getCommentState(this);
    const rect = this.canvas.getBoundingClientRect();
    const xCanvas = evt.clientX - rect.left;
    const yCanvas = evt.clientY - rect.top;
    if (state?.isDrawing && state.draftComment) {
      const point = this.toImageCoordinates(evt);
      state.draftComment.x2 = point.x;
      state.draftComment.y2 = point.y;
      if (typeof this.normalizeAnnotationCoords === "function") {
        this.normalizeAnnotationCoords(state.draftComment);
      }
      this.canvas.style.cursor = "crosshair";
      this.requestRedraw(false);
      return;
    }

    if (state?.isDragging && state.draggedCommentClientUid) {
      const stored = getStoredRegionCommentByClientUid(
        this,
        state.draggedCommentClientUid
      );
      if (!stored) {
        state.isDragging = false;
        return;
      }

      const point = this.toImageCoordinates(evt);
      const scale = (this.baseScale || 1) * (this.zoom || 1);
      if (state.dragMode === "resize" && state.resizeStart) {
        switch (state.dragHandle) {
          case "nw":
            stored.x1 = point.x;
            stored.y1 = point.y;
            stored.x2 = state.resizeStart.x2;
            stored.y2 = state.resizeStart.y2;
            break;
          case "ne":
            stored.x1 = state.resizeStart.x1;
            stored.y1 = point.y;
            stored.x2 = point.x;
            stored.y2 = state.resizeStart.y2;
            break;
          case "se":
            stored.x1 = state.resizeStart.x1;
            stored.y1 = state.resizeStart.y1;
            stored.x2 = point.x;
            stored.y2 = point.y;
            break;
          case "sw":
            stored.x1 = point.x;
            stored.y1 = state.resizeStart.y1;
            stored.x2 = state.resizeStart.x2;
            stored.y2 = point.y;
            break;
          default:
            break;
        }
      } else {
        const p1 = this.fromImageToCanvasCoords(stored.x1, stored.y1);
        const dxCanvas = xCanvas - state.dragOffset.x - p1.x;
        const dyCanvas = yCanvas - state.dragOffset.y - p1.y;
        const dx = dxCanvas / (scale || 1);
        const dy = dyCanvas / (scale || 1);
        stored.x1 += dx;
        stored.y1 += dy;
        stored.x2 += dx;
        stored.y2 += dy;
      }

      if (typeof this.clampAnnotationToImage === "function") {
        this.clampAnnotationToImage(stored);
      }
      syncVisibleComments(this);
      setActiveCommentClientUid(this, stored.client_uid);
      this.canvas.style.cursor =
        state.dragMode === "resize"
          ? getCommentCursor(this, { handle: state.dragHandle })
          : "move";
      this.requestRedraw(false);
      return;
    }

    if (state?.drawMode && !this.isPanning) {
      this.canvas.style.cursor = "crosshair";
      return;
    }

    originalOnMouseMove(evt);
  };

  canvas.onMouseUp = function onMouseUpWithComments(evt) {
    const state = getCommentState(this);
    if (state?.isDrawing && state.draftComment) {
      evt?.preventDefault?.();
      state.isDrawing = false;
      if (typeof this.normalizeAnnotationCoords === "function") {
        this.normalizeAnnotationCoords(state.draftComment);
      }
      if (
        typeof this.isDegenerateAnnotation === "function" &&
        this.isDegenerateAnnotation(state.draftComment)
      ) {
        state.draftComment = null;
        this.requestRedraw(false);
        return;
      }

      if (evt) {
        this.updateHoverCursor(evt.clientX, evt.clientY);
      }
      this.requestRedraw(false);
      openRegionCommentEditor(this, null);
      return;
    }

    if (state?.isDragging && state.draggedCommentClientUid) {
      evt?.preventDefault?.();
      const stored = getStoredRegionCommentByClientUid(
        this,
        state.draggedCommentClientUid
      );
      state.isDragging = false;
      state.dragMode = null;
      state.dragHandle = null;
      state.draggedCommentClientUid = null;
      state.resizeStart = null;
      if (evt) {
        this.updateHoverCursor(evt.clientX, evt.clientY);
      } else {
        this.canvas.style.cursor = "crosshair";
      }

      if (stored) {
        if (typeof this.normalizeAnnotationCoords === "function") {
          this.normalizeAnnotationCoords(stored);
        }
        if (
          typeof this.isDegenerateAnnotation === "function" &&
          this.isDegenerateAnnotation(stored) &&
          state.dragOriginal
        ) {
          Object.assign(stored, state.dragOriginal);
        }
        const changed = !state.dragOriginal || !commentsEqual(stored, state.dragOriginal);
        if (changed) {
          upsertRegionComment(this, stored);
          state.isDirty = true;
          saveRegionComments(this);
        } else {
          syncVisibleComments(this);
          setActiveCommentClientUid(this, stored.client_uid);
        }
        renderRegionCommentPanel(this);
        this.requestRedraw(false);
      }
      state.dragOriginal = null;
      return;
    }

    originalOnMouseUp(evt);
  };

  canvas.redraw = function redrawWithComments(withList = true) {
    originalRedraw(withList);
    drawRegionComments(this);
    if (withList || !this.renderLoopActive) {
      renderRegionCommentPanel(this);
    }
  };
}
