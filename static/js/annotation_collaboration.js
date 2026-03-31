function clonePolygonPoints(points) {
  if (!Array.isArray(points)) return null;
  const normalized = points
    .filter((point) => Array.isArray(point) && point.length === 2)
    .map((point) => [Number(point[0]), Number(point[1])])
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  return normalized.length ? normalized : null;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function buildWebSocketUrl(path) {
  if (!path) return null;
  if (path.startsWith("ws://") || path.startsWith("wss://")) {
    return path;
  }
  if (typeof window === "undefined") {
    return null;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

function getCollaborationState(canvas) {
  return canvas.collaboration || null;
}

function getDisplayName(participant) {
  if (participant?.email) {
    return participant.email;
  }
  if (Number.isInteger(participant?.user_id)) {
    return `User #${participant.user_id}`;
  }
  return "Unknown teammate";
}

function getRemoteParticipants(canvas) {
  const state = getCollaborationState(canvas);
  if (!state) return [];
  return Array.from(state.participants.values())
    .filter((participant) => participant.participant_id !== state.participantId)
    .sort((left, right) => {
      const leftEmail = left.email || "";
      const rightEmail = right.email || "";
      if (leftEmail !== rightEmail) {
        return leftEmail.localeCompare(rightEmail);
      }
      return (left.participant_id || "").localeCompare(right.participant_id || "");
    });
}

function usesAsyncVideoFramePresentation(canvas, source) {
  if (canvas?.kind !== "video") {
    return false;
  }

  return source === "scrub" || source === "step" || source === "collaboration-follow";
}

function clampPointToImage(canvas, point) {
  const maxWidth =
    canvas.imageWidth || canvas.mediaEl.naturalWidth || canvas.mediaEl.videoWidth || 0;
  const maxHeight =
    canvas.imageHeight || canvas.mediaEl.naturalHeight || canvas.mediaEl.videoHeight || 0;

  if (!maxWidth || !maxHeight) {
    return point;
  }

  return {
    x: Math.max(0, Math.min(maxWidth, Number(point.x) || 0)),
    y: Math.max(0, Math.min(maxHeight, Number(point.y) || 0)),
  };
}

function readCursorFromEvent(canvas, evt) {
  if (!evt || !canvas?.canvas) return null;
  const rect = canvas.canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;

  const inside =
    evt.clientX >= rect.left &&
    evt.clientX <= rect.right &&
    evt.clientY >= rect.top &&
    evt.clientY <= rect.bottom;
  if (!inside) {
    return null;
  }

  return clampPointToImage(canvas, canvas.toImageCoordinates(evt));
}

function deriveLocalAction(canvas) {
  const state = getCollaborationState(canvas);
  if (!state) return "idle";
  if (canvas.isPanning) return "panning";
  if (canvas.isDragging) return "editing";
  if (canvas.isDrawing || canvas.isPolygonDrawing) return "drawing";
  if (canvas.mediaEl?.tagName === "VIDEO" && !canvas.mediaEl.paused) {
    return "playing";
  }
  if (state.followParticipantId) return "following";
  if (state.cursorInside) return "hover";
  return "idle";
}

function deriveLocalTool(canvas) {
  const sam2State = canvas.sam2 || null;
  if (sam2State?.enabled && sam2State.promptMode && sam2State.promptMode !== "none") {
    return `sam2:${sam2State.promptMode}`;
  }
  if (typeof canvas.getGeometryKindForLabel === "function") {
    return canvas.getGeometryKindForLabel(canvas.currentLabelClassId) || null;
  }
  return null;
}

function buildDraftPayload(canvas) {
  const source =
    canvas.currentDrawingAnnotation ||
    (canvas.isDragging ? canvas.draggedAnnotation : null) ||
    null;
  if (!source) {
    return null;
  }

  const geometryKind =
    typeof canvas.getGeometryKindForLabel === "function"
      ? canvas.getGeometryKindForLabel(source.label_class_id || canvas.currentLabelClassId)
      : "bbox";

  const draft = {
    geometry_kind: geometryKind || "bbox",
    label_class_id: source.label_class_id || canvas.currentLabelClassId || null,
    track_id: Number.isInteger(source.track_id) ? source.track_id : null,
    client_uid: source.client_uid || null,
    x1: Number(source.x1),
    y1: Number(source.y1),
    x2: Number(source.x2),
    y2: Number(source.y2),
  };

  const polygonPoints = clonePolygonPoints(source.polygon_points);
  if (polygonPoints) {
    draft.polygon_points = polygonPoints;
  }
  return draft;
}

function capturePresenceState(canvas) {
  const state = getCollaborationState(canvas);
  if (!state) return null;

  const activeAnnotation =
    canvas.activeAnnotation?._storedAnnotation || canvas.activeAnnotation || null;
  const frameIndex = canvas.kind === "video" ? canvas.currentFrameIndex : null;
  const cursor = state.lastCursor
    ? {
        x: Number(state.lastCursor.x),
        y: Number(state.lastCursor.y),
        visible: true,
      }
    : null;

  return {
    frame_index: Number.isInteger(frameIndex) ? frameIndex : null,
    current_time_sec:
      Number.isInteger(frameIndex) && Number.isFinite(Number(canvas.fps)) && Number(canvas.fps) > 0
        ? frameIndex / Number(canvas.fps)
        : null,
    label_class_id: Number.isInteger(canvas.currentLabelClassId)
      ? canvas.currentLabelClassId
      : null,
    active_track_id: Number.isInteger(canvas.activeTrackId)
      ? canvas.activeTrackId
      : Number.isInteger(activeAnnotation?.track_id)
        ? activeAnnotation.track_id
        : null,
    active_annotation_uid: activeAnnotation?.client_uid || null,
    action: deriveLocalAction(canvas),
    tool: deriveLocalTool(canvas),
    playing: canvas.mediaEl?.tagName === "VIDEO" ? !canvas.mediaEl.paused : false,
    cursor,
    draft: buildDraftPayload(canvas),
  };
}

function sendPresenceUpdate(canvas, { force = false } = {}) {
  const state = getCollaborationState(canvas);
  if (!state?.ws || state.ws.readyState !== WebSocket.OPEN) {
    return;
  }

  if (state.sendTimer) {
    window.clearTimeout(state.sendTimer);
    state.sendTimer = null;
  }

  const payload = capturePresenceState(canvas);
  if (!payload) return;

  const serialized = JSON.stringify(payload);
  const now = Date.now();
  if (!force && serialized === state.lastSentPayload && now - state.lastSentAt < 7000) {
    return;
  }

  try {
    state.ws.send(
      JSON.stringify({
        type: "presence.update",
        state: payload,
      })
    );
    state.lastSentPayload = serialized;
    state.lastSentAt = now;
  } catch (error) {
    console.error("Failed to send collaboration presence", error);
  }
}

function schedulePresenceUpdate(canvas, { immediate = false } = {}) {
  const state = getCollaborationState(canvas);
  if (!state?.enabled) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  if (immediate) {
    sendPresenceUpdate(canvas, { force: true });
    return;
  }

  if (state.sendTimer) {
    return;
  }

  const elapsed = Date.now() - state.lastSentAt;
  const delay = Math.max(0, state.minSendIntervalMs - elapsed);
  state.sendTimer = window.setTimeout(() => {
    state.sendTimer = null;
    sendPresenceUpdate(canvas);
  }, delay);
}

function updateCursorState(canvas, evt = null) {
  const state = getCollaborationState(canvas);
  if (!state) return;

  const cursor = readCursorFromEvent(canvas, evt);
  if (cursor) {
    state.lastCursor = cursor;
    state.cursorInside = true;
    return;
  }

  if (!canvas.isDrawing && !canvas.isDragging && !canvas.isPanning && !canvas.isPolygonDrawing) {
    state.lastCursor = null;
    state.cursorInside = false;
  }
}

function clearCursorState(canvas) {
  const state = getCollaborationState(canvas);
  if (!state) return;
  state.lastCursor = null;
  state.cursorInside = false;
}

function describeTool(tool) {
  if (!tool) return null;
  if (tool.startsWith("sam2:")) {
    return `SAM2 ${tool.split(":")[1]}`;
  }
  if (tool === "bbox") return "BBox";
  if (tool === "polygon") return "Polygon";
  if (tool === "tag") return "Tag";
  return tool;
}

function describeParticipant(canvas, participant) {
  const parts = [];

  if (canvas.kind === "video" && Number.isInteger(participant.frame_index)) {
    parts.push(`Frame ${participant.frame_index + 1}`);
  } else {
    parts.push("Image");
  }

  if (participant.playing) {
    parts.push("Playing");
  } else {
    switch (participant.action) {
      case "drawing":
        parts.push("Drawing");
        break;
      case "editing":
        parts.push("Editing");
        break;
      case "panning":
        parts.push("Panning");
        break;
      case "hover":
        parts.push("Hovering");
        break;
      case "following":
        parts.push("Following");
        break;
      default:
        parts.push("Viewing");
        break;
    }
  }

  const labelName =
    Number.isInteger(participant.label_class_id) &&
    typeof canvas.getLabelClassName === "function"
      ? canvas.getLabelClassName(participant.label_class_id)
      : null;
  if (labelName && labelName !== "?") {
    parts.push(labelName);
  }

  const toolName = describeTool(participant.tool);
  if (toolName) {
    parts.push(toolName);
  }

  if (Number.isInteger(participant.active_track_id)) {
    parts.push(`Object ${participant.active_track_id}`);
  }

  return parts.join(" | ");
}

function ensureTimelineLayer(canvas) {
  const state = getCollaborationState(canvas);
  if (!state) return null;
  if (state.timelineLayerEl) return state.timelineLayerEl;

  state.timelineLayerEl = document.getElementById("video-timeline-collaboration-layer");
  return state.timelineLayerEl;
}

function renderTimelineParticipants(canvas) {
  const state = getCollaborationState(canvas);
  const layerEl = ensureTimelineLayer(canvas);
  if (!state || !layerEl) return;

  layerEl.innerHTML = "";
  if (canvas.kind !== "video" || !Number.isInteger(canvas.totalFrames) || canvas.totalFrames <= 0) {
    return;
  }

  const participants = getRemoteParticipants(canvas).filter((participant) =>
    Number.isInteger(participant.frame_index)
  );

  participants.forEach((participant, index) => {
    const marker = document.createElement("button");
    marker.type = "button";
    marker.className =
      "timeline-collaboration-marker" +
      (state.followParticipantId === participant.participant_id ? " is-following" : "");
    marker.style.left = `${(participant.frame_index / Math.max(canvas.totalFrames - 1, 1)) * 100}%`;
    marker.style.setProperty("--lf-collab-color", participant.color || "#39a0ed");
    marker.style.setProperty("--lf-collab-stack", String(index % 4));
    marker.title = `${getDisplayName(participant)} on frame ${participant.frame_index + 1}`;
    marker.setAttribute("aria-label", marker.title);
    marker.addEventListener("click", (evt) => {
      evt.preventDefault();
      evt.stopPropagation();
      toggleFollowParticipant(canvas, participant.participant_id);
    });
    layerEl.appendChild(marker);
  });
}

function setStatusText(canvas, text) {
  const state = getCollaborationState(canvas);
  if (!state?.statusEl) return;
  state.statusEl.textContent = text;
}

function renderParticipantList(canvas) {
  const state = getCollaborationState(canvas);
  if (!state?.listEl) return;

  const participants = getRemoteParticipants(canvas);
  if (state.liveCountEl) {
    state.liveCountEl.textContent = String(participants.length);
  }

  state.listEl.innerHTML = "";
  if (!participants.length) {
    const empty = document.createElement("div");
    empty.className = "collaboration-empty";
    empty.textContent = "No other teammates are currently on this item.";
    state.listEl.appendChild(empty);
  } else {
    participants.forEach((participant) => {
      const row = document.createElement("div");
      row.className =
        "collaboration-participant-row" +
        (state.followParticipantId === participant.participant_id ? " is-following" : "");

      const main = document.createElement("div");
      main.className = "collaboration-participant-main";

      const swatch = document.createElement("span");
      swatch.className = "collaboration-swatch";
      swatch.style.setProperty("--lf-collab-color", participant.color || "#39a0ed");
      main.appendChild(swatch);

      const meta = document.createElement("div");
      meta.className = "collaboration-participant-meta";

      const name = document.createElement("div");
      name.className = "collaboration-participant-name";
      name.textContent = getDisplayName(participant);
      meta.appendChild(name);

      const detail = document.createElement("div");
      detail.className = "collaboration-participant-detail";
      detail.textContent = describeParticipant(canvas, participant);
      meta.appendChild(detail);

      main.appendChild(meta);
      row.appendChild(main);

      const actions = document.createElement("div");
      actions.className = "collaboration-participant-actions";

      const followBtn = document.createElement("button");
      followBtn.type = "button";
      followBtn.className =
        "btn btn-sm " +
        (state.followParticipantId === participant.participant_id
          ? "btn-info"
          : "btn-outline-info");
      followBtn.textContent =
        state.followParticipantId === participant.participant_id
          ? "Following"
          : canvas.kind === "video"
            ? "Follow"
            : "Focus";
      followBtn.addEventListener("click", (evt) => {
        evt.preventDefault();
        evt.stopPropagation();
        toggleFollowParticipant(canvas, participant.participant_id);
      });
      actions.appendChild(followBtn);
      row.appendChild(actions);

      row.addEventListener("click", () => {
        toggleFollowParticipant(canvas, participant.participant_id);
      });

      state.listEl.appendChild(row);
    });
  }

  if (state.followStatusEl) {
    const followed = state.followParticipantId
      ? state.participants.get(state.followParticipantId) || null
      : null;
    if (followed) {
      state.followStatusEl.classList.remove("d-none");
      state.followStatusEl.innerHTML = `Following <strong>${escapeHtml(
        getDisplayName(followed)
      )}</strong>. Click the badge again or interact locally to stop.`;
    } else {
      state.followStatusEl.classList.add("d-none");
      state.followStatusEl.textContent = "";
    }
  }

  renderTimelineParticipants(canvas);
}

function getViewportSize(canvas) {
  return {
    width: canvas.viewportWidth || canvas.canvas.width / (canvas.pixelRatio || 1) || 0,
    height:
      canvas.viewportHeight || canvas.canvas.height / (canvas.pixelRatio || 1) || 0,
  };
}

function keepPointInView(canvas, point) {
  const { width, height } = getViewportSize(canvas);
  if (!width || !height) return;

  const padding = 96;
  const current = canvas.fromImageToCanvasCoords(point.x, point.y);
  const inside =
    current.x >= padding &&
    current.x <= width - padding &&
    current.y >= padding &&
    current.y <= height - padding;
  if (inside) {
    return;
  }

  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);
  canvas.translateX = width / 2 - point.x * scale;
  canvas.translateY = height / 2 - point.y * scale;
  canvas.requestRedraw(false);
}

function applyFollowParticipant(canvas, participant) {
  if (!participant) return;

  if (canvas.kind === "video" && Number.isInteger(participant.frame_index)) {
    if (canvas.currentFrameIndex !== participant.frame_index) {
      canvas.setCurrentFrame(participant.frame_index, {
        source: "collaboration-follow",
      });
    }
  }

  if (participant.cursor && Number.isFinite(participant.cursor.x) && Number.isFinite(participant.cursor.y)) {
    keepPointInView(canvas, participant.cursor);
  }

  canvas.requestRedraw(false);
}

function stopFollowingParticipant(canvas) {
  const state = getCollaborationState(canvas);
  if (!state || !state.followParticipantId) return;
  state.followParticipantId = null;
  renderParticipantList(canvas);
  schedulePresenceUpdate(canvas, { immediate: true });
}

function toggleFollowParticipant(canvas, participantId) {
  const state = getCollaborationState(canvas);
  if (!state) return;

  if (state.followParticipantId === participantId) {
    stopFollowingParticipant(canvas);
    return;
  }

  const participant = state.participants.get(participantId);
  if (!participant) return;

  state.followParticipantId = participantId;
  renderParticipantList(canvas);
  const shouldWaitForFrameSync =
    canvas.kind === "video" &&
    Number.isInteger(participant.frame_index) &&
    canvas.currentFrameIndex !== participant.frame_index;
  applyFollowParticipant(canvas, participant);
  schedulePresenceUpdate(canvas, { immediate: !shouldWaitForFrameSync });
}

function resolveParticipantAnnotation(canvas, participant) {
  if (!participant) return null;

  if (
    participant.active_annotation_uid &&
    typeof canvas.findAnnotationByClientUid === "function"
  ) {
    const annotation = canvas.findAnnotationByClientUid(participant.active_annotation_uid);
    if (annotation) {
      return annotation;
    }
  }

  if (Number.isInteger(participant.active_track_id)) {
    return (
      canvas.annotations.find((annotation) => annotation.track_id === participant.active_track_id) ||
      null
    );
  }

  return null;
}

function drawRemoteShape(canvas, shape, color, { dashed = true, fillAlpha = 0.08 } = {}) {
  if (!shape) return;

  const geometryKind =
    shape.geometry_kind ||
    (typeof canvas.getGeometryKindForLabel === "function"
      ? canvas.getGeometryKindForLabel(shape.label_class_id)
      : "bbox");
  const ctx = canvas.ctx;
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1) || 1;

  ctx.save();
  ctx.lineWidth = 2 / scale;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  if (dashed) {
    ctx.setLineDash([10 / scale, 6 / scale]);
  }

  if (geometryKind === "polygon" && Array.isArray(shape.polygon_points) && shape.polygon_points.length >= 3) {
    ctx.beginPath();
    ctx.moveTo(shape.polygon_points[0][0], shape.polygon_points[0][1]);
    for (let index = 1; index < shape.polygon_points.length; index += 1) {
      ctx.lineTo(shape.polygon_points[index][0], shape.polygon_points[index][1]);
    }
    ctx.closePath();
    ctx.save();
    ctx.globalAlpha = fillAlpha;
    ctx.fill();
    ctx.restore();
    ctx.stroke();
  } else if (
    Number.isFinite(shape.x1) &&
    Number.isFinite(shape.y1) &&
    Number.isFinite(shape.x2) &&
    Number.isFinite(shape.y2)
  ) {
    const x = Math.min(shape.x1, shape.x2);
    const y = Math.min(shape.y1, shape.y2);
    const width = Math.abs(shape.x2 - shape.x1);
    const height = Math.abs(shape.y2 - shape.y1);
    ctx.save();
    ctx.globalAlpha = fillAlpha;
    ctx.fillRect(x, y, width, height);
    ctx.restore();
    ctx.strokeRect(x, y, width, height);
  }

  ctx.restore();
}

function drawRemoteCursor(canvas, participant) {
  if (!participant?.cursor) return;

  const ctx = canvas.ctx;
  const point = participant.cursor;
  const color = participant.color || "#39a0ed";
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1) || 1;
  const radius = 7 / scale;

  ctx.save();
  ctx.lineWidth = 2 / scale;
  ctx.strokeStyle = color;
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.moveTo(point.x - 14 / scale, point.y);
  ctx.lineTo(point.x + 14 / scale, point.y);
  ctx.moveTo(point.x, point.y - 14 / scale);
  ctx.lineTo(point.x, point.y + 14 / scale);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.restore();

  const screenPoint = canvas.fromImageToCanvasCoords(point.x, point.y);
  ctx.save();
  ctx.setTransform(canvas.pixelRatio, 0, 0, canvas.pixelRatio, 0, 0);
  ctx.font = "600 12px sans-serif";
  const label = getDisplayName(participant);
  const textWidth = ctx.measureText(label).width;
  const boxX = screenPoint.x + 12;
  const boxY = screenPoint.y + 12;
  ctx.fillStyle = color;
  ctx.fillRect(boxX, boxY, textWidth + 14, 20);
  ctx.fillStyle = "#081018";
  ctx.fillText(label, boxX + 7, boxY + 14);
  ctx.restore();
}

function shouldRenderParticipantOnCurrentFrame(canvas, participant) {
  if (canvas.kind !== "video") {
    return true;
  }
  return participant.frame_index === canvas.currentFrameIndex;
}

function drawParticipantOverlays(canvas) {
  const participants = getRemoteParticipants(canvas);
  if (!participants.length) return;

  const imgWidth =
    canvas.imageWidth || canvas.mediaEl.naturalWidth || canvas.mediaEl.videoWidth || 0;
  const imgHeight =
    canvas.imageHeight || canvas.mediaEl.naturalHeight || canvas.mediaEl.videoHeight || 0;
  if (!imgWidth || !imgHeight) return;

  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);
  const translateX = canvas.translateX || 0;
  const translateY = canvas.translateY || 0;
  const ctx = canvas.ctx;

  ctx.save();
  ctx.setTransform(
    canvas.pixelRatio * scale,
    0,
    0,
    canvas.pixelRatio * scale,
    canvas.pixelRatio * translateX,
    canvas.pixelRatio * translateY
  );

  participants.forEach((participant) => {
    if (!shouldRenderParticipantOnCurrentFrame(canvas, participant)) {
      return;
    }

    const color = participant.color || "#39a0ed";
    const draft = participant.draft || null;
    if (draft) {
      drawRemoteShape(canvas, draft, color, { dashed: true, fillAlpha: 0.1 });
    }

    const activeAnnotation = resolveParticipantAnnotation(canvas, participant);
    if (activeAnnotation && typeof canvas.isAnnotationVisible === "function") {
      if (canvas.isAnnotationVisible(activeAnnotation)) {
        drawRemoteShape(canvas, activeAnnotation, color, {
          dashed: !draft,
          fillAlpha: 0.06,
        });
      }
    }

    drawRemoteCursor(canvas, participant);
  });

  ctx.restore();
}

function upsertParticipant(canvas, participant) {
  const state = getCollaborationState(canvas);
  if (!state || !participant?.participant_id) return;
  state.participants.set(participant.participant_id, participant);
  if (state.followParticipantId === participant.participant_id) {
    applyFollowParticipant(canvas, participant);
  }
  renderParticipantList(canvas);
  canvas.requestRedraw(false);
}

function removeParticipant(canvas, participantId) {
  const state = getCollaborationState(canvas);
  if (!state) return;
  state.participants.delete(participantId);
  if (state.followParticipantId === participantId) {
    state.followParticipantId = null;
  }
  renderParticipantList(canvas);
  canvas.requestRedraw(false);
}

function applyPendingRemoteCommit(canvas) {
  const state = getCollaborationState(canvas);
  if (!state?.pendingRemoteCommit) return;
  if (canvas.isSaving || canvas.isDirty || canvas.isInteractionActive()) return;

  const payload = state.pendingRemoteCommit;
  state.pendingRemoteCommit = null;

  if (Array.isArray(payload.annotations)) {
    canvas.applyServerAnnotations(payload.annotations, Number(payload.revision) || 0);
  }
  if (payload.item_status && typeof canvas.updateStatusBadge === "function") {
    canvas.updateStatusBadge(payload.item_status);
  }
  setStatusText(canvas, "Synced latest teammate annotations.");
}

function handleRemoteCommit(canvas, payload) {
  const revision = Number(payload?.revision);
  if (!Number.isFinite(revision)) return;
  if (revision <= Number(canvas.annotationRevision || 0)) return;

  if (canvas.isSaving || canvas.isDirty || canvas.isInteractionActive()) {
    const state = getCollaborationState(canvas);
    if (!state) return;
    state.pendingRemoteCommit = payload;
    setStatusText(
      canvas,
      "A teammate saved new annotations. Sync will apply when your current edit settles."
    );
    return;
  }

  if (Array.isArray(payload.annotations)) {
    canvas.applyServerAnnotations(payload.annotations, revision);
  }
  if (payload.item_status && typeof canvas.updateStatusBadge === "function") {
    canvas.updateStatusBadge(payload.item_status);
  }
  setStatusText(canvas, "Synced latest teammate annotations.");
}

function handleSocketMessage(canvas, event) {
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch (error) {
    console.error("Invalid collaboration payload", error);
    return;
  }

  const state = getCollaborationState(canvas);
  if (!state || !payload?.type) return;

  switch (payload.type) {
    case "collaboration.hello":
      state.participantId = payload.participant_id || null;
      state.participants = new Map(
        Array.isArray(payload.participants)
          ? payload.participants
              .filter((participant) => participant?.participant_id)
              .map((participant) => [participant.participant_id, participant])
          : []
      );
      setStatusText(canvas, "Live teammate channel connected.");
      renderParticipantList(canvas);
      sendPresenceUpdate(canvas, { force: true });
      break;
    case "collaboration.participant_state":
      if (payload.participant) {
        upsertParticipant(canvas, payload.participant);
      }
      break;
    case "collaboration.participant_left":
      if (payload.participant_id) {
        removeParticipant(canvas, payload.participant_id);
      }
      break;
    case "collaboration.annotations_committed":
      handleRemoteCommit(canvas, payload);
      break;
    default:
      break;
  }
}

function scheduleReconnect(canvas) {
  const state = getCollaborationState(canvas);
  if (!state || state.closedByClient || state.reconnectTimer) return;

  const delay = Math.min(6000, 800 * Math.max(1, state.reconnectAttempt));
  state.reconnectAttempt += 1;
  state.reconnectTimer = window.setTimeout(() => {
    state.reconnectTimer = null;
    openCollaborationSocket(canvas);
  }, delay);
}

function closeCollaborationSocket(canvas) {
  const state = getCollaborationState(canvas);
  if (!state) return;
  state.closedByClient = true;
  if (state.reconnectTimer) {
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
  if (state.ws) {
    try {
      state.ws.close();
    } catch (_error) {
      // Ignore close errors during unload.
    }
    state.ws = null;
  }
}

function openCollaborationSocket(canvas) {
  const state = getCollaborationState(canvas);
  if (!state?.enabled || !state.wsUrl) return;
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  state.closedByClient = false;
  setStatusText(canvas, "Connecting collaboration channel...");

  const ws = new WebSocket(state.wsUrl);
  state.ws = ws;

  ws.addEventListener("open", () => {
    state.reconnectAttempt = 1;
    setStatusText(canvas, "Collaboration channel connected.");
  });

  ws.addEventListener("message", (event) => {
    handleSocketMessage(canvas, event);
  });

  ws.addEventListener("close", () => {
    if (state.ws === ws) {
      state.ws = null;
    }
    if (!state.closedByClient) {
      setStatusText(canvas, "Collaboration channel disconnected. Reconnecting...");
      scheduleReconnect(canvas);
    }
  });

  ws.addEventListener("error", () => {
    setStatusText(canvas, "Collaboration channel error. Reconnecting...");
  });
}

function bindUi(canvas) {
  const state = getCollaborationState(canvas);
  if (!state || state.bound) return;

  state.listEl = document.getElementById("collaboration-participant-list");
  state.statusEl = document.getElementById("collaboration-status");
  state.followStatusEl = document.getElementById("collaboration-follow-status");
  state.liveCountEl = document.getElementById("collaboration-live-count");
  state.timelineLayerEl = document.getElementById("video-timeline-collaboration-layer");

  canvas.canvas.addEventListener("mouseleave", () => {
    clearCursorState(canvas);
    schedulePresenceUpdate(canvas, { immediate: true });
  });
  window.addEventListener("blur", () => {
    clearCursorState(canvas);
    schedulePresenceUpdate(canvas, { immediate: true });
  });
  window.addEventListener("beforeunload", () => {
    closeCollaborationSocket(canvas);
  });

  state.maintenanceTimer = window.setInterval(() => {
    applyPendingRemoteCommit(canvas);
    if (state.ws && state.ws.readyState === WebSocket.OPEN && Date.now() - state.lastSentAt > 8000) {
      sendPresenceUpdate(canvas, { force: true });
    }
  }, 1000);

  renderParticipantList(canvas);
  openCollaborationSocket(canvas);
  state.bound = true;
}

export function enhanceAnnotationCanvasWithCollaboration(canvas, config = {}) {
  canvas.collaboration = {
    enabled: !!config.enabled && !!config.wsPath,
    wsPath: config.wsPath || null,
    wsUrl: buildWebSocketUrl(config.wsPath || ""),
    currentUser: config.currentUser || null,
    ws: null,
    participantId: null,
    participants: new Map(),
    followParticipantId: null,
    pendingRemoteCommit: null,
    reconnectAttempt: 1,
    reconnectTimer: null,
    sendTimer: null,
    maintenanceTimer: null,
    minSendIntervalMs: 60,
    lastSentAt: 0,
    lastSentPayload: null,
    lastCursor: null,
    cursorInside: false,
    closedByClient: false,
    bound: false,
    listEl: null,
    statusEl: null,
    followStatusEl: null,
    liveCountEl: null,
    timelineLayerEl: null,
  };

  if (!canvas.collaboration.enabled) {
    return;
  }

  const originalAttachEvents = canvas.attachEvents.bind(canvas);
  const originalOnMouseDown = canvas.onMouseDown.bind(canvas);
  const originalOnMouseMove = canvas.onMouseMove.bind(canvas);
  const originalOnMouseUp = canvas.onMouseUp.bind(canvas);
  const originalRedraw = canvas.redraw.bind(canvas);
  const originalApplyVisibleFrame = canvas.applyVisibleFrame.bind(canvas);
  const originalSetCurrentFrame = canvas.setCurrentFrame.bind(canvas);
  const originalMarkActiveAnnotation = canvas.markActiveAnnotation.bind(canvas);
  const originalTogglePlayback = canvas.togglePlayback.bind(canvas);
  const originalApplyLabelSelection =
    typeof canvas.applyLabelSelection === "function"
      ? canvas.applyLabelSelection.bind(canvas)
      : null;
  const originalApplyServerAnnotations =
    typeof canvas.applyServerAnnotations === "function"
      ? canvas.applyServerAnnotations.bind(canvas)
      : null;

  canvas.attachEvents = function patchedAttachEvents() {
    originalAttachEvents();
    bindUi(canvas);
  };

  canvas.onMouseDown = function patchedOnMouseDown(evt) {
    stopFollowingParticipant(canvas);
    const result = originalOnMouseDown(evt);
    updateCursorState(canvas, evt);
    schedulePresenceUpdate(canvas, { immediate: true });
    return result;
  };

  canvas.onMouseMove = function patchedOnMouseMove(evt) {
    const result = originalOnMouseMove(evt);
    updateCursorState(canvas, evt);
    schedulePresenceUpdate(canvas);
    return result;
  };

  canvas.onMouseUp = function patchedOnMouseUp(evt) {
    const result = originalOnMouseUp(evt);
    updateCursorState(canvas, evt);
    schedulePresenceUpdate(canvas, { immediate: true });
    applyPendingRemoteCommit(canvas);
    return result;
  };

  canvas.redraw = function patchedRedraw(withList = true) {
    originalRedraw(withList);
    drawParticipantOverlays(canvas);
  };

  canvas.applyVisibleFrame = function patchedApplyVisibleFrame(frameIndex, options = {}) {
    const result = originalApplyVisibleFrame(frameIndex, options);
    const source = options?.source || "internal";
    if (usesAsyncVideoFramePresentation(canvas, source)) {
      schedulePresenceUpdate(canvas, { immediate: true });
    }
    return result;
  };

  canvas.setCurrentFrame = function patchedSetCurrentFrame(frameIndex, options = {}) {
    const result = originalSetCurrentFrame(frameIndex, options);
    const source = options?.source || "scrub";
    if (!usesAsyncVideoFramePresentation(canvas, source)) {
      schedulePresenceUpdate(canvas);
    }
    return result;
  };

  canvas.markActiveAnnotation = function patchedMarkActiveAnnotation(annotation, options = {}) {
    const result = originalMarkActiveAnnotation(annotation, options);
    schedulePresenceUpdate(canvas);
    return result;
  };

  canvas.togglePlayback = function patchedTogglePlayback() {
    const result = originalTogglePlayback();
    schedulePresenceUpdate(canvas, { immediate: true });
    return result;
  };

  if (originalApplyLabelSelection) {
    canvas.applyLabelSelection = function patchedApplyLabelSelection(labelClassId) {
      const result = originalApplyLabelSelection(labelClassId);
      schedulePresenceUpdate(canvas, { immediate: true });
      return result;
    };
  }

  if (originalApplyServerAnnotations) {
    canvas.applyServerAnnotations = function patchedApplyServerAnnotations(
      annotations,
      revision = canvas.annotationRevision
    ) {
      const result = originalApplyServerAnnotations(annotations, revision);
      renderParticipantList(canvas);
      schedulePresenceUpdate(canvas, { immediate: true });
      return result;
    };
  }
}
