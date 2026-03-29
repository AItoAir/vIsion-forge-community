// Image/Video annotation canvas with frame-aware bbox and polygon support.

function generateClientUid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `lf${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

function clonePolygonPoints(points) {
  if (!Array.isArray(points)) return null;

  const normalizedPoints = points
    .filter((point) => Array.isArray(point) && point.length === 2)
    .map((point) => [Number(point[0]), Number(point[1])])
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));

  return normalizedPoints.length ? normalizedPoints : null;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const ITEM_FRAME_QUERY_PARAM = "frame";
const REGION_COMMENT_QUERY_PARAM = "region_comment";

function parseRequestedFrameIndexFromLocation() {
  if (typeof window === "undefined") return null;

  let url;
  try {
    url = new URL(window.location.href);
  } catch (_error) {
    return null;
  }

  const raw = url.searchParams.get(ITEM_FRAME_QUERY_PARAM);
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.trunc(parsed) - 1);
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

function cloneAnnotationAuditMetadata(annotation, options = {}) {
  if (options.preserve === false) {
    return {
      created_at: null,
      updated_at: null,
      created_by: null,
      updated_by: null,
      created_by_user: null,
      updated_by_user: null,
    };
  }

  return {
    created_at:
      typeof annotation?.created_at === "string" && annotation.created_at
        ? annotation.created_at
        : null,
    updated_at:
      typeof annotation?.updated_at === "string" && annotation.updated_at
        ? annotation.updated_at
        : null,
    created_by: Number.isInteger(annotation?.created_by)
      ? annotation.created_by
      : null,
    updated_by: Number.isInteger(annotation?.updated_by)
      ? annotation.updated_by
      : null,
    created_by_user: cloneAuditUser(annotation?.created_by_user),
    updated_by_user: cloneAuditUser(annotation?.updated_by_user),
  };
}

const auditTimestampFormatter =
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

function formatAuditTimestamp(value) {
  if (typeof value !== "string" || !value) {
    return null;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  if (auditTimestampFormatter) {
    return auditTimestampFormatter.format(parsed);
  }
  return parsed.toISOString().replace("T", " ").slice(0, 19);
}

function getAuditTimestampSortValue(value, fallbackValue) {
  if (typeof value !== "string" || !value) {
    return fallbackValue;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return fallbackValue;
  }
  return parsed.getTime();
}

function getAuditUserLabel(user, userId) {
  if (user?.email) {
    return user.email;
  }
  if (Number.isInteger(user?.id)) {
    return `User #${user.id}`;
  }
  if (Number.isInteger(userId)) {
    return `User #${userId}`;
  }
  return "Unknown user";
}

function buildAnnotationAuditMetaHtml(auditMeta) {
  if (!auditMeta) return "";

  const createdTimestamp = formatAuditTimestamp(auditMeta.created_at);
  const updatedTimestamp = formatAuditTimestamp(auditMeta.updated_at);
  const createdText = createdTimestamp
    ? `${createdTimestamp} | ${getAuditUserLabel(
        auditMeta.created_by_user,
        auditMeta.created_by
      )}`
    : "Pending save";
  const updatedText = updatedTimestamp
    ? `${updatedTimestamp} | ${getAuditUserLabel(
        auditMeta.updated_by_user,
        auditMeta.updated_by
      )}`
    : createdTimestamp
      ? "Same as created"
      : "Pending save";

  return `
    <div class="annotation-audit-meta">
      <div><span class="annotation-audit-label">Created</span> <span class="annotation-audit-value">${escapeHtml(createdText)}</span></div>
      <div><span class="annotation-audit-label">Updated</span> <span class="annotation-audit-value">${escapeHtml(updatedText)}</span></div>
    </div>
  `;
}

function polygonPointsEqual(left, right) {
  if (!left && !right) return true;
  if (!left || !right || left.length !== right.length) return false;

  for (let index = 0; index < left.length; index += 1) {
    if (left[index][0] !== right[index][0] || left[index][1] !== right[index][1]) {
      return false;
    }
  }
  return true;
}

function syncPolygonBounds(annotation) {
  const polygonPoints = clonePolygonPoints(annotation?.polygon_points);
  if (!polygonPoints || !polygonPoints.length) {
    return annotation;
  }

  const xs = polygonPoints.map((point) => point[0]);
  const ys = polygonPoints.map((point) => point[1]);

  annotation.polygon_points = polygonPoints;
  annotation.x1 = Math.min(...xs);
  annotation.y1 = Math.min(...ys);
  annotation.x2 = Math.max(...xs);
  annotation.y2 = Math.max(...ys);
  return annotation;
}

function polygonSignedArea(points) {
  if (!Array.isArray(points) || points.length < 3) return 0;

  let area = 0;
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index];
    const next = points[(index + 1) % points.length];
    area += current[0] * next[1] - next[0] * current[1];
  }
  return area / 2;
}

function pointInPolygonCanvas(x, y, points) {
  if (!Array.isArray(points) || points.length < 3) return false;

  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
    const xi = points[i].x;
    const yi = points[i].y;
    const xj = points[j].x;
    const yj = points[j].y;

    const intersects =
      (yi > y) !== (yj > y) &&
      x < ((xj - xi) * (y - yi)) / ((yj - yi) || Number.EPSILON) + xi;

    if (intersects) {
      inside = !inside;
    }
  }

  return inside;
}

function distancePointToSegmentSq(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;

  if (dx === 0 && dy === 0) {
    const ddx = px - ax;
    const ddy = py - ay;
    return ddx * ddx + ddy * ddy;
  }

  const t = Math.max(
    0,
    Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))
  );
  const closestX = ax + dx * t;
  const closestY = ay + dy * t;
  const ddx = px - closestX;
  const ddy = py - closestY;
  return ddx * ddx + ddy * ddy;
}

function getAnnotationHitArea(annotation) {
  const polygonPoints = clonePolygonPoints(annotation?.polygon_points);
  if (polygonPoints && polygonPoints.length >= 3) {
    return Math.abs(polygonSignedArea(polygonPoints));
  }

  const width = Math.abs((annotation?.x2 ?? 0) - (annotation?.x1 ?? 0));
  const height = Math.abs((annotation?.y2 ?? 0) - (annotation?.y1 ?? 0));
  return width * height;
}

function getHitPriority(handle) {
  if (typeof handle !== "string" || !handle.length) {
    return 1;
  }
  if (handle.startsWith("edge:")) {
    return 2;
  }
  return 3;
}

function compareAnnotationHits(left, right, activeAnnotation) {
  const leftPriority = getHitPriority(left?.handle);
  const rightPriority = getHitPriority(right?.handle);
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

  const leftArea = getAnnotationHitArea(left?.ann);
  const rightArea = getAnnotationHitArea(right?.ann);
  if (Math.abs(leftArea - rightArea) > 1e-6) {
    return leftArea - rightArea;
  }

  const leftIsActive = left?.ann === activeAnnotation ? 1 : 0;
  const rightIsActive = right?.ann === activeAnnotation ? 1 : 0;
  if (leftIsActive !== rightIsActive) {
    return rightIsActive - leftIsActive;
  }

  return (right?.index ?? -1) - (left?.index ?? -1);
}

function lerpPolygonPoints(startPoints, endPoints, ratio) {
  if (
    !Array.isArray(startPoints) ||
    !Array.isArray(endPoints) ||
    startPoints.length !== endPoints.length
  ) {
    return null;
  }

  return startPoints.map((startPoint, index) => {
    const endPoint = endPoints[index];
    return [
      lerp(startPoint[0], endPoint[0], ratio),
      lerp(startPoint[1], endPoint[1], ratio),
    ];
  });
}

function cloneAnnotation(ann, frameIndex) {
  const cloned = {
    client_uid: ann.client_uid || generateClientUid(),
    id: ann.id,
    label_class_id: ann.label_class_id,
    frame_index: frameIndex,
    x1: ann.x1,
    y1: ann.y1,
    x2: ann.x2,
    y2: ann.y2,
    status: ann.status || "pending",
    track_id: ann.track_id != null ? ann.track_id : null,
    propagation_frames:
      ann.propagation_frames != null ? ann.propagation_frames : 0,
    ...cloneAnnotationFlags(ann),
    ...cloneAnnotationAuditMetadata(ann),
    review_change_state: getReviewChangeState(ann),
    polygon_points: clonePolygonPoints(ann.polygon_points),
  };
  return syncPolygonBounds(cloned);
}

function lerp(startValue, endValue, ratio) {
  return startValue + (endValue - startValue) * ratio;
}

function cloneAnnotationFlags(annotation) {
  return {
    is_occluded: !!annotation?.is_occluded,
    is_truncated: !!annotation?.is_truncated,
    is_outside: !!annotation?.is_outside,
    is_lost: !!annotation?.is_lost,
  };
}

function getReviewChangeState(annotation) {
  const value = annotation?.review_change_state;
  return value === "new" || value === "changed" || value === "unchanged"
    ? value
    : null;
}

function getReviewChangeBadgeHtml(changeState) {
  if (changeState === "new") {
    return '<span class="annotation-change-badge new">New</span>';
  }
  if (changeState === "changed") {
    return '<span class="annotation-change-badge changed">Changed</span>';
  }
  return "";
}

function annotationHasAnyFlags(annotation) {
  const flags = cloneAnnotationFlags(annotation);
  return (
    flags.is_occluded || flags.is_truncated || flags.is_outside || flags.is_lost
  );
}

function getAnnotationFlagLabels(annotation) {
  const labels = [];
  if (annotation?.is_occluded) labels.push("Occluded");
  if (annotation?.is_truncated) labels.push("Truncated");
  if (annotation?.is_outside) labels.push("Outside");
  if (annotation?.is_lost) labels.push("Lost");
  return labels;
}

function applyAnnotationVisualState(ctx, annotation, scale) {
  const safeScale = scale || 1;
  if (annotation?.is_lost) {
    ctx.setLineDash([12 / safeScale, 8 / safeScale]);
  } else if (annotation?.is_occluded) {
    ctx.setLineDash([8 / safeScale, 6 / safeScale]);
  } else if (annotation?.is_truncated) {
    ctx.setLineDash([3 / safeScale, 4 / safeScale]);
  } else {
    ctx.setLineDash([]);
  }
  if (annotation?.is_outside) {
    ctx.globalAlpha *= 0.6;
  }
}

function sparseAnnotationEqual(left, right) {
  return (
    left.client_uid === right.client_uid &&
    left.label_class_id === right.label_class_id &&
    left.frame_index === right.frame_index &&
    left.track_id === right.track_id &&
    left.propagation_frames === right.propagation_frames &&
    !!left.is_occluded === !!right.is_occluded &&
    !!left.is_truncated === !!right.is_truncated &&
    !!left.is_outside === !!right.is_outside &&
    !!left.is_lost === !!right.is_lost &&
    left.status === right.status &&
    left.x1 === right.x1 &&
    left.y1 === right.y1 &&
    left.x2 === right.x2 &&
    left.y2 === right.y2 &&
    polygonPointsEqual(left.polygon_points, right.polygon_points)
  );
}

// NEW: debug logger for video init issues
export function lfDebug(tag, payload = {}) {
  if (typeof window === "undefined") return;
  // Logging is disabled by default. Set window.LF_VIDEO_DEBUG = true in the console
  // (or from a template) when you actually want to see these logs.
  if (!window.LF_VIDEO_DEBUG) return;
  try {
    const entry = {
      tag,
      ts: new Date().toISOString(),
      ...payload,
    };
    if (!window.__LF_DEBUG_LOGS) {
      window.__LF_DEBUG_LOGS = [];
    }
    window.__LF_DEBUG_LOGS.push(entry);
    console.log("[LF-VIDEO]", tag, entry);
  } catch (_) {
    // ignore logging failures
  }
}

// Geometry adapters: bbox and polygon are implemented; line remains a future extension.

const bboxGeometry = {
  hitTest({
    annotation,
    index,
    xCanvas,
    yCanvas,
    fromImageToCanvasCoords,
    handleRadiusSq,
  }) {
    const p1 = fromImageToCanvasCoords(annotation.x1, annotation.y1);
    const p2 = fromImageToCanvasCoords(annotation.x2, annotation.y2);
    const minX = Math.min(p1.x, p2.x);
    const maxX = Math.max(p1.x, p2.x);
    const minY = Math.min(p1.y, p2.y);
    const maxY = Math.max(p1.y, p2.y);

    // Corner handles have priority over body hit.
    const corners = [
      { name: "nw", cx: minX, cy: minY },
      { name: "ne", cx: maxX, cy: minY },
      { name: "se", cx: maxX, cy: maxY },
      { name: "sw", cx: minX, cy: maxY },
    ];
    for (const c of corners) {
      const dx = xCanvas - c.cx;
      const dy = yCanvas - c.cy;
      if (dx * dx + dy * dy <= handleRadiusSq) {
        return {
          ann: annotation,
          index,
          handle: c.name,
          hitDistanceSq: dx * dx + dy * dy,
        };
      }
    }

    if (
      xCanvas >= minX &&
      xCanvas <= maxX &&
      yCanvas >= minY &&
      yCanvas <= maxY
    ) {
      return { ann: annotation, index, handle: null, hitDistanceSq: 0 };
    }

    return null;
  },

  draw({
    ctx,
    annotation,
    isActive,
    color,
    worldLineWidthActive,
    worldLineWidthNormal,
    worldHandleRadius,
    worldHandleBorderWidth,
    viewScale,
  }) {
    const x1 = annotation.x1;
    const y1 = annotation.y1;
    const x2 = annotation.x2;
    const y2 = annotation.y2;

    const x = Math.min(x1, x2);
    const y = Math.min(y1, y2);
    const w = Math.abs(x2 - x1);
    const h = Math.abs(y2 - y1);

    ctx.save();
    applyAnnotationVisualState(ctx, annotation, viewScale);
    ctx.lineWidth = isActive ? worldLineWidthActive : worldLineWidthNormal;
    ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);

    if (!isActive) {
      ctx.restore();
      return;
    }

    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    const corners = [
      { cx: x, cy: y },
      { cx: x + w, cy: y },
      { cx: x + w, cy: y + h },
      { cx: x, cy: y + h },
    ];
    for (const c of corners) {
      ctx.beginPath();
      ctx.arc(c.cx, c.cy, worldHandleRadius, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.lineWidth = worldHandleBorderWidth;
      ctx.strokeStyle = color;
      ctx.stroke();
    }
    ctx.restore();
  },

  move({ annotation, dx, dy, clampAnnotationToImage }) {
    annotation.x1 += dx;
    annotation.y1 += dy;
    annotation.x2 += dx;
    annotation.y2 += dy;
    clampAnnotationToImage(annotation);
  },

  resize({ annotation, handle, imgPt, resizeStart, clampAnnotationToImage }) {
    if (!resizeStart) return;
    const xs = resizeStart;

    switch (handle) {
      case "nw":
        annotation.x1 = imgPt.x;
        annotation.y1 = imgPt.y;
        annotation.x2 = xs.x2;
        annotation.y2 = xs.y2;
        break;
      case "ne":
        annotation.x1 = xs.x1;
        annotation.y1 = imgPt.y;
        annotation.x2 = imgPt.x;
        annotation.y2 = xs.y2;
        break;
      case "se":
        annotation.x1 = xs.x1;
        annotation.y1 = xs.y1;
        annotation.x2 = imgPt.x;
        annotation.y2 = imgPt.y;
        break;
      case "sw":
        annotation.x1 = imgPt.x;
        annotation.y1 = xs.y1;
        annotation.x2 = xs.x2;
        annotation.y2 = imgPt.y;
        break;
      default:
        break;
    }

    clampAnnotationToImage(annotation);
  },
};

const polygonGeometry = {
  hitTest(args) {
    const polygonPoints = clonePolygonPoints(args.annotation.polygon_points);
    if (!polygonPoints || !polygonPoints.length) {
      return bboxGeometry.hitTest(args);
    }

    const canvasPoints = polygonPoints.map(([x, y]) =>
      args.fromImageToCanvasCoords(x, y)
    );

    for (let vertexIndex = 0; vertexIndex < canvasPoints.length; vertexIndex += 1) {
      const point = canvasPoints[vertexIndex];
      const dx = args.xCanvas - point.x;
      const dy = args.yCanvas - point.y;
      if (dx * dx + dy * dy <= args.handleRadiusSq) {
        return {
          ann: args.annotation,
          index: args.index,
          handle: `vertex:${vertexIndex}`,
          hitDistanceSq: dx * dx + dy * dy,
        };
      }
    }

    for (let edgeIndex = 0; edgeIndex < canvasPoints.length; edgeIndex += 1) {
      const current = canvasPoints[edgeIndex];
      const next = canvasPoints[(edgeIndex + 1) % canvasPoints.length];
      const edgeDistanceSq = distancePointToSegmentSq(
        args.xCanvas,
        args.yCanvas,
        current.x,
        current.y,
        next.x,
        next.y
      );
      if (edgeDistanceSq <= args.handleRadiusSq) {
        return {
          ann: args.annotation,
          index: args.index,
          handle: `edge:${edgeIndex}`,
          hitDistanceSq: edgeDistanceSq,
        };
      }
    }

    if (pointInPolygonCanvas(args.xCanvas, args.yCanvas, canvasPoints)) {
      return {
        ann: args.annotation,
        index: args.index,
        handle: null,
        hitDistanceSq: 0,
      };
    }

    return null;
  },
  draw(args) {
    const polygonPoints = clonePolygonPoints(args.annotation.polygon_points);
    if (!polygonPoints || polygonPoints.length < 2) {
      return bboxGeometry.draw(args);
    }

    const { ctx, isActive, color } = args;
    ctx.save();
    applyAnnotationVisualState(ctx, args.annotation, args.viewScale);

    ctx.beginPath();
    ctx.moveTo(polygonPoints[0][0], polygonPoints[0][1]);
    for (let pointIndex = 1; pointIndex < polygonPoints.length; pointIndex += 1) {
      const point = polygonPoints[pointIndex];
      ctx.lineTo(point[0], point[1]);
    }
    if (polygonPoints.length >= 3) {
      ctx.closePath();
    }

    ctx.save();
    ctx.fillStyle = color;
    ctx.globalAlpha = isActive ? 0.18 : 0.08;
    if (polygonPoints.length >= 3) {
      ctx.fill();
    }
    ctx.restore();

    ctx.lineWidth = isActive
      ? args.worldLineWidthActive
      : args.worldLineWidthNormal;
    ctx.strokeStyle = color;
    ctx.stroke();

    if (!isActive) {
      ctx.restore();
      return;
    }

    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    polygonPoints.forEach(([x, y], vertexIndex) => {
      const isSelectedVertex =
        isActive &&
        Number.isInteger(args.activeVertexIndex) &&
        vertexIndex === args.activeVertexIndex;

      ctx.beginPath();
      ctx.arc(x, y, args.worldHandleRadius, 0, Math.PI * 2);
      ctx.fillStyle = isSelectedVertex ? color : "#ffffff";
      ctx.fill();
      ctx.lineWidth = args.worldHandleBorderWidth;
      ctx.strokeStyle = isSelectedVertex ? "#ffffff" : color;
      ctx.stroke();
    });
    ctx.restore();
  },
  move(args) {
    const polygonPoints = clonePolygonPoints(args.annotation.polygon_points);
    if (!polygonPoints || !polygonPoints.length) {
      return bboxGeometry.move(args);
    }

    args.annotation.polygon_points = polygonPoints.map(([x, y]) => [
      x + args.dx,
      y + args.dy,
    ]);
    syncPolygonBounds(args.annotation);
    args.clampAnnotationToImage(args.annotation);
  },
  resize(args) {
    if (typeof args.handle !== "string" || !args.handle.startsWith("vertex:")) {
      return;
    }

    const polygonPoints = clonePolygonPoints(args.annotation.polygon_points);
    if (!polygonPoints || !polygonPoints.length) {
      return;
    }

    const vertexIndex = parseInt(args.handle.slice("vertex:".length), 10);
    if (
      !Number.isFinite(vertexIndex) ||
      vertexIndex < 0 ||
      vertexIndex >= polygonPoints.length
    ) {
      return;
    }

    polygonPoints[vertexIndex] = [args.imgPt.x, args.imgPt.y];
    args.annotation.polygon_points = polygonPoints;
    syncPolygonBounds(args.annotation);
    args.clampAnnotationToImage(args.annotation);
  },
};

// TODO: Implement true line support (endpoints, segment hit test, etc.).
// For now, line behaves like a bbox so existing data still works once "line"
// geometry_kind is added server-side.
const lineGeometry = {
  hitTest(args) {
    // TODO: line hit test (endpoints / segment).
    return bboxGeometry.hitTest(args);
  },
  draw(args) {
    // TODO: line drawing.
    return bboxGeometry.draw(args);
  },
  move(args) {
    // TODO: line move (translate both endpoints).
    return bboxGeometry.move(args);
  },
  resize(args) {
    // TODO: line resize (drag endpoints).
    return bboxGeometry.resize(args);
  },
};

function getGeometryAdapter(geometryKind) {
  switch (geometryKind) {
    case "polygon":
      return polygonGeometry;
    case "line":
      return lineGeometry;
    default:
      // "bbox" and "tag" (and unknown kinds) fall back to bbox behavior.
      return bboxGeometry;
  }
}

export class AnnotationCanvas {
  constructor(canvas, mediaEl, options) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.stageEl = canvas.parentElement;
    this.canvas.style.cursor = "crosshair";
    this.mediaEl = mediaEl;
    this.mediaEl.style.transformOrigin = "top left";
    this.mediaEl.style.willChange = "transform";
    this.mediaEl.style.display = "block";
    this.itemId = options.itemId;
    this.apiBase = options.apiBase;
    this.kind = options.kind || "image";
    this.fps = options.fps || 30;
    this.durationSec = options.durationSec || null;
    this.labelClasses = options.labelClasses || [];
    this.currentLabelClassId = options.currentLabelClassId || null;
    this.prevItemId =
      typeof options.prevItemId === "number"
        ? options.prevItemId
        : options.prevItemId != null
          ? parseInt(options.prevItemId, 10)
          : null;
    this.prevItemUrl = options.prevItemUrl || null;
    this.nextItemUrl = options.nextItemUrl || null;
    this.readOnly = !!options.readOnly;
    this.annotationRevision = Number.isFinite(Number(options.annotationRevision))
      ? Number(options.annotationRevision)
      : 0;
    this.showPreviousFrameOverlay = false;
    this.validationIssues = [];
    this.validationCursor = -1;
    this.pendingReassignSelection = null;
    this.pendingMergeTargetTrackId = null;
    this.annotationClipboard = null;
    this.hiddenAnnotationClientUids = new Set();
    this.objectContextMenuEl = null;
    this.objectContextMenuTarget = null;
    this.lastSavedSparseState = new Map();
    this.renderQueued = false;
    this.renderQueuedWithList = false;

    this.frameAnnotations = new Map();
    this.annotations = [];
    this.currentFrameIndex = 0;
    this.lastFrameIndex = null;
    this.pendingFrameIndex = null;
    this.pendingFrameSource = null;
    this.pendingTrackRestoreId = null;
    this.hasPresentedVideoFrame = this.kind !== "video";

    this.isDrawing = false;
    this.isPolygonDrawing = false;
    this.isDragging = false;
    this.draggedAnnotation = null;
    this.dragMode = null; // "move" or "resize"
    this.dragHandle = null; // "nw" | "ne" | "se" | "sw"
    this.dragOffset = { x: 0, y: 0 };
    this.resizeStart = null; // original coords when resizing
    this.startPoint = { x: 0, y: 0 };

    this.activeAnnotation = null;
    this.activeVertexIndex = null;
    this.handleRadius = 6;

    this.pixelRatio = window.devicePixelRatio || 1;

    // Viewport / zoom / pan state
    this.imageWidth = null;
    this.imageHeight = null;
    this.viewportWidth = null;
    this.viewportHeight = null;
    this.baseScale = 1;
    this.zoom = 1;
    this.minZoom = 0.25;
    this.maxZoom = 8;
    this.translateX = 0;
    this.translateY = 0;
    this.isPanning = false;
    this.panStart = { x: 0, y: 0 };
    this.panTranslateStart = { x: 0, y: 0 };

    // Render loop for smooth video playback when using canvas as surface
    this.renderLoopActive = false;
    this.renderLoopHandle = null;

    // Whether to render video frames on canvas (vs native <video>)
    this.useCanvasVideo = false;


    this.frameDisplayEl = document.getElementById("frame-display-current");
    this.frameTotalEl = document.getElementById("frame-display-total");
    this.timeDisplayEl = document.getElementById("time-display");
    this.copyFrameLinkBtnEl = document.getElementById("btn-copy-frame-link");
    this.copyFrameLinkLabelEl = document.getElementById("frame-link-button-label");
    this.copyFrameLinkFeedbackTimer = null;
    this.requestedFrameIndexFromUrl = parseRequestedFrameIndexFromLocation();
    this.overviewSidebarEl = document.getElementById("video-overview-sidebar");
    this.overviewSegmentCount = 40;
    this.totalFrames = null;
    this.timelineInitialized = false;
    this.timelineTrackEl = document.getElementById("video-timeline-track");
    this.timelinePlayheadEl = document.getElementById("video-timeline-playhead");
    this.timelineAnnotationLayerEl = document.getElementById("video-timeline-annotation-layer");
    this.timelineObjectsLayerEl = document.getElementById("object-timeline-list");
    this.interpolationPanelEl = document.getElementById("interpolation-panel");
    this.interpolationTrackLabelEl = document.getElementById("interpolation-track-label");
    this.interpolationKeyframesEl = document.getElementById("interpolation-keyframes");
    this.interpolationStartFrameEl = document.getElementById("interpolation-start-frame");
    this.interpolationEndFrameEl = document.getElementById("interpolation-end-frame");
    this.interpolationHintEl = document.getElementById("interpolation-hint");
    this.interpolationSelection = {
      trackId: null,
      startFrame: null,
      endFrame: null,
    };
    this.manualKeyframesByTrack = new Map();

    this.currentDrawingAnnotation = null;
    this.nextTrackId = 1;
    this.defaultPropagationFrames = 0;
    this.propagationLengthInput = document.getElementById(
      "propagation-length-input"
    );

    this.saveTimer = null;
    this.isDirty = false;
    this.isSaving = false;
    this.pendingSaveRequested = false;
    this.historyLimit = 30;
    this.historyUndoStack = [];
    this.historyRedoStack = [];
    this.useFixedSizeBBox = false;
    this.fixedBBoxWidth = 64;
    this.fixedBBoxHeight = 64;
    this.trackViewStateById = new Map();
    this.soloTrackId = null;
    this.loadTrackUiStateFromStorage();
    this.showAnnotationLabels = this.loadAnnotationLabelVisibilityFromStorage();

    this.loadingOverlayEl = document.getElementById("annotation-loading-overlay");

    // object/track selection + timeline drag state
    this.activeTrackId = null;
    this.timelineDragState = null;
    this.onTimelineDragMoveBound = (evt) => this.onTimelineDragMove(evt);
    this.onTimelineDragEndBound = () => this.onTimelineDragEnd();

    lfDebug("ctor", {
      kind: this.kind,
      fps: this.fps,
      durationSec: this.durationSec,
      readyState: this.mediaEl.readyState,
      videoWidth: this.mediaEl.videoWidth,
      videoHeight: this.mediaEl.videoHeight,
      src: this.mediaEl.currentSrc || this.mediaEl.src,
    });
  }

  init(initialAnnotations) {
    lfDebug("init.start", {
      kind: this.kind,
      readyState: this.mediaEl.readyState,
      videoWidth: this.mediaEl.videoWidth,
      videoHeight: this.mediaEl.videoHeight,
    });
    this.initializeStore(initialAnnotations || []);
    this.replaceSavedSparseState();

    if (this.mediaEl.tagName === "IMG") {
      const syncAndLoad = () => {
        this.syncCanvasSize(true);
        this.mediaEl.style.opacity = "0";
        this.mediaEl.style.pointerEvents = "none";
        this.loadFrame(null, false);
        this.hideLoadingOverlay();
      };
      if (this.mediaEl.complete) {
        syncAndLoad();
      } else {
        this.mediaEl.addEventListener("load", syncAndLoad, { once: true });
      }
    } else {
      const onMetadata = () => {
        lfDebug("video.loadedmetadata", {
          readyState: this.mediaEl.readyState,
          duration: this.mediaEl.duration,
          videoWidth: this.mediaEl.videoWidth,
          videoHeight: this.mediaEl.videoHeight,
        });
        this.syncCanvasSize(true);
        this.loadFrame(0, false);
        this.setupTimeline();
        this.applyRequestedFrameFromLocation();
        this.primeVideoFirstFrame();
        this.forceDecodeFirstFrame();
      };

      if (this.mediaEl.readyState >= 1) {
        onMetadata();
      } else {
        this.mediaEl.addEventListener("loadedmetadata", onMetadata, { once: true });
      }

      const onFirstFrame = () => {
        this.switchToCanvasVideoIfReady();
      };

      if (this.mediaEl.readyState >= 2) {
        this.switchToCanvasVideoIfReady();
      } else {
        this.mediaEl.addEventListener("loadeddata", onFirstFrame, { once: true });
      }
    }

    this.attachEvents();
    this.updateInterpolationPanel();
    this.requestRedraw();
  }

  hideLoadingOverlay() {
    if (!this.loadingOverlayEl) return;
    this.loadingOverlayEl.style.display = "none";
  }

  showLoadingOverlay() {
    if (!this.loadingOverlayEl) return;
    if (this.kind === "video" && this.hasPresentedVideoFrame) return;
    this.loadingOverlayEl.style.display = "flex";
  }

  requestRedraw(withList = true) {
    this.renderQueuedWithList = this.renderQueuedWithList || !!withList;
    if (this.renderQueued) {
      return;
    }

    this.renderQueued = true;
    window.requestAnimationFrame(() => {
      const nextWithList = this.renderQueuedWithList;
      this.renderQueued = false;
      this.renderQueuedWithList = false;
      this.redraw(nextWithList);
    });
  }

  applyMediaTransform() {
    if (!this.mediaEl) return;

    const zoom = this.zoom || 1;
    const translateX = this.translateX || 0;
    const translateY = this.translateY || 0;
    this.mediaEl.style.transformOrigin = "top left";
    this.mediaEl.style.transform = `matrix(${zoom}, 0, 0, ${zoom}, ${translateX}, ${translateY})`;
  }

  replaceSavedSparseState(snapshot = this.makeSparseSnapshotMap()) {
    this.lastSavedSparseState = new Map(
      Array.from(snapshot.entries()).map(([clientUid, annotation]) => [
        clientUid,
        {
          ...annotation,
          ...cloneAnnotationAuditMetadata(annotation),
          polygon_points: clonePolygonPoints(annotation.polygon_points),
        },
      ])
    );
  }

  buildSparsePatch(currentState) {
    const upserts = [];
    const deletes = [];

    currentState.forEach((annotation, clientUid) => {
      const savedAnnotation = this.lastSavedSparseState.get(clientUid);
      if (!savedAnnotation || !sparseAnnotationEqual(savedAnnotation, annotation)) {
        upserts.push(annotation);
      }
    });

    this.lastSavedSparseState.forEach((_annotation, clientUid) => {
      if (!currentState.has(clientUid)) {
        deletes.push(clientUid);
      }
    });

    return { upserts, deletes };
  }

  applyServerAnnotations(annotations, revision = this.annotationRevision) {
    const currentFrameIndex = this.currentFrameIndex | 0;
    const activeClientUid =
      this.activeAnnotation?.client_uid ||
      this.activeAnnotation?._storedAnnotation?.client_uid ||
      null;
    const activeTrackId = Number.isInteger(this.activeTrackId)
      ? this.activeTrackId
      : null;
    const activeVertexIndex = Number.isInteger(this.activeVertexIndex)
      ? this.activeVertexIndex
      : null;
    const hiddenAnnotationClientUids = new Set(this.hiddenAnnotationClientUids);
    const historyUndoStack = Array.isArray(this.historyUndoStack)
      ? [...this.historyUndoStack]
      : [];
    const historyRedoStack = Array.isArray(this.historyRedoStack)
      ? [...this.historyRedoStack]
      : [];
    this.initializeStore(Array.isArray(annotations) ? annotations : []);
    this.historyUndoStack = historyUndoStack;
    this.historyRedoStack = historyRedoStack;
    if (Number.isFinite(Number(revision))) {
      this.annotationRevision = Number(revision);
    }
    this.hiddenAnnotationClientUids = hiddenAnnotationClientUids;
    this.pruneHiddenAnnotationState();
    this.replaceSavedSparseState();

    if (this.kind === "video") {
      this.setCurrentFrame(currentFrameIndex, { source: "internal" });
      if (activeClientUid) {
        const nextActive = this.findAnnotationByClientUid(activeClientUid);
        if (nextActive) {
          this.markActiveAnnotation(nextActive, {
            vertexIndex: activeVertexIndex,
          });
        } else if (Number.isInteger(activeTrackId)) {
          this.restoreActiveAnnotationForTrack(activeTrackId, {
            vertexIndex: activeVertexIndex,
          });
        }
      } else if (Number.isInteger(activeTrackId)) {
        this.restoreActiveAnnotationForTrack(activeTrackId, {
          vertexIndex: activeVertexIndex,
        });
      }
      this.refreshCurrentHistoryCheckpoint();
      return;
    }

    this.loadFrame(null, false);
    if (activeClientUid) {
      const nextActive = this.findAnnotationByClientUid(activeClientUid);
      if (nextActive) {
        this.markActiveAnnotation(nextActive, {
          vertexIndex: activeVertexIndex,
        });
      }
    }
    this.refreshCurrentHistoryCheckpoint();
  }

  refreshCurrentHistoryCheckpoint() {
    if (!Array.isArray(this.historyUndoStack) || !this.historyUndoStack.length) {
      return;
    }
    this.historyUndoStack[this.historyUndoStack.length - 1] =
      this.serializeCurrentStateForHistory();
    this.updateHistoryButtons();
  }

  getStatusBadgeClass(statusValue) {
    switch (statusValue) {
      case "in_progress":
        return "badge text-bg-primary";
      case "needs_review":
        return "badge text-bg-warning";
      case "done":
        return "badge text-bg-success";
      default:
        return "badge text-bg-secondary";
    }
  }

  updateStatusBadge(statusValue) {
    const badge = document.getElementById("item-status-badge");
    if (!badge || !statusValue) return;
    badge.textContent = statusValue;
    badge.className = this.getStatusBadgeClass(statusValue);
  }

  normalizeAnnotationCoords(annotation) {
    if (!annotation) return annotation;

    const polygonPoints = clonePolygonPoints(annotation.polygon_points);
    if (polygonPoints && polygonPoints.length) {
      annotation.polygon_points = polygonPoints;
      syncPolygonBounds(annotation);
      return annotation;
    }

    if (annotation.x1 > annotation.x2) {
      [annotation.x1, annotation.x2] = [annotation.x2, annotation.x1];
    }
    if (annotation.y1 > annotation.y2) {
      [annotation.y1, annotation.y2] = [annotation.y2, annotation.y1];
    }
    return annotation;
  }

  normalizePropagationFrames(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return 0;
    }
    return Math.max(0, Math.trunc(parsed));
  }

  isDegenerateAnnotation(annotation) {
    if (!annotation) return true;
    const polygonPoints = clonePolygonPoints(annotation.polygon_points);
    if (polygonPoints && polygonPoints.length) {
      if (polygonPoints.length < 3) {
        return true;
      }
      return Math.abs(polygonSignedArea(polygonPoints)) <= 1e-6;
    }
    const width = Math.abs((annotation.x2 ?? 0) - (annotation.x1 ?? 0));
    const height = Math.abs((annotation.y2 ?? 0) - (annotation.y1 ?? 0));
    return width <= 0 || height <= 0;
  }

  removeAnnotationWithoutSave(annotation) {
    if (!annotation) return;

    const idx = this.annotations.indexOf(annotation);
    if (idx >= 0) {
      this.annotations.splice(idx, 1);
    }

    if (this.activeAnnotation === annotation) {
      this.activeAnnotation = null;
      this.activeVertexIndex = null;
    }
  }

  sanitizeBucket(bucket, frameIndex) {
    const cleaned = [];

    for (const ann of bucket) {
      this.normalizeAnnotationCoords(ann);
      ann.frame_index = frameIndex;

      if (this.isDegenerateAnnotation(ann)) {
        if (this.activeAnnotation === ann) {
          this.activeAnnotation = null;
        }
        continue;
      }

      cleaned.push(ann);
    }

    return cleaned;
  }

  ensureClientUid(annotation) {
    if (!annotation) return null;
    if (!annotation.client_uid) {
      annotation.client_uid = generateClientUid();
    }
    return annotation.client_uid;
  }

  toPersistedSparseAnnotation(annotation) {
    const normalized = syncPolygonBounds({
      client_uid: annotation.client_uid || generateClientUid(),
      label_class_id: annotation.label_class_id,
      frame_index:
        annotation.frame_index == null
          ? null
          : Math.max(0, Math.trunc(Number(annotation.frame_index))),
      x1: Number(annotation.x1),
      y1: Number(annotation.y1),
      x2: Number(annotation.x2),
      y2: Number(annotation.y2),
      status: annotation.status || "pending",
      track_id: annotation.track_id != null ? annotation.track_id : null,
      propagation_frames: Math.max(
        0,
        Math.trunc(Number(annotation.propagation_frames ?? 0) || 0)
      ),
      ...cloneAnnotationFlags(annotation),
      ...cloneAnnotationAuditMetadata(annotation),
      polygon_points: clonePolygonPoints(annotation.polygon_points),
    });
    if (this.kind !== "video") {
      normalized.frame_index = null;
      normalized.propagation_frames = 0;
    }
    this.ensureClientUid(normalized);
    return normalized;
  }

  makeSparseSnapshotMap() {
    const snapshot = new Map();
    for (const annotation of this.getSparseAnnotations()) {
      const normalized = this.toPersistedSparseAnnotation(annotation);
      snapshot.set(normalized.client_uid, normalized);
    }
    return snapshot;
  }

  serializeCurrentStateForHistory() {
    const annotations = Array.from(this.makeSparseSnapshotMap().values())
      .sort((left, right) => {
        const leftFrame = left.frame_index ?? -1;
        const rightFrame = right.frame_index ?? -1;
        if (leftFrame !== rightFrame) return leftFrame - rightFrame;
        const leftTrackId = left.track_id ?? Number.MAX_SAFE_INTEGER;
        const rightTrackId = right.track_id ?? Number.MAX_SAFE_INTEGER;
        if (leftTrackId !== rightTrackId) return leftTrackId - rightTrackId;
        if (left.label_class_id !== right.label_class_id) {
          return left.label_class_id - right.label_class_id;
        }
        return (left.client_uid || "").localeCompare(right.client_uid || "");
      })
      .map((annotation) => ({
        ...annotation,
        polygon_points: clonePolygonPoints(annotation.polygon_points),
      }));

    const manualKeyframes = {};
    Array.from(this.manualKeyframesByTrack.entries())
      .sort((left, right) => left[0] - right[0])
      .forEach(([trackId, frames]) => {
        manualKeyframes[String(trackId)] = Array.from(frames).sort((a, b) => a - b);
      });

    return {
      annotations,
      manual_keyframes: manualKeyframes,
      current_frame_index: this.currentFrameIndex | 0,
      current_label_class_id: this.currentLabelClassId ?? null,
      active_track_id: this.activeTrackId ?? null,
      active_client_uid: this.activeAnnotation?.client_uid ?? null,
      active_vertex_index: Number.isInteger(this.activeVertexIndex) ? this.activeVertexIndex : null,
    };
  }

  historyStateSignature(state) {
    return JSON.stringify(state);
  }

  restoreManualKeyframesFromHistory(payload) {
    const nextMap = new Map();
    if (!payload || typeof payload !== "object") return nextMap;
    Object.entries(payload).forEach(([trackIdValue, frames]) => {
      const trackId = Number(trackIdValue);
      if (!Number.isInteger(trackId) || !Array.isArray(frames)) return;
      const normalizedFrames = [...new Set(
        frames
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value))
          .map((value) => Math.max(0, Math.trunc(value)))
      )].sort((a, b) => a - b);
      if (normalizedFrames.length) nextMap.set(trackId, new Set(normalizedFrames));
    });
    return nextMap;
  }

  resetHistory() {
    this.historyUndoStack = [this.serializeCurrentStateForHistory()];
    this.historyRedoStack = [];
    this.updateHistoryButtons();
  }

  pushHistoryCheckpoint() {
    const nextState = this.serializeCurrentStateForHistory();
    const previous = this.historyUndoStack[this.historyUndoStack.length - 1] || null;
    if (
      previous &&
      this.historyStateSignature(previous) === this.historyStateSignature(nextState)
    ) {
      this.updateHistoryButtons();
      return;
    }
    this.historyUndoStack.push(nextState);
    if (this.historyUndoStack.length > this.historyLimit) this.historyUndoStack.shift();
    this.historyRedoStack = [];
    this.updateHistoryButtons();
  }

  applyHistoryState(state) {
    if (!state || !Array.isArray(state.annotations)) return;
    this.initializeStore(state.annotations);
    this.manualKeyframesByTrack = this.restoreManualKeyframesFromHistory(
      state.manual_keyframes
    );
    this.persistManualKeyframesToStorage();
    const frameIndex = Math.max(0, Math.trunc(Number(state.current_frame_index ?? 0)));
    if (this.kind === "video") {
      this.pendingTrackRestoreId = Number.isInteger(state.active_track_id)
        ? state.active_track_id
        : null;
      this.setCurrentFrame(frameIndex, { source: "scrub" });
    } else {
      this.loadFrame(null, false);
    }
    if (state.current_label_class_id != null) {
      this.currentLabelClassId = state.current_label_class_id;
      const select = document.getElementById("label-class-select");
      if (select) select.value = String(state.current_label_class_id);
    }
    if (this.kind !== "video" && state.active_client_uid) {
      this.activeAnnotation =
        this.annotations.find((annotation) => annotation.client_uid === state.active_client_uid) || null;
    }
    this.updateFastActionButtons();
    this.requestRedraw();
  }

  undo() {
    if (this.readOnly || this.historyUndoStack.length <= 1) return;
    const current = this.historyUndoStack.pop();
    if (current) this.historyRedoStack.push(current);
    const previous = this.historyUndoStack[this.historyUndoStack.length - 1] || null;
    if (!previous) return;
    const undoStackSnapshot = this.historyUndoStack.slice();
    const redoStackSnapshot = this.historyRedoStack.slice();
    this.applyHistoryState(previous);
    this.historyUndoStack = undoStackSnapshot;
    this.historyRedoStack = redoStackSnapshot;
    this.updateHistoryButtons();
    this.scheduleSave();
  }

  redo() {
    if (this.readOnly || !this.historyRedoStack.length) return;
    const next = this.historyRedoStack.pop();
    if (!next) return;
    this.historyUndoStack.push(next);
    const undoStackSnapshot = this.historyUndoStack.slice();
    const redoStackSnapshot = this.historyRedoStack.slice();
    this.applyHistoryState(next);
    this.historyUndoStack = undoStackSnapshot;
    this.historyRedoStack = redoStackSnapshot;
    this.updateHistoryButtons();
    this.scheduleSave();
  }

  updateHistoryButtons() {
    const undoBtn = document.getElementById("btn-undo");
    const redoBtn = document.getElementById("btn-redo");
    if (undoBtn) undoBtn.disabled = this.readOnly || this.historyUndoStack.length <= 1;
    if (redoBtn) redoBtn.disabled = this.readOnly || this.historyRedoStack.length === 0;
  }

  updateFastActionButtons() {
    const gapCount = Number.isInteger(this.activeTrackId)
      ? this.getTrackGaps(this.activeTrackId).length
      : 0;
    const prevGapBtn = document.getElementById("btn-prev-gap");
    const nextGapBtn = document.getElementById("btn-next-gap");
    if (prevGapBtn) prevGapBtn.disabled = this.readOnly || gapCount === 0;
    if (nextGapBtn) nextGapBtn.disabled = this.readOnly || gapCount === 0;
  }

  getAnnotationBounds(annotation) {
    return {
      x1: Math.min(Number(annotation.x1), Number(annotation.x2)),
      y1: Math.min(Number(annotation.y1), Number(annotation.y2)),
      x2: Math.max(Number(annotation.x1), Number(annotation.x2)),
      y2: Math.max(Number(annotation.y1), Number(annotation.y2)),
    };
  }

  getAnnotationGeometrySignature(annotation) {
    const bounds = this.getAnnotationBounds(annotation);
    const polygonPoints = clonePolygonPoints(annotation.polygon_points);
    const polygonSignature = polygonPoints
      ? polygonPoints
          .map(([x, y]) => `${Number(x).toFixed(3)},${Number(y).toFixed(3)}`)
          .join(";")
      : "";

    return [
      bounds.x1.toFixed(3),
      bounds.y1.toFixed(3),
      bounds.x2.toFixed(3),
      bounds.y2.toFixed(3),
      polygonSignature,
    ].join("|");
  }

  annotationTouchesImageBoundary(annotation) {
    const imageWidth = Number(
      this.imageWidth || this.mediaEl.naturalWidth || this.mediaEl.videoWidth || 0
    );
    const imageHeight = Number(
      this.imageHeight || this.mediaEl.naturalHeight || this.mediaEl.videoHeight || 0
    );

    if (!imageWidth || !imageHeight) {
      return false;
    }

    const polygonPoints = clonePolygonPoints(annotation.polygon_points);
    if (polygonPoints && polygonPoints.length) {
      return polygonPoints.some(
        ([x, y]) =>
          Number(x) < 0 ||
          Number(y) < 0 ||
          Number(x) > imageWidth ||
          Number(y) > imageHeight
      );
    }

    const bounds = this.getAnnotationBounds(annotation);
    return (
      bounds.x1 < 0 ||
      bounds.y1 < 0 ||
      bounds.x2 > imageWidth ||
      bounds.y2 > imageHeight
    );
  }

  formatValidationFrameLabel(frameIndex) {
    if (!Number.isInteger(frameIndex)) {
      return this.kind === "video" ? "this item" : "this image";
    }
    return `frame ${frameIndex + 1}`;
  }

  getValidationIssueContext(issue) {
    const parts = [];
    if (Number.isInteger(issue.trackId)) {
      parts.push(`Obj ${issue.trackId}`);
    }
    if (
      Number.isInteger(issue.startFrame) &&
      Number.isInteger(issue.endFrame) &&
      issue.startFrame !== issue.endFrame
    ) {
      parts.push(`Frames ${issue.startFrame + 1}–${issue.endFrame + 1}`);
    } else if (Number.isInteger(issue.frameIndex)) {
      parts.push(`Frame ${issue.frameIndex + 1}`);
    } else if (this.kind !== "video") {
      parts.push("Image");
    }
    return parts.join(" · ");
  }

  computeValidationIssues() {
    const currentState = Array.from(this.makeSparseSnapshotMap().values());
    const issues = [];

    if (!currentState.length) {
      return issues;
    }

    const annotationsByFrame = new Map();
    currentState.forEach((annotation) => {
      const frameKey = Number.isInteger(annotation.frame_index)
        ? annotation.frame_index
        : -1;
      const bucket = annotationsByFrame.get(frameKey) || [];
      bucket.push(annotation);
      annotationsByFrame.set(frameKey, bucket);
    });

    annotationsByFrame.forEach((bucket, frameKey) => {
      const duplicateGroups = new Map();
      bucket.forEach((annotation) => {
        const signature = [
          annotation.label_class_id,
          Number.isInteger(annotation.track_id)
            ? `track:${annotation.track_id}`
            : "track:none",
          this.getAnnotationGeometrySignature(annotation),
        ].join("|");
        const group = duplicateGroups.get(signature) || [];
        group.push(annotation);
        duplicateGroups.set(signature, group);
      });

      duplicateGroups.forEach((group) => {
        if (group.length < 2) {
          return;
        }
        issues.push({
          severity: "error",
          code: "duplicate_annotation",
          message: `Duplicate ${this.getLabelClassName(
            group[0].label_class_id
          )} annotations share identical geometry on ${this.formatValidationFrameLabel(
            frameKey >= 0 ? frameKey : null
          )}.`,
          frameIndex: frameKey >= 0 ? frameKey : null,
          trackId: group[0].track_id ?? null,
          clientUid: group[0].client_uid,
          relatedClientUids: group.map((annotation) => annotation.client_uid),
        });
      });
    });

    currentState.forEach((annotation) => {
      const touchesBoundary = this.annotationTouchesImageBoundary(annotation);
      if (touchesBoundary && !annotation.is_outside) {
        issues.push({
          severity: "warning",
          code: "outside_flag_missing",
          message: `${this.getLabelClassName(
            annotation.label_class_id
          )} extends beyond the image but Outside is not set.`,
          frameIndex: Number.isInteger(annotation.frame_index)
            ? annotation.frame_index
            : null,
          trackId: annotation.track_id ?? null,
          clientUid: annotation.client_uid,
        });
      } else if (!touchesBoundary && annotation.is_outside) {
        issues.push({
          severity: "warning",
          code: "outside_flag_inconsistent",
          message: `${this.getLabelClassName(
            annotation.label_class_id
          )} is inside the image but Outside is still set.`,
          frameIndex: Number.isInteger(annotation.frame_index)
            ? annotation.frame_index
            : null,
          trackId: annotation.track_id ?? null,
          clientUid: annotation.client_uid,
        });
      }
    });

    if (this.kind === "video") {
      const trackIds = Array.from(
        new Set(
          currentState
            .map((annotation) => annotation.track_id)
            .filter((trackId) => Number.isInteger(trackId))
        )
      ).sort((left, right) => left - right);

      trackIds.forEach((trackId) => {
        const trackSegments = this.getTrackSegments(trackId);
        for (let index = 1; index < trackSegments.length; index += 1) {
          const previousSegment = trackSegments[index - 1];
          const currentSegment = trackSegments[index];
          if (currentSegment.startFrame <= previousSegment.endFrame) {
            issues.push({
              severity: "error",
              code: "track_overlap",
              message: `Obj ${trackId} has overlapping sparse segments.`,
              trackId,
              frameIndex: currentSegment.startFrame,
              startFrame: currentSegment.startFrame,
              endFrame: previousSegment.endFrame,
            });
            break;
          }
        }

        const gaps = this.getTrackGaps(trackId);
        if (gaps.length) {
          issues.push({
            severity: "warning",
            code: "track_gap",
            message: `Obj ${trackId} has ${gaps.length} gap(s) between annotated ranges.`,
            trackId,
            frameIndex: gaps[0].startFrame,
            startFrame: gaps[0].startFrame,
            endFrame: gaps[0].endFrame,
          });
        }

        const trackAnnotations = this.getTrackSparseAnnotations(trackId);
        let jumpWarningAdded = false;
        let scaleWarningAdded = false;

        for (let index = 1; index < trackAnnotations.length; index += 1) {
          const previousAnnotation = trackAnnotations[index - 1];
          const currentAnnotation = trackAnnotations[index];
          const previousFrame = previousAnnotation.frame_index ?? 0;
          const currentFrame = currentAnnotation.frame_index ?? 0;
          const frameDelta = Math.max(1, currentFrame - previousFrame);

          const previousBounds = this.getAnnotationBounds(previousAnnotation);
          const currentBounds = this.getAnnotationBounds(currentAnnotation);
          const previousWidth = Math.max(1, previousBounds.x2 - previousBounds.x1);
          const previousHeight = Math.max(1, previousBounds.y2 - previousBounds.y1);
          const currentWidth = Math.max(1, currentBounds.x2 - currentBounds.x1);
          const currentHeight = Math.max(1, currentBounds.y2 - currentBounds.y1);

          const previousCenterX = (previousBounds.x1 + previousBounds.x2) / 2;
          const previousCenterY = (previousBounds.y1 + previousBounds.y2) / 2;
          const currentCenterX = (currentBounds.x1 + currentBounds.x2) / 2;
          const currentCenterY = (currentBounds.y1 + currentBounds.y2) / 2;

          const referenceSize = Math.max(
            previousWidth,
            previousHeight,
            currentWidth,
            currentHeight,
            1
          );
          const distance = Math.hypot(
            currentCenterX - previousCenterX,
            currentCenterY - previousCenterY
          );
          if (!jumpWarningAdded && distance / (referenceSize * frameDelta) > 0.85) {
            issues.push({
              severity: "warning",
              code: "abrupt_track_jump",
              message: `Obj ${trackId} moves sharply between frame ${
                previousFrame + 1
              } and frame ${currentFrame + 1}.`,
              trackId,
              frameIndex: currentFrame,
              startFrame: previousFrame,
              endFrame: currentFrame,
              clientUid: currentAnnotation.client_uid,
            });
            jumpWarningAdded = true;
          }

          const previousArea = previousWidth * previousHeight;
          const currentArea = currentWidth * currentHeight;
          const areaRatio = currentArea / Math.max(previousArea, 1);
          if (
            !scaleWarningAdded &&
            frameDelta <= 3 &&
            (areaRatio > 4 || areaRatio < 0.25)
          ) {
            issues.push({
              severity: "warning",
              code: "abrupt_scale_change",
              message: `Obj ${trackId} changes scale abruptly between frame ${
                previousFrame + 1
              } and frame ${currentFrame + 1}.`,
              trackId,
              frameIndex: currentFrame,
              startFrame: previousFrame,
              endFrame: currentFrame,
              clientUid: currentAnnotation.client_uid,
            });
            scaleWarningAdded = true;
          }
        }
      });
    }

    const severityRank = { error: 0, warning: 1 };
    return issues.sort((left, right) => {
      const leftSeverity = severityRank[left.severity] ?? 99;
      const rightSeverity = severityRank[right.severity] ?? 99;
      if (leftSeverity !== rightSeverity) {
        return leftSeverity - rightSeverity;
      }
      const leftFrame =
        Number.isInteger(left.frameIndex) ? left.frameIndex : Number.MAX_SAFE_INTEGER;
      const rightFrame =
        Number.isInteger(right.frameIndex) ? right.frameIndex : Number.MAX_SAFE_INTEGER;
      if (leftFrame !== rightFrame) {
        return leftFrame - rightFrame;
      }
      const leftTrack =
        Number.isInteger(left.trackId) ? left.trackId : Number.MAX_SAFE_INTEGER;
      const rightTrack =
        Number.isInteger(right.trackId) ? right.trackId : Number.MAX_SAFE_INTEGER;
      if (leftTrack !== rightTrack) {
        return leftTrack - rightTrack;
      }
      return String(left.code || "").localeCompare(String(right.code || ""));
    });
  }

  updateValidationPanel() {
    const panel = document.getElementById("validation-panel");
    const summaryEl = document.getElementById("validation-summary");
    const errorCountEl = document.getElementById("validation-errors-count");
    const warningCountEl = document.getElementById("validation-warnings-count");
    const listEl = document.getElementById("validation-issues-list");
    const prevIssueBtn = document.getElementById("btn-prev-validation-issue");
    const nextIssueBtn = document.getElementById("btn-next-validation-issue");

    if (!panel || !summaryEl || !errorCountEl || !warningCountEl || !listEl) {
      return;
    }

    this.validationIssues = this.computeValidationIssues();
    if (!this.validationIssues.length) {
      this.validationCursor = -1;
    } else if (
      this.validationCursor < 0 ||
      this.validationCursor >= this.validationIssues.length
    ) {
      this.validationCursor = 0;
    }

    const errorCount = this.validationIssues.filter(
      (issue) => issue.severity === "error"
    ).length;
    const warningCount = this.validationIssues.filter(
      (issue) => issue.severity === "warning"
    ).length;

    summaryEl.textContent = errorCount
      ? `${errorCount} error(s) and ${warningCount} warning(s) detected.`
      : warningCount
        ? `${warningCount} warning(s) detected.`
        : "No validation issues detected.";

    errorCountEl.textContent = String(errorCount);
    warningCountEl.textContent = String(warningCount);

    if (prevIssueBtn) {
      prevIssueBtn.disabled = this.validationIssues.length <= 1;
    }
    if (nextIssueBtn) {
      nextIssueBtn.disabled = this.validationIssues.length <= 1;
    }

    listEl.innerHTML = "";
    if (!this.validationIssues.length) {
      listEl.innerHTML =
        '<div class="px-2 py-2 text-muted small">No validation issues detected.</div>';
      return;
    }

    this.validationIssues.forEach((issue, index) => {
      const row = document.createElement("button");
      row.type = "button";
      row.dataset.validationIssueIndex = String(index);
      row.className =
        "list-group-item list-group-item-action bg-dark text-light border-secondary";
      if (index === this.validationCursor) {
        row.classList.add("active");
      }

      const badgeClass =
        issue.severity === "error" ? "text-bg-danger" : "text-bg-warning";
      const contextLabel = this.getValidationIssueContext(issue);
      row.innerHTML = `
        <div class="d-flex justify-content-between align-items-center gap-2">
          <span class="badge ${badgeClass} text-uppercase">${issue.severity}</span>
          ${
            contextLabel
              ? `<span class="small text-secondary">${contextLabel}</span>`
              : ""
          }
        </div>
        <div class="mt-1 text-start">${issue.message}</div>
      `;
      row.addEventListener("click", () => this.focusValidationIssue(index));
      listEl.appendChild(row);
    });
  }

  focusValidationIssue(index) {
    if (!this.validationIssues.length) {
      this.updateValidationPanel();
      if (!this.validationIssues.length) {
        return;
      }
    }

    const normalizedIndex =
      ((index % this.validationIssues.length) + this.validationIssues.length) %
      this.validationIssues.length;
    this.validationCursor = normalizedIndex;
    const issue = this.validationIssues[normalizedIndex];
    const targetFrame = Number.isInteger(issue.frameIndex)
      ? issue.frameIndex
      : Number.isInteger(issue.startFrame)
        ? issue.startFrame
        : null;

    if (this.kind === "video" && Number.isInteger(targetFrame)) {
      this.seekToFrame(targetFrame);
    }

    if (Number.isInteger(issue.trackId)) {
      this.restoreActiveAnnotationForTrack(issue.trackId);
    } else {
      const focusClientUid =
        issue.clientUid ||
        (Array.isArray(issue.relatedClientUids)
          ? issue.relatedClientUids[0]
          : null);
      if (focusClientUid) {
        const activeAnnotation =
          this.annotations.find(
            (annotation) => annotation.client_uid === focusClientUid
          ) || null;
        if (activeAnnotation) {
          this.markActiveAnnotation(activeAnnotation);
        } else {
          this.requestRedraw();
        }
      } else {
        this.requestRedraw();
      }
    }

    this.updateValidationPanel();
    window.requestAnimationFrame(() => {
      const row = document.querySelector(
        `[data-validation-issue-index="${normalizedIndex}"]`
      );
      row?.scrollIntoView({ block: "nearest" });
    });
  }

  focusValidationIssueByDirection(direction) {
    this.updateValidationPanel();
    if (!this.validationIssues.length) {
      return;
    }

    if (this.validationCursor < 0) {
      this.focusValidationIssue(direction < 0 ? this.validationIssues.length - 1 : 0);
      return;
    }

    this.focusValidationIssue(this.validationCursor + direction);
  }

  handleSubmitForReviewForm(event) {
    this.updateValidationPanel();

    const errors = this.validationIssues.filter(
      (issue) => issue.severity === "error"
    );
    const warnings = this.validationIssues.filter(
      (issue) => issue.severity === "warning"
    );

    if (errors.length) {
      event.preventDefault();
      this.focusValidationIssue(0);
      window.alert(
        `${errors.length} validation error(s) must be fixed before submitting for review.`
      );
      return;
    }

    if (warnings.length) {
      const shouldSubmit = window.confirm(
        `${warnings.length} validation warning(s) were found. Submit for review anyway?`
      );
      if (!shouldSubmit) {
        event.preventDefault();
        this.focusValidationIssue(0);
      }
    }
  }

  getPreviousReferenceFrameIndex() {
    if (this.kind !== "video") return null;
    return this.findPreviousFrameIndex(this.currentFrameIndex);
  }

  drawPreviousFrameOverlay(scale) {
    if (!this.showPreviousFrameOverlay || this.kind !== "video") return;

    const referenceFrameIndex = this.getPreviousReferenceFrameIndex();
    if (referenceFrameIndex == null) return;

    const referenceAnnotations = this.buildAnnotationsForFrame(referenceFrameIndex);
    if (!referenceAnnotations.length) return;

    const worldLineWidth = 1.5 / scale;

    for (const annotation of referenceAnnotations) {
      if (!this.isAnnotationVisible(annotation)) continue;

      const color = this.getLabelClassColor(annotation.label_class_id);
      const geometryKind = this.getGeometryKindForLabel(annotation.label_class_id);
      const adapter = getGeometryAdapter(geometryKind);

      this.ctx.save();
      this.ctx.globalAlpha = 0.22;
      adapter.draw({
        ctx: this.ctx,
        annotation,
        isActive: false,
        color,
        worldLineWidthActive: worldLineWidth,
        worldLineWidthNormal: worldLineWidth,
        worldHandleRadius: 0,
        worldHandleBorderWidth: 0,
        viewScale: scale,
        activeVertexIndex: null,
      });
      this.ctx.restore();
    }
  }

  getAdjacentTrackKeyframe(trackId, direction) {
    if (!Number.isInteger(trackId)) return null;

    const keyframes = this.getTrackKeyframes(trackId);
    if (!keyframes.length) return null;

    if (direction < 0) {
      return (
        [...keyframes]
          .reverse()
          .find((frameIndex) => frameIndex < this.currentFrameIndex) ?? null
      );
    }

    return keyframes.find((frameIndex) => frameIndex > this.currentFrameIndex) ?? null;
  }

  jumpToActiveTrackKeyframe(direction) {
    if (this.kind !== "video" || !Number.isInteger(this.activeTrackId)) return;

    const trackId = this.activeTrackId;
    const targetFrame = this.getAdjacentTrackKeyframe(trackId, direction);
    if (targetFrame == null) return;

    this.seekToFrame(targetFrame);
    this.restoreActiveAnnotationForTrack(trackId);
  }

  getActiveTrackReassignmentRange() {
    if (this.kind !== "video" || !Number.isInteger(this.activeTrackId)) {
      return null;
    }

    const selectedRange = this.getSelectedTrackFrameRange();
    if (selectedRange) {
      return selectedRange;
    }

    const trackRange = this.getTrackFrameRange(this.activeTrackId);
    if (!trackRange) {
      return null;
    }

    const currentFrame = this.currentFrameIndex | 0;
    if (currentFrame < trackRange.start || currentFrame > trackRange.end) {
      return null;
    }

    return {
      trackId: this.activeTrackId,
      startFrame: currentFrame,
      endFrame: currentFrame,
    };
  }

  canArmReassignSelection() {
    const selection = this.getActiveTrackReassignmentRange();
    return (
      !!selection &&
      !this.readOnly &&
      !this.isTrackLocked(selection.trackId)
    );
  }

  toggleReassignSelectionMode() {
    if (!this.canArmReassignSelection()) {
      return;
    }

    const selection = this.getActiveTrackReassignmentRange();
    if (!selection) {
      return;
    }

    const sameSelection =
      this.pendingReassignSelection &&
      this.pendingReassignSelection.trackId === selection.trackId &&
      this.pendingReassignSelection.startFrame === selection.startFrame &&
      this.pendingReassignSelection.endFrame === selection.endFrame;

    if (sameSelection) {
      this.pendingReassignSelection = null;
    } else {
      this.pendingMergeTargetTrackId = null;
      this.pendingReassignSelection = {
        trackId: selection.trackId,
        startFrame: selection.startFrame,
        endFrame: selection.endFrame,
      };
    }

    this.updateReferenceControls();
    this.requestRedraw(false);
  }

  canReassignSelectionToTarget(targetTrackId) {
    const selection = this.pendingReassignSelection;
    if (
      !selection ||
      !Number.isInteger(targetTrackId) ||
      targetTrackId === selection.trackId
    ) {
      return false;
    }

    if (
      this.isTrackLocked(targetTrackId) ||
      this.isTrackLocked(selection.trackId)
    ) {
      return false;
    }

    const sourceAnnotations = this.getTrackSparseAnnotations(selection.trackId);
    const targetAnnotations = this.getTrackSparseAnnotations(targetTrackId);
    if (!sourceAnnotations.length || !targetAnnotations.length) {
      return false;
    }

    const sourceLabelClassId = sourceAnnotations[0].label_class_id;
    const targetLabelClassId = targetAnnotations[0].label_class_id;
    if (sourceLabelClassId !== targetLabelClassId) {
      return false;
    }

    const sourceGeometry = this.getGeometryKindForLabel(sourceLabelClassId);
    const targetGeometry = this.getGeometryKindForLabel(targetLabelClassId);
    if (sourceGeometry !== targetGeometry) {
      return false;
    }

    const targetSegments = this.getTrackSegments(targetTrackId);
    return !targetSegments.some(
      (segment) =>
        selection.startFrame <= segment.endFrame &&
        selection.endFrame >= segment.startFrame
    );
  }

  reassignSelectionToTarget(targetTrackId) {
    const selection = this.pendingReassignSelection;
    if (!selection) {
      return;
    }

    if (!this.canReassignSelectionToTarget(targetTrackId)) {
      window.alert(
        "Reassignment requires a target object with the same label and geometry, and the target must not already overlap the selected frames."
      );
      return;
    }

    const { trackId: sourceTrackId, startFrame, endFrame } = selection;
    const sourceSegments = this.getTrackSegments(sourceTrackId);
    const targetSegments = this.getTrackSegments(targetTrackId);
    const sourceKeyframes = this.getTrackKeyframes(sourceTrackId);
    const targetKeyframes = this.getTrackKeyframes(targetTrackId);
    const remainingSourceSegments = [];
    const movedSegments = [];

    sourceSegments.forEach((segment) => {
      if (segment.endFrame < startFrame || segment.startFrame > endFrame) {
        remainingSourceSegments.push(segment);
        return;
      }

      if (segment.startFrame < startFrame) {
        remainingSourceSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            startFrame - 1
          )
        );
      }

      movedSegments.push(
        this.createSegment(
          segment.annotation,
          Math.max(segment.startFrame, startFrame),
          Math.min(segment.endFrame, endFrame)
        )
      );

      if (segment.endFrame > endFrame) {
        remainingSourceSegments.push(
          this.createSegment(
            segment.annotation,
            endFrame + 1,
            segment.endFrame
          )
        );
      }
    });

    if (!movedSegments.length) {
      window.alert("No track data was found in the selected range.");
      return;
    }

    if (remainingSourceSegments.length) {
      this.setTrackSegments(sourceTrackId, remainingSourceSegments);
      this.setTrackKeyframes(sourceTrackId, [
        ...sourceKeyframes.filter(
          (frameIndex) => frameIndex < startFrame || frameIndex > endFrame
        ),
        ...remainingSourceSegments.map((segment) => segment.startFrame),
      ]);
    } else {
      this.clearTrackSparseAnnotations(sourceTrackId);
      this.manualKeyframesByTrack.delete(sourceTrackId);
      this.trackViewStateById.delete(sourceTrackId);
      if (this.soloTrackId === sourceTrackId) {
        this.soloTrackId = null;
      }
    }

    this.setTrackSegments(targetTrackId, [...targetSegments, ...movedSegments]);
    this.setTrackKeyframes(targetTrackId, [
      ...targetKeyframes,
      ...sourceKeyframes.filter(
        (frameIndex) => frameIndex >= startFrame && frameIndex <= endFrame
      ),
      ...movedSegments.map((segment) => segment.startFrame),
    ]);

    this.pendingReassignSelection = null;
    this.persistManualKeyframesToStorage();
    this.persistTrackUiStateToStorage();

    if (
      this.interpolationSelection.trackId === sourceTrackId ||
      this.interpolationSelection.trackId === targetTrackId
    ) {
      this.interpolationSelection = {
        trackId: targetTrackId,
        startFrame: null,
        endFrame: null,
      };
    }

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.restoreActiveAnnotationForTrack(targetTrackId);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  getSelectedTrackFrameRange() {
    if (this.kind !== "video" || !Number.isInteger(this.activeTrackId)) {
      return null;
    }

    if (this.interpolationSelection.trackId !== this.activeTrackId) {
      return null;
    }

    let { startFrame, endFrame } = this.interpolationSelection;
    if (!Number.isInteger(startFrame) || !Number.isInteger(endFrame)) {
      return null;
    }
    if (startFrame === endFrame) {
      return null;
    }
    if (startFrame > endFrame) {
      [startFrame, endFrame] = [endFrame, startFrame];
    }

    return {
      trackId: this.activeTrackId,
      startFrame,
      endFrame,
    };
  }

  togglePreviousFrameOverlay(forceValue = null) {
    if (this.kind !== "video") return;

    const nextValue =
      forceValue == null ? !this.showPreviousFrameOverlay : !!forceValue;
    if (nextValue && this.getPreviousReferenceFrameIndex() == null) {
      return;
    }

    this.pendingReassignSelection = null;
    this.showPreviousFrameOverlay = nextValue;
    this.updateReferenceControls();
    this.requestRedraw(false);
  }

  canDeleteSelectedRange() {
    const selectedRange = this.getSelectedTrackFrameRange();
    return (
      !!selectedRange &&
      !this.readOnly &&
      !this.isTrackLocked(selectedRange.trackId)
    );
  }

  deleteActiveTrackSelectedRange() {
    const selectedRange = this.getSelectedTrackFrameRange();
    if (!selectedRange) {
      window.alert("Select A and B on the active object first.");
      return;
    }
    if (this.readOnly || this.isTrackLocked(selectedRange.trackId)) {
      return;
    }

    const { trackId, startFrame, endFrame } = selectedRange;
    const originalSegments = this.getTrackSegments(trackId);
    const originalKeyframes = this.getTrackKeyframes(trackId);
    const nextSegments = [];

    originalSegments.forEach((segment) => {
      if (segment.endFrame < startFrame || segment.startFrame > endFrame) {
        nextSegments.push(segment);
        return;
      }

      if (segment.startFrame < startFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            startFrame - 1
          )
        );
      }

      if (segment.endFrame > endFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            endFrame + 1,
            segment.endFrame
          )
        );
      }
    });

    this.pendingReassignSelection = null;
    this.pendingMergeTargetTrackId = null;

    if (!nextSegments.length) {
      this.interpolationSelection = {
        trackId: null,
        startFrame: null,
        endFrame: null,
      };
      this.deleteTrack(trackId);
      return;
    }

    this.setTrackSegments(trackId, nextSegments);
    this.setTrackKeyframes(trackId, [
      ...originalKeyframes.filter(
        (frameIndex) => frameIndex < startFrame || frameIndex > endFrame
      ),
      ...nextSegments.map((segment) => segment.startFrame),
    ]);

    this.interpolationSelection = {
      trackId,
      startFrame: null,
      endFrame: null,
    };
    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.restoreActiveAnnotationForTrack(trackId);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  canApplyCurrentFlagsToSelectedRange() {
    const selectedRange = this.getSelectedTrackFrameRange();
    return (
      !!selectedRange &&
      !this.readOnly &&
      !!this.activeAnnotation &&
      this.activeAnnotation.track_id === selectedRange.trackId &&
      !this.isTrackLocked(selectedRange.trackId)
    );
  }

  applyCurrentFlagsToSelectedRange() {
    const selectedRange = this.getSelectedTrackFrameRange();
    if (!selectedRange) {
      window.alert("Select A and B on the active object first.");
      return;
    }
    if (
      this.readOnly ||
      !this.activeAnnotation ||
      this.activeAnnotation.track_id !== selectedRange.trackId ||
      this.isTrackLocked(selectedRange.trackId)
    ) {
      return;
    }

    const { trackId, startFrame, endFrame } = selectedRange;
    const nextFlags = cloneAnnotationFlags(this.activeAnnotation);
    const originalSegments = this.getTrackSegments(trackId);
    const originalKeyframes = this.getTrackKeyframes(trackId);
    const nextSegments = [];

    originalSegments.forEach((segment) => {
      if (segment.endFrame < startFrame || segment.startFrame > endFrame) {
        nextSegments.push(segment);
        return;
      }

      if (segment.startFrame < startFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            startFrame - 1
          )
        );
      }

      nextSegments.push(
        this.createSegment(
          {
            ...segment.annotation,
            ...nextFlags,
          },
          Math.max(segment.startFrame, startFrame),
          Math.min(segment.endFrame, endFrame)
        )
      );

      if (segment.endFrame > endFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            endFrame + 1,
            segment.endFrame
          )
        );
      }
    });

    this.pendingReassignSelection = null;
    this.pendingMergeTargetTrackId = null;
    this.setTrackSegments(trackId, nextSegments);
    this.setTrackKeyframes(trackId, [
      ...originalKeyframes,
      startFrame,
      endFrame,
      this.currentFrameIndex,
    ]);
    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.restoreActiveAnnotationForTrack(trackId);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  canSplitTrackAtCurrentFrame(trackId = this.activeTrackId) {
    if (!Number.isInteger(trackId) || this.kind !== "video") return false;

    const trackRange = this.getTrackFrameRange(trackId);
    if (!trackRange) return false;

    return (
      this.currentFrameIndex > trackRange.start &&
      this.currentFrameIndex <= trackRange.end
    );
  }

  canArmMergeTrack() {
    return (
      !this.readOnly &&
      this.kind === "video" &&
      Number.isInteger(this.activeTrackId) &&
      !this.isTrackLocked(this.activeTrackId) &&
      this.getTrackSparseAnnotations(this.activeTrackId).length > 0
    );
  }

  toggleMergeSelectionMode() {
    if (!this.canArmMergeTrack()) {
      return;
    }

    if (this.pendingMergeTargetTrackId === this.activeTrackId) {
      this.pendingMergeTargetTrackId = null;
    } else {
      this.pendingMergeTargetTrackId = this.activeTrackId;
    }

    this.updateReferenceControls();
    this.requestRedraw(false);
  }

  canMergeTrackIntoTarget(targetTrackId, sourceTrackId) {
    if (
      !Number.isInteger(targetTrackId) ||
      !Number.isInteger(sourceTrackId) ||
      targetTrackId === sourceTrackId
    ) {
      return false;
    }

    const targetAnnotations = this.getTrackSparseAnnotations(targetTrackId);
    const sourceAnnotations = this.getTrackSparseAnnotations(sourceTrackId);
    if (!targetAnnotations.length || !sourceAnnotations.length) {
      return false;
    }

    const targetLabelClassId = targetAnnotations[0].label_class_id;
    const sourceLabelClassId = sourceAnnotations[0].label_class_id;
    if (targetLabelClassId !== sourceLabelClassId) {
      return false;
    }

    const targetGeometry = this.getGeometryKindForLabel(targetLabelClassId);
    const sourceGeometry = this.getGeometryKindForLabel(sourceLabelClassId);
    if (targetGeometry !== sourceGeometry) {
      return false;
    }

    const targetSegments = this.getTrackSegments(targetTrackId);
    const sourceSegments = this.getTrackSegments(sourceTrackId);

    for (const targetSegment of targetSegments) {
      for (const sourceSegment of sourceSegments) {
        const overlaps =
          sourceSegment.startFrame <= targetSegment.endFrame &&
          sourceSegment.endFrame >= targetSegment.startFrame;
        if (overlaps) {
          return false;
        }
      }
    }

    return true;
  }

  mergeTrackIntoTarget(targetTrackId, sourceTrackId) {
    if (this.readOnly) return;

    if (!this.canMergeTrackIntoTarget(targetTrackId, sourceTrackId)) {
      window.alert(
        "Tracks can be merged only when they share the same label and do not overlap in time."
      );
      return;
    }

    this.pendingReassignSelection = null;
    if (this.isTrackLocked(targetTrackId) || this.isTrackLocked(sourceTrackId)) {
      return;
    }

    const targetSegments = this.getTrackSegments(targetTrackId);
    const sourceSegments = this.getTrackSegments(sourceTrackId);
    const targetKeyframes = this.getTrackKeyframes(targetTrackId);
    const sourceKeyframes = this.getTrackKeyframes(sourceTrackId);

    this.setTrackSegments(targetTrackId, [...targetSegments, ...sourceSegments]);
    this.setTrackKeyframes(targetTrackId, [
      ...targetKeyframes,
      ...sourceKeyframes,
    ]);

    this.clearTrackSparseAnnotations(sourceTrackId);
    this.manualKeyframesByTrack.delete(sourceTrackId);
    this.trackViewStateById.delete(sourceTrackId);
    if (this.soloTrackId === sourceTrackId) {
      this.soloTrackId = null;
    }

    this.pendingMergeTargetTrackId = null;
    this.persistManualKeyframesToStorage();
    this.persistTrackUiStateToStorage();

    if (
      this.interpolationSelection.trackId === sourceTrackId ||
      this.interpolationSelection.trackId === targetTrackId
    ) {
      this.interpolationSelection = {
        trackId: targetTrackId,
        startFrame: null,
        endFrame: null,
      };
    }

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.restoreActiveAnnotationForTrack(targetTrackId);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  splitActiveTrackAtCurrentFrame() {
    if (
      this.readOnly ||
      this.kind !== "video" ||
      !Number.isInteger(this.activeTrackId)
    ) {
      return;
    }

    const trackId = this.activeTrackId;
    if (this.isTrackLocked(trackId)) return;
    if (!this.canSplitTrackAtCurrentFrame(trackId)) {
      window.alert(
        "Move to a frame inside the active object and not at its first frame."
      );
      return;
    }

    const currentFrame = this.currentFrameIndex | 0;
    const originalSegments = this.getTrackSegments(trackId);
    const originalKeyframes = this.getTrackKeyframes(trackId);
    const oldSegments = [];
    const newSegments = [];

    originalSegments.forEach((segment) => {
      if (segment.endFrame < currentFrame) {
        oldSegments.push(segment);
        return;
      }

      if (segment.startFrame >= currentFrame) {
        newSegments.push(segment);
        return;
      }

      oldSegments.push(
        this.createSegment(
          segment.annotation,
          segment.startFrame,
          currentFrame - 1
        )
      );
      newSegments.push(
        this.createSegment(segment.annotation, currentFrame, segment.endFrame)
      );
    });

    if (!oldSegments.length || !newSegments.length) {
      window.alert("The active object cannot be split at the current frame.");
      return;
    }

    this.pendingMergeTargetTrackId = null;
    const newTrackId = this.nextTrackId++;
    this.setTrackSegments(trackId, oldSegments);
    this.setTrackSegments(newTrackId, newSegments);

    this.setTrackKeyframes(
      trackId,
      originalKeyframes.filter((frameIndex) => frameIndex < currentFrame)
    );
    this.setTrackKeyframes(newTrackId, [
      ...originalKeyframes.filter((frameIndex) => frameIndex >= currentFrame),
      currentFrame,
    ]);

    const existingViewState = { ...this.getTrackViewState(trackId) };
    if (existingViewState.hidden || existingViewState.locked) {
      this.trackViewStateById.set(newTrackId, {
        hidden: !!existingViewState.hidden,
        locked: false,
      });
    }
    if (this.soloTrackId === trackId) {
      this.soloTrackId = null;
    }
    if (
      this.pendingReassignSelection &&
      this.pendingReassignSelection.trackId === trackId
    ) {
      this.pendingReassignSelection = null;
    }
    if (this.pendingMergeTargetTrackId === trackId) {
      this.pendingMergeTargetTrackId = null;
    }
    this.hideObjectContextMenu();
    this.persistTrackUiStateToStorage();

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.setActiveTrackId(newTrackId);
    this.restoreActiveAnnotationForTrack(newTrackId);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  canApplySelectedLabelToActiveObject() {
    if (!this.currentLabelClassId) return false;

    const nextGeometry = this.getGeometryKindForLabel(this.currentLabelClassId);

    if (this.kind === "video" && Number.isInteger(this.activeTrackId)) {
      const trackAnnotations = this.getTrackSparseAnnotations(this.activeTrackId);
      if (!trackAnnotations.length) return false;
      return (
        this.getGeometryKindForLabel(trackAnnotations[0].label_class_id) ===
        nextGeometry
      );
    }

    if (!this.activeAnnotation) return false;
    return (
      this.getGeometryKindForLabel(this.activeAnnotation.label_class_id) ===
      nextGeometry
    );
  }

  canApplySelectedLabelToSelectedRange() {
    const selectedRange = this.getSelectedTrackFrameRange();
    if (
      !selectedRange ||
      !this.currentLabelClassId ||
      this.readOnly ||
      this.isTrackLocked(selectedRange.trackId)
    ) {
      return false;
    }

    const trackAnnotations = this.getTrackSparseAnnotations(selectedRange.trackId);
    if (!trackAnnotations.length) {
      return false;
    }

    return (
      this.getGeometryKindForLabel(trackAnnotations[0].label_class_id) ===
      this.getGeometryKindForLabel(this.currentLabelClassId)
    );
  }

  applySelectedLabelToSelectedRange() {
    const selectedRange = this.getSelectedTrackFrameRange();
    if (!selectedRange) {
      window.alert("Select A and B on the active object first.");
      return;
    }

    if (!this.canApplySelectedLabelToSelectedRange()) {
      window.alert(
        "The selected label must use the same geometry as the selected range."
      );
      return;
    }

    const { trackId, startFrame, endFrame } = selectedRange;
    const originalSegments = this.getTrackSegments(trackId);
    const originalKeyframes = this.getTrackKeyframes(trackId);
    const nextSegments = [];

    originalSegments.forEach((segment) => {
      if (segment.endFrame < startFrame || segment.startFrame > endFrame) {
        nextSegments.push(segment);
        return;
      }

      if (segment.startFrame < startFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            startFrame - 1
          )
        );
      }

      nextSegments.push(
        this.createSegment(
          {
            ...segment.annotation,
            label_class_id: this.currentLabelClassId,
          },
          Math.max(segment.startFrame, startFrame),
          Math.min(segment.endFrame, endFrame)
        )
      );

      if (segment.endFrame > endFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            endFrame + 1,
            segment.endFrame
          )
        );
      }
    });

    this.pendingReassignSelection = null;
    this.pendingMergeTargetTrackId = null;
    this.setTrackSegments(trackId, nextSegments);
    this.setTrackKeyframes(trackId, [
      ...originalKeyframes,
      startFrame,
      endFrame,
    ]);
    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.restoreActiveAnnotationForTrack(trackId);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  applySelectedLabelToActiveObject() {
    if (this.readOnly || !this.currentLabelClassId) return;

    if (!this.canApplySelectedLabelToActiveObject()) {
      window.alert(
        "The selected label must use the same geometry as the active object."
      );
      return;
    }

    this.pendingReassignSelection = null;
    if (this.kind === "video" && Number.isInteger(this.activeTrackId)) {
      const trackId = this.activeTrackId;
      this.pendingMergeTargetTrackId = null;
      this.getTrackSparseAnnotations(trackId).forEach((annotation) => {
        annotation.label_class_id = this.currentLabelClassId;
      });
      this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
      this.restoreActiveAnnotationForTrack(trackId);
    } else if (this.activeAnnotation) {
      const storedAnnotation =
        this.kind === "video"
          ? this.activeAnnotation._storedAnnotation || this.activeAnnotation
          : this.activeAnnotation;
      storedAnnotation.label_class_id = this.currentLabelClassId;
      this.requestRedraw();
    } else {
      return;
    }

    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  updateReferenceControls() {
    const overlayBtn = document.getElementById("btn-toggle-prev-overlay");
    const prevKeyframeBtn = document.getElementById("btn-prev-keyframe");
    const nextKeyframeBtn = document.getElementById("btn-next-keyframe");
    const summaryEl = document.getElementById("reference-overlay-summary");
    const splitBtn = document.getElementById("btn-split-track");
    const reassignBtn = document.getElementById("btn-arm-reassign-track");
    const mergeBtn = document.getElementById("btn-arm-merge-track");
    const relabelBtn = document.getElementById("btn-apply-label-track");
    const relabelRangeBtn = document.getElementById("btn-apply-label-range");
    const deleteRangeBtn = document.getElementById("btn-delete-range");
    const applyRangeFlagsBtn = document.getElementById(
      "btn-apply-range-flags"
    );
    const trackEditSummaryEl = document.getElementById("track-edit-summary");

    const previousFrameIndex = this.getPreviousReferenceFrameIndex();
    const prevKeyframe = this.getAdjacentTrackKeyframe(this.activeTrackId, -1);
    const nextKeyframe = this.getAdjacentTrackKeyframe(this.activeTrackId, 1);
    const reassignSelection = this.pendingReassignSelection;
    const selectedRange = this.getSelectedTrackFrameRange();

    if (overlayBtn) {
      overlayBtn.classList.toggle("active", this.showPreviousFrameOverlay);
      overlayBtn.disabled =
        this.kind !== "video" ||
        (!this.showPreviousFrameOverlay && previousFrameIndex == null);
    }

    if (prevKeyframeBtn) {
      prevKeyframeBtn.disabled = prevKeyframe == null;
    }
    if (nextKeyframeBtn) {
      nextKeyframeBtn.disabled = nextKeyframe == null;
    }

    if (summaryEl) {
      if (this.kind !== "video") {
        summaryEl.textContent =
          "Frame reference tools are available for video items only.";
      } else {
        const overlayLabel =
          previousFrameIndex == null
            ? "No previous annotated frame is available."
            : `Ghost overlay source: frame ${previousFrameIndex + 1}.`;

        if (!Number.isInteger(this.activeTrackId)) {
          summaryEl.textContent = `${overlayLabel} Select an object to jump across keyframes.`;
        } else {
          summaryEl.textContent = `${overlayLabel} Active object ${
            this.activeTrackId
          } · prev ${prevKeyframe == null ? "–" : prevKeyframe + 1} · next ${
            nextKeyframe == null ? "–" : nextKeyframe + 1
          }.`;
        }
      }
    }

    if (splitBtn) {
      splitBtn.disabled =
        this.readOnly ||
        !this.canSplitTrackAtCurrentFrame(this.activeTrackId) ||
        this.isTrackLocked(this.activeTrackId);
    }
    if (reassignBtn) {
      reassignBtn.disabled = !this.canArmReassignSelection();
      reassignBtn.classList.toggle(
        "active",
        !!reassignSelection &&
          Number.isInteger(this.activeTrackId) &&
          reassignSelection.trackId === this.activeTrackId &&
          reassignSelection.startFrame ===
            (selectedRange?.startFrame ?? this.currentFrameIndex) &&
          reassignSelection.endFrame ===
            (selectedRange?.endFrame ?? this.currentFrameIndex)
      );
    }
    if (mergeBtn) {
      mergeBtn.disabled = !this.canArmMergeTrack();
      mergeBtn.classList.toggle(
        "active",
        Number.isInteger(this.activeTrackId) &&
          this.pendingMergeTargetTrackId === this.activeTrackId
      );
    }
    if (relabelBtn) {
      relabelBtn.disabled =
        this.readOnly || !this.canApplySelectedLabelToActiveObject();
    }
    if (relabelRangeBtn) {
      relabelRangeBtn.disabled = !this.canApplySelectedLabelToSelectedRange();
    }
    if (deleteRangeBtn) {
      deleteRangeBtn.disabled = !this.canDeleteSelectedRange();
    }
    if (applyRangeFlagsBtn) {
      applyRangeFlagsBtn.disabled = !this.canApplyCurrentFlagsToSelectedRange();
    }

    if (trackEditSummaryEl) {
      if (!Number.isInteger(this.activeTrackId)) {
        trackEditSummaryEl.textContent =
          "Select an active object to split it, arm merge mode, relabel it, or edit an A/B-selected range.";
      } else if (reassignSelection) {
        const rangeLabel =
          reassignSelection.startFrame === reassignSelection.endFrame
            ? `frame ${reassignSelection.startFrame + 1}`
            : `frames ${reassignSelection.startFrame + 1}–${
                reassignSelection.endFrame + 1
              }`;
        trackEditSummaryEl.textContent = `Reassign armed: move ${rangeLabel} from Obj ${reassignSelection.trackId} into another object with the same label by clicking the target object.`;
      } else if (
        Number.isInteger(this.pendingMergeTargetTrackId) &&
        this.pendingMergeTargetTrackId === this.activeTrackId
      ) {
        trackEditSummaryEl.textContent = `Obj ${this.activeTrackId} is the merge target. Click another object to merge it into Obj ${this.activeTrackId}.`;
      } else if (selectedRange) {
        trackEditSummaryEl.textContent = `Selected range: frame ${
          selectedRange.startFrame + 1
        }–${selectedRange.endFrame + 1} on Obj ${
          selectedRange.trackId
        }. You can delete that range or apply the current annotation tags across it.`;
      } else if (this.isTrackLocked(this.activeTrackId)) {
        trackEditSummaryEl.textContent = `Obj ${this.activeTrackId} is locked. Unlock it before splitting or relabeling.`;
      } else {
        trackEditSummaryEl.textContent = `Obj ${this.activeTrackId} · current frame ${
          this.currentFrameIndex + 1
        } · split keeps frames before the cursor on the current track. Use A/B to target a range.`;
      }
    }
  }

  syncFixedBBoxControls() {
    const toggle = document.getElementById("fixed-bbox-toggle");
    const widthInput = document.getElementById("fixed-bbox-width");
    const heightInput = document.getElementById("fixed-bbox-height");
    if (toggle) {
      toggle.checked = !!this.useFixedSizeBBox;
      toggle.disabled = this.readOnly;
    }
    if (widthInput) {
      widthInput.disabled = this.readOnly || !this.useFixedSizeBBox;
      widthInput.value = String(this.fixedBBoxWidth);
    }
    if (heightInput) {
      heightInput.disabled = this.readOnly || !this.useFixedSizeBBox;
      heightInput.value = String(this.fixedBBoxHeight);
    }
  }

  getTrackUiStorageKey() {
    return `vision-forge:item:${this.itemId}:track-ui`;
  }

  loadTrackUiStateFromStorage() {
    this.trackViewStateById = new Map();
    this.soloTrackId = null;

    if (typeof window === "undefined" || !window.localStorage) return;

    try {
      window.localStorage.removeItem(this.getTrackUiStorageKey());
    } catch (_error) {
      // ignore storage failures
    }
  }

  persistTrackUiStateToStorage() {
    if (typeof window === "undefined" || !window.localStorage) return;
    try {
      window.localStorage.removeItem(this.getTrackUiStorageKey());
    } catch (_error) {
      // ignore storage failures
    }
  }

  normalizeTrackUiState() {
    this.trackViewStateById.clear();
    this.soloTrackId = null;
    this.persistTrackUiStateToStorage();
  }

  getAnnotationLabelVisibilityStorageKey() {
    return "vision-forge:annotation:show-canvas-labels";
  }

  loadAnnotationLabelVisibilityFromStorage() {
    if (typeof window === "undefined" || !window.localStorage) {
      return false;
    }

    try {
      const rawValue = window.localStorage.getItem(
        this.getAnnotationLabelVisibilityStorageKey()
      );
      if (rawValue == null) {
        return false;
      }
      return rawValue === "1";
    } catch (_error) {
      return false;
    }
  }

  persistAnnotationLabelVisibilityToStorage() {
    if (typeof window === "undefined" || !window.localStorage) {
      return;
    }

    try {
      window.localStorage.setItem(
        this.getAnnotationLabelVisibilityStorageKey(),
        this.showAnnotationLabels ? "1" : "0"
      );
    } catch (_error) {
      // ignore storage failures
    }
  }

  syncAnnotationLabelVisibilityControl() {
    const toggle = document.getElementById("toggle-object-labels");
    if (!toggle) {
      return;
    }
    toggle.checked = !!this.showAnnotationLabels;
  }

  toggleAnnotationLabelVisibility(forceValue = null) {
    const nextValue =
      forceValue == null ? !this.showAnnotationLabels : !!forceValue;
    if (nextValue === this.showAnnotationLabels) {
      this.syncAnnotationLabelVisibilityControl();
      return;
    }

    this.showAnnotationLabels = nextValue;
    this.persistAnnotationLabelVisibilityToStorage();
    this.syncAnnotationLabelVisibilityControl();
    this.requestRedraw(false);
  }

  getTrackViewState(trackId) {
    return this.trackViewStateById.get(trackId) || { locked: false, hidden: false };
  }

  isTrackLocked(trackId) {
    return !this.readOnly && Number.isInteger(trackId) && !!this.getTrackViewState(trackId).locked;
  }

  isTrackHidden(trackId) {
    return !this.readOnly && Number.isInteger(trackId) && !!this.getTrackViewState(trackId).hidden;
  }

  isTrackVisible(trackId) {
    if (!Number.isInteger(trackId)) return !Number.isInteger(this.soloTrackId);
    if (this.isTrackHidden(trackId)) return false;
    if (Number.isInteger(this.soloTrackId)) return this.soloTrackId === trackId;
    return true;
  }

  getMutableAnnotationTarget(annotation) {
    if (!annotation) return null;
    if (this.kind === "video" && annotation._storedAnnotation) {
      return annotation._storedAnnotation;
    }
    return annotation;
  }

  isAnnotationHidden(annotation) {
    const target = this.getMutableAnnotationTarget(annotation);
    if (!target) return false;
    if (Number.isInteger(target.track_id)) {
      return this.isTrackHidden(target.track_id);
    }
    return !!target.client_uid && this.hiddenAnnotationClientUids.has(target.client_uid);
  }

  revealAnnotation(annotation) {
    const target = this.getMutableAnnotationTarget(annotation);
    if (!target) return;

    if (Number.isInteger(target.track_id)) {
      this.revealTrack(target.track_id);
      return;
    }

    if (target.client_uid) {
      this.hiddenAnnotationClientUids.delete(target.client_uid);
    }
  }

  pruneHiddenAnnotationState() {
    const liveClientUids = new Set(
      this.getSparseAnnotations()
        .map((annotation) => annotation.client_uid)
        .filter((clientUid) => !!clientUid)
    );

    this.hiddenAnnotationClientUids.forEach((clientUid) => {
      if (!liveClientUids.has(clientUid)) {
        this.hiddenAnnotationClientUids.delete(clientUid);
      }
    });
  }

  isAnnotationVisible(annotation) {
    if (!annotation) return false;
    if (this.isAnnotationHidden(annotation)) return false;
    if (annotation.track_id == null) return !Number.isInteger(this.soloTrackId);
    return this.isTrackVisible(annotation.track_id);
  }

  getTrackGaps(trackId) {
    if (!Number.isInteger(trackId)) return [];
    const segments = this.getTrackSegments(trackId).sort((a, b) => a.startFrame - b.startFrame);
    if (segments.length <= 1) return [];
    const gaps = [];
    for (let i = 1; i < segments.length; i += 1) {
      const prev = segments[i - 1];
      const cur = segments[i];
      if (cur.startFrame > prev.endFrame + 1) {
        gaps.push({ startFrame: prev.endFrame + 1, endFrame: cur.startFrame - 1 });
      }
    }
    return gaps;
  }

  getTrackReviewChangeState(trackId) {
    if (!Number.isInteger(trackId)) {
      return null;
    }

    const states = this.getTrackSparseAnnotations(trackId)
      .map((annotation) => getReviewChangeState(annotation))
      .filter(Boolean);

    if (states.includes("new")) {
      return "new";
    }
    if (states.includes("changed")) {
      return "changed";
    }
    if (states.includes("unchanged")) {
      return "unchanged";
    }
    return null;
  }

  getTrackAuditMetadata(trackId) {
    const sparseAnnotations = this.getTrackSparseAnnotations(trackId);
    if (!sparseAnnotations.length) {
      return null;
    }

    const sortedByCreated = [...sparseAnnotations].sort(
      (left, right) =>
        getAuditTimestampSortValue(left.created_at, Number.MAX_SAFE_INTEGER) -
        getAuditTimestampSortValue(right.created_at, Number.MAX_SAFE_INTEGER)
    );
    const sortedByUpdated = [...sparseAnnotations].sort(
      (left, right) =>
        getAuditTimestampSortValue(right.updated_at, Number.MIN_SAFE_INTEGER) -
        getAuditTimestampSortValue(left.updated_at, Number.MIN_SAFE_INTEGER)
    );

    const createdSource = sortedByCreated[0] || null;
    const updatedSource = sortedByUpdated[0] || createdSource;
    if (!createdSource && !updatedSource) {
      return null;
    }

    return {
      created_at: createdSource?.created_at ?? null,
      created_by: createdSource?.created_by ?? null,
      created_by_user: cloneAuditUser(createdSource?.created_by_user),
      updated_at: updatedSource?.updated_at ?? createdSource?.updated_at ?? null,
      updated_by:
        updatedSource?.updated_by ??
        createdSource?.updated_by ??
        createdSource?.created_by ??
        null,
      updated_by_user:
        cloneAuditUser(updatedSource?.updated_by_user) ||
        cloneAuditUser(createdSource?.updated_by_user) ||
        cloneAuditUser(createdSource?.created_by_user),
    };
  }

  jumpToAdjacentGap(direction) {
    if (!Number.isInteger(this.activeTrackId)) return;
    const gaps = this.getTrackGaps(this.activeTrackId);
    if (!gaps.length) return;
    const current = this.currentFrameIndex | 0;
    let target = null;
    if (direction < 0) {
      target = [...gaps].reverse().find((gap) => gap.endFrame < current) || gaps[gaps.length - 1];
    } else {
      target = gaps.find((gap) => gap.startFrame > current) || gaps[0];
    }
    if (!target) return;
    this.seekToFrame(target.startFrame);
  }

  toggleTrackLock(trackId) {
    if (this.readOnly || !Number.isInteger(trackId)) return;
    const nextState = { ...this.getTrackViewState(trackId) };
    nextState.locked = !nextState.locked;
    if (!nextState.locked && !nextState.hidden) this.trackViewStateById.delete(trackId);
    else this.trackViewStateById.set(trackId, nextState);
    this.persistTrackUiStateToStorage();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();
    this.requestRedraw();
  }

  toggleTrackHidden(trackId) {
    if (this.readOnly || !Number.isInteger(trackId)) return;
    const nextState = { ...this.getTrackViewState(trackId) };
    nextState.hidden = !nextState.hidden;
    if (nextState.hidden && this.soloTrackId === trackId) this.soloTrackId = null;
    if (!nextState.locked && !nextState.hidden) this.trackViewStateById.delete(trackId);
    else this.trackViewStateById.set(trackId, nextState);
    this.persistTrackUiStateToStorage();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();
    this.requestRedraw();
  }

  toggleTrackSolo(trackId) {
    if (this.readOnly || !Number.isInteger(trackId)) return;
    this.soloTrackId = this.soloTrackId === trackId ? null : trackId;
    this.persistTrackUiStateToStorage();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();
    this.requestRedraw();
  }

  revealTrack(trackId) {
    if (!Number.isInteger(trackId)) return;

    let changed = false;

    if (Number.isInteger(this.soloTrackId) && this.soloTrackId !== trackId) {
      this.soloTrackId = null;
      changed = true;
    }

    const state = this.trackViewStateById.get(trackId);
    if (state?.hidden) {
      const nextState = { ...state, hidden: false };
      if (!nextState.locked && !nextState.hidden) {
        this.trackViewStateById.delete(trackId);
      } else {
        this.trackViewStateById.set(trackId, nextState);
      }
      changed = true;
    }

    if (changed) {
      this.persistTrackUiStateToStorage();
    }
  }

  syncTrackVisibilityControls() {
    const hasActiveTrack = Number.isInteger(this.activeTrackId);
    const lockBtn = document.getElementById("btn-track-lock");
    const hideBtn = document.getElementById("btn-track-hide");
    const soloBtn = document.getElementById("btn-track-solo");
    const summaryEl = document.getElementById("active-track-state-summary");
    if (lockBtn) {
      lockBtn.disabled = this.readOnly || !hasActiveTrack;
      lockBtn.classList.toggle("active", hasActiveTrack && this.isTrackLocked(this.activeTrackId));
    }
    if (hideBtn) {
      hideBtn.disabled = this.readOnly || !hasActiveTrack;
      hideBtn.classList.toggle("active", hasActiveTrack && this.isTrackHidden(this.activeTrackId));
    }
    if (soloBtn) {
      soloBtn.disabled = this.readOnly || !hasActiveTrack;
      soloBtn.classList.toggle("active", hasActiveTrack && this.soloTrackId === this.activeTrackId);
    }
    if (summaryEl) {
      if (!hasActiveTrack) summaryEl.textContent = "Select a tracked object to lock, hide, solo, or jump to gaps.";
      else summaryEl.textContent = `Active object: Obj ${this.activeTrackId} · gaps ${this.getTrackGaps(this.activeTrackId).length}`;
    }
    this.updateReferenceControls();
  }

  syncAnnotationStateControls() {
    const activeAnnotation =
      this.activeAnnotation && this.isAnnotationVisible(this.activeAnnotation)
        ? this.activeAnnotation
        : null;
    const summaryEl = document.getElementById("annotation-state-summary");
    const bindings = [
      ["annotation-state-occluded", "is_occluded"],
      ["annotation-state-truncated", "is_truncated"],
      ["annotation-state-outside", "is_outside"],
      ["annotation-state-lost", "is_lost"],
    ];
    bindings.forEach(([id, key]) => {
      const input = document.getElementById(id);
      if (!input) return;
      const editable =
        !this.readOnly &&
        !!activeAnnotation &&
        !(activeAnnotation.track_id != null && this.isTrackLocked(activeAnnotation.track_id));
      input.disabled = !editable;
      input.checked = !!activeAnnotation?.[key];
    });
    if (!summaryEl) return;
    if (!activeAnnotation) {
      summaryEl.textContent = "Select a bbox or polygon to set annotation tags for that object only.";
      return;
    }
    const labels = getAnnotationFlagLabels(activeAnnotation);
    summaryEl.textContent = labels.length ? `Selected annotation tags: ${labels.join(", ")}` : "Selected annotation tags: visible";
  }

  toggleAnnotationVisibility(annotation) {
    if (this.readOnly) return false;

    const target = this.getMutableAnnotationTarget(annotation);
    if (!target) return false;

    if (Number.isInteger(target.track_id)) {
      this.toggleTrackHidden(target.track_id);
      return true;
    }

    const clientUid = this.ensureClientUid(target);
    if (!clientUid) return false;

    if (this.hiddenAnnotationClientUids.has(clientUid)) {
      this.hiddenAnnotationClientUids.delete(clientUid);
    } else {
      this.hiddenAnnotationClientUids.add(clientUid);
    }

    this.updateFastActionButtons();
    this.syncAnnotationStateControls();
    this.requestRedraw();
    return true;
  }

  updateAnnotationFlags(annotation, nextFlags) {
    if (this.readOnly) return false;

    const target = this.getMutableAnnotationTarget(annotation);
    if (!target) return false;
    if (Number.isInteger(target.track_id) && this.isTrackLocked(target.track_id)) {
      return false;
    }

    Object.assign(target, nextFlags);

    if (this.kind === "video") {
      this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
      const visibleAnnotation =
        this.findVisibleAnnotationByStoredAnnotation(target) || null;
      if (visibleAnnotation) {
        this.markActiveAnnotation(visibleAnnotation);
      } else {
        this.activeAnnotation = null;
        this.activeVertexIndex = null;
        this.requestRedraw();
      }

      if (this.timelineInitialized && this.totalFrames) {
        this.updateTimelineAnnotations();
        this.updateTimelinePlayhead();
      }
    } else {
      const visibleAnnotation =
        this.findVisibleAnnotationByStoredAnnotation(target) || target;
      this.markActiveAnnotation(visibleAnnotation);
    }

    this.pushHistoryCheckpoint();
    this.scheduleSave();
    return true;
  }

  toggleAnnotationFlag(flagKey, annotation = this.activeAnnotation) {
    const target = this.getMutableAnnotationTarget(annotation);
    if (!target) return false;
    return this.updateAnnotationFlags(annotation, {
      [flagKey]: !target[flagKey],
    });
  }

  ensureObjectContextMenu() {
    if (this.objectContextMenuEl) {
      return;
    }

    const menu =
      document.getElementById("annotation-object-context-menu") ||
      document.createElement("div");
    menu.id = "annotation-object-context-menu";
    menu.className = "annotation-context-menu";
    menu.hidden = true;
    menu.addEventListener("click", (event) => event.stopPropagation());
    menu.addEventListener("contextmenu", (event) => event.preventDefault());

    if (!menu.parentElement) {
      document.body.appendChild(menu);
    }

    document.addEventListener("click", () => this.hideObjectContextMenu());
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        this.hideObjectContextMenu();
      }
    });
    window.addEventListener("blur", () => this.hideObjectContextMenu());
    window.addEventListener("resize", () => this.hideObjectContextMenu());
    window.addEventListener(
      "scroll",
      () => this.hideObjectContextMenu(),
      true
    );

    this.objectContextMenuEl = menu;
  }

  appendContextMenuDivider(menu) {
    const divider = document.createElement("div");
    divider.className = "annotation-context-menu-divider";
    menu.appendChild(divider);
  }

  appendContextMenuItem(
    menu,
    { label, detail = null, checked = false, disabled = false, tone = "default", onClick }
  ) {
    const button = document.createElement("button");
    button.type = "button";
    button.className =
      "annotation-context-menu-item" +
      (checked ? " is-checked" : "") +
      (tone === "danger" ? " is-danger" : "");
    button.disabled = !!disabled;
    button.innerHTML = `
      <span class="annotation-context-menu-check">${checked ? "✓" : ""}</span>
      <span class="annotation-context-menu-label">${label}</span>
      ${detail ? `<span class="annotation-context-menu-detail">${detail}</span>` : ""}
    `;
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (button.disabled) {
        return;
      }
      this.hideObjectContextMenu();
      onClick?.();
    });
    menu.appendChild(button);
  }

  showObjectContextMenu({ annotation, clientX, clientY }) {
    if (this.readOnly) return;

    const target = this.getMutableAnnotationTarget(annotation);
    if (!target) return;

    this.ensureObjectContextMenu();
    const menu = this.objectContextMenuEl;
    if (!menu) return;

    const trackId = Number.isInteger(target.track_id) ? target.track_id : null;
    const isHidden = this.isAnnotationHidden(annotation);
    const canEditFlags =
      !(trackId != null && this.isTrackLocked(trackId));

    menu.innerHTML = "";

    const title = document.createElement("div");
    title.className = "annotation-context-menu-title";
    const { className, objectNumber } = this.getAnnotationIdentityMeta(annotation);
    title.textContent = `${className} · Object ${objectNumber}`;
    menu.appendChild(title);

    this.appendContextMenuItem(menu, {
      label: isHidden ? "Show object" : "Hide object",
      detail: trackId != null ? "Visibility" : "Annotation visibility",
      onClick: () => this.toggleAnnotationVisibility(annotation),
    });

    this.appendContextMenuDivider(menu);

    [
      ["is_occluded", "Occluded"],
      ["is_truncated", "Truncated"],
      ["is_outside", "Outside"],
      ["is_lost", "Lost"],
    ].forEach(([flagKey, label]) => {
      this.appendContextMenuItem(menu, {
        label,
        checked: !!target[flagKey],
        disabled: !canEditFlags,
        onClick: () => this.toggleAnnotationFlag(flagKey, annotation),
      });
    });

    if (trackId != null && this.kind === "video") {
      const selectedRange = this.getSelectedTrackFrameRange();
      const hasSelectedRange = selectedRange?.trackId === trackId;

      this.appendContextMenuDivider(menu);

      if (this.canSplitTrackAtCurrentFrame(trackId) && !this.isTrackLocked(trackId)) {
        this.appendContextMenuItem(menu, {
          label: "Split here",
          detail: `Frame ${this.currentFrameIndex + 1}`,
          onClick: () => {
            this.restoreActiveAnnotationForTrack(trackId);
            this.splitActiveTrackAtCurrentFrame();
          },
        });
      }

      if (this.canApplySelectedLabelToActiveObject()) {
        this.appendContextMenuItem(menu, {
          label: "Apply selected label",
          detail: this.getLabelClassName(this.currentLabelClassId),
          onClick: () => {
            this.restoreActiveAnnotationForTrack(trackId);
            this.applySelectedLabelToActiveObject();
          },
        });
      }

      if (Number.isInteger(this.activeTrackId) && this.activeTrackId !== trackId) {
        if (this.canMergeTrackIntoTarget(trackId, this.activeTrackId)) {
          this.appendContextMenuItem(menu, {
            label: "Merge active object into this object",
            detail: `Active Object ${this.activeTrackId}`,
            onClick: () => this.mergeTrackIntoTarget(trackId, this.activeTrackId),
          });
        }
      } else {
        this.appendContextMenuItem(menu, {
          label:
            this.pendingMergeTargetTrackId === trackId
              ? "Clear merge target"
              : "Set as merge target",
          onClick: () => {
            this.restoreActiveAnnotationForTrack(trackId);
            this.toggleMergeSelectionMode();
          },
        });
      }

      if (hasSelectedRange && this.canDeleteSelectedRange()) {
        this.appendContextMenuItem(menu, {
          label: "Delete selected A–B range",
          tone: "danger",
          onClick: () => this.deleteActiveTrackSelectedRange(),
        });
      }

      if (hasSelectedRange && this.canApplyCurrentFlagsToSelectedRange()) {
        this.appendContextMenuItem(menu, {
          label: "Apply current tags to A–B",
          onClick: () => this.applyCurrentFlagsToSelectedRange(),
        });
      }

      this.appendContextMenuDivider(menu);

      this.appendContextMenuItem(menu, {
        label: "Delete annotation on this frame",
        tone: "danger",
        onClick: () => this.deleteAnnotationOnCurrentFrame(annotation),
      });
      this.appendContextMenuItem(menu, {
        label: "Delete object across video",
        tone: "danger",
        onClick: () => this.deleteTrack(trackId),
      });
    } else {
      this.appendContextMenuDivider(menu);

      if (this.canApplySelectedLabelToActiveObject()) {
        this.appendContextMenuItem(menu, {
          label: "Apply selected label",
          detail: this.getLabelClassName(this.currentLabelClassId),
          onClick: () => {
            this.markActiveAnnotation(annotation);
            this.applySelectedLabelToActiveObject();
          },
        });
      }

      this.appendContextMenuItem(menu, {
        label: "Delete annotation",
        tone: "danger",
        onClick: () => this.deleteAnnotationOnCurrentFrame(annotation),
      });
    }

    menu.hidden = false;
    menu.style.left = "0px";
    menu.style.top = "0px";

    const viewportPadding = 8;
    const rect = menu.getBoundingClientRect();
    const left = Math.max(
      viewportPadding,
      Math.min(clientX, window.innerWidth - rect.width - viewportPadding)
    );
    const top = Math.max(
      viewportPadding,
      Math.min(clientY, window.innerHeight - rect.height - viewportPadding)
    );

    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    this.objectContextMenuTarget = target;
  }

  hideObjectContextMenu() {
    if (!this.objectContextMenuEl) return;
    this.objectContextMenuEl.hidden = true;
    this.objectContextMenuEl.innerHTML = "";
    this.objectContextMenuTarget = null;
  }

  onCanvasContextMenu(evt) {
    evt.preventDefault();
    if (this.readOnly) return;

    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    const hit = this.findAnnotationAtCanvasPoint(
      evt.clientX - rect.left,
      evt.clientY - rect.top
    );
    if (!hit || !hit.ann) {
      this.hideObjectContextMenu();
      return;
    }

    this.markActiveAnnotation(hit.ann, {
      vertexIndex: this.parsePolygonVertexHandle(hit.handle),
    });
    this.showObjectContextMenu({
      annotation: hit.ann,
      clientX: evt.clientX,
      clientY: evt.clientY,
    });
  }

  applyActiveAnnotationStateFromControls() {
    if (this.readOnly || !this.activeAnnotation) return;
    const ann = this.activeAnnotation;
    if (ann.track_id != null && this.isTrackLocked(ann.track_id)) {
      this.syncAnnotationStateControls();
      return;
    }
    const nextFlags = {
      is_occluded: !!document.getElementById("annotation-state-occluded")?.checked,
      is_truncated: !!document.getElementById("annotation-state-truncated")?.checked,
      is_outside: !!document.getElementById("annotation-state-outside")?.checked,
      is_lost: !!document.getElementById("annotation-state-lost")?.checked,
    };
    this.updateAnnotationFlags(ann, nextFlags);
    this.syncAnnotationStateControls();
  }

  addSparseAnnotation(annotation) {
    this.ensureClientUid(annotation);
    const key = this.kind === "video" ? annotation.frame_index : null;
    const bucket = this.frameAnnotations.get(key) || [];
    bucket.push(annotation);
    this.frameAnnotations.set(key, bucket);
  }

  removeSparseAnnotation(annotation) {
    if (!annotation) return;

    const key = this.kind === "video" ? annotation.frame_index : null;
    const bucket = this.frameAnnotations.get(key);
    if (!bucket || !bucket.length) {
      return;
    }

    const idx = bucket.indexOf(annotation);
    if (idx >= 0) {
      bucket.splice(idx, 1);
    }

    if (bucket.length) {
      this.frameAnnotations.set(key, bucket);
    } else {
      this.frameAnnotations.delete(key);
    }
  }

  getSparseAnnotations() {
    const all = [];
    for (const [, bucket] of this.frameAnnotations.entries()) {
      if (!bucket || !bucket.length) continue;
      all.push(...bucket);
    }
    return all;
  }

  getTrackSparseAnnotations(trackId) {
    return this.getSparseAnnotations()
      .filter((annotation) => annotation.track_id === trackId)
      .sort((left, right) => (left.frame_index ?? 0) - (right.frame_index ?? 0));
  }

  clearTrackSparseAnnotations(trackId) {
    const frameKeys = Array.from(this.frameAnnotations.keys());
    for (const frameKey of frameKeys) {
      const bucket = this.frameAnnotations.get(frameKey);
      if (!bucket || !bucket.length) continue;

      const remaining = bucket.filter((annotation) => annotation.track_id !== trackId);
      if (remaining.length) {
        this.frameAnnotations.set(frameKey, remaining);
      } else {
        this.frameAnnotations.delete(frameKey);
      }
    }
  }

  createSegment(annotation, startFrame, endFrame, options = {}) {
    return {
      startFrame,
      endFrame,
      preserveStart: !!options.preserveStart,
      annotation: syncPolygonBounds({
        client_uid:
          options.preserveClientUid && annotation.client_uid
            ? annotation.client_uid
            : generateClientUid(),
        id: annotation.id ?? null,
        label_class_id: annotation.label_class_id,
        x1: annotation.x1,
        y1: annotation.y1,
        x2: annotation.x2,
        y2: annotation.y2,
        polygon_points: clonePolygonPoints(annotation.polygon_points),
        status: annotation.status || "pending",
        ...cloneAnnotationFlags(annotation),
        ...cloneAnnotationAuditMetadata(annotation, {
          preserve: !!(options.preserveClientUid && annotation.client_uid),
        }),
        track_id: annotation.track_id ?? null,
      }),
    };
  }

  canMergeSegments(left, right) {
    return (
      !right.preserveStart &&
      left.endFrame + 1 === right.startFrame &&
      left.annotation.label_class_id === right.annotation.label_class_id &&
      left.annotation.status === right.annotation.status &&
      !!left.annotation.is_occluded === !!right.annotation.is_occluded &&
      !!left.annotation.is_truncated === !!right.annotation.is_truncated &&
      !!left.annotation.is_outside === !!right.annotation.is_outside &&
      !!left.annotation.is_lost === !!right.annotation.is_lost &&
      left.annotation.x1 === right.annotation.x1 &&
      left.annotation.y1 === right.annotation.y1 &&
      left.annotation.x2 === right.annotation.x2 &&
      left.annotation.y2 === right.annotation.y2 &&
      polygonPointsEqual(
        left.annotation.polygon_points,
        right.annotation.polygon_points
      )
    );
  }

  getTrackSegments(trackId) {
    return this.getTrackSparseAnnotations(trackId).map((annotation) => {
      const startFrame = annotation.frame_index ?? 0;
      const runLength = Math.max(0, annotation.propagation_frames ?? 0);

      return this.createSegment(
        annotation,
        startFrame,
        startFrame + runLength,
        { preserveClientUid: true }
      );
    });
  }

  getPropagationRunLengthForTrackEdit(annotation, fallbackRunLength = 0) {
    if (this.kind !== "video" || !annotation || annotation.track_id == null) {
      return fallbackRunLength;
    }

    const frameIndex = annotation.frame_index ?? this.currentFrameIndex ?? 0;

    if (
      Number.isInteger(annotation._sparseEndFrame) &&
      annotation._sparseEndFrame >= frameIndex
    ) {
      return Math.max(0, annotation._sparseEndFrame - frameIndex);
    }

    const segment = this.getTrackSegments(annotation.track_id).find(
      (currentSegment) =>
        frameIndex >= currentSegment.startFrame &&
        frameIndex <= currentSegment.endFrame
    );

    if (!segment) {
      return fallbackRunLength;
    }

    return Math.max(0, segment.endFrame - frameIndex);
  }

  setTrackSegments(trackId, segments) {
    this.clearTrackSparseAnnotations(trackId);

    const orderedSegments = segments
      .filter((segment) => segment && segment.endFrame >= segment.startFrame)
      .sort((left, right) => left.startFrame - right.startFrame);

    const mergedSegments = [];
    for (const segment of orderedSegments) {
      const normalizedSegment = {
        startFrame: segment.startFrame,
        endFrame: segment.endFrame,
        preserveStart: !!segment.preserveStart,
        annotation: {
          ...segment.annotation,
          track_id: trackId,
        },
      };

      const previous = mergedSegments[mergedSegments.length - 1];
      if (previous && this.canMergeSegments(previous, normalizedSegment)) {
        previous.endFrame = Math.max(previous.endFrame, normalizedSegment.endFrame);
        continue;
      }

      mergedSegments.push(normalizedSegment);
    }

    for (const segment of mergedSegments) {
      this.addSparseAnnotation({
        client_uid: segment.annotation.client_uid || generateClientUid(),
        id: segment.annotation.id ?? null,
        label_class_id: segment.annotation.label_class_id,
        frame_index: segment.startFrame,
        x1: segment.annotation.x1,
        y1: segment.annotation.y1,
        x2: segment.annotation.x2,
        y2: segment.annotation.y2,
        polygon_points: clonePolygonPoints(segment.annotation.polygon_points),
        status: segment.annotation.status || "pending",
        ...cloneAnnotationFlags(segment.annotation),
        ...cloneAnnotationAuditMetadata(segment.annotation),
        track_id: trackId,
        propagation_frames: segment.endFrame - segment.startFrame,
      });
    }
  }

  findActiveSparseAnnotation(trackId, frameIndex) {
    let bestMatch = null;

    for (const annotation of this.getTrackSparseAnnotations(trackId)) {
      const startFrame = annotation.frame_index ?? 0;
      const endFrame = startFrame + Math.max(0, annotation.propagation_frames ?? 0);

      if (frameIndex < startFrame || frameIndex > endFrame) {
        continue;
      }

      if (!bestMatch || startFrame > (bestMatch.frame_index ?? -1)) {
        bestMatch = annotation;
      }
    }

    return bestMatch;
  }

  buildAnnotationsForFrame(frameIndex) {
    if (this.kind !== "video") {
      return this.frameAnnotations.get(null) || [];
    }

    const trackViews = new Map();
    const singleViews = [];

    for (const storedAnnotation of this.getSparseAnnotations()) {
      const startFrame = storedAnnotation.frame_index ?? 0;
      const endFrame =
        startFrame + Math.max(0, storedAnnotation.propagation_frames ?? 0);

      if (frameIndex < startFrame || frameIndex > endFrame) {
        continue;
      }

      const view = cloneAnnotation(storedAnnotation, frameIndex);
      view._storedAnnotation = storedAnnotation;
      view._sparseStartFrame = startFrame;
      view._sparseEndFrame = endFrame;

      if (storedAnnotation.track_id == null) {
        singleViews.push(view);
        continue;
      }

      const existing = trackViews.get(storedAnnotation.track_id);
      if (
        !existing ||
        (view._sparseStartFrame ?? 0) > (existing._sparseStartFrame ?? -1)
      ) {
        trackViews.set(storedAnnotation.track_id, view);
      }
    }

    return [...trackViews.values(), ...singleViews].sort((left, right) => {
      const leftTrackId = left.track_id ?? Number.MAX_SAFE_INTEGER;
      const rightTrackId = right.track_id ?? Number.MAX_SAFE_INTEGER;
      if (leftTrackId !== rightTrackId) {
        return leftTrackId - rightTrackId;
      }
      return left.label_class_id - right.label_class_id;
    });
  }

  commitVideoAnnotation(annotation, runLength = 0) {
    if (this.kind !== "video") return;

    const frameIndex = annotation.frame_index ?? this.currentFrameIndex;
    const maxFrame =
      this.totalFrames && this.totalFrames > 0
        ? this.totalFrames - 1
        : frameIndex + Math.max(0, runLength);
    const newEndFrame = Math.max(
      frameIndex,
      Math.min(maxFrame, frameIndex + Math.max(0, runLength))
    );

    if (annotation.track_id == null) {
      if (annotation._storedAnnotation) {
        this.removeSparseAnnotation(annotation._storedAnnotation);
      }

      this.addSparseAnnotation({
        client_uid: annotation.client_uid || generateClientUid(),
        id: annotation.id ?? null,
        label_class_id: annotation.label_class_id,
        frame_index: frameIndex,
        x1: annotation.x1,
        y1: annotation.y1,
        x2: annotation.x2,
        y2: annotation.y2,
        polygon_points: clonePolygonPoints(annotation.polygon_points),
        status: annotation.status || "pending",
        ...cloneAnnotationAuditMetadata(annotation),
        track_id: null,
        propagation_frames: newEndFrame - frameIndex,
      });
      return;
    }

    const trackId = annotation.track_id;
    const segments = this.getTrackSegments(trackId);
    const nextSegments = [];
    let inserted = false;

    for (const segment of segments) {
      if (frameIndex < segment.startFrame || frameIndex > segment.endFrame) {
        nextSegments.push(segment);
        continue;
      }

      if (segment.startFrame < frameIndex) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            frameIndex - 1,
            { preserveClientUid: true }
          )
        );
      }

      nextSegments.push(
        this.createSegment(annotation, frameIndex, newEndFrame, {
          preserveClientUid: segment.startFrame === frameIndex,
        })
      );

      if (newEndFrame < segment.endFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            newEndFrame + 1,
            segment.endFrame
          )
        );
      }

      inserted = true;
    }

    if (!inserted) {
      nextSegments.push(
        this.createSegment(annotation, frameIndex, newEndFrame)
      );
    }

    this.setTrackSegments(trackId, nextSegments);
  }

  removeTrackFrame(trackId, frameIndex) {
    const segments = this.getTrackSegments(trackId);
    const nextSegments = [];

    for (const segment of segments) {
      if (frameIndex < segment.startFrame || frameIndex > segment.endFrame) {
        nextSegments.push(segment);
        continue;
      }

      if (segment.startFrame < frameIndex) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            frameIndex - 1,
            { preserveClientUid: true }
          )
        );
      }

      if (frameIndex < segment.endFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            frameIndex + 1,
            segment.endFrame,
            { preserveClientUid: segment.startFrame >= frameIndex }
          )
        );
      }
    }

    this.setTrackSegments(trackId, nextSegments);

    if (!this.getTrackSparseAnnotations(trackId).length) {
      this.manualKeyframesByTrack.delete(trackId);
      this.persistManualKeyframesToStorage();
    } else {
      this.removeManualKeyframe(trackId, frameIndex);
    }
  }


  clearCurrentFrameAnnotations() {
    const currentViews = [...this.annotations];

    for (const annotation of currentViews) {
      if (annotation.track_id != null) {
        this.removeTrackFrame(annotation.track_id, this.currentFrameIndex);
      } else if (annotation._storedAnnotation) {
        this.removeSparseAnnotation(annotation._storedAnnotation);
      }
    }

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.activeAnnotation = null;
    this.activeVertexIndex = null;
    this.setActiveTrackId(null);
  }


  initializeStore(initialAnnotations) {
    this.frameAnnotations = new Map();
    this.annotations = [];
    this.activeAnnotation = null;
    this.activeVertexIndex = null;
    this.hiddenAnnotationClientUids = new Set();
    this.hideObjectContextMenu();

    if (this.kind === "video") {
      const trackedAnnotations = new Map();

      initialAnnotations.forEach((annotation) => {
        const frameIndex = annotation.frame_index ?? 0;
        const cloned = cloneAnnotation(annotation, frameIndex);
        cloned.propagation_frames = this.normalizePropagationFrames(
          annotation.propagation_frames
        );
        cloned._hasExplicitPropagation = annotation.propagation_frames != null;

        if (cloned.track_id == null) {
          if (!cloned._hasExplicitPropagation) {
            cloned.propagation_frames = 0;
          }
          this.addSparseAnnotation(cloned);
          return;
        }

        const bucket = trackedAnnotations.get(cloned.track_id) || [];
        bucket.push(cloned);
        trackedAnnotations.set(cloned.track_id, bucket);
      });

      trackedAnnotations.forEach((trackFrames) => {
        trackFrames.sort(
          (left, right) => (left.frame_index ?? 0) - (right.frame_index ?? 0)
        );

        const shouldPreserveSparseRows = trackFrames.some(
          (annotation) => annotation._hasExplicitPropagation
        );

        if (shouldPreserveSparseRows) {
          trackFrames.forEach((annotation) => {
            annotation.propagation_frames = this.normalizePropagationFrames(
              annotation.propagation_frames
            );
            this.addSparseAnnotation(annotation);
          });
          return;
        }

        let runStart = trackFrames[0];
        let previous = trackFrames[0];

        for (let index = 1; index < trackFrames.length; index += 1) {
          const current = trackFrames[index];
          const previousFrame = previous.frame_index ?? 0;
          const currentFrame = current.frame_index ?? 0;

          const sameRun =
            currentFrame === previousFrame + 1 &&
            current.label_class_id === previous.label_class_id &&
            current.status === previous.status &&
            current.x1 === previous.x1 &&
            current.y1 === previous.y1 &&
            current.x2 === previous.x2 &&
            current.y2 === previous.y2 &&
            polygonPointsEqual(
              current.polygon_points,
              previous.polygon_points
            );

          if (sameRun) {
            previous = current;
            continue;
          }

          this.addSparseAnnotation({
            ...runStart,
            frame_index: runStart.frame_index ?? 0,
            propagation_frames:
              (previous.frame_index ?? 0) - (runStart.frame_index ?? 0),
          });

          runStart = current;
          previous = current;
        }

        this.addSparseAnnotation({
          ...runStart,
          frame_index: runStart.frame_index ?? 0,
          propagation_frames:
            (previous.frame_index ?? 0) - (runStart.frame_index ?? 0),
        });
      });
    } else {
      this.frameAnnotations.set(
        null,
        initialAnnotations.map((annotation) => cloneAnnotation(annotation, null))
      );
    }

    this.nextTrackId = 1;
    for (const annotation of this.getSparseAnnotations()) {
      if (annotation.track_id != null && annotation.track_id >= this.nextTrackId) {
        this.nextTrackId = annotation.track_id + 1;
      }
      if (typeof annotation.propagation_frames !== "number") {
        annotation.propagation_frames = 0;
      }
    }

    const defaultManualKeyframes = this.buildDefaultManualKeyframeMap();
    this.loadManualKeyframesFromStorage(defaultManualKeyframes);
    this.pruneHiddenAnnotationState();
    this.resetHistory();
    this.normalizeTrackUiState();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();
    
    this.updateReferenceControls();
    this.updateFastActionButtons();

    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
    }
  }


  syncCanvasSize(resetView = false) {
    const measurementEl =
      this.kind === "video" && !this.useCanvasVideo && this.stageEl
        ? this.stageEl
        : this.mediaEl;
    const rect = measurementEl.getBoundingClientRect();
    const viewportWidth = rect.width;
    const viewportHeight = rect.height;

    if (!viewportWidth || !viewportHeight) {
      return;
    }

    this.viewportWidth = viewportWidth;
    this.viewportHeight = viewportHeight;

    this.canvas.style.width = `${viewportWidth}px`;
    this.canvas.style.height = `${viewportHeight}px`;
    this.canvas.width = viewportWidth * this.pixelRatio;
    this.canvas.height = viewportHeight * this.pixelRatio;

    const naturalWidth =
      this.mediaEl.naturalWidth || this.mediaEl.videoWidth || viewportWidth;
    const naturalHeight =
      this.mediaEl.naturalHeight || this.mediaEl.videoHeight || viewportHeight;

    this.imageWidth = naturalWidth;
    this.imageHeight = naturalHeight;

    if (resetView || !this.baseScale) {
      const scaleX = viewportWidth / (naturalWidth || viewportWidth);
      const scaleY = viewportHeight / (naturalHeight || viewportHeight);
      this.baseScale = Math.min(scaleX || 1, scaleY || 1);
      this.zoom = 1;

      // Center the image in the viewport
      this.translateX =
        (viewportWidth - this.imageWidth * this.baseScale) / 2;
      this.translateY =
        (viewportHeight - this.imageHeight * this.baseScale) / 2;
    }

    this.requestRedraw();
  }

  resetView() {
    this.isPanning = false;
    this.isDrawing = false;
    this.isDragging = false;
    this.draggedAnnotation = null;
    this.dragMode = null;
    this.dragHandle = null;
    this.resizeStart = null;
    this.currentDrawingAnnotation = null;
    this.canvas.style.cursor = "crosshair";
    this.syncCanvasSize(true);
  }

  attachEvents() {
    const handleMouseDown = (e) => this.onMouseDown(e);

    if (this.canvas) {
      this.canvas.addEventListener("mousedown", handleMouseDown);
      this.canvas.addEventListener("dblclick", (e) => this.onDoubleClick(e));
      this.canvas.addEventListener("contextmenu", (e) => this.onCanvasContextMenu(e));
      this.canvas.addEventListener("auxclick", (e) => {
        if (e.button === 1) {
          e.preventDefault();
        }
      });
      this.canvas.addEventListener(
        "wheel",
        (e) => this.onWheel(e),
        { passive: false }
      );
    }
    window.addEventListener("mousemove", (e) => this.onMouseMove(e));
    window.addEventListener("mouseup", (e) => this.onMouseUp(e));
    window.addEventListener("resize", () => this.syncCanvasSize(true));

    document.addEventListener("keydown", (e) => {
      if (!this.readOnly && this.isPolygonDrawing) {
        if (e.key === "Enter") {
          e.preventDefault();
          e.stopPropagation();
          this.finalizePolygonDrawing();
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          this.cancelPolygonDrawing();
          return;
        }
      }

      const activeTag = document.activeElement?.tagName;
      if (activeTag === "INPUT" || activeTag === "TEXTAREA" || activeTag === "SELECT") {
        return;
      }

      if (this.isFrameTransitionPending() && !this.isFrameNavigationShortcut(e)) {
        if (e.key === "Escape") {
          e.preventDefault();
        }
        return;
      }

      if (e.key === "Escape") {
        if (this.activeAnnotation || Number.isInteger(this.activeTrackId)) {
          e.preventDefault();
          this.clearActiveAnnotationSelection();
          return;
        }
      }

      const modifierPressed = e.ctrlKey || e.metaKey;
      if (!this.readOnly && modifierPressed && !e.altKey && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) {
          this.redo();
        } else {
          this.undo();
        }
        return;
      }
      if (!this.readOnly && modifierPressed && !e.altKey && e.key.toLowerCase() === "y") {
        e.preventDefault();
        this.redo();
        return;
      }
      if (!this.readOnly && modifierPressed && !e.altKey && e.key.toLowerCase() === "c") {
        if (this.activeAnnotation && this.isAnnotationVisible(this.activeAnnotation)) {
          e.preventDefault();
          this.copyActiveAnnotationToClipboard();
        }
        return;
      }
      if (!this.readOnly && modifierPressed && !e.altKey && e.key.toLowerCase() === "v") {
        if (this.annotationClipboard) {
          e.preventDefault();
          this.pasteAnnotationFromClipboard();
        }
        return;
      }

      if (!this.readOnly && e.altKey && e.code === "KeyC" && this.kind === "video") {
        e.preventDefault();
        this.copyFromPreviousItemLastFrame();
        return;
      }

      if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "A" || e.key === "a")) {
        if (this.prevItemUrl) {
          e.preventDefault();
          this.togglePreviousFrameOverlay(false);
          window.location.href = this.prevItemUrl;
        }
        return;
      }
      if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "D" || e.key === "d")) {
        if (this.nextItemUrl) {
          e.preventDefault();
          this.togglePreviousFrameOverlay(false);
          window.location.href = this.nextItemUrl;
        }
        return;
      }

      if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        const targetUrl =
          e.key === "ArrowLeft" ? this.prevItemUrl : this.nextItemUrl;
        if (targetUrl) {
          e.preventDefault();
          this.togglePreviousFrameOverlay(false);
          window.location.href = targetUrl;
        }
        return;
      }

      if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "G" || e.key === "g")) {
        e.preventDefault();
        this.togglePreviousFrameOverlay();
        return;
      }

      if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "J" || e.key === "j")) {
        e.preventDefault();
        this.jumpToActiveTrackKeyframe(-1);
        return;
      }

      if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "L" || e.key === "l")) {
        e.preventDefault();
        this.jumpToActiveTrackKeyframe(1);
        return;
      }

      if (
        !this.readOnly &&
        !modifierPressed &&
        !e.altKey &&
        e.shiftKey &&
        (e.key === "S" || e.key === "s")
      ) {
        e.preventDefault();
        this.splitActiveTrackAtCurrentFrame();
        return;
      }

      if (
        !this.readOnly &&
        !modifierPressed &&
        !e.altKey &&
        e.shiftKey &&
        (e.key === "R" || e.key === "r")
      ) {
        e.preventDefault();
        this.applySelectedLabelToActiveObject();
        return;
      }

      if (
        !this.readOnly &&
        !modifierPressed &&
        !e.altKey &&
        e.shiftKey &&
        (e.key === "T" || e.key === "t")
      ) {
        e.preventDefault();
        this.toggleReassignSelectionMode();
        return;
      }

      if (
        !this.readOnly &&
        !modifierPressed &&
        !e.altKey &&
        e.shiftKey &&
        (e.key === "M" || e.key === "m")
      ) {
        e.preventDefault();
        this.toggleMergeSelectionMode();
        return;
      }

      if (
        !this.readOnly &&
        !modifierPressed &&
        !e.altKey &&
        e.shiftKey &&
        (e.key === "X" || e.key === "x")
      ) {
        e.preventDefault();
        this.deleteActiveTrackSelectedRange();
        return;
      }

      if (
        !this.readOnly &&
        !modifierPressed &&
        !e.altKey &&
        e.shiftKey &&
        (e.key === "F" || e.key === "f")
      ) {
        e.preventDefault();
        this.applyCurrentFlagsToSelectedRange();
        return;
      }

      if (!this.readOnly && e.key === "Delete") {
        e.preventDefault();
        this.handleDeleteKey();
        return;
      }

      if (!this.readOnly && e.key >= "1" && e.key <= "9") {
        const idx = parseInt(e.key, 10) - 1;
        if (idx >= 0 && idx < this.labelClasses.length) {
          const lc = this.labelClasses[idx];
          this.applyLabelSelection(lc.id);
        }
      }

      if (this.mediaEl.tagName === "VIDEO") {
        if (e.code === "Space") {
          e.preventDefault();
          this.togglePlayback();
        }
        if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "Q" || e.key === "q")) {
          e.preventDefault();
          this.stepFrames(-1);
          return;
        }
        if (!modifierPressed && !e.altKey && !e.shiftKey && (e.key === "E" || e.key === "e")) {
          e.preventDefault();
          this.stepFrames(1);
          return;
        }
        if (e.key === "ArrowRight") {
          e.preventDefault();
          this.stepFrames(e.shiftKey ? 5 : 1);
        }
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          this.stepFrames(e.shiftKey ? -5 : -1);
        }
      }
    });

    const select = document.getElementById("label-class-select");
    if (select) {
      if (this.readOnly) {
        select.disabled = true;
      } else {
        select.addEventListener("change", (e) => {
          this.applyLabelSelection(e.target.value);
        });
      }
      if (!this.currentLabelClassId && select.value) {
        this.currentLabelClassId = parseInt(select.value, 10);
      }
    }

    if (this.propagationLengthInput) {
      const v = parseInt(this.propagationLengthInput.value || "0", 10);
      this.defaultPropagationFrames = isNaN(v) ? 0 : Math.max(0, v);

      if (this.readOnly) {
        this.propagationLengthInput.disabled = true;
      } else {
        this.propagationLengthInput.addEventListener("change", () => {
          const vv = parseInt(this.propagationLengthInput.value || "0", 10);
          this.defaultPropagationFrames = isNaN(vv) ? 0 : Math.max(0, vv);
        });
      }
    }

    const saveBtn = document.getElementById("save-annotations-btn");
    if (saveBtn && !this.readOnly) {
      saveBtn.addEventListener("click", () => this.saveAnnotations());
    }
    const undoBtn = document.getElementById("btn-undo");
    if (undoBtn && !this.readOnly) undoBtn.addEventListener("click", () => this.undo());
    const redoBtn = document.getElementById("btn-redo");
    if (redoBtn && !this.readOnly) redoBtn.addEventListener("click", () => this.redo());
    const trackLockBtn = document.getElementById("btn-track-lock");
    if (trackLockBtn && !this.readOnly) {
      trackLockBtn.addEventListener("click", () => this.toggleTrackLock(this.activeTrackId));
    }
    const trackHideBtn = document.getElementById("btn-track-hide");
    if (trackHideBtn && !this.readOnly) {
      trackHideBtn.addEventListener("click", () => this.toggleTrackHidden(this.activeTrackId));
    }
    const trackSoloBtn = document.getElementById("btn-track-solo");
    if (trackSoloBtn && !this.readOnly) {
      trackSoloBtn.addEventListener("click", () => this.toggleTrackSolo(this.activeTrackId));
    }
    const prevGapBtn = document.getElementById("btn-prev-gap");
    if (prevGapBtn && !this.readOnly) {
      prevGapBtn.addEventListener("click", () => this.jumpToAdjacentGap(-1));
    }
    const nextGapBtn = document.getElementById("btn-next-gap");
    if (nextGapBtn && !this.readOnly) {
      nextGapBtn.addEventListener("click", () => this.jumpToAdjacentGap(1));
    }
    [
      "annotation-state-occluded",
      "annotation-state-truncated",
      "annotation-state-outside",
      "annotation-state-lost",
    ].forEach((id) => {
      const input = document.getElementById(id);
      if (!input || this.readOnly) return;
      input.addEventListener("change", () => this.applyActiveAnnotationStateFromControls());
    });
    const fixedBBoxToggle = document.getElementById("fixed-bbox-toggle");
    if (fixedBBoxToggle) {
      fixedBBoxToggle.addEventListener("change", () => {
        this.useFixedSizeBBox = !!fixedBBoxToggle.checked;
        this.syncFixedBBoxControls();
      });
    }
    const fixedBBoxWidthInput = document.getElementById("fixed-bbox-width");
    if (fixedBBoxWidthInput) {
      fixedBBoxWidthInput.addEventListener("change", () => {
        this.fixedBBoxWidth = Math.max(
          1,
          Math.trunc(Number(fixedBBoxWidthInput.value) || this.fixedBBoxWidth)
        );
        this.syncFixedBBoxControls();
      });
    }
    const fixedBBoxHeightInput = document.getElementById("fixed-bbox-height");
    if (fixedBBoxHeightInput) {
      fixedBBoxHeightInput.addEventListener("change", () => {
        this.fixedBBoxHeight = Math.max(
          1,
          Math.trunc(Number(fixedBBoxHeightInput.value) || this.fixedBBoxHeight)
        );
        this.syncFixedBBoxControls();
      });
    }

    const resetViewBtn = document.getElementById("btn-reset-view");
    if (resetViewBtn) {
      resetViewBtn.addEventListener("click", () => this.resetView());
    }
    const objectLabelsToggle = document.getElementById("toggle-object-labels");
    if (objectLabelsToggle) {
      objectLabelsToggle.addEventListener("change", () =>
        this.toggleAnnotationLabelVisibility(objectLabelsToggle.checked)
      );
    }

    if (this.mediaEl.tagName === "VIDEO") {
      const btnTogglePrevOverlay = document.getElementById(
        "btn-toggle-prev-overlay"
      );
      const btnPrevKeyframe = document.getElementById("btn-prev-keyframe");
      const btnNextKeyframe = document.getElementById("btn-next-keyframe");
      const btnSplitTrack = document.getElementById("btn-split-track");
      const btnArmReassignTrack = document.getElementById("btn-arm-reassign-track");
      const btnArmMergeTrack = document.getElementById("btn-arm-merge-track");
      const btnApplyLabelTrack = document.getElementById("btn-apply-label-track");
      const btnApplyLabelRange = document.getElementById("btn-apply-label-range");
      const btnDeleteRange = document.getElementById("btn-delete-range");
      const btnApplyRangeFlags = document.getElementById(
        "btn-apply-range-flags"
      );

      btnTogglePrevOverlay?.addEventListener("click", () =>
        this.togglePreviousFrameOverlay()
      );
      btnPrevKeyframe?.addEventListener("click", () =>
        this.jumpToActiveTrackKeyframe(-1)
      );
      btnNextKeyframe?.addEventListener("click", () =>
        this.jumpToActiveTrackKeyframe(1)
      );
      btnSplitTrack?.addEventListener("click", () =>
        this.splitActiveTrackAtCurrentFrame()
      );
      btnArmReassignTrack?.addEventListener("click", () =>
        this.toggleReassignSelectionMode()
      );
      btnArmMergeTrack?.addEventListener("click", () =>
        this.toggleMergeSelectionMode()
      );
      btnApplyLabelTrack?.addEventListener("click", () =>
        this.applySelectedLabelToActiveObject()
      );
      btnApplyLabelRange?.addEventListener("click", () =>
        this.applySelectedLabelToSelectedRange()
      );
      btnDeleteRange?.addEventListener("click", () =>
        this.deleteActiveTrackSelectedRange()
      );
      btnApplyRangeFlags?.addEventListener("click", () =>
        this.applyCurrentFlagsToSelectedRange()
      );

      const prevValidationIssueBtn = document.getElementById(
        "btn-prev-validation-issue"
      );
      const nextValidationIssueBtn = document.getElementById(
        "btn-next-validation-issue"
      );
      prevValidationIssueBtn?.addEventListener("click", () =>
        this.focusValidationIssueByDirection(-1)
      );
      nextValidationIssueBtn?.addEventListener("click", () =>
        this.focusValidationIssueByDirection(1)
      );

      this.mediaEl.addEventListener("timeupdate", () => {
        lfDebug("video.timeupdate", {
          readyState: this.mediaEl.readyState,
          currentTime: this.mediaEl.currentTime,
        });
        this.tryFinalizePendingFrame("timeupdate");
        this.handleVideoTimeUpdate();
        this.switchToCanvasVideoIfReady();
      });
      this.mediaEl.addEventListener("canplay", () => {
        lfDebug("video.canplay", {
          readyState: this.mediaEl.readyState,
          currentTime: this.mediaEl.currentTime,
        });
        this.tryFinalizePendingFrame("canplay");
        this.switchToCanvasVideoIfReady();
      });
      this.mediaEl.addEventListener("seeked", () => {
        lfDebug("video.seeked", {
          readyState: this.mediaEl.readyState,
          currentTime: this.mediaEl.currentTime,
        });
        this.tryFinalizePendingFrame("seeked");
        this.switchToCanvasVideoIfReady();
      });
      this.mediaEl.addEventListener("ended", () => {
        lfDebug("video.ended", {
          readyState: this.mediaEl.readyState,
          currentTime: this.mediaEl.currentTime,
          duration: this.mediaEl.duration,
        });
        this.stopRenderLoop();
        this.syncPlaybackTerminalFrame();
        this.switchToCanvasVideoIfReady();
        this.requestRedraw();
      });

      const btnTogglePlay = document.getElementById("btn-toggle-play");
      const btnPrev5 = document.getElementById("btn-prev5-frame");
      const btnPrev = document.getElementById("btn-prev-frame");
      const btnNext = document.getElementById("btn-next-frame");
      const btnNext5 = document.getElementById("btn-next5-frame");
      const btnCopyFrameLink = document.getElementById("btn-copy-frame-link");
      const btnCopyPrev = document.getElementById("btn-copy-prev");
      const btnCopyPrevItemLast = document.getElementById(
        "btn-copy-prev-item-last"
      );

      btnTogglePlay?.addEventListener("click", () => this.togglePlayback());
      btnPrev5?.addEventListener("click", () => this.stepFrames(-5));
      btnPrev?.addEventListener("click", () => this.stepFrames(-1));
      btnNext?.addEventListener("click", () => this.stepFrames(1));
      btnNext5?.addEventListener("click", () => this.stepFrames(5));
      btnCopyFrameLink?.addEventListener("click", () => this.copyCurrentFramePermalink());

      const btnSetInterpolationStart = document.getElementById(
        "btn-set-interpolation-start"
      );
      const btnSetInterpolationEnd = document.getElementById(
        "btn-set-interpolation-end"
      );
      const btnApplyInterpolation = document.getElementById(
        "btn-apply-interpolation"
      );
      const btnClearInterpolation = document.getElementById(
        "btn-clear-interpolation"
      );

      btnSetInterpolationStart?.addEventListener("click", () =>
        this.setInterpolationBoundary("start")
      );
      btnSetInterpolationEnd?.addEventListener("click", () =>
        this.setInterpolationBoundary("end")
      );
      btnApplyInterpolation?.addEventListener("click", () =>
        this.applyInterpolationSelection()
      );
      btnClearInterpolation?.addEventListener("click", () =>
        this.clearInterpolationSelection()
      );

      if (!this.readOnly) {
        btnCopyPrev?.addEventListener("click", () => this.copyFromPrevious());
        btnCopyPrevItemLast?.addEventListener("click", () =>
          this.copyFromPreviousItemLastFrame()
        );
      }
    }
    this.syncFixedBBoxControls();
    this.syncAnnotationLabelVisibilityControl();
    this.applySelectedLabelPreset();
    this.updateHistoryButtons();
    this.updateFastActionButtons();
    this.syncTrackVisibilityControls();
    this.updateReferenceControls();
    this.pendingReassignSelection = null;
    this.syncAnnotationStateControls();

    const submitReviewForm = document.getElementById("submit-review-form");
    if (submitReviewForm && !this.readOnly) {
      submitReviewForm.addEventListener("submit", (event) =>
        this.handleSubmitForReviewForm(event)
      );
    }
  }

  onWheel(evt) {
    // Zoom only when Ctrl is held. Otherwise, let the page scroll normally.
    if (!evt.ctrlKey) {
      return;
    }

    if (!this.imageWidth || !this.imageHeight) {
      return;
    }

    // Prevent browser-page zoom / scroll when we are handling Ctrl+wheel ourselves.
    evt.preventDefault();

    const delta = evt.deltaY;
    const zoomFactor = delta < 0 ? 1.1 : 0.9;
    const oldZoom = this.zoom;
    let newZoom = oldZoom * zoomFactor;
    newZoom = Math.max(this.minZoom, Math.min(this.maxZoom, newZoom));
    if (newZoom === oldZoom) {
      return;
    }

    const rect = this.canvas.getBoundingClientRect();
    const xCss = evt.clientX - rect.left;
    const yCss = evt.clientY - rect.top;

    const scaleOld = (this.baseScale || 1) * oldZoom;
    const scaleNew = (this.baseScale || 1) * newZoom;

    // World (image) coordinates under cursor before zoom
    const xImg = (xCss - this.translateX) / scaleOld;
    const yImg = (yCss - this.translateY) / scaleOld;

    this.zoom = newZoom;

    // Adjust translation so that the same image point stays under the cursor
    this.translateX = xCss - xImg * scaleNew;
    this.translateY = yCss - yImg * scaleNew;

    this.requestRedraw();
  }

  setupTimeline() {
    if (this.timelineInitialized) return;
    if (this.kind !== "video") return;
    if (!this.mediaEl || !this.timelineTrackEl) return;

    const duration =
      typeof this.mediaEl.duration === "number" && this.mediaEl.duration > 0
        ? this.mediaEl.duration
        : this.durationSec || 0;

    if (!duration || !isFinite(duration) || duration <= 0) {
      return;
    }

    this.totalFrames = Math.max(1, Math.round(duration * this.fps));
    this.timelineInitialized = true;

    this.timelineTrackEl.addEventListener("click", (evt) => {
      if (!this.totalFrames) return;
      const rect = this.timelineTrackEl.getBoundingClientRect();
      if (!rect.width) return;
      const ratio = (evt.clientX - rect.left) / rect.width;
      const clampedRatio = Math.max(0, Math.min(1, ratio));
      const targetFrame = Math.round(
        clampedRatio * (this.totalFrames - 1)
      );
      this.seekToFrame(targetFrame);
    });

    this.setupOverviewSidebar();
    this.updateFrameDisplay();
    this.updateTimelineAnnotations();
    this.updateTimelinePlayhead();
  }

  updateTimelinePlayhead() {
    if (
      this.kind !== "video" ||
      !this.timelineTrackEl ||
      !this.timelinePlayheadEl ||
      !this.totalFrames ||
      this.totalFrames <= 0
    ) {
      return;
    }
    const ratio = Math.max(
      0,
      Math.min(1, this.currentFrameIndex / (this.totalFrames - 1))
    );
    this.timelinePlayheadEl.style.left = `${ratio * 100}%`;
  }

  updateTimelineAnnotations() {
    if (
      this.kind !== "video" ||
      !this.timelineAnnotationLayerEl ||
      !this.totalFrames ||
      this.totalFrames <= 0
    ) {
      return;
    }

    const layer = this.timelineAnnotationLayerEl;
    layer.innerHTML = "";

    const markerFrames = new Set();

    for (const frames of this.manualKeyframesByTrack.values()) {
      for (const frameIndex of frames) {
        markerFrames.add(frameIndex);
      }
    }

    for (const annotation of this.getSparseAnnotations()) {
      if (annotation.track_id != null) continue;
      markerFrames.add(annotation.frame_index ?? 0);
    }

    [...markerFrames]
      .sort((left, right) => left - right)
      .forEach((frameIndex) => {
        if (frameIndex < 0 || frameIndex >= this.totalFrames) {
          return;
        }

        const marker = document.createElement("div");
        marker.className = "timeline-annotation-marker";
        const ratio =
          this.totalFrames > 1 ? frameIndex / (this.totalFrames - 1) : 0;
        marker.style.left = `${ratio * 100}%`;
        layer.appendChild(marker);
      });

    this.updateOverviewSidebarAnnotations();
    this.updateTrackTimelineSegments();
    this.updateInterpolationPanel();
  }


  setupOverviewSidebar() {
    if (!this.overviewSidebarEl || !this.totalFrames || this.totalFrames <= 0) {
      return;
    }

    const root = this.overviewSidebarEl;
    root.innerHTML = "";

    const segments = this.overviewSegmentCount || 40;
    for (let i = 0; i < segments; i++) {
      const seg = document.createElement("div");
      seg.className = "video-overview-segment";
      const inner = document.createElement("div");
      inner.className = "video-overview-segment-inner";
      seg.appendChild(inner);

      const startFrame = Math.floor((i * this.totalFrames) / segments);
      const endFrame = Math.floor(((i + 1) * this.totalFrames) / segments) - 1;
      seg.dataset.startFrame = String(startFrame);
      seg.dataset.endFrame = String(Math.max(startFrame, endFrame));

      seg.addEventListener("click", () => {
        const s = parseInt(seg.dataset.startFrame, 10) || 0;
        const e = parseInt(seg.dataset.endFrame, 10) || s;
        const center = Math.floor((s + e) / 2);
        this.seekToFrame(center);
      });

      root.appendChild(seg);
    }

    this.updateOverviewSidebarAnnotations();
  }

  updateOverviewSidebarAnnotations() {
    if (!this.overviewSidebarEl || !this.totalFrames || this.totalFrames <= 0) {
      return;
    }

    const segments = this.overviewSidebarEl.querySelectorAll(
      ".video-overview-segment"
    );
    if (!segments.length) return;

    const hasAnnotation = new Array(segments.length).fill(false);

    const markRange = (startFrame, endFrame) => {
      const startIndex = Math.max(
        0,
        Math.min(
          segments.length - 1,
          Math.floor((startFrame * segments.length) / this.totalFrames)
        )
      );
      const endIndex = Math.max(
        startIndex,
        Math.min(
          segments.length - 1,
          Math.floor((endFrame * segments.length) / this.totalFrames)
        )
      );

      for (let index = startIndex; index <= endIndex; index += 1) {
        hasAnnotation[index] = true;
      }
    };

    for (const track of this.computeTrackMap().values()) {
      markRange(track.startFrame, track.endFrame);
    }

    for (const annotation of this.getSparseAnnotations()) {
      if (annotation.track_id != null) continue;

      const startFrame = annotation.frame_index ?? 0;
      const endFrame =
        startFrame + Math.max(0, annotation.propagation_frames ?? 0);
      markRange(startFrame, endFrame);
    }

    segments.forEach((segment, index) => {
      if (hasAnnotation[index]) {
        segment.classList.add("has-annotations");
      } else {
        segment.classList.remove("has-annotations");
      }
    });
  }


  computeTrackMap() {
    const tracks = new Map();
    if (this.kind !== "video") {
      return tracks;
    }

    for (const annotation of this.getSparseAnnotations()) {
      if (annotation.track_id == null) continue;

      const startFrame = annotation.frame_index ?? 0;
      const endFrame =
        startFrame + Math.max(0, annotation.propagation_frames ?? 0);

      const existing = tracks.get(annotation.track_id);
      if (!existing) {
        tracks.set(annotation.track_id, {
          trackId: annotation.track_id,
          label_class_id: annotation.label_class_id,
          startFrame,
          endFrame,
        });
        continue;
      }

      existing.startFrame = Math.min(existing.startFrame, startFrame);
      existing.endFrame = Math.max(existing.endFrame, endFrame);
    }

    return tracks;
  }


  getRepresentativeAnnotationForTrack(trackId) {
    if (this.kind !== "video") return null;

    const keyframes = this.getTrackSparseAnnotations(trackId);
    if (!keyframes.length) return null;

    const annotation = keyframes[0];
    return cloneAnnotation(annotation, annotation.frame_index ?? 0);
  }


  getFramesForTrack(trackId) {
    if (this.kind !== "video") return [];

    const frames = [];
    for (const segment of this.getTrackSegments(trackId)) {
      frames.push(segment.startFrame);
      if (segment.endFrame !== segment.startFrame) {
        frames.push(segment.endFrame);
      }
    }

    return [...new Set(frames)].sort((left, right) => left - right);
  }


  updateTrackTimelineSegments() {
    if (
      this.kind !== "video" ||
      !this.timelineObjectsLayerEl ||
      !this.totalFrames ||
      this.totalFrames <= 0
    ) {
      return;
    }

    const layer = this.timelineObjectsLayerEl;
    layer.innerHTML = "";

    const tracks = this.computeTrackMap();
    if (!tracks.size) {
      return;
    }

    const maxFrame = this.totalFrames - 1;

    tracks.forEach((t, trackId) => {
      const start = Math.max(0, Math.min(maxFrame, t.startFrame | 0));
      const end = Math.max(start, Math.min(maxFrame, t.endFrame | 0));
      if (end < 0 || start > maxFrame) return;

      const startRatio = maxFrame > 0 ? start / maxFrame : 0;
      const endRatio = maxFrame > 0 ? end / maxFrame : 0;
      const leftPct = startRatio * 100;
      const rightPct = endRatio * 100;
      const widthPct = Math.max(0.8, rightPct - leftPct);

      const row = document.createElement("div");
      row.className = "object-timeline-row";
      row.dataset.trackId = String(trackId);
      if (this.activeTrackId != null && trackId === this.activeTrackId) {
        row.classList.add("active");
      }
      if (this.isTrackHidden(trackId)) {
        row.classList.add("is-muted");
      } else if (!this.isTrackVisible(trackId)) {
        row.classList.add("is-muted");
      }

      const labelEl = document.createElement("div");
      labelEl.className = "object-timeline-label";
      const lc = this.labelClasses.find((x) => x.id === t.label_class_id);
      labelEl.textContent = lc
        ? `${lc.name} (Object ${trackId})`
        : `Object ${trackId}`;
      row.appendChild(labelEl);

      const bar = document.createElement("div");
      bar.className = "object-timeline-bar";

      const line = document.createElement("div");
      line.className = "object-timeline-line";
      bar.appendChild(line);

      const range = document.createElement("div");
      range.className = "object-timeline-range timeline-object-segment";
      range.dataset.trackId = String(trackId);
      range.style.left = `${leftPct}%`;
      range.style.width = `${widthPct}%`;

      if (this.activeTrackId != null && trackId === this.activeTrackId) {
        range.classList.add("active");
      }

      const handleStart = document.createElement("div");
      handleStart.className =
        "timeline-object-handle timeline-object-handle-start";
      const body = document.createElement("div");
      body.className = "timeline-object-body";
      const handleEnd = document.createElement("div");
      handleEnd.className =
        "timeline-object-handle timeline-object-handle-end";

      range.appendChild(handleStart);
      range.appendChild(body);
      range.appendChild(handleEnd);
      bar.appendChild(range);

      this.getTrackGaps(trackId).forEach((gap) => {
        const gapStartRatio = maxFrame > 0 ? gap.startFrame / maxFrame : 0;
        const gapEndRatio = maxFrame > 0 ? gap.endFrame / maxFrame : 0;
        const gapEl = document.createElement("div");
        gapEl.className = "object-timeline-gap";
        gapEl.style.left = `${gapStartRatio * 100}%`;
        gapEl.style.width = `${Math.max(0.6, (gapEndRatio - gapStartRatio) * 100)}%`;
        gapEl.title = `Gap ${gap.startFrame + 1}-${gap.endFrame + 1}`;
        bar.appendChild(gapEl);
      });

      const framesForTrack = this.getTrackKeyframes(trackId);
      const markerFrames = this.sampleMarkerFrames(framesForTrack, 120);
      markerFrames.forEach((frameIdx) => {
        const marker = document.createElement("div");
        marker.className = "object-timeline-keyframe";
        if (
          this.interpolationSelection.trackId === trackId &&
          frameIdx === this.interpolationSelection.startFrame
        ) {
          marker.classList.add("selected-start");
        }
        if (
          this.interpolationSelection.trackId === trackId &&
          frameIdx === this.interpolationSelection.endFrame
        ) {
          marker.classList.add("selected-end");
        }
        const ratio = maxFrame > 0 ? frameIdx / maxFrame : 0;
        marker.style.left = `${ratio * 100}%`;
        marker.dataset.frameIndex = String(frameIdx);
        marker.title = `Keyframe ${frameIdx + 1}`;
        marker.addEventListener("click", (evt) => {
          evt.stopPropagation();
          this.setActiveTrackId(trackId);
          this.seekToFrame(frameIdx);
        });
        bar.appendChild(marker);
      });

      row.appendChild(bar);
      layer.appendChild(row);

      const attachDrag = (element, mode) => {
        element.addEventListener("mousedown", (evt) => {
          if (this.isTrackLocked(trackId)) {
            evt.preventDefault();
            evt.stopPropagation();
            return;
          }
          evt.preventDefault();
          evt.stopPropagation();
          const rect = bar.getBoundingClientRect();
          this.startTimelineDrag(
            mode,
            trackId,
            start,
            end,
            evt.clientX,
            rect.left,
            rect.width
          );
        });
      };

      attachDrag(handleStart, "resize-start");
      attachDrag(handleEnd, "resize-end");
      attachDrag(body, "move");

      bar.addEventListener("click", (evt) => {
        const rect = bar.getBoundingClientRect();
        if (!rect.width) return;
        const ratio = (evt.clientX - rect.left) / rect.width;
        const clamped = Math.max(0, Math.min(1, ratio));
        const targetFrame = Math.round(clamped * (this.totalFrames - 1));
        this.setActiveTrackId(trackId);
        this.seekToFrame(targetFrame);
      });

      row.addEventListener("click", () => {
        this.setActiveTrackId(trackId);
      });
    });
  }

  setActiveTrackId(trackId) {
    if (Number.isInteger(trackId)) {
      this.revealTrack(trackId);
    }

    if (
      this.pendingReassignSelection &&
      Number.isInteger(trackId) &&
      trackId !== this.pendingReassignSelection.trackId
    ) {
      this.reassignSelectionToTarget(trackId);
      return;
    }

    if (
      Number.isInteger(this.pendingMergeTargetTrackId) &&
      Number.isInteger(trackId) &&
      trackId !== this.pendingMergeTargetTrackId
    ) {
      this.mergeTrackIntoTarget(this.pendingMergeTargetTrackId, trackId);
      return;
    }

    if (trackId == null) {
      this.pendingReassignSelection = null;
      this.pendingMergeTargetTrackId = null;
    }

    this.activeTrackId = trackId;

    if (trackId == null) {
      this.interpolationSelection = {
        trackId: null,
        startFrame: null,
        endFrame: null,
      };
    } else if (this.interpolationSelection.trackId !== trackId) {
      this.interpolationSelection = {
        trackId,
        startFrame: null,
        endFrame: null,
      };
    }

    this.updateTrackTimelineActiveState();
    this.updateInterpolationPanel();
    this.updateReferenceControls();
  }

  updateTrackTimelineActiveState() {
    if (!this.timelineObjectsLayerEl) return;
    const segments = this.timelineObjectsLayerEl.querySelectorAll(
      ".timeline-object-segment"
    );
    segments.forEach((seg) => {
      const idStr = seg.dataset.trackId;
      const id = idStr != null ? parseInt(idStr, 10) : null;
      if (this.activeTrackId != null && id === this.activeTrackId) {
        seg.classList.add("active");
      } else {
        seg.classList.remove("active");
      }
    });

    const rows = this.timelineObjectsLayerEl.querySelectorAll(
      ".object-timeline-row"
    );
    rows.forEach((row) => {
      const idStr = row.dataset.trackId;
      const id = idStr != null ? parseInt(idStr, 10) : null;
      if (this.activeTrackId != null && id === this.activeTrackId) {
        row.classList.add("active");
      } else {
        row.classList.remove("active");
      }
    });
  }

  getTrackFrameRange(trackId) {
    const track = this.computeTrackMap().get(trackId);
    if (!track) return null;
    return { start: track.startFrame, end: track.endFrame };
  }

  getManualKeyframesStorageKey() {
    return `vision-forge:item:${this.itemId}:manual-keyframes`;
  }

  buildDefaultManualKeyframeMap() {
    const nextMap = new Map();

    for (const annotation of this.getSparseAnnotations()) {
      if (annotation.track_id == null || annotation.frame_index == null) {
        continue;
      }

      const set = nextMap.get(annotation.track_id) || new Set();
      set.add(annotation.frame_index);
      nextMap.set(annotation.track_id, set);
    }

    return nextMap;
  }

  loadManualKeyframesFromStorage(defaultMap) {
    if (typeof window === "undefined" || !window.localStorage) {
      this.manualKeyframesByTrack = defaultMap;
      return;
    }

    let parsed = null;
    try {
      const raw = window.localStorage.getItem(this.getManualKeyframesStorageKey());
      parsed = raw ? JSON.parse(raw) : null;
    } catch (_error) {
      parsed = null;
    }

    if (!parsed || typeof parsed !== "object") {
      this.manualKeyframesByTrack = defaultMap;
      return;
    }

    const hydrated = new Map();
    const knownTrackIds = new Set(defaultMap.keys());

    for (const trackId of knownTrackIds) {
      const storedFrames = Array.isArray(parsed[String(trackId)])
        ? parsed[String(trackId)]
        : Array.from(defaultMap.get(trackId) || []);

      const normalizedFrames = [...new Set(
        storedFrames
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value))
          .map((value) => Math.max(0, Math.trunc(value)))
      )].sort((left, right) => left - right);

      if (normalizedFrames.length) {
        hydrated.set(trackId, new Set(normalizedFrames));
      } else if (defaultMap.has(trackId)) {
        hydrated.set(trackId, new Set(defaultMap.get(trackId)));
      }
    }

    this.manualKeyframesByTrack = hydrated;
    this.persistManualKeyframesToStorage();
  }

  persistManualKeyframesToStorage() {
    if (typeof window === "undefined" || !window.localStorage) {
      return;
    }

    const payload = {};
    for (const [trackId, frames] of this.manualKeyframesByTrack.entries()) {
      payload[String(trackId)] = Array.from(frames).sort((left, right) => left - right);
    }

    try {
      window.localStorage.setItem(
        this.getManualKeyframesStorageKey(),
        JSON.stringify(payload)
      );
    } catch (_error) {
      // Ignore storage quota and serialization failures.
    }
  }

  getTrackKeyframes(trackId) {
    const trackRange = this.getTrackFrameRange(trackId);
    const storedFrames = this.manualKeyframesByTrack.get(trackId);
    const fallbackFrames = this.getTrackSparseAnnotations(trackId).map(
      (annotation) => annotation.frame_index ?? 0
    );

    const sourceFrames = storedFrames ? Array.from(storedFrames) : fallbackFrames;
    const normalized = [...new Set(sourceFrames)]
      .filter((frameIndex) => Number.isFinite(frameIndex))
      .map((frameIndex) => Math.max(0, Math.trunc(frameIndex)))
      .filter((frameIndex) => {
        if (!trackRange) return true;
        return frameIndex >= trackRange.start && frameIndex <= trackRange.end;
      })
      .sort((left, right) => left - right);

    if (!normalized.length && trackRange) {
      return [trackRange.start];
    }

    return normalized;
  }

  setTrackKeyframes(trackId, frames) {
    if (trackId == null) return;

    const normalized = [...new Set(frames)]
      .filter((frameIndex) => Number.isFinite(frameIndex))
      .map((frameIndex) => Math.max(0, Math.trunc(frameIndex)))
      .sort((left, right) => left - right);

    if (!normalized.length) {
      this.manualKeyframesByTrack.delete(trackId);
    } else {
      this.manualKeyframesByTrack.set(trackId, new Set(normalized));
    }

    this.persistManualKeyframesToStorage();
  }

  addManualKeyframe(trackId, frameIndex) {
    if (trackId == null || frameIndex == null) return;
    const nextFrames = this.getTrackKeyframes(trackId);
    nextFrames.push(frameIndex);
    this.setTrackKeyframes(trackId, nextFrames);
  }

  removeManualKeyframe(trackId, frameIndex) {
    if (trackId == null || frameIndex == null) return;
    const nextFrames = this.getTrackKeyframes(trackId).filter(
      (value) => value !== frameIndex
    );
    this.setTrackKeyframes(trackId, nextFrames);
  }

  shiftTrackKeyframes(trackId, deltaFrames) {
    if (trackId == null || !deltaFrames) return;

    const nextFrames = this.getTrackKeyframes(trackId).map(
      (frameIndex) => Math.max(0, frameIndex + deltaFrames)
    );
    this.setTrackKeyframes(trackId, nextFrames);

    if (this.interpolationSelection.trackId === trackId) {
      if (this.interpolationSelection.startFrame != null) {
        this.interpolationSelection.startFrame = Math.max(
          0,
          this.interpolationSelection.startFrame + deltaFrames
        );
      }
      if (this.interpolationSelection.endFrame != null) {
        this.interpolationSelection.endFrame = Math.max(
          0,
          this.interpolationSelection.endFrame + deltaFrames
        );
      }
    }
  }

  syncTrackKeyframesToRange(trackId, startFrame, endFrame) {
    if (trackId == null) return;

    const nextFrames = this.getTrackKeyframes(trackId).filter(
      (frameIndex) => frameIndex >= startFrame && frameIndex <= endFrame
    );
    nextFrames.push(startFrame);
    nextFrames.push(endFrame);
    this.setTrackKeyframes(trackId, nextFrames);

    if (this.interpolationSelection.trackId === trackId) {
      if (
        this.interpolationSelection.startFrame != null &&
        (
          this.interpolationSelection.startFrame < startFrame ||
          this.interpolationSelection.startFrame > endFrame
        )
      ) {
        this.interpolationSelection.startFrame = null;
      }
      if (
        this.interpolationSelection.endFrame != null &&
        (
          this.interpolationSelection.endFrame < startFrame ||
          this.interpolationSelection.endFrame > endFrame
        )
      ) {
        this.interpolationSelection.endFrame = null;
      }
    }
  }

  getTrackSparseAnnotationAtFrame(trackId, frameIndex) {
    return (
      this.getTrackSparseAnnotations(trackId).find(
        (annotation) => (annotation.frame_index ?? 0) === frameIndex
      ) || null
    );
  }

  materializeTrackKeyframe(trackId, frameIndex) {
    const existing = this.getTrackSparseAnnotationAtFrame(trackId, frameIndex);
    if (existing) {
      return existing;
    }

    const segments = this.getTrackSegments(trackId);
    if (!segments.length) {
      return null;
    }

    let splitOccurred = false;
    const nextSegments = [];

    for (const segment of segments) {
      if (frameIndex < segment.startFrame || frameIndex > segment.endFrame) {
        nextSegments.push(segment);
        continue;
      }

      if (segment.startFrame < frameIndex) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            frameIndex - 1
          )
        );
      }

      nextSegments.push(
        this.createSegment(
          segment.annotation,
          frameIndex,
          segment.endFrame,
          { preserveStart: true }
        )
      );
      splitOccurred = true;
    }

    if (!splitOccurred) {
      return null;
    }

    this.setTrackSegments(trackId, nextSegments);
    return this.getTrackSparseAnnotationAtFrame(trackId, frameIndex);
  }

  setInterpolationBoundary(kind) {
    const trackId = this.activeTrackId;
    if (trackId == null) {
      alert("Select an object first.");
      return;
    }

    const trackRange = this.getTrackFrameRange(trackId);
    if (!trackRange) {
      alert("The selected object has no valid frame range.");
      return;
    }

    const frameIndex = this.currentFrameIndex | 0;
    if (frameIndex < trackRange.start || frameIndex > trackRange.end) {
      alert("Move to a frame inside the selected object's range first.");
      return;
    }

    if (this.interpolationSelection.trackId !== trackId) {
      this.interpolationSelection = {
        trackId,
        startFrame: null,
        endFrame: null,
      };
    }

    if (kind === "start") {
      this.interpolationSelection.startFrame = frameIndex;
    } else {
      this.interpolationSelection.endFrame = frameIndex;
    }

    this.updateTimelineAnnotations();
    this.updateInterpolationPanel();
  }

  clearInterpolationSelection() {
    this.interpolationSelection = {
      trackId: this.activeTrackId ?? null,
      startFrame: null,
      endFrame: null,
    };
    this.updateTimelineAnnotations();
    this.updateInterpolationPanel();
  }

  applyInterpolationSelection() {
    if (this.readOnly || this.kind !== "video") return;

    const trackId = this.interpolationSelection.trackId ?? this.activeTrackId;
    if (trackId == null) {
      alert("Select an object first.");
      return;
    }

    let startFrame = this.interpolationSelection.startFrame;
    let endFrame = this.interpolationSelection.endFrame;

    if (startFrame == null || endFrame == null) {
      alert("Select both interpolation points first.");
      return;
    }

    if (startFrame === endFrame) {
      alert("Interpolation requires two different frames.");
      return;
    }

    if (startFrame > endFrame) {
      [startFrame, endFrame] = [endFrame, startFrame];
    }

    const startKeyframe = this.materializeTrackKeyframe(trackId, startFrame);
    const endKeyframe = this.materializeTrackKeyframe(trackId, endFrame);

    if (!startKeyframe || !endKeyframe) {
      alert("Failed to prepare the selected interpolation frames.");
      return;
    }

    if (startKeyframe.label_class_id !== endKeyframe.label_class_id) {
      alert("Interpolation requires the same label class on both frames.");
      return;
    }

    const geometryKind = this.getGeometryKindForLabel(
      startKeyframe.label_class_id
    );
    const startPolygonPoints = clonePolygonPoints(startKeyframe.polygon_points);
    const endPolygonPoints = clonePolygonPoints(endKeyframe.polygon_points);

    if (geometryKind === "polygon") {
      if (
        !startPolygonPoints ||
        !endPolygonPoints ||
        startPolygonPoints.length !== endPolygonPoints.length
      ) {
        alert(
          "Polygon interpolation requires the same number of vertices on both frames."
        );
        return;
      }
    }

    const preservedManualFrames = this.getTrackKeyframes(trackId).filter(
      (frameIndex) => frameIndex < startFrame || frameIndex > endFrame
    );

    const segments = this.getTrackSegments(trackId);
    const nextSegments = [];

    for (const segment of segments) {
      if (segment.endFrame < startFrame || segment.startFrame > endFrame) {
        nextSegments.push(segment);
        continue;
      }

      if (segment.startFrame < startFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            segment.startFrame,
            startFrame - 1
          )
        );
      }

      if (segment.endFrame > endFrame) {
        nextSegments.push(
          this.createSegment(
            segment.annotation,
            endFrame + 1,
            segment.endFrame
          )
        );
      }
    }

    const frameSpan = endFrame - startFrame;
    for (let frameIndex = startFrame; frameIndex <= endFrame; frameIndex += 1) {
      const ratio = frameSpan === 0 ? 0 : (frameIndex - startFrame) / frameSpan;

      const interpolatedAnnotation = {
        id: null,
        label_class_id: startKeyframe.label_class_id,
        status: startKeyframe.status || "pending",
        track_id: trackId,
      };

      if (geometryKind === "polygon") {
        interpolatedAnnotation.polygon_points = lerpPolygonPoints(
          startPolygonPoints,
          endPolygonPoints,
          ratio
        );
        syncPolygonBounds(interpolatedAnnotation);
      } else {
        interpolatedAnnotation.x1 = lerp(startKeyframe.x1, endKeyframe.x1, ratio);
        interpolatedAnnotation.y1 = lerp(startKeyframe.y1, endKeyframe.y1, ratio);
        interpolatedAnnotation.x2 = lerp(startKeyframe.x2, endKeyframe.x2, ratio);
        interpolatedAnnotation.y2 = lerp(startKeyframe.y2, endKeyframe.y2, ratio);
      }

      nextSegments.push(
        this.createSegment(
          interpolatedAnnotation,
          frameIndex,
          frameIndex
        )
      );
    }

    this.setTrackSegments(trackId, nextSegments);
    this.setTrackKeyframes(trackId, [
      ...preservedManualFrames,
      startFrame,
      endFrame,
    ]);

    this.interpolationSelection = {
      trackId,
      startFrame,
      endFrame,
    };

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.restoreActiveAnnotationForTrack(trackId);
    this.updateTimelineAnnotations();
    this.updateTimelinePlayhead();
    this.scheduleSave();
  }

  updateInterpolationPanel() {
    if (!this.interpolationPanelEl) return;

    const setButtonDisabled = (id, disabled) => {
      const button = document.getElementById(id);
      if (button) {
        button.disabled = disabled;
      }
    };

    const trackId = this.activeTrackId;
    const trackRange = trackId != null ? this.getTrackFrameRange(trackId) : null;

    if (trackId == null || !trackRange) {
      if (this.interpolationTrackLabelEl) {
        this.interpolationTrackLabelEl.textContent =
          "Select an object on the canvas or object timeline.";
      }
      if (this.interpolationKeyframesEl) {
        this.interpolationKeyframesEl.innerHTML =
          '<div class="small text-secondary">No object selected.</div>';
      }
      if (this.interpolationStartFrameEl) {
        this.interpolationStartFrameEl.textContent = "–";
      }
      if (this.interpolationEndFrameEl) {
        this.interpolationEndFrameEl.textContent = "–";
      }
      if (this.interpolationHintEl) {
        this.interpolationHintEl.textContent =
          "Pick an object first, then choose two frames and apply interpolation only when you want it.";
      }

      setButtonDisabled("btn-set-interpolation-start", true);
      setButtonDisabled("btn-set-interpolation-end", true);
      setButtonDisabled("btn-apply-interpolation", true);
      setButtonDisabled("btn-clear-interpolation", true);
      return;
    }

    if (this.interpolationSelection.trackId !== trackId) {
      this.interpolationSelection = {
        trackId,
        startFrame: null,
        endFrame: null,
      };
    }

    const hasTrackFrame = (frameIndex) =>
      frameIndex != null && !!this.findActiveSparseAnnotation(trackId, frameIndex);

    if (!hasTrackFrame(this.interpolationSelection.startFrame)) {
      this.interpolationSelection.startFrame = null;
    }
    if (!hasTrackFrame(this.interpolationSelection.endFrame)) {
      this.interpolationSelection.endFrame = null;
    }

    if (this.interpolationTrackLabelEl) {
      this.interpolationTrackLabelEl.textContent =
        `Active object: Obj ${trackId} · Range ${trackRange.start + 1}-${trackRange.end + 1}`;
    }

    const keyframes = this.getTrackKeyframes(trackId);
    if (this.interpolationKeyframesEl) {
      this.interpolationKeyframesEl.innerHTML = "";

      if (!keyframes.length) {
        this.interpolationKeyframesEl.innerHTML =
          '<div class="small text-secondary">No keyframes yet.</div>';
      } else {
        keyframes.forEach((frameIndex) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "interpolation-keyframe-btn";
          if (frameIndex === this.currentFrameIndex) {
            button.classList.add("current");
          }

          const tags = [];
          if (frameIndex === this.interpolationSelection.startFrame) {
            tags.push("A");
          }
          if (frameIndex === this.interpolationSelection.endFrame) {
            tags.push("B");
          }

          button.textContent =
            tags.length > 0
              ? `F ${frameIndex + 1} · ${tags.join("/")}`
              : `F ${frameIndex + 1}`;

          button.addEventListener("click", () => {
            this.setActiveTrackId(trackId);
            this.seekToFrame(frameIndex);
          });

          this.interpolationKeyframesEl.appendChild(button);
        });
      }
    }

    if (this.interpolationStartFrameEl) {
      this.interpolationStartFrameEl.textContent =
        this.interpolationSelection.startFrame == null
          ? "–"
          : `Frame ${this.interpolationSelection.startFrame + 1}`;
    }

    if (this.interpolationEndFrameEl) {
      this.interpolationEndFrameEl.textContent =
        this.interpolationSelection.endFrame == null
          ? "–"
          : `Frame ${this.interpolationSelection.endFrame + 1}`;
    }

    const currentFrameInsideTrack =
      this.currentFrameIndex >= trackRange.start &&
      this.currentFrameIndex <= trackRange.end;

    if (this.interpolationHintEl) {
      this.interpolationHintEl.textContent = currentFrameInsideTrack
        ? `Current frame ${this.currentFrameIndex + 1} is inside the selected object range. You can use it as A or B even if it is not already listed as a manual keyframe.`
        : "Move to a frame inside the selected object range, then set A and B.";
    }

    setButtonDisabled("btn-set-interpolation-start", !currentFrameInsideTrack);
    setButtonDisabled("btn-set-interpolation-end", !currentFrameInsideTrack);
    setButtonDisabled(
      "btn-apply-interpolation",
      this.interpolationSelection.startFrame == null ||
        this.interpolationSelection.endFrame == null ||
        this.interpolationSelection.startFrame === this.interpolationSelection.endFrame
    );
    setButtonDisabled(
      "btn-clear-interpolation",
      this.interpolationSelection.startFrame == null &&
        this.interpolationSelection.endFrame == null
    );
    this.updateReferenceControls();
  }


  restoreActiveAnnotationForTrack(trackId, options = {}) {
    if (this.kind !== "video" || trackId == null) return;

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    const targetAnnotation =
      this.annotations.find((annotation) => annotation.track_id === trackId) || null;

    if (!targetAnnotation) {
      this.activeAnnotation = null;
      this.activeVertexIndex = null;
      this.setActiveTrackId(null);
      this.requestRedraw();
      return;
    }

    this.markActiveAnnotation(targetAnnotation, {
      vertexIndex:
        Number.isInteger(options.vertexIndex) ? options.vertexIndex : null,
    });

    if (this.isDragging) {
      this.draggedAnnotation = targetAnnotation;
    }

    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
      this.updateTimelinePlayhead();
    }
  }


  startTimelineDrag(mode, trackId, startFrame, endFrame, clientX, rectLeft, rectWidth) {
    if (
      this.kind !== "video" ||
      !this.totalFrames ||
      this.totalFrames <= 0
    ) {
      return;
    }

    if (this.mediaEl.tagName === "VIDEO" && !this.mediaEl.paused) {
      this.mediaEl.pause();
      this.stopRenderLoop();
    }

    if (!rectWidth) {
      return;
    }

    this.timelineDragState = {
      mode,
      trackId,
      startFrame,
      endFrame,
      pointerStartX: clientX,
      rectLeft,
      rectWidth,
      originalStartFrame: startFrame,
      originalEndFrame: endFrame,
    };

    document.addEventListener("mousemove", this.onTimelineDragMoveBound);
    document.addEventListener("mouseup", this.onTimelineDragEndBound);

    this.setActiveTrackId(trackId);
  }

  onTimelineDragMove(evt) {
    const state = this.timelineDragState;
    if (
      !state ||
      !this.totalFrames ||
      this.totalFrames <= 0
    ) {
      return;
    }

    const maxFrame = this.totalFrames - 1;

    const dx = evt.clientX - state.pointerStartX;
    const ratioDelta = dx / state.rectWidth;
    let frameDelta = Math.round(ratioDelta * maxFrame);

    let newStart = state.startFrame;
    let newEnd = state.endFrame;

    if (state.mode === "move") {
      const length = state.originalEndFrame - state.originalStartFrame;
      let desiredStart = state.originalStartFrame + frameDelta;
      let desiredEnd = desiredStart + length;

      if (desiredStart < 0) {
        desiredStart = 0;
        desiredEnd = length;
      }
      if (desiredEnd > maxFrame) {
        desiredEnd = maxFrame;
        desiredStart = Math.max(0, maxFrame - length);
      }

      newStart = desiredStart;
      newEnd = desiredEnd;

      const incrementalDelta = newStart - state.startFrame;
      if (!incrementalDelta) {
        return;
      }
      this.moveTrack(state.trackId, incrementalDelta);
      state.startFrame = newStart;
      state.endFrame = newEnd;
    } else if (state.mode === "resize-start") {
      newStart = state.originalStartFrame + frameDelta;
      newStart = Math.max(0, Math.min(newStart, state.endFrame));
      if (newStart === state.startFrame) {
        return;
      }
      state.startFrame = newStart;
      this.updateTrackRange(state.trackId, newStart, state.endFrame);
    } else if (state.mode === "resize-end") {
      newEnd = state.originalEndFrame + frameDelta;
      newEnd = Math.min(maxFrame, Math.max(newEnd, state.startFrame));
      if (newEnd === state.endFrame) {
        return;
      }
      state.endFrame = newEnd;
      this.updateTrackRange(state.trackId, state.startFrame, newEnd);
    } else {
      return;
    }

    this.loadFrame(this.currentFrameIndex, false);
    this.updateTimelineAnnotations();
    this.updateTimelinePlayhead();
  }

  onTimelineDragEnd() {
    if (!this.timelineDragState) {
      return;
    }
    this.timelineDragState = null;
    document.removeEventListener("mousemove", this.onTimelineDragMoveBound);
    document.removeEventListener("mouseup", this.onTimelineDragEndBound);
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  updateTrackRange(trackId, newStart, newEnd) {
    if (this.kind !== "video") return;
    if (!this.totalFrames || this.totalFrames <= 0) return;

    const maxFrame = this.totalFrames - 1;
    const startFrame = Math.max(0, Math.min(maxFrame, newStart | 0));
    const endFrame = Math.max(startFrame, Math.min(maxFrame, newEnd | 0));

    const segments = this.getTrackSegments(trackId);
    if (!segments.length) {
      return;
    }

    const nextSegments = [];
    for (const segment of segments) {
      const clippedStart = Math.max(segment.startFrame, startFrame);
      const clippedEnd = Math.min(segment.endFrame, endFrame);

      if (clippedStart > clippedEnd) {
        continue;
      }

      nextSegments.push(
        this.createSegment(segment.annotation, clippedStart, clippedEnd)
      );
    }

    if (!nextSegments.length) {
      nextSegments.push(
        this.createSegment(segments[0].annotation, startFrame, startFrame)
      );
    }

    nextSegments.sort((left, right) => left.startFrame - right.startFrame);

    if (startFrame < nextSegments[0].startFrame) {
      nextSegments.unshift(
        this.createSegment(
          nextSegments[0].annotation,
          startFrame,
          nextSegments[0].startFrame - 1
        )
      );
    }

    const lastSegment = nextSegments[nextSegments.length - 1];
    if (endFrame > lastSegment.endFrame) {
      nextSegments.push(
        this.createSegment(
          lastSegment.annotation,
          lastSegment.endFrame + 1,
          endFrame
        )
      );
    }

    this.setTrackSegments(trackId, nextSegments);
    this.syncTrackKeyframesToRange(trackId, startFrame, endFrame);
  }


  moveTrack(trackId, deltaFrames) {
    if (!deltaFrames) return;
    if (this.kind !== "video") return;
    if (!this.totalFrames || this.totalFrames <= 0) return;

    const segments = this.getTrackSegments(trackId);
    if (!segments.length) return;

    const maxFrame = this.totalFrames - 1;
    const originalStart = segments[0].startFrame;
    const originalEnd = segments[segments.length - 1].endFrame;

    let adjustedDelta = deltaFrames;
    if (originalStart + adjustedDelta < 0) {
      adjustedDelta = -originalStart;
    }
    if (originalEnd + adjustedDelta > maxFrame) {
      adjustedDelta = maxFrame - originalEnd;
    }
    if (!adjustedDelta) return;

    const movedSegments = segments.map((segment) =>
      this.createSegment(
        segment.annotation,
        segment.startFrame + adjustedDelta,
        segment.endFrame + adjustedDelta
      )
    );

    this.setTrackSegments(trackId, movedSegments);
    this.shiftTrackKeyframes(trackId, adjustedDelta);
  }


  deleteTrack(trackId) {
    if (this.kind !== "video") return;
    if (this.isTrackLocked(trackId)) return;

    const hadTrack = this.getTrackSparseAnnotations(trackId).length > 0;
    if (!hadTrack) return;

    this.clearTrackSparseAnnotations(trackId);
    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.trackViewStateById.delete(trackId);
    if (this.soloTrackId === trackId) {
      this.soloTrackId = null;
    }
    if (
      this.pendingReassignSelection &&
      this.pendingReassignSelection.trackId === trackId
    ) {
      this.pendingReassignSelection = null;
    }
    if (this.pendingMergeTargetTrackId === trackId) {
      this.pendingMergeTargetTrackId = null;
    }
    this.persistTrackUiStateToStorage();

    this.manualKeyframesByTrack.delete(trackId);
    this.persistManualKeyframesToStorage();

    if (this.interpolationSelection.trackId === trackId) {
      this.interpolationSelection = {
        trackId: null,
        startFrame: null,
        endFrame: null,
      };
    }

    if (this.activeTrackId === trackId) {
      this.activeTrackId = null;
    }
    if (this.activeAnnotation && this.activeAnnotation.track_id === trackId) {
      this.activeAnnotation = null;
      this.activeVertexIndex = null;
    }

    this.requestRedraw();
    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
      this.updateTimelinePlayhead();
    }
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }


  handleDeleteKey() {
    if (this.readOnly || !this.activeAnnotation) return;
    if (
      this.activeAnnotation.track_id != null &&
      this.isTrackLocked(this.activeAnnotation.track_id)
    ) {
      return;
    }

    const ann = this.activeAnnotation;
    if (this.isPolygonAnnotation(ann) && this.activeVertexIndex != null) {
      this.deletePolygonVertex(ann, this.activeVertexIndex);
      return;
    }

    if (this.kind !== "video") {
      const ok = window.confirm("Delete this annotation?");
      if (!ok) return;
      this.deleteAnnotationOnCurrentFrame(ann);
      return;
    }

    if (ann.track_id != null) {
      const choice = window.prompt(
        "Delete the active annotation?\n\n" +
          "Enter one of:\n" +
          "  f = delete only this frame\n" +
          "  o = delete this object across all frames\n" +
          "  (anything else = cancel)"
      );
      if (!choice) {
        return;
      }

      const c = choice.trim().toLowerCase();
      if (c === "f") {
        this.deleteAnnotationOnCurrentFrame(ann);
      } else if (c === "o") {
        const ok = window.confirm(
          "Delete this object across all propagated frames?\n" +
            "This cannot be undone."
        );
        if (!ok) return;
        this.deleteTrack(ann.track_id);
      }
      return;
    }

    const ok = window.confirm("Delete this annotation on this frame?");
    if (!ok) return;
    this.deleteAnnotationOnCurrentFrame(ann);
  }

  deleteAnnotationOnCurrentFrame(annotation) {
    if (this.readOnly || !annotation) return;

    const contextClientUid =
      annotation.client_uid ||
      annotation._storedAnnotation?.client_uid ||
      null;

    if (this.kind === "video") {
      if (annotation.track_id != null) {
        this.removeTrackFrame(annotation.track_id, this.currentFrameIndex);
      } else if (annotation._storedAnnotation) {
        this.hiddenAnnotationClientUids.delete(annotation._storedAnnotation.client_uid);
        this.removeSparseAnnotation(annotation._storedAnnotation);
      }
      this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    } else {
      const idx = this.annotations.indexOf(annotation);
      if (idx >= 0) {
        this.annotations.splice(idx, 1);
      }
      if (annotation.client_uid) {
        this.hiddenAnnotationClientUids.delete(annotation.client_uid);
      }

      if (this.activeAnnotation === annotation) {
        this.activeAnnotation = null;
        this.activeVertexIndex = null;
      }
    }

    this.hideObjectContextMenu();

    if (
      this.kind === "video" &&
      this.timelineInitialized &&
      this.totalFrames
    ) {
      this.updateTimelineAnnotations();
    }

    this.updateFastActionButtons();
    this.requestRedraw();
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }


  clampPointToImage(x, y) {
    return { x, y };
  }

  clampAnnotationToImage(ann) {
    return ann;
  }

  toImageCoordinates(evt) {
    const rect = this.canvas.getBoundingClientRect();
    const xCss = evt.clientX - rect.left;
    const yCss = evt.clientY - rect.top;
    const scale = (this.baseScale || 1) * (this.zoom || 1);

    if (!scale) {
      return { x: 0, y: 0 };
    }

    const xImg = (xCss - this.translateX) / scale;
    const yImg = (yCss - this.translateY) / scale;

    return { x: xImg, y: yImg };
  }

  fromImageToCanvasCoords(x, y) {
    const scale = (this.baseScale || 1) * (this.zoom || 1);
    const sx = x * scale + this.translateX;
    const sy = y * scale + this.translateY;
    return { x: sx, y: sy };
  }

  findAnnotationAtCanvasPoint(x, y) {
    const handleRadius = this.handleRadius || 6;
    const handleRadiusSq = handleRadius * handleRadius;
    let bestHit = null;

    for (let i = 0; i < this.annotations.length; i += 1) {
      const ann = this.annotations[i];
      if (!this.isAnnotationVisible(ann)) continue;
      const geometryKind = this.getGeometryKindForLabel(ann.label_class_id);
      const adapter = getGeometryAdapter(geometryKind);

      const hit = adapter.hitTest({
        annotation: ann,
        index: i,
        xCanvas: x,
        yCanvas: y,
        fromImageToCanvasCoords: (xi, yi) =>
          this.fromImageToCanvasCoords(xi, yi),
        handleRadiusSq,
      });

      if (hit) {
        if (!bestHit || compareAnnotationHits(hit, bestHit, this.activeAnnotation) < 0) {
          bestHit = hit;
        }
      }
    }
    return bestHit;
  }

  getCursorForHit(hit) {
    if (!hit) {
      return this.readOnly ? "default" : "crosshair";
    }
    if (hit.ann && hit.ann.track_id != null && this.isTrackLocked(hit.ann.track_id)) {
      return "not-allowed";
    }
    if (this.readOnly) {
      return "pointer";
    }
    if (typeof hit.handle === "string" && hit.handle.startsWith("vertex:")) {
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

  updateHoverCursor(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }

    const xCanvas = clientX - rect.left;
    const yCanvas = clientY - rect.top;

    if (
      xCanvas < 0 ||
      yCanvas < 0 ||
      xCanvas > rect.width ||
      yCanvas > rect.height
    ) {
      this.canvas.style.cursor = "default";
      return;
    }

    const hit = this.findAnnotationAtCanvasPoint(xCanvas, yCanvas);
    this.canvas.style.cursor = this.getCursorForHit(hit);
  }

  sampleMarkerFrames(frames, maxMarkers = 120) {
    if (!frames || !frames.length || frames.length <= maxMarkers) {
      return frames || [];
    }

    const sampled = [];
    const lastIndex = frames.length - 1;

    for (let i = 0; i < maxMarkers; i++) {
      const idx = Math.round((i * lastIndex) / (maxMarkers - 1));
      const frame = frames[idx];
      if (sampled[sampled.length - 1] !== frame) {
        sampled.push(frame);
      }
    }

    return sampled;
  }

  getTimelineMarkerFrames(frames) {
    if (!frames || !frames.length) {
      return [];
    }

    const markers = [frames[0]];

    for (let i = 1; i < frames.length; i++) {
      const prev = frames[i - 1];
      const current = frames[i];

      if (current !== prev + 1) {
        if (markers[markers.length - 1] !== prev) {
          markers.push(prev);
        }
        markers.push(current);
      }
    }

    const last = frames[frames.length - 1];
    if (markers[markers.length - 1] !== last) {
      markers.push(last);
    }

    return this.sampleMarkerFrames(markers, 120);
  }

  ensureFrame(frameIndex, copyFromPrev) {
    const key = this.kind === "video" ? frameIndex : null;
    if (this.frameAnnotations.has(key)) return;

    if (this.kind === "video") {
      this.frameAnnotations.set(key, []);
      return;
    }

    this.frameAnnotations.set(key, []);
  }


  loadFrame(frameIndex, copyFromPrev) {
    if (this.kind === "video") {
      const idx = typeof frameIndex === "number" ? frameIndex : 0;
      this.setCurrentFrame(idx, {
        source: "internal",
        copyFromPrev: !!copyFromPrev,
      });
      return;
    }

    // Image items: single logical frame at key = null.
    const key = null;
    this.ensureFrame(null, false);
    this.annotations = this.frameAnnotations.get(key) || [];
    this.currentFrameIndex = 0;
    this.lastFrameIndex = 0;
    this.activeAnnotation = null;
    this.activeVertexIndex = null;
    this.updateFrameDisplay();
    this.requestRedraw();
  }

  findPreviousFrameIndex(target) {
    for (let frameIndex = target - 1; frameIndex >= 0; frameIndex -= 1) {
      if (this.buildAnnotationsForFrame(frameIndex).length > 0) {
        return frameIndex;
      }
    }
    return null;
  }


  async copyFromPreviousItemLastFrame() {
    if (this.readOnly || this.kind !== "video") return;

    if (!this.prevItemId) {
      alert("No previous item is available in this project.");
      return;
    }

    if (this.mediaEl.tagName === "VIDEO" && !this.mediaEl.paused) {
      this.mediaEl.pause();
      this.stopRenderLoop();
    }

    const url = `${this.apiBase}/items/${this.prevItemId}/annotations`;

    let src;
    try {
      const res = await fetch(url);
      if (!res.ok) {
        console.error("Failed to load previous item annotations", await res.text());
        alert("Failed to load previous item annotations");
        return;
      }
      src = await res.json();
    } catch (error) {
      console.error("Error while loading previous item annotations", error);
      alert("Error while loading previous item annotations");
      return;
    }

    let maxFrame = null;
    for (const annotation of src) {
      if (typeof annotation.frame_index === "number") {
        maxFrame =
          maxFrame == null
            ? annotation.frame_index
            : Math.max(maxFrame, annotation.frame_index);
      }
    }

    if (maxFrame == null) {
      alert("Previous item has no frame annotations to copy.");
      return;
    }

    const sourceOnLastFrame = src.filter(
      (annotation) =>
        typeof annotation.frame_index === "number" &&
        annotation.frame_index === maxFrame
    );

    if (!sourceOnLastFrame.length) {
      alert("No annotations found on the last annotated frame.");
      return;
    }

    if (this.annotations.length > 0) {
      const ok = window.confirm(
        `This frame already has ${this.annotations.length} visible annotation(s). Replace them with ${sourceOnLastFrame.length} copied annotation(s)?`
      );
      if (!ok) return;
      this.clearCurrentFrameAnnotations();
    }

    const trackMap = new Map();

    for (const annotation of sourceOnLastFrame) {
      const oldTrackId =
        typeof annotation.track_id === "number" ? annotation.track_id : null;

      let newTrackId;
      if (oldTrackId != null) {
        newTrackId = trackMap.get(oldTrackId);
        if (newTrackId == null) {
          newTrackId = this.nextTrackId++;
          trackMap.set(oldTrackId, newTrackId);
        }
      } else {
        newTrackId = this.nextTrackId++;
      }

      const newAnnotation = {
        client_uid: generateClientUid(),
        id: null,
        label_class_id: annotation.label_class_id,
        frame_index: this.currentFrameIndex,
        x1: annotation.x1,
        y1: annotation.y1,
        x2: annotation.x2,
        y2: annotation.y2,
        polygon_points: clonePolygonPoints(annotation.polygon_points),
        status: "pending",
        ...cloneAnnotationFlags(annotation),
        track_id: newTrackId,
        propagation_frames: 0,
      };

      this.clampAnnotationToImage(newAnnotation);
      this.propagateAnnotation(newAnnotation);
      this.commitVideoAnnotation(
        newAnnotation,
        newAnnotation.propagation_frames ?? 0
      );
      this.addManualKeyframe(newTrackId, this.currentFrameIndex);
    }

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);

    if (this.annotations.length > 0) {
      this.markActiveAnnotation(this.annotations[0]);
    } else {
      this.requestRedraw();
    }

    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
      this.updateTimelinePlayhead();
    }

    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }


  copyFromPrevious() {
    if (this.readOnly || this.kind !== "video") return;

    const previousFrame = this.findPreviousFrameIndex(this.currentFrameIndex);
    if (previousFrame === null) return;

    const previousAnnotations = this.buildAnnotationsForFrame(previousFrame);
    this.clearCurrentFrameAnnotations();

    for (const annotation of previousAnnotations) {
      const newAnnotation = {
        client_uid: generateClientUid(),
        id: null,
        label_class_id: annotation.label_class_id,
        frame_index: this.currentFrameIndex,
        x1: annotation.x1,
        y1: annotation.y1,
        x2: annotation.x2,
        y2: annotation.y2,
        polygon_points: clonePolygonPoints(annotation.polygon_points),
        status: annotation.status || "pending",
        ...cloneAnnotationFlags(annotation),
        track_id: annotation.track_id != null ? annotation.track_id : null,
        propagation_frames: 0,
      };
      this.commitVideoAnnotation(newAnnotation, 0);
      this.addManualKeyframe(newAnnotation.track_id, this.currentFrameIndex);
    }

    this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
    this.requestRedraw();

    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
    }
    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  copyActiveAnnotationToClipboard() {
    if (this.readOnly || !this.activeAnnotation) return false;
    if (
      this.activeAnnotation.track_id != null &&
      this.isTrackLocked(this.activeAnnotation.track_id)
    ) {
      return false;
    }
    if (!this.isAnnotationVisible(this.activeAnnotation)) return false;

    const source = this.activeAnnotation;
    this.annotationClipboard = {
      label_class_id: source.label_class_id,
      x1: source.x1,
      y1: source.y1,
      x2: source.x2,
      y2: source.y2,
      polygon_points: clonePolygonPoints(source.polygon_points),
      status: source.status || "pending",
      ...cloneAnnotationFlags(source),
    };
    return true;
  }

  pasteAnnotationFromClipboard() {
    if (this.readOnly || !this.annotationClipboard) return false;

    const source = this.annotationClipboard;
    const scale = (this.baseScale || 1) * (this.zoom || 1);
    const offset = Math.max(1, 12 / (scale || 1));
    const pastedAnnotation = syncPolygonBounds({
      client_uid: generateClientUid(),
      id: null,
      label_class_id: source.label_class_id,
      frame_index: this.kind === "video" ? this.currentFrameIndex : null,
      x1: source.x1 + offset,
      y1: source.y1 + offset,
      x2: source.x2 + offset,
      y2: source.y2 + offset,
      polygon_points: clonePolygonPoints(source.polygon_points)?.map(([x, y]) => [
        x + offset,
        y + offset,
      ]) || null,
      status: source.status || "pending",
      ...cloneAnnotationFlags(source),
      track_id: this.kind === "video" ? this.nextTrackId++ : null,
      propagation_frames: 0,
    });

    if (this.kind === "video") {
      this.commitVideoAnnotation(pastedAnnotation, 0);
      this.addManualKeyframe(pastedAnnotation.track_id, this.currentFrameIndex);
      this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
      this.restoreActiveAnnotationForTrack(pastedAnnotation.track_id);
      if (this.timelineInitialized && this.totalFrames) {
        this.updateTimelineAnnotations();
        this.updateTimelinePlayhead();
      }
    } else {
      this.annotations.push(pastedAnnotation);
      this.frameAnnotations.set(null, this.annotations);
      this.markActiveAnnotation(pastedAnnotation);
    }

    this.pushHistoryCheckpoint();
    this.updateFastActionButtons();
    this.scheduleSave();
    return true;
  }

  duplicateActiveAnnotation() {
    if (!this.copyActiveAnnotationToClipboard()) return;
    this.pasteAnnotationFromClipboard();
  }


  computeTotalFrames() {
    if (this.kind !== "video") return null;
    const duration =
      typeof this.mediaEl.duration === "number" && this.mediaEl.duration > 0
        ? this.mediaEl.duration
        : this.durationSec || 0;
    if (!duration || !isFinite(duration) || duration <= 0) return null;
    return Math.max(1, Math.round(duration * this.fps));
  }

  clampFrameIndex(frameIndex) {
    const normalized = Math.max(0, Math.trunc(Number(frameIndex) || 0));
    if (this.totalFrames && this.totalFrames > 0) {
      return Math.min(this.totalFrames - 1, normalized);
    }
    return normalized;
  }

  isFrameTransitionPending() {
    return this.kind === "video" && Number.isInteger(this.pendingFrameIndex);
  }

  isFrameNavigationShortcut(event) {
    if (!event) return false;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      return !event.altKey;
    }

    const key = String(event.key || "").toLowerCase();
    return !event.ctrlKey && !event.metaKey && !event.altKey && (key === "q" || key === "e");
  }

  getFrameNavigationBaseIndex() {
    if (this.isFrameTransitionPending()) {
      return this.pendingFrameIndex | 0;
    }
    return this.currentFrameIndex | 0;
  }

  getTargetTimeForFrame(frameIndex) {
    const safeFps = Number(this.fps) > 0 ? Number(this.fps) : 30;
    return this.clampFrameIndex(frameIndex) / safeFps;
  }

  getPresentedFrameIndex() {
    if (this.kind !== "video") {
      return this.currentFrameIndex | 0;
    }

    const currentTime = Number(this.mediaEl?.currentTime);
    if (!Number.isFinite(currentTime)) {
      return this.currentFrameIndex | 0;
    }

    const safeFps = Number(this.fps) > 0 ? Number(this.fps) : 30;
    return this.clampFrameIndex(Math.round(currentTime * safeFps));
  }

  isVideoReadyForFrame(frameIndex) {
    if (this.kind !== "video" || !this.mediaEl || this.mediaEl.tagName !== "VIDEO") {
      return false;
    }
    if (this.mediaEl.readyState < 2) {
      return false;
    }

    const targetTime = this.getTargetTimeForFrame(frameIndex);
    const currentTime = Number(this.mediaEl.currentTime);
    if (!Number.isFinite(currentTime)) {
      return false;
    }

    const safeFps = Number(this.fps) > 0 ? Number(this.fps) : 30;
    return Math.abs(currentTime - targetTime) <= 0.5 / safeFps;
  }

  restorePendingTrackSelection() {
    if (!Number.isInteger(this.pendingTrackRestoreId)) {
      return;
    }
    const trackId = this.pendingTrackRestoreId;
    this.pendingTrackRestoreId = null;
    this.restoreActiveAnnotationForTrack(trackId);
  }

  requestFramePresentation(frameIndex, options = {}) {
    if (this.kind !== "video") {
      return false;
    }

    const clamped = this.clampFrameIndex(frameIndex);
    const restoreTrackId = Number.isInteger(options.restoreTrackId)
      ? options.restoreTrackId
      : null;
    const requestedSource = options.source || "scrub";

    if (
      !this.isFrameTransitionPending() &&
      clamped === (this.currentFrameIndex | 0) &&
      this.isVideoReadyForFrame(clamped)
    ) {
      if (restoreTrackId != null) {
        this.pendingTrackRestoreId = restoreTrackId;
        this.restorePendingTrackSelection();
      }
      this.updateFrameDisplay();
      return false;
    }

    this.pendingFrameIndex = clamped;
    this.pendingFrameSource = requestedSource;
    if (restoreTrackId != null) {
      this.pendingTrackRestoreId = restoreTrackId;
    }
    this.showLoadingOverlay();

    const targetTime = this.getTargetTimeForFrame(clamped);
    try {
      if (typeof this.mediaEl.fastSeek === "function") {
        this.mediaEl.fastSeek(targetTime);
      } else {
        this.mediaEl.currentTime = targetTime;
      }
    } catch (_error) {
      try {
        this.mediaEl.currentTime = targetTime;
      } catch (_innerError) {
        // Ignore currentTime assignment failures.
      }
    }

    if (typeof this.mediaEl.requestVideoFrameCallback === "function") {
      this.mediaEl.requestVideoFrameCallback(() => {
        this.tryFinalizePendingFrame("videoFrameCallback");
      });
    } else {
      window.requestAnimationFrame(() => {
        this.tryFinalizePendingFrame("animationFrame");
      });
    }

    return true;
  }

  tryFinalizePendingFrame(trigger = "unknown") {
    if (!this.isFrameTransitionPending()) {
      return false;
    }
    if (!this.isVideoReadyForFrame(this.pendingFrameIndex)) {
      return false;
    }

    const source = this.pendingFrameSource || "scrub";
    const presentedFrameIndex = this.getPresentedFrameIndex();
    this.pendingFrameIndex = null;
    this.pendingFrameSource = null;
    this.hideLoadingOverlay();
    lfDebug("video.pendingFrame.resolved", {
      trigger,
      source,
      presentedFrameIndex,
    });
    this.applyVisibleFrame(presentedFrameIndex, { source });
    return true;
  }

  primeVideoFirstFrame() {
    if (this.kind !== "video") return;
    if (!this.mediaEl || this.mediaEl.tagName !== "VIDEO") return;

    lfDebug("video.primeFirstFrame.before", {
      readyState: this.mediaEl.readyState,
      currentTime: this.mediaEl.currentTime,
    });

    // If frame data is already available, nothing to do.
    if (this.mediaEl.readyState >= 2) {
      lfDebug("video.primeFirstFrame.skip", { reason: "readyState>=2" });
      return;
    }

    try {
      // Nudge currentTime slightly so browsers that only fetched metadata
      // will decode the first frame and fire "loadeddata".
      const EPS = 1e-4;
      const t = Number.isFinite(this.mediaEl.currentTime)
        ? this.mediaEl.currentTime
        : 0;
      this.mediaEl.currentTime = t === 0 ? EPS : t;
      lfDebug("video.primeFirstFrame.afterSet", {
        newCurrentTime: this.mediaEl.currentTime,
      });
    } catch (e) {
      lfDebug("video.primeFirstFrame.error", {
        name: e?.name,
        message: e?.message,
      });
    }
  }

  async forceDecodeFirstFrame() {
    if (this.kind !== "video") return;
    if (!this.mediaEl || this.mediaEl.tagName !== "VIDEO") return;
    if (this.mediaEl.readyState >= 2) return;

    try {
      lfDebug("video.forceDecodeFirstFrame.play.try", {
        readyState: this.mediaEl.readyState,
        currentTime: this.mediaEl.currentTime,
      });
      const wasMuted = this.mediaEl.muted;
      this.mediaEl.muted = true;
      const playPromise = this.mediaEl.play();
      if (playPromise && typeof playPromise.then === "function") {
        await playPromise;
      }
      this.mediaEl.pause();
      this.mediaEl.muted = wasMuted;
      lfDebug("video.forceDecodeFirstFrame.play.ok", {
        readyState: this.mediaEl.readyState,
      });
    } catch (e) {
      lfDebug("video.forceDecodeFirstFrame.play.error", {
        name: e?.name,
        message: e?.message,
      });
    }
  }

  switchToCanvasVideoIfReady() {
    if (this.kind !== "video") return;
    if (!this.mediaEl || this.mediaEl.tagName !== "VIDEO") return;
    lfDebug("video.switchToCanvas.check", {
      useCanvasVideo: this.useCanvasVideo,
      readyState: this.mediaEl.readyState,
      videoWidth: this.mediaEl.videoWidth,
      videoHeight: this.mediaEl.videoHeight,
      currentTime: this.mediaEl.currentTime,
    });
    if (this.mediaEl.readyState < 2) return;

    // Keep the native <video> visible and draw annotations on the transparent
    // overlay canvas. Hiding the video makes the whole stage blank if canvas
    // video rendering regresses.
    this.useCanvasVideo = false;
    this.mediaEl.style.opacity = "1";
    this.mediaEl.style.pointerEvents = "none";
    if (!this.isFrameTransitionPending()) {
      this.hideLoadingOverlay();
    }
    lfDebug("video.switchToCanvas.enabled", {
      useCanvasVideo: this.useCanvasVideo,
    });
    this.requestRedraw(true);
  }

  propagateAnnotation(annotation) {
    if (this.kind !== "video") return;

    const totalFrames = this.totalFrames || this.computeTotalFrames();
    if (!totalFrames || typeof annotation.frame_index !== "number") {
      annotation.propagation_frames = 0;
      return;
    }

    const startFrame = annotation.frame_index;
    const framesToPropagate =
      typeof annotation.propagation_frames === "number" &&
      annotation.propagation_frames > 0
        ? annotation.propagation_frames
        : this.defaultPropagationFrames;

    const endFrame =
      framesToPropagate > 0
        ? Math.min(totalFrames - 1, startFrame + framesToPropagate)
        : totalFrames - 1;

    annotation.propagation_frames = Math.max(0, endFrame - startFrame);
  }


  findFirstFrameForAnnotation(ann) {
    if (this.kind !== "video") return null;
    const tid = ann.track_id;
    if (tid != null) {
      const frames = Array.from(this.frameAnnotations.keys())
        .filter((k) => typeof k === "number")
        .sort((a, b) => a - b);
      for (const f of frames) {
        const bucket = this.frameAnnotations.get(f) || [];
        if (bucket.some((a) => a.track_id === tid)) {
          return f;
        }
      }
    }
    if (typeof ann.frame_index === "number") {
      return ann.frame_index;
    }
    return null;
  }

  parsePolygonVertexHandle(handle) {
    if (typeof handle !== "string" || !handle.startsWith("vertex:")) {
      return null;
    }

    const vertexIndex = parseInt(handle.slice("vertex:".length), 10);
    return Number.isInteger(vertexIndex) ? vertexIndex : null;
  }

  parsePolygonEdgeHandle(handle) {
    if (typeof handle !== "string" || !handle.startsWith("edge:")) {
      return null;
    }

    const edgeIndex = parseInt(handle.slice("edge:".length), 10);
    return Number.isInteger(edgeIndex) ? edgeIndex : null;
  }

  blurActiveFormControl() {
    const activeEl = document.activeElement;
    if (
      !activeEl ||
      activeEl === this.canvas ||
      typeof activeEl.blur !== "function"
    ) {
      return;
    }

    if (
      activeEl.tagName === "INPUT" ||
      activeEl.tagName === "TEXTAREA" ||
      activeEl.tagName === "SELECT" ||
      activeEl.tagName === "BUTTON"
    ) {
      activeEl.blur();
    }
  }

  isPolygonAnnotation(annotation) {
    return (
      !!annotation &&
      this.getGeometryKindForLabel(annotation.label_class_id) === "polygon"
    );
  }

  getDraftPolygonCommittedPointCount(annotation = this.currentDrawingAnnotation) {
    const polygonPoints = clonePolygonPoints(annotation?.polygon_points);
    if (!polygonPoints || polygonPoints.length < 2) {
      return 0;
    }
    return polygonPoints.length - 1;
  }

  startPolygonDrawing(imgPt) {
    const newAnnotation = {
      client_uid: generateClientUid(),
      id: null,
      label_class_id: this.currentLabelClassId,
      frame_index: this.kind === "video" ? this.currentFrameIndex : null,
      x1: imgPt.x,
      y1: imgPt.y,
      x2: imgPt.x,
      y2: imgPt.y,
      polygon_points: [
        [imgPt.x, imgPt.y],
        [imgPt.x, imgPt.y],
      ],
      status: "pending",
      track_id: this.kind === "video" ? this.nextTrackId++ : null,
      propagation_frames: this.kind === "video" ? this.defaultPropagationFrames : 0,
    };

    syncPolygonBounds(newAnnotation);
    this.currentDrawingAnnotation = newAnnotation;
    this.isPolygonDrawing = true;
    this.annotations.push(newAnnotation);
    this.markActiveAnnotation(newAnnotation);
  }

  updatePolygonDraftPreview(imgPt) {
    if (!this.currentDrawingAnnotation) return;

    const polygonPoints =
      clonePolygonPoints(this.currentDrawingAnnotation.polygon_points) || [];
    if (!polygonPoints.length) return;

    polygonPoints[polygonPoints.length - 1] = [imgPt.x, imgPt.y];
    this.currentDrawingAnnotation.polygon_points = polygonPoints;
    syncPolygonBounds(this.currentDrawingAnnotation);
  }

  handlePolygonDraftClick(evt) {
    if (!this.currentDrawingAnnotation) return;

    const polygonPoints =
      clonePolygonPoints(this.currentDrawingAnnotation.polygon_points) || [];
    if (!polygonPoints.length) return;

    const committedCount = this.getDraftPolygonCommittedPointCount();
    const rect = this.canvas.getBoundingClientRect();
    const xCanvas = evt.clientX - rect.left;
    const yCanvas = evt.clientY - rect.top;
    const closeRadiusSq = (this.handleRadius * 2) * (this.handleRadius * 2);

    if (committedCount >= 3) {
      const firstVertex = this.fromImageToCanvasCoords(
        polygonPoints[0][0],
        polygonPoints[0][1]
      );
      const dx = xCanvas - firstVertex.x;
      const dy = yCanvas - firstVertex.y;
      if (dx * dx + dy * dy <= closeRadiusSq) {
        this.finalizePolygonDrawing();
        return;
      }
    }

    const imgPt = this.toImageCoordinates(evt);
    const previewIndex = polygonPoints.length - 1;
    polygonPoints[previewIndex] = [imgPt.x, imgPt.y];
    polygonPoints.splice(previewIndex, 0, [imgPt.x, imgPt.y]);
    this.currentDrawingAnnotation.polygon_points = polygonPoints;
    syncPolygonBounds(this.currentDrawingAnnotation);
    this.requestRedraw(false);
  }

  finalizePolygonDrawing() {
    if (!this.isPolygonDrawing || !this.currentDrawingAnnotation) {
      return false;
    }

    const annotation = this.currentDrawingAnnotation;
    const polygonPoints = clonePolygonPoints(annotation.polygon_points) || [];
    if (polygonPoints.length >= 2) {
      polygonPoints.pop();
    }
    annotation.polygon_points = polygonPoints;
    syncPolygonBounds(annotation);

    this.isPolygonDrawing = false;
    this.currentDrawingAnnotation = null;

    if (this.isDegenerateAnnotation(annotation)) {
      this.removeAnnotationWithoutSave(annotation);
      this.requestRedraw();
      return false;
    }

    if (this.kind === "video" && typeof annotation.frame_index === "number") {
      this.propagateAnnotation(annotation);
      this.commitVideoAnnotation(
        annotation,
        annotation.propagation_frames ?? 0
      );
      this.addManualKeyframe(annotation.track_id, this.currentFrameIndex);
      this.restoreActiveAnnotationForTrack(annotation.track_id);
    } else {
      this.requestRedraw();
    }

    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
    }

    this.pushHistoryCheckpoint();
    this.updateFastActionButtons();
    this.scheduleSave();
    return true;
  }

  cancelPolygonDrawing() {
    if (!this.isPolygonDrawing || !this.currentDrawingAnnotation) return;

    const annotation = this.currentDrawingAnnotation;
    this.isPolygonDrawing = false;
    this.currentDrawingAnnotation = null;
    this.removeAnnotationWithoutSave(annotation);
    this.requestRedraw();
  }

  commitPolygonPointEdit(annotation, options = {}) {
    if (!annotation) return;

    const vertexIndex =
      Number.isInteger(options.vertexIndex) ? options.vertexIndex : null;

    this.normalizeAnnotationCoords(annotation);
    if (this.isDegenerateAnnotation(annotation)) {
      return;
    }

    if (this.kind === "video") {
      const propagatedRunLength = this.getPropagationRunLengthForTrackEdit(
        annotation,
        0
      );
      this.commitVideoAnnotation(annotation, propagatedRunLength);
      this.addManualKeyframe(annotation.track_id, this.currentFrameIndex);
      this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);

      if (annotation.track_id != null) {
        this.restoreActiveAnnotationForTrack(annotation.track_id, {
          vertexIndex,
        });
      } else {
        const updatedAnnotation =
          this.annotations.find(
            (candidate) =>
              candidate.label_class_id === annotation.label_class_id &&
              candidate.frame_index === this.currentFrameIndex
          ) || null;

        if (updatedAnnotation) {
          this.markActiveAnnotation(updatedAnnotation, { vertexIndex });
        } else {
          this.activeAnnotation = null;
          this.activeVertexIndex = null;
          this.requestRedraw();
        }
      }
    } else {
      this.markActiveAnnotation(annotation, { vertexIndex });
    }

    if (this.timelineInitialized && this.totalFrames) {
      this.updateTimelineAnnotations();
      this.updateTimelinePlayhead();
    }

    this.pushHistoryCheckpoint();
    this.scheduleSave();
  }

  insertPolygonVertex(annotation, edgeIndex, imgPt) {
    if (!this.isPolygonAnnotation(annotation)) return;

    const polygonPoints = clonePolygonPoints(annotation.polygon_points) || [];
    if (
      !Number.isInteger(edgeIndex) ||
      edgeIndex < 0 ||
      edgeIndex >= polygonPoints.length
    ) {
      return;
    }

    const insertIndex = edgeIndex + 1;
    polygonPoints.splice(insertIndex, 0, [imgPt.x, imgPt.y]);
    annotation.polygon_points = polygonPoints;
    syncPolygonBounds(annotation);
    this.commitPolygonPointEdit(annotation, { vertexIndex: insertIndex });
  }

  deletePolygonVertex(annotation, vertexIndex) {
    if (!this.isPolygonAnnotation(annotation)) return;

    const polygonPoints = clonePolygonPoints(annotation.polygon_points) || [];
    if (
      !Number.isInteger(vertexIndex) ||
      vertexIndex < 0 ||
      vertexIndex >= polygonPoints.length
    ) {
      return;
    }

    if (polygonPoints.length <= 3) {
      alert("A polygon must keep at least 3 points.");
      return;
    }

    polygonPoints.splice(vertexIndex, 1);
    annotation.polygon_points = polygonPoints;
    syncPolygonBounds(annotation);

    const nextVertexIndex = Math.min(vertexIndex, polygonPoints.length - 1);
    this.commitPolygonPointEdit(annotation, { vertexIndex: nextVertexIndex });
  }

  onDoubleClick(evt) {
    if (this.readOnly || this.isPolygonDrawing || this.isFrameTransitionPending()) {
      return;
    }

    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }

    const xCanvas = evt.clientX - rect.left;
    const yCanvas = evt.clientY - rect.top;
    const hit = this.findAnnotationAtCanvasPoint(xCanvas, yCanvas);
    if (!hit || !this.isPolygonAnnotation(hit.ann)) {
      return;
    }

    evt.preventDefault();

    const vertexIndex = this.parsePolygonVertexHandle(hit.handle);
    if (vertexIndex != null) {
      this.deletePolygonVertex(hit.ann, vertexIndex);
      return;
    }

    const edgeIndex = this.parsePolygonEdgeHandle(hit.handle);
    if (edgeIndex != null) {
      this.insertPolygonVertex(hit.ann, edgeIndex, this.toImageCoordinates(evt));
    }
  }

  onMouseDown(evt) {
    evt.preventDefault();
    if (this.isFrameTransitionPending()) {
      return;
    }
    this.blurActiveFormControl();
    this.hideObjectContextMenu();

    let rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      this.syncCanvasSize(false);
      rect = this.canvas.getBoundingClientRect();
    }

    const xCanvas = evt.clientX - rect.left;
    const yCanvas = evt.clientY - rect.top;

    if (this.isPolygonDrawing) {
      if (evt.button === 0) {
        this.handlePolygonDraftClick(evt);
      }
      return;
    }

    if (this.mediaEl.tagName === "VIDEO" && !this.mediaEl.paused) {
      this.mediaEl.pause();
      this.stopRenderLoop?.();
    }

    const hit = this.findAnnotationAtCanvasPoint(xCanvas, yCanvas);

    if (evt.button === 1 || evt.button === 2) {
      if (!this.readOnly && hit) {
        this.markActiveAnnotation(hit.ann, {
          vertexIndex: this.parsePolygonVertexHandle(hit.handle),
        });
        this.canvas.style.cursor = this.getCursorForHit(hit);
        if (evt.button === 1) {
          this.showObjectContextMenu({
            annotation: hit.ann,
            clientX: evt.clientX,
            clientY: evt.clientY,
          });
        }
        return;
      }

      this.isPanning = true;
      this.panStart = { x: evt.clientX, y: evt.clientY };
      this.panTranslateStart = { x: this.translateX, y: this.translateY };
      this.canvas.style.cursor = "grab";
      return;
    }

    if (evt.button === 0 && evt.ctrlKey) {
      this.isPanning = true;
      this.panStart = { x: evt.clientX, y: evt.clientY };
      this.panTranslateStart = { x: this.translateX, y: this.translateY };
      this.canvas.style.cursor = "grab";
      return;
    }

    this.dragMode = null;
    this.dragHandle = null;
    this.resizeStart = null;

    if (this.readOnly) {
      if (hit) {
        this.markActiveAnnotation(hit.ann, {
          vertexIndex: this.parsePolygonVertexHandle(hit.handle),
        });
        this.canvas.style.cursor = this.getCursorForHit(hit);
      } else {
        this.activeAnnotation = null;
        this.activeVertexIndex = null;
        this.setActiveTrackId(null);
        this.requestRedraw();
        this.canvas.style.cursor = this.getCursorForHit(null);
      }
      return;
    }

    if (hit) {
      const vertexIndex = this.parsePolygonVertexHandle(hit.handle);
      const edgeIndex = this.parsePolygonEdgeHandle(hit.handle);
      this.markActiveAnnotation(hit.ann, { vertexIndex });
      if (hit.ann.track_id != null && this.isTrackLocked(hit.ann.track_id)) {
        this.canvas.style.cursor = "not-allowed";
        return;
      }

      this.isDragging = true;
      this.draggedAnnotation = hit.ann;
      this.dragMode = hit.handle && edgeIndex == null ? "resize" : "move";
      this.dragHandle = edgeIndex != null ? null : hit.handle;

      if (this.dragMode === "move") {
        const p1 = this.fromImageToCanvasCoords(hit.ann.x1, hit.ann.y1);
        this.dragOffset.x = xCanvas - p1.x;
        this.dragOffset.y = yCanvas - p1.y;
        this.resizeStart = null;
      } else {
        this.dragOffset.x = 0;
        this.dragOffset.y = 0;
        this.resizeStart = {
          x1: hit.ann.x1,
          y1: hit.ann.y1,
          x2: hit.ann.x2,
          y2: hit.ann.y2,
        };
      }

      this.canvas.style.cursor = this.getCursorForHit(hit);
      return;
    }

    if (!this.currentLabelClassId) {
      console.warn("No label class selected");
      return;
    }

    const geometry = this.getGeometryKindForLabel(this.currentLabelClassId);

    if (geometry === "tag") {
      const baseWidth =
        this.mediaEl.naturalWidth ||
        this.mediaEl.videoWidth ||
        this.imageWidth ||
        this.viewportWidth ||
        this.canvas.width / this.pixelRatio;
      const baseHeight =
        this.mediaEl.naturalHeight ||
        this.mediaEl.videoHeight ||
        this.imageHeight ||
        this.viewportHeight ||
        this.canvas.height / this.pixelRatio;

      const newAnn = {
        client_uid: generateClientUid(),
        id: null,
        label_class_id: this.currentLabelClassId,
        frame_index: this.kind === "video" ? this.currentFrameIndex : null,
        x1: 0,
        y1: 0,
        x2: baseWidth,
        y2: baseHeight,
        status: "pending",
      };
      this.annotations.push(newAnn);
      this.markActiveAnnotation(newAnn);
      this.pushHistoryCheckpoint();
      this.scheduleSave();
      return;
    }

    if (geometry === "polygon") {
      this.startPolygonDrawing(this.toImageCoordinates(evt));
      this.requestRedraw(false);
      return;
    }

    if (geometry === "bbox" && this.useFixedSizeBBox) {
      const imgPt = this.toImageCoordinates(evt);
      const halfWidth = Math.max(1, this.fixedBBoxWidth) / 2;
      const halfHeight = Math.max(1, this.fixedBBoxHeight) / 2;
      const newAnnotation = {
        client_uid: generateClientUid(),
        id: null,
        label_class_id: this.currentLabelClassId,
        frame_index: this.kind === "video" ? this.currentFrameIndex : null,
        x1: imgPt.x - halfWidth,
        y1: imgPt.y - halfHeight,
        x2: imgPt.x + halfWidth,
        y2: imgPt.y + halfHeight,
        polygon_points: null,
        status: "pending",
        track_id: this.kind === "video" ? this.nextTrackId++ : null,
        propagation_frames: this.kind === "video" ? this.defaultPropagationFrames : 0,
      };
      if (this.kind === "video") {
        this.propagateAnnotation(newAnnotation);
        this.commitVideoAnnotation(newAnnotation, newAnnotation.propagation_frames ?? 0);
        this.addManualKeyframe(newAnnotation.track_id, this.currentFrameIndex);
        this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
        this.restoreActiveAnnotationForTrack(newAnnotation.track_id);
        if (this.timelineInitialized && this.totalFrames) {
          this.updateTimelineAnnotations();
          this.updateTimelinePlayhead();
        }
      } else {
        this.annotations.push(newAnnotation);
        this.frameAnnotations.set(null, this.annotations);
        this.markActiveAnnotation(newAnnotation);
      }
      this.pushHistoryCheckpoint();
      this.updateFastActionButtons();
      this.scheduleSave();
      return;
    }

    this.isDrawing = true;
    this.startPoint = this.toImageCoordinates(evt);
    const newAnn = {
      client_uid: generateClientUid(),
      id: null,
      label_class_id: this.currentLabelClassId,
      frame_index: this.kind === "video" ? this.currentFrameIndex : null,
      x1: this.startPoint.x,
      y1: this.startPoint.y,
      x2: this.startPoint.x,
      y2: this.startPoint.y,
      status: "pending",
    };

    if (this.kind === "video") {
      newAnn.track_id = this.nextTrackId++;
      newAnn.propagation_frames = this.defaultPropagationFrames;
    } else {
      newAnn.track_id = null;
      newAnn.propagation_frames = 0;
    }
    this.currentDrawingAnnotation = newAnn;
    this.annotations.push(newAnn);

    this.markActiveAnnotation(newAnn);
  }

  onMouseMove(evt) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }

    // Panning mode (started by Ctrl+left or right button)
    if (this.isPanning) {
      const dx = evt.clientX - this.panStart.x;
      const dy = evt.clientY - this.panStart.y;
      this.translateX = this.panTranslateStart.x + dx;
      this.translateY = this.panTranslateStart.y + dy;
      this.canvas.style.cursor = "grabbing";
      this.requestRedraw(false);
      return;
    }

    if (this.isPolygonDrawing && this.currentDrawingAnnotation) {
      this.canvas.style.cursor = "crosshair";
      const imgPt = this.toImageCoordinates(evt);
      this.updatePolygonDraftPreview(imgPt);
      this.requestRedraw(false);
      return;
    }

    const xCanvas = evt.clientX - rect.left;
    const yCanvas = evt.clientY - rect.top;

    // Hover feedback when idle.
    if (!this.isDrawing && !this.isDragging) {
      this.updateHoverCursor(evt.clientX, evt.clientY);
      return;
    }

    const imgPt = this.toImageCoordinates(evt);

    if (this.isDrawing) {
      this.canvas.style.cursor = "crosshair";
      // For now all geometries use bbox-like drawing (x1,y1,x2,y2).
      const ann = this.annotations[this.annotations.length - 1];
      ann.x2 = imgPt.x;
      ann.y2 = imgPt.y;
      this.clampAnnotationToImage(ann);
    } else if (this.isDragging && this.draggedAnnotation) {
      this.canvas.style.cursor =
        this.dragMode === "resize"
          ? this.getCursorForHit({ handle: this.dragHandle })
          : "move";
      const geometryKind = this.getGeometryKindForLabel(
        this.draggedAnnotation.label_class_id
      );
      const adapter = getGeometryAdapter(geometryKind);

      if (this.dragMode === "resize" && this.resizeStart) {
        adapter.resize({
          annotation: this.draggedAnnotation,
          handle: this.dragHandle,
          imgPt,
          resizeStart: this.resizeStart,
          clampAnnotationToImage: (ann) => this.clampAnnotationToImage(ann),
        });
      } else {
        // Move whole shape in image space.
        const p1 = this.fromImageToCanvasCoords(
          this.draggedAnnotation.x1,
          this.draggedAnnotation.y1
        );
        const dxCanvas = xCanvas - this.dragOffset.x - p1.x;
        const dyCanvas = yCanvas - this.dragOffset.y - p1.y;

        const scale = (this.baseScale || 1) * (this.zoom || 1);
        if (!scale) {
          return;
        }

        const dx = dxCanvas / scale;
        const dy = dyCanvas / scale;

        adapter.move({
          annotation: this.draggedAnnotation,
          dx,
          dy,
          clampAnnotationToImage: (ann) => this.clampAnnotationToImage(ann),
        });
      }
    }

    this.requestRedraw(false);
  }

  onMouseUp(evt) {
    if (this.isPolygonDrawing && !this.isPanning) {
      this.canvas.style.cursor = "crosshair";
      return;
    }

    const wasPanning = this.isPanning;
    const wasDrawing = this.isDrawing;
    const wasDragging = this.isDragging;
    const drawingAnnotation = this.currentDrawingAnnotation;
    const draggedAnnotation = this.draggedAnnotation;
    const draggedHandle = this.dragHandle;

    this.isPanning = false;
    this.isDrawing = false;
    this.isDragging = false;
    this.draggedAnnotation = null;
    this.dragMode = null;
    this.dragHandle = null;
    this.resizeStart = null;

    if (evt) {
      this.updateHoverCursor(evt.clientX, evt.clientY);
    } else {
      this.canvas.style.cursor = "crosshair";
    }

    if (wasPanning) {
      return;
    }

    let shouldSave = false;

    if (wasDrawing && drawingAnnotation) {
      this.normalizeAnnotationCoords(drawingAnnotation);

      if (this.isDegenerateAnnotation(drawingAnnotation)) {
        this.removeAnnotationWithoutSave(drawingAnnotation);
        this.currentDrawingAnnotation = null;
        this.requestRedraw();
        return;
      }

      if (this.kind === "video" && typeof drawingAnnotation.frame_index === "number") {
        this.propagateAnnotation(drawingAnnotation);
        this.commitVideoAnnotation(
          drawingAnnotation,
          drawingAnnotation.propagation_frames ?? 0
        );
        this.addManualKeyframe(drawingAnnotation.track_id, this.currentFrameIndex);
        this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
      }

      shouldSave = true;
    } else if (wasDragging && draggedAnnotation) {
      const draggedVertexIndex = this.parsePolygonVertexHandle(draggedHandle);
      this.normalizeAnnotationCoords(draggedAnnotation);

      if (this.isDegenerateAnnotation(draggedAnnotation)) {
        if (draggedAnnotation.track_id != null) {
          this.removeTrackFrame(draggedAnnotation.track_id, this.currentFrameIndex);
        } else if (draggedAnnotation._storedAnnotation) {
          this.removeSparseAnnotation(draggedAnnotation._storedAnnotation);
        }
      } else if (this.kind === "video") {
        const propagatedRunLength = this.getPropagationRunLengthForTrackEdit(
          draggedAnnotation,
          0
        );
        this.commitVideoAnnotation(draggedAnnotation, propagatedRunLength);
        this.addManualKeyframe(
          draggedAnnotation.track_id,
          this.currentFrameIndex
        );
        if (draggedAnnotation.track_id != null) {
          this.restoreActiveAnnotationForTrack(draggedAnnotation.track_id, {
            vertexIndex: draggedVertexIndex,
          });
        }
      }

      if (this.kind === "video") {
        this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);
        if (draggedAnnotation.track_id != null) {
          this.restoreActiveAnnotationForTrack(draggedAnnotation.track_id, {
            vertexIndex: draggedVertexIndex,
          });
        }
      }

      shouldSave = true;
    }

    this.currentDrawingAnnotation = null;

    if (shouldSave) {
      if (this.timelineInitialized && this.totalFrames) {
        this.updateTimelineAnnotations();
      }
      this.pushHistoryCheckpoint();
      this.updateFastActionButtons();
      this.requestRedraw();
      this.scheduleSave();
    }
  }


  getLabelClassColor(labelClassId) {
    const lc = this.labelClasses.find((x) => x.id === labelClassId);
    return lc ? lc.color_hex || "#00ff00" : "#00ff00";
  }

  getLabelClassName(labelClassId) {
    const lc = this.labelClasses.find((x) => x.id === labelClassId);
    return lc ? lc.name || "?" : "?";
  }

  getLabelClassConfig(labelClassId) {
    return this.labelClasses.find((x) => x.id === labelClassId) || null;
  }

  applySelectedLabelPreset() {
    const labelClass = this.getLabelClassConfig(this.currentLabelClassId);
    if (!labelClass) {
      return;
    }

    const geometryKind = labelClass.geometry_kind || "bbox";
    const resolvedBoxWidth =
      Number.isFinite(Number(labelClass.default_box_w)) &&
      Number(labelClass.default_box_w) > 0
        ? Math.max(1, Math.trunc(Number(labelClass.default_box_w)))
        : this.fixedBBoxWidth;
    const resolvedBoxHeight =
      Number.isFinite(Number(labelClass.default_box_h)) &&
      Number(labelClass.default_box_h) > 0
        ? Math.max(1, Math.trunc(Number(labelClass.default_box_h)))
        : this.fixedBBoxHeight;
    const resolvedPropagationFrames =
      Number.isFinite(Number(labelClass.default_propagation_frames)) &&
      Number(labelClass.default_propagation_frames) >= 0
        ? Math.max(0, Math.trunc(Number(labelClass.default_propagation_frames)))
        : 0;

    if (geometryKind === "bbox") {
      this.fixedBBoxWidth = resolvedBoxWidth;
      this.fixedBBoxHeight = resolvedBoxHeight;
      this.useFixedSizeBBox = !!labelClass.default_use_fixed_box;
    } else {
      this.useFixedSizeBBox = false;
    }

    this.defaultPropagationFrames = resolvedPropagationFrames;
    if (this.propagationLengthInput) {
      this.propagationLengthInput.value = String(this.defaultPropagationFrames);
    }

    this.syncFixedBBoxControls();
  }

  applyLabelSelection(labelClassId) {
    const nextLabelClassId = parseInt(labelClassId, 10);
    if (!Number.isFinite(nextLabelClassId)) {
      return;
    }

    this.currentLabelClassId = nextLabelClassId;
    const select = document.getElementById("label-class-select");
    if (select && select.value !== String(nextLabelClassId)) {
      select.value = String(nextLabelClassId);
    }

    this.applySelectedLabelPreset();
    this.updateReferenceControls();
  }

  getAnnotationObjectNumber(annotation, index) {
    if (this.kind === "video" && typeof annotation.track_id === "number") {
      return annotation.track_id;
    }
    return Math.max(1, (Number.isInteger(index) ? index : 0) + 1);
  }

  getAnnotationIdentityMeta(annotation, index = null) {
    const className = this.getLabelClassName(annotation.label_class_id);
    const resolvedIndex =
      Number.isInteger(index) && index >= 0
        ? index
        : this.annotations.findIndex((candidate) => {
            if (candidate === annotation) return true;
            if (
              candidate?._storedAnnotation &&
              candidate._storedAnnotation === annotation
            ) {
              return true;
            }
            return (
              !!candidate?.client_uid &&
              !!annotation?.client_uid &&
              candidate.client_uid === annotation.client_uid
            );
          });
    const objectNumber = this.getAnnotationObjectNumber(
      annotation,
      resolvedIndex >= 0 ? resolvedIndex : 0
    );
    return { className, objectNumber };
  }

  drawAnnotationLabels() {
    if (!this.showAnnotationLabels) return;
    if (!this.annotations || !this.annotations.length) return;

    const ctx = this.ctx;
    const viewportWidth =
      this.viewportWidth || this.canvas.width / (this.pixelRatio || 1);
    const viewportHeight =
      this.viewportHeight || this.canvas.height / (this.pixelRatio || 1);

    ctx.save();
    // Screen-space drawing (CSS pixels) so labels stay the same size under zoom.
    ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
    ctx.font =
      "12px system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif";
    ctx.textBaseline = "top";
    ctx.textAlign = "left";

    const padding = 3;
    const margin = 2;

    for (let i = 0; i < this.annotations.length; i++) {
      const ann = this.annotations[i];
      if (!this.isAnnotationVisible(ann)) continue;
      const { className, objectNumber } = this.getAnnotationIdentityMeta(ann, i);
      const reviewChangeState = getReviewChangeState(ann);
      const changePrefix =
        reviewChangeState === "new"
          ? "NEW · "
          : reviewChangeState === "changed"
            ? "CHANGED · "
            : "";
      const text = `${changePrefix}Obj ${objectNumber}: ${className}`;

      const minX = Math.min(ann.x1, ann.x2);
      const minY = Math.min(ann.y1, ann.y2);
      const p = this.fromImageToCanvasCoords(minX, minY);

      const metrics = ctx.measureText(text);
      const textW = metrics.width || 0;
      const textH =
        (metrics.actualBoundingBoxAscent || 10) +
        (metrics.actualBoundingBoxDescent || 4);

      const boxW = textW + padding * 2;
      const boxH = textH + padding * 2;

      let x = (p.x || 0) + margin;
      let y = (p.y || 0) + margin;

      // Keep the label inside the current viewport.
      if (x + boxW > viewportWidth) x = Math.max(0, viewportWidth - boxW);
      if (y + boxH > viewportHeight) y = Math.max(0, viewportHeight - boxH);
      if (x < 0) x = 0;
      if (y < 0) y = 0;

      const color = this.getLabelClassColor(ann.label_class_id);
      const labelBorderColor =
        reviewChangeState === "new"
          ? "#198754"
          : reviewChangeState === "changed"
            ? "#ffc107"
            : color;
      ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
      ctx.fillRect(x, y, boxW, boxH);
      ctx.lineWidth = 1;
      ctx.strokeStyle = labelBorderColor;
      ctx.strokeRect(x + 0.5, y + 0.5, boxW - 1, boxH - 1);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(text, x + padding, y + padding);
    }

    ctx.restore();
  }

  getGeometryKindForLabel(labelClassId) {
    const lc = this.getLabelClassConfig(labelClassId);
    return lc && lc.geometry_kind ? lc.geometry_kind : "bbox";
  }

  redraw(withList = true) {
    if (!this.canvas.width || !this.canvas.height) return;
    const ctx = this.ctx;

    if (this.kind === "video" && !this.useCanvasVideo) {
      this.applyMediaTransform();
    }

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    const imgW =
      this.imageWidth ||
      this.mediaEl.naturalWidth ||
      this.mediaEl.videoWidth ||
      0;
    const imgH =
      this.imageHeight ||
      this.mediaEl.naturalHeight ||
      this.mediaEl.videoHeight ||
      0;

    if (!imgW || !imgH) {
      if (this.kind === "video") {
        lfDebug("video.redraw.noDimensions", {
          useCanvasVideo: this.useCanvasVideo,
          readyState: this.mediaEl.readyState,
          videoWidth: this.mediaEl.videoWidth,
          videoHeight: this.mediaEl.videoHeight,
        });
      }
      return;
    }

    const scale = (this.baseScale || 1) * (this.zoom || 1);
    const translateX = this.translateX || 0;
    const translateY = this.translateY || 0;

    ctx.setTransform(
      this.pixelRatio * scale,
      0,
      0,
      this.pixelRatio * scale,
      this.pixelRatio * translateX,
      this.pixelRatio * translateY
    );

    const renderSource =
      this.kind === "image" || this.useCanvasVideo ? this.mediaEl : null;

    if (renderSource) {
      try {
        ctx.drawImage(renderSource, 0, 0, imgW, imgH);
        if (this.kind === "video") {
          lfDebug("video.redraw.drawImage.ok", {
            useCanvasVideo: this.useCanvasVideo,
            readyState: this.mediaEl.readyState,
            currentTime: this.mediaEl.currentTime,
          });
        }
      } catch (e) {
        if (this.kind === "video") {
          lfDebug("video.redraw.drawImage.error", {
            useCanvasVideo: this.useCanvasVideo,
            name: e?.name,
            message: e?.message,
            readyState: this.mediaEl.readyState,
          });
        }
      }
    } else if (this.kind === "video") {
      lfDebug("video.redraw.skipped", {
        useCanvasVideo: this.useCanvasVideo,
        readyState: this.mediaEl.readyState,
      });
    }

    this.drawImageRegionOverlay(imgW, imgH, scale, translateX, translateY);
    this.drawPreviousFrameOverlay(scale);

    const handleRadiusScreen = this.handleRadius || 6;
    const worldLineWidthActive = 3 / scale;
    const worldLineWidthNormal = 2 / scale;
    const worldHandleRadius = handleRadiusScreen / scale;
    const worldHandleBorderWidth = 2 / scale;

    for (const ann of this.annotations) {
      if (!this.isAnnotationVisible(ann)) continue;
      const color = this.getLabelClassColor(ann.label_class_id);
      const isActive = this.activeAnnotation === ann;
      const geometryKind = this.getGeometryKindForLabel(ann.label_class_id);
      const adapter = getGeometryAdapter(geometryKind);

      adapter.draw({
        ctx,
        annotation: ann,
        isActive,
        color,
        worldLineWidthActive,
        worldLineWidthNormal,
        worldHandleRadius,
        worldHandleBorderWidth,
        viewScale: scale,
        activeVertexIndex:
          isActive && geometryKind === "polygon" ? this.activeVertexIndex : null,
      });
    }

    ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);
    this.drawAnnotationLabels();

    if (withList) {
      this.renderList();
      this.updateValidationPanel();
    }
  }

  drawImageRegionOverlay(imgW, imgH, scale, translateX, translateY) {
    const viewportWidth =
      this.viewportWidth || this.canvas.width / this.pixelRatio;
    const viewportHeight =
      this.viewportHeight || this.canvas.height / this.pixelRatio;

    if (!viewportWidth || !viewportHeight) {
      return;
    }

    const imgLeft = translateX;
    const imgTop = translateY;
    const imgWidthCss = imgW * scale;
    const imgHeightCss = imgH * scale;

    const imgRight = imgLeft + imgWidthCss;
    const imgBottom = imgTop + imgHeightCss;

    const visibleLeft = Math.max(0, imgLeft);
    const visibleTop = Math.max(0, imgTop);
    const visibleRight = Math.min(viewportWidth, imgRight);
    const visibleBottom = Math.min(viewportHeight, imgBottom);

    // Do nothing if the image is fully outside the viewport
    if (visibleRight <= visibleLeft || visibleBottom <= visibleTop) {
      return;
    }

    const ctx = this.ctx;
    ctx.save();

    // Switch to screen-space coordinates (CSS ピクセル)
    ctx.setTransform(this.pixelRatio, 0, 0, this.pixelRatio, 0, 0);

    // Dim only the area outside the visible image with four bands
    ctx.fillStyle = "rgba(0, 0, 0, 0.55)";

    // Top band
    if (visibleTop > 0) {
      ctx.fillRect(0, 0, viewportWidth, visibleTop);
    }
    // Bottom band
    if (visibleBottom < viewportHeight) {
      ctx.fillRect(
        0,
        visibleBottom,
        viewportWidth,
        viewportHeight - visibleBottom
      );
    }
    const innerHeight = visibleBottom - visibleTop;

    // Left band
    if (visibleLeft > 0 && innerHeight > 0) {
      ctx.fillRect(0, visibleTop, visibleLeft, innerHeight);
    }
    // Right band
    if (visibleRight < viewportWidth && innerHeight > 0) {
      ctx.fillRect(
        visibleRight,
        visibleTop,
        viewportWidth - visibleRight,
        innerHeight
      );
    }

    // White outline around the visible image boundary
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
    ctx.strokeRect(
      visibleLeft + 0.5,
      visibleTop + 0.5,
      visibleRight - visibleLeft - 1,
      visibleBottom - visibleTop - 1
    );

    ctx.restore();
  }

  renderList() {
    const listEl = document.getElementById("annotation-list");
    if (!listEl) return;

    listEl.innerHTML = "";

    if (this.kind === "video") {
      this.renderVideoObjectList(listEl);
      return;
    }

    this.annotations.forEach((ann, idx) => {
      const lc = this.labelClasses.find((x) => x.id === ann.label_class_id);
      const geometry = lc && lc.geometry_kind ? lc.geometry_kind : "bbox";
      const { objectNumber } = this.getAnnotationIdentityMeta(ann, idx);
      const isActive = this.activeAnnotation === ann;
      const reviewChangeState = getReviewChangeState(ann);
      const changeBadgeHtml = getReviewChangeBadgeHtml(reviewChangeState);
      const badgeHtml = [
        ...getAnnotationFlagLabels(ann),
        ...(this.isAnnotationHidden(ann) ? ["Hidden"] : []),
      ]
        .map((label) => `<span class="annotation-flag-badge">${label}</span>`)
        .join("");
      const pointMeta =
        geometry === "polygon" && Array.isArray(ann.polygon_points)
          ? `<div class="text-muted small">${ann.polygon_points.length} points</div>`
          : "";
      const auditMetaHtml = buildAnnotationAuditMetaHtml(
        cloneAnnotationAuditMetadata(ann)
      );

      const row = document.createElement("div");
      row.className =
        "annotation-row d-flex justify-content-between align-items-center px-2 py-1 border-bottom" +
        (isActive ? " active" : "");
      if (reviewChangeState === "new") {
        row.classList.add("change-new");
      } else if (reviewChangeState === "changed") {
        row.classList.add("change-changed");
      }
      if (this.isAnnotationHidden(ann)) {
        row.classList.add("is-muted");
      }
      row.dataset.index = String(idx);

      row.innerHTML = `
        <div>
          <div>
            ${lc ? lc.name : "?"}
            <span class="text-secondary small">[${geometry}]</span>
          </div>
          <div class="text-secondary small">Object ${objectNumber}</div>
          <div class="text-muted small">
            (${ann.x1.toFixed(1)}, ${ann.y1.toFixed(1)}) – (${ann.x2.toFixed(1)}, ${ann.y2.toFixed(1)})
          </div>
          ${pointMeta}
          ${auditMetaHtml}
          ${changeBadgeHtml || badgeHtml ? `<div class="annotation-badge-list">${changeBadgeHtml}${badgeHtml}</div>` : ""}
        </div>
        <div class="d-flex align-items-center gap-1">
          ${this.readOnly ? "" : `
          <button class="btn btn-sm btn-outline-danger btn-delete" type="button" title="Delete annotation">
            ×
          </button>
          `}
        </div>
      `;

      if (!this.readOnly) {
        const deleteBtn = row.querySelector(".btn-delete");
        if (deleteBtn) {
          deleteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            this.deleteAnnotationOnCurrentFrame(ann);
          });
        }
      }

      row.addEventListener("click", () => {
        this.revealAnnotation(ann);
        this.markActiveAnnotation(ann);
      });

      row.addEventListener("contextmenu", (e) => {
        if (this.readOnly) return;
        e.preventDefault();
        e.stopPropagation();
        this.revealAnnotation(ann);
        this.markActiveAnnotation(ann);
        this.showObjectContextMenu({
          annotation: ann,
          clientX: e.clientX,
          clientY: e.clientY,
        });
      });

      listEl.appendChild(row);
    });
  }

  findVisibleAnnotationByStoredAnnotation(storedAnnotation) {
    if (!storedAnnotation) return null;

    return (
      this.annotations.find((annotation) => {
        if (annotation === storedAnnotation) return true;
        if (annotation._storedAnnotation && annotation._storedAnnotation === storedAnnotation) {
          return true;
        }
        return (
          !!annotation.client_uid &&
          !!storedAnnotation.client_uid &&
          annotation.client_uid === storedAnnotation.client_uid
        );
      }) || null
    );
  }

  findAnnotationByClientUid(clientUid) {
    if (!clientUid) return null;

    return (
      this.annotations.find(
        (annotation) =>
          annotation.client_uid === clientUid ||
          annotation._storedAnnotation?.client_uid === clientUid
      ) || null
    );
  }

  focusAnnotationObject(item) {
    if (!item) return;

    if (this.kind !== "video") {
      const storedAnnotation = item.ann || null;
      this.revealAnnotation(storedAnnotation);
      const visibleAnnotation =
        this.findVisibleAnnotationByStoredAnnotation(storedAnnotation) ||
        storedAnnotation ||
        null;
      if (visibleAnnotation) {
        this.markActiveAnnotation(visibleAnnotation);
      }
      return;
    }

    if (item.kind === "track" && Number.isInteger(item.trackId)) {
      if (Number.isInteger(this.soloTrackId) && this.soloTrackId !== item.trackId) {
        this.soloTrackId = null;
        this.persistTrackUiStateToStorage();
      }

      const targetFrame =
        this.currentFrameIndex >= item.startFrame &&
        this.currentFrameIndex <= item.endFrame
          ? this.currentFrameIndex
          : item.startFrame;

      if (this.currentFrameIndex !== targetFrame) {
        this.seekToFrame(targetFrame);
      }
      this.restoreActiveAnnotationForTrack(item.trackId);
      return;
    }

    if (Number.isInteger(this.soloTrackId)) {
      this.soloTrackId = null;
      this.persistTrackUiStateToStorage();
    }

    const targetFrame = Number.isInteger(item.startFrame)
      ? item.startFrame
      : this.currentFrameIndex;

    if (this.currentFrameIndex !== targetFrame) {
      this.seekToFrame(targetFrame);
    }

    this.revealAnnotation(item.ann);
    const visibleAnnotation = this.findVisibleAnnotationByStoredAnnotation(item.ann);
    if (visibleAnnotation) {
      this.markActiveAnnotation(visibleAnnotation);
    }
  }

  renderVideoObjectList(listEl) {
    const items = [];

    const tracks = this.computeTrackMap();
    tracks.forEach((t, trackId) => {
      const repAnn = this.getRepresentativeAnnotationForTrack(trackId);
      if (!repAnn) return;
      items.push({
        kind: "track",
        trackId,
        label_class_id: t.label_class_id,
        startFrame: t.startFrame,
        endFrame: t.endFrame,
        ann: repAnn,
      });
    });

    for (const [frameIdx, anns] of this.frameAnnotations.entries()) {
      if (typeof frameIdx !== "number" || !anns || !anns.length) continue;
      for (const ann of anns) {
        if (ann.track_id == null) {
          items.push({
            kind: "single",
            trackId: null,
            label_class_id: ann.label_class_id,
            startFrame: frameIdx,
            endFrame: frameIdx,
            ann,
          });
        }
      }
    }

    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "text-muted small px-2 py-2";
      empty.textContent = "No objects yet.";
      listEl.appendChild(empty);
      return;
    }

    items.sort((a, b) => {
      if (a.startFrame !== b.startFrame) return a.startFrame - b.startFrame;
      if (a.trackId != null && b.trackId != null) {
        return a.trackId - b.trackId;
      }
      if (a.trackId != null) return -1;
      if (b.trackId != null) return 1;
      return 0;
    });

    const active = this.activeAnnotation;

    items.forEach((item, idx) => {
      const ann = item.ann;
      const lc = this.labelClasses.find(
        (x) => x.id === item.label_class_id
      );
      const geometry = lc && lc.geometry_kind ? lc.geometry_kind : "bbox";
      const pointMeta =
        geometry === "polygon" && Array.isArray(ann.polygon_points)
          ? `<div class="text-muted small">${ann.polygon_points.length} points</div>`
          : "";
      const auditMeta =
        item.kind === "track" && item.trackId != null
          ? this.getTrackAuditMetadata(item.trackId)
          : cloneAnnotationAuditMetadata(ann);
      const auditMetaHtml = buildAnnotationAuditMetaHtml(auditMeta);
      const reviewChangeState =
        item.kind === "track" && item.trackId != null
          ? this.getTrackReviewChangeState(item.trackId)
          : getReviewChangeState(ann);
      const changeBadgeHtml = getReviewChangeBadgeHtml(reviewChangeState);
      const stateBadges = getAnnotationFlagLabels(ann);
      const viewBadges = [];
      if (item.kind === "track" && item.trackId != null) {
        if (this.isTrackLocked(item.trackId)) viewBadges.push("Locked");
        if (this.isTrackHidden(item.trackId)) viewBadges.push("Hidden");
        if (this.soloTrackId === item.trackId) viewBadges.push("Solo");
        const gapCount = this.getTrackGaps(item.trackId).length;
        if (gapCount) viewBadges.push(`Gap ${gapCount}`);
      } else if (item.kind === "single" && this.isAnnotationHidden(ann)) {
        viewBadges.push("Hidden");
      }
      const badgeHtml = [...stateBadges, ...viewBadges]
        .map((label) => `<span class="annotation-flag-badge">${label}</span>`)
        .join("");

      let frameMeta;
      if (item.kind === "track") {
        const startFrameDisp = (item.startFrame | 0) + 1;
        const endFrameDisp = (item.endFrame | 0) + 1;
        frameMeta = `<div class="text-secondary small">
          Frames ${startFrameDisp}–${endFrameDisp} (Object ${item.trackId})
        </div>`;
      } else {
        const frameDisp = (item.startFrame | 0) + 1;
        frameMeta = `<div class="text-secondary small">
          Frame ${frameDisp}
        </div>`;
      }

      let isActive = false;
      if (item.kind === "track" && item.trackId != null) {
        isActive = this.activeTrackId === item.trackId;
      } else if (active && ann && item.kind === "single") {
        isActive =
          active === ann ||
          active._storedAnnotation === ann ||
          (!!active.client_uid &&
            !!ann.client_uid &&
            active.client_uid === ann.client_uid);
      }

      const row = document.createElement("div");
      row.className =
        "annotation-row d-flex justify-content-between align-items-center px-2 py-1 border-bottom" +
        (isActive ? " active" : "");
      if (reviewChangeState === "new") {
        row.classList.add("change-new");
      } else if (reviewChangeState === "changed") {
        row.classList.add("change-changed");
      }
      if (
        (item.kind === "track" && item.trackId != null && !this.isTrackVisible(item.trackId)) ||
        (item.kind === "single" &&
          (this.isAnnotationHidden(ann) || Number.isInteger(this.soloTrackId)))
      ) {
        row.classList.add("is-muted");
      }
      row.dataset.index = String(idx);
      row.dataset.kind = item.kind;
      row.dataset.startFrame = String(item.startFrame);
      if (item.trackId != null) {
        row.dataset.trackId = String(item.trackId);
      }

      row.innerHTML = `
        <div>
          <div>
            ${lc ? lc.name : "?"}
            <span class="text-secondary small">[${geometry}]</span>
          </div>
          <div class="text-muted small">
            (${ann.x1.toFixed(1)}, ${ann.y1.toFixed(1)}) – (${ann.x2.toFixed(1)}, ${ann.y2.toFixed(1)})
          </div>
          ${pointMeta}
          ${auditMetaHtml}
          ${changeBadgeHtml || badgeHtml ? `<div class="annotation-badge-list">${changeBadgeHtml}${badgeHtml}</div>` : ""}
          ${frameMeta}
        </div>
        <div class="d-flex align-items-center gap-1">
          ${this.readOnly ? "" : `
          <button class="btn btn-sm btn-outline-danger btn-delete-object" type="button" title="Delete this object">
            ×
          </button>
          `}
        </div>
      `;

      if (!this.readOnly) {
        const deleteBtn = row.querySelector(".btn-delete-object");
        if (deleteBtn) {
          deleteBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            if (item.kind === "track" && item.trackId != null) {
              if (this.isTrackLocked(item.trackId)) return;
              const ok = window.confirm(
                "Delete this object across all propagated frames?"
              );
              if (!ok) return;
              this.deleteTrack(item.trackId);
            } else {
              if (typeof item.startFrame === "number") {
                if (this.currentFrameIndex !== item.startFrame) {
                  this.seekToFrame(item.startFrame);
                }
              }
              this.deleteAnnotationOnCurrentFrame(ann);
            }
          });
        }
      }

      row.addEventListener("click", () => {
        this.focusAnnotationObject(item);
      });

      row.addEventListener("contextmenu", (e) => {
        if (this.readOnly) return;
        e.preventDefault();
        e.stopPropagation();
        this.focusAnnotationObject(item);
        const contextAnnotation =
          item.kind === "track" && item.trackId != null
            ? this.annotations.find((annotation) => annotation.track_id === item.trackId) || ann
            : this.findVisibleAnnotationByStoredAnnotation(ann) || ann;
        this.showObjectContextMenu({
          annotation: contextAnnotation,
          clientX: e.clientX,
          clientY: e.clientY,
        });
      });

      listEl.appendChild(row);
    });
  }

  markActiveAnnotation(ann, options = {}) {
    this.activeAnnotation = ann;
    this.activeVertexIndex =
      this.isPolygonAnnotation(ann) && Number.isInteger(options.vertexIndex)
        ? options.vertexIndex
        : null;

    if (this.kind === "video" && ann && ann.track_id != null) {
      this.setActiveTrackId(ann.track_id);
    } else {
      this.setActiveTrackId(null);
    }

    // redraw() 内で renderList() が呼ばれ、activeAnnotation に応じて
    // 一覧側の active クラスも再計算される。
    this.updateFastActionButtons();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();
    
    this.requestRedraw();
  }

  clearActiveAnnotationSelection(options = {}) {
    const { requestRedraw = true } = options;

    this.activeAnnotation = null;
    this.activeVertexIndex = null;
    this.setActiveTrackId(null);
    this.hideObjectContextMenu();
    this.updateFastActionButtons();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();

    if (this.canvas) {
      this.canvas.style.cursor = this.getCursorForHit(null);
    }

    if (requestRedraw) {
      this.requestRedraw();
    }
  }

  scheduleSave() {
    if (this.readOnly) return;

    this.isDirty = true;
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
    }
    this.saveTimer = window.setTimeout(() => {
      this.saveTimer = null;
      if (!this.isDirty) return;
      this.isDirty = false;
      this.saveAnnotations();
    }, 500);
  }

  isInteractionActive() {
    return (
      this.isDrawing ||
      this.isPolygonDrawing ||
      this.isDragging ||
      this.isPanning ||
      !!this.timelineDragState
    );
  }

  collectAllAnnotations() {
    if (this.kind === "video") {
      const cleanedAnnotations = [];
      const rebuiltMap = new Map();

      for (const annotation of this.getSparseAnnotations()) {
        this.normalizeAnnotationCoords(annotation);
        if (this.isDegenerateAnnotation(annotation)) {
          continue;
        }

        const frameIndex = annotation.frame_index ?? 0;
        this.ensureClientUid(annotation);
        const bucket = rebuiltMap.get(frameIndex) || [];
        bucket.push({
          ...annotation,
          frame_index: frameIndex,
          propagation_frames: Math.max(0, annotation.propagation_frames ?? 0),
        });
        rebuiltMap.set(frameIndex, bucket);
      }

      this.frameAnnotations = rebuiltMap;
      this.annotations = this.buildAnnotationsForFrame(this.currentFrameIndex);

      for (const [frameIndex, bucket] of this.frameAnnotations.entries()) {
        const orderedBucket = bucket.sort((left, right) => {
          const leftTrackId = left.track_id ?? Number.MAX_SAFE_INTEGER;
          const rightTrackId = right.track_id ?? Number.MAX_SAFE_INTEGER;
          if (leftTrackId !== rightTrackId) {
            return leftTrackId - rightTrackId;
          }
          return (left.frame_index ?? frameIndex) - (right.frame_index ?? frameIndex);
        });

        this.frameAnnotations.set(frameIndex, orderedBucket);
        cleanedAnnotations.push(
          ...orderedBucket.map((annotation) => ({
            ...annotation,
            frame_index: frameIndex,
          }))
        );
      }

      return cleanedAnnotations.sort((left, right) => {
        const leftTrackId = left.track_id ?? Number.MAX_SAFE_INTEGER;
        const rightTrackId = right.track_id ?? Number.MAX_SAFE_INTEGER;
        if (leftTrackId !== rightTrackId) {
          return leftTrackId - rightTrackId;
        }
        return (left.frame_index ?? 0) - (right.frame_index ?? 0);
      });
    }

    const bucket = this.frameAnnotations.get(null) || [];
    const cleaned = this.sanitizeBucket(bucket, null);
    this.frameAnnotations.set(null, cleaned);
    this.annotations = cleaned;

    return cleaned.map((annotation) => ({
      ...annotation,
      frame_index: null,
    }));
  }


  async saveAnnotations() {
    if (this.readOnly) return;

    if (this.isInteractionActive()) {
      this.scheduleSave();
      return;
    }

    if (this.isSaving) {
      this.pendingSaveRequested = true;
      return;
    }

    this.collectAllAnnotations();
    const currentState = this.makeSparseSnapshotMap();
    const patch = this.buildSparsePatch(currentState);
    if (!patch.upserts.length && !patch.deletes.length) {
      this.isDirty = false;
      this.refreshCurrentHistoryCheckpoint();
      return;
    }

    const payload = {
      base_revision: this.annotationRevision,
      upserts: patch.upserts.map((annotation) => ({
        client_uid: annotation.client_uid || generateClientUid(),
        label_class_id: annotation.label_class_id,
        frame_index: annotation.frame_index,
        x1: annotation.x1,
        y1: annotation.y1,
        x2: annotation.x2,
        y2: annotation.y2,
        polygon_points: clonePolygonPoints(annotation.polygon_points),
        track_id: annotation.track_id != null ? annotation.track_id : null,
        propagation_frames:
          typeof annotation.propagation_frames === "number"
            ? annotation.propagation_frames
            : 0,
        is_occluded: !!annotation.is_occluded,
        is_truncated: !!annotation.is_truncated,
        is_outside: !!annotation.is_outside,
        is_lost: !!annotation.is_lost,
        status: annotation.status || "pending",
      })),
      deletes: patch.deletes,
    };

    const url = `${this.apiBase}/items/${this.itemId}/annotations`;
    this.isSaving = true;
    try {
      const response = await fetch(url, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      let data = null;
      try {
        data = await response.json();
      } catch (_error) {
        data = {};
      }

      if (response.status === 409) {
        if (Array.isArray(data?.annotations)) {
          this.applyServerAnnotations(data.annotations, data.revision);
        } else if (Number.isFinite(Number(data?.revision))) {
          this.annotationRevision = Number(data.revision);
        }
        this.isDirty = false;
        this.updateHistoryButtons();
        alert(
          "A teammate saved newer annotations first. The latest server state was loaded. Please review it and retry your last edit if needed."
        );
        return;
      }

      if (!response.ok) {
        this.isDirty = true;
        console.error("Failed to save annotations", data);
        alert("Failed to save annotations");
        return;
      }

      if (Array.isArray(data.annotations)) {
        this.applyServerAnnotations(data.annotations, data.revision);
      } else {
        if (Number.isFinite(Number(data.revision))) {
          this.annotationRevision = Number(data.revision);
        }
        this.replaceSavedSparseState();
        this.refreshCurrentHistoryCheckpoint();
      }
      this.isDirty = false;
      this.updateStatusBadge(data.item_status);
      this.persistManualKeyframesToStorage();
      this.updateHistoryButtons();
      console.log(
        "Annotations saved",
        data.annotation_count ?? payload.upserts.length
      );
    } catch (error) {
      this.isDirty = true;
      console.error("Error while saving annotations", error);
      alert("Error while saving annotations");
    } finally {
      this.isSaving = false;
      if (this.pendingSaveRequested) {
        this.pendingSaveRequested = false;
        this.scheduleSave();
      }
    }
  }


  updateFrameDisplay() {
    if (this.kind !== "video") return;
    if (this.frameDisplayEl) {
      this.frameDisplayEl.textContent = String((this.currentFrameIndex | 0) + 1);
    }
    if (this.frameTotalEl && this.totalFrames && this.totalFrames > 0) {
      this.frameTotalEl.textContent = String(this.totalFrames);
    }
    if (this.timeDisplayEl) {
      const seconds = this.currentFrameIndex / this.fps;
      this.timeDisplayEl.textContent = seconds.toFixed(2);
    }
    this.updateFramePermalinkButton();
  }

  getCurrentFramePermalink(frameIndex = this.currentFrameIndex) {
    if (typeof window === "undefined") return null;

    const url = new URL(window.location.href);
    url.searchParams.delete(REGION_COMMENT_QUERY_PARAM);
    if (this.kind === "video") {
      const normalized = Math.max(0, Math.trunc(Number(frameIndex) || 0));
      url.searchParams.set(ITEM_FRAME_QUERY_PARAM, String(normalized + 1));
    } else {
      url.searchParams.delete(ITEM_FRAME_QUERY_PARAM);
    }

    return url.toString();
  }

  updateFramePermalinkButton(copied = false) {
    const button = this.copyFrameLinkBtnEl;
    if (!button || this.kind !== "video") return;

    const label = copied ? "Copied" : "Copy frame link";
    if (this.copyFrameLinkLabelEl) {
      this.copyFrameLinkLabelEl.textContent = label;
    } else {
      button.textContent = label;
    }

    const permalink = this.getCurrentFramePermalink();
    if (permalink) {
      button.title = permalink;
      button.setAttribute("aria-label", `Copy link for frame ${(this.currentFrameIndex | 0) + 1}`);
    }
  }

  async copyCurrentFramePermalink() {
    const permalink = this.getCurrentFramePermalink();
    if (!permalink) return;

    const copied = await copyTextToClipboard(permalink);
    if (!copied) {
      alert("Failed to copy frame link");
      return;
    }

    this.updateFramePermalinkButton(true);
    if (this.copyFrameLinkFeedbackTimer) {
      window.clearTimeout(this.copyFrameLinkFeedbackTimer);
    }
    this.copyFrameLinkFeedbackTimer = window.setTimeout(() => {
      this.copyFrameLinkFeedbackTimer = null;
      this.updateFramePermalinkButton(false);
    }, 1800);
  }

  applyRequestedFrameFromLocation() {
    if (this.kind !== "video") return;
    if (!Number.isInteger(this.requestedFrameIndexFromUrl)) return;

    const targetFrame = this.totalFrames && this.totalFrames > 0
      ? Math.max(0, Math.min(this.totalFrames - 1, this.requestedFrameIndexFromUrl))
      : Math.max(0, this.requestedFrameIndexFromUrl);
    this.requestedFrameIndexFromUrl = null;

    if (targetFrame === (this.currentFrameIndex | 0)) {
      this.updateFrameDisplay();
      return;
    }

    this.seekToFrame(targetFrame);
  }

  setCurrentFrame(frameIndex, options = {}) {
    this.hideObjectContextMenu();
    if (this.kind !== "video") {
      this.loadFrame(null, false);
      return;
    }

    const { source = "scrub" } = options;
    if (source === "scrub") {
      this.requestFramePresentation(frameIndex, options);
      return;
    }

    this.applyVisibleFrame(frameIndex, options);
  }

  applyVisibleFrame(frameIndex, options = {}) {
    const { source = "internal" } = options;

    const clamped = this.clampFrameIndex(frameIndex);

    this.hasPresentedVideoFrame = true;
    this.currentFrameIndex = clamped;
    this.lastFrameIndex = clamped;
    this.annotations = this.buildAnnotationsForFrame(clamped);
    this.activeAnnotation = null;
    this.activeVertexIndex = null;

    this.updateFrameDisplay();
    this.updateFastActionButtons();
    this.syncTrackVisibilityControls();
    this.syncAnnotationStateControls();
    this.requestRedraw(source !== "playback");
    this.updateReferenceControls();
    this.updateTimelinePlayhead();
    this.updateInterpolationPanel();
    this.restorePendingTrackSelection();
  }

  syncPlaybackTerminalFrame() {
    if (this.kind !== "video" || !this.mediaEl || this.mediaEl.tagName !== "VIDEO") {
      return;
    }

    const totalFrames = this.totalFrames || this.computeTotalFrames();
    if (!totalFrames || totalFrames <= 0) {
      this.updateFrameDisplay();
      this.updateTimelinePlayhead();
      return;
    }

    const safeFps = Number(this.fps) > 0 ? Number(this.fps) : 30;
    const currentTime = Number(this.mediaEl.currentTime);
    const duration = Number(this.mediaEl.duration);
    const terminalThreshold = 1 / safeFps;
    const shouldSnapToLastFrame =
      this.mediaEl.ended ||
      (Number.isFinite(currentTime) &&
        Number.isFinite(duration) &&
        currentTime >= duration - terminalThreshold);

    if (!shouldSnapToLastFrame) {
      this.updateFrameDisplay();
      this.updateTimelinePlayhead();
      return;
    }

    const finalFrameIndex = totalFrames - 1;
    if (finalFrameIndex !== this.lastFrameIndex) {
      this.setCurrentFrame(finalFrameIndex, { source: "playback" });
      return;
    }

    this.updateFrameDisplay();
    this.updateTimelinePlayhead();
  }


  handleVideoTimeUpdate() {
    if (this.renderLoopActive) {
      return;
    }

    if (this.mediaEl.paused || this.mediaEl.ended) {
      this.syncPlaybackTerminalFrame();
      return;
    }

    let frameIdx = Math.round(this.mediaEl.currentTime * this.fps);
    if (this.totalFrames && this.totalFrames > 0 && frameIdx >= this.totalFrames) {
      frameIdx = this.totalFrames - 1;
    }
    if (frameIdx !== this.lastFrameIndex) {
      this.setCurrentFrame(frameIdx, {
        source: "playback",
        copyFromPrev: true,
      });
    } else {
      this.updateFrameDisplay();
      this.updateTimelinePlayhead();
    }
  }

  togglePlayback() {
    if (this.isFrameTransitionPending()) {
      return;
    }
    if (this.mediaEl.paused) {
      this.mediaEl.play();
      this.startRenderLoop();
    } else {
      this.mediaEl.pause();
      this.stopRenderLoop();
      // Ensure the final paused frame is drawn
      this.requestRedraw();
    }
  }

  startRenderLoop() {
    if (this.renderLoopActive) return;
    if (!this.mediaEl || this.mediaEl.tagName !== "VIDEO") return;

    this.renderLoopActive = true;

    const tick = () => {
      if (!this.renderLoopActive) {
        this.renderLoopHandle = null;
        return;
      }

      if (!this.mediaEl.paused && !this.mediaEl.ended) {
        const totalFrames = this.totalFrames || this.computeTotalFrames();
        let frameIdx = Math.round(this.mediaEl.currentTime * this.fps);
        if (totalFrames && totalFrames > 0 && frameIdx >= totalFrames) {
          frameIdx = totalFrames - 1;
        }
        if (frameIdx !== this.lastFrameIndex) {
          this.setCurrentFrame(frameIdx, { source: "playback" });
        } else {
          this.updateFrameDisplay();
          this.updateTimelinePlayhead();
        }

        this.renderLoopHandle = window.requestAnimationFrame(tick);
        return;
      }

      this.renderLoopActive = false;
      this.renderLoopHandle = null;
      this.syncPlaybackTerminalFrame();
      this.requestRedraw();
    };

    this.renderLoopHandle = window.requestAnimationFrame(tick);
  }

  stopRenderLoop() {
    this.renderLoopActive = false;
    if (this.renderLoopHandle != null) {
      window.cancelAnimationFrame(this.renderLoopHandle);
      this.renderLoopHandle = null;
    }
  }

  seekToFrame(frameIndex, options = {}) {
    if (this.mediaEl.tagName === "VIDEO" && !this.mediaEl.paused) {
      this.mediaEl.pause();
      this.stopRenderLoop();
    }
    if (this.kind !== "video") return;
    this.setCurrentFrame(frameIndex, {
      ...options,
      source: "scrub",
    });
  }

  stepFrames(delta) {
    const targetFrame = this.getFrameNavigationBaseIndex() + delta;
    const previousTrackId =
      this.kind === "video" &&
      this.activeAnnotation &&
      typeof this.activeAnnotation.track_id === "number"
        ? this.activeAnnotation.track_id
        : null;

    this.seekToFrame(targetFrame, {
      restoreTrackId: previousTrackId,
    });
  }

}
