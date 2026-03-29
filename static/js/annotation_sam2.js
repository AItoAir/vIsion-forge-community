function clonePolygonPoints(points) {
  if (!Array.isArray(points)) return null;
  const out = points
    .filter((point) => Array.isArray(point) && point.length === 2)
    .map((point) => [Number(point[0]), Number(point[1])])
    .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  return out.length ? out : null;
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

function getCurrentLabelClass(canvas) {
  return canvas.labelClasses.find(
    (labelClass) => labelClass.id === canvas.currentLabelClassId
  ) || null;
}

function getLabelGeometryKind(canvas, labelClassId = null) {
  const labelClass =
    labelClassId == null
      ? getCurrentLabelClass(canvas)
      : canvas.labelClasses.find((candidate) => candidate.id === labelClassId) || null;
  return labelClass && labelClass.geometry_kind ? labelClass.geometry_kind : null;
}

function isSam2SupportedGeometry(geometryKind) {
  return geometryKind === "bbox" || geometryKind === "polygon";
}

function isSam2OutputLabelSelected(canvas) {
  return isSam2SupportedGeometry(getLabelGeometryKind(canvas));
}

function getSam2FrameIndex(canvas) {
  return canvas.kind === "video" ? canvas.currentFrameIndex : null;
}

function getSam2State(canvas) {
  return canvas.sam2 || null;
}

function clonePromptBox(box) {
  if (!Array.isArray(box) || box.length !== 4) return null;
  const coords = box.map((value) => Number(value));
  return coords.every((value) => Number.isFinite(value)) ? coords : null;
}

function clonePromptPoints(points) {
  if (!Array.isArray(points)) return [];
  return points
    .map((point) => ({
      x: Number(point.x),
      y: Number(point.y),
      label: point.label === 0 ? 0 : 1,
    }))
    .filter(
      (point) =>
        Number.isFinite(point.x) &&
        Number.isFinite(point.y) &&
        (point.label === 0 || point.label === 1)
    );
}

function parseOptionalFrameValue(value) {
  if (value == null) return null;
  const text = String(value).trim();
  if (!text) return null;
  const numericValue = Number(text);
  if (!Number.isFinite(numericValue)) return null;
  return Math.max(0, Math.trunc(numericValue));
}

function rememberLastPromptBox(state) {
  const candidate = normalizePromptBox(state.boxDraft || state.promptBox);
  if (candidate) {
    state.lastPromptBox = [...candidate];
  }
}

function getTrackFrameRange(canvas) {
  const state = getSam2State(canvas);
  if (!state || canvas.kind !== "video") {
    return { startFrame: null, endFrame: null };
  }
  return {
    startFrame: Number.isInteger(state.trackStartFrame) ? state.trackStartFrame : null,
    endFrame: Number.isInteger(state.trackEndFrame) ? state.trackEndFrame : null,
  };
}

function summarizeTrackFrameRange(canvas) {
  if (canvas.kind !== "video") {
    return null;
  }

  const { startFrame, endFrame } = getTrackFrameRange(canvas);
  if (!Number.isInteger(startFrame) && !Number.isInteger(endFrame)) {
    return null;
  }
  if (Number.isInteger(startFrame) && Number.isInteger(endFrame)) {
    return `frames ${startFrame}-${endFrame}`;
  }
  if (Number.isInteger(startFrame)) {
    return `from ${startFrame}`;
  }
  if (Number.isInteger(endFrame)) {
    return `to ${endFrame}`;
  }
  return null;
}

function clearPreview(canvas) {
  const state = getSam2State(canvas);
  if (!state) return;
  state.previewAnnotation = null;
}

function clearPrompts(canvas, { keepMode = true, silent = false } = {}) {
  const state = getSam2State(canvas);
  if (!state) return;

  rememberLastPromptBox(state);
  state.promptPoints = [];
  state.promptBox = null;
  state.promptFrameIndex = getSam2FrameIndex(canvas);
  state.previewAnnotation = null;
  state.boxDraft = null;
  state.isDrawingBox = false;

  if (!keepMode) {
    state.promptMode = "none";
  }

  if (!silent) {
    updateUi(canvas);
    canvas.redraw(false);
  }
}

function setBusy(canvas, busy) {
  const state = getSam2State(canvas);
  if (!state) return;
  state.busy = !!busy;

  if (canvas.loadingOverlayEl) {
    canvas.loadingOverlayEl.style.display = busy ? "flex" : "none";
  }

  updateUi(canvas);
}

function setPromptMode(canvas, mode) {
  const state = getSam2State(canvas);
  if (!state || canvas.readOnly || !state.enabled || state.busy) {
    return;
  }

  state.promptMode = mode;
  state.promptFrameIndex = getSam2FrameIndex(canvas);
  state.previewAnnotation = null;
  state.boxDraft = null;
  state.isDrawingBox = false;
  updateUi(canvas);
  canvas.redraw(false);
}

function addPromptPoint(canvas, pointLabel, imgPt) {
  const state = getSam2State(canvas);
  if (!state) return;

  const frameIndex = getSam2FrameIndex(canvas);
  if (state.promptFrameIndex !== frameIndex) {
    if (state.keepPromptsAcrossFrames) {
      state.promptFrameIndex = frameIndex;
      state.boxDraft = null;
      clearPreview(canvas);
    } else {
      clearPrompts(canvas, { keepMode: true, silent: true });
      state.promptFrameIndex = frameIndex;
    }
  }

  state.promptPoints.push({
    x: Number(imgPt.x),
    y: Number(imgPt.y),
    label: pointLabel,
  });
  state.previewAnnotation = null;
  updateUi(canvas);
  canvas.redraw(false);
}

function summarizePromptState(canvas) {
  const state = getSam2State(canvas);
  if (!state) return "Object masking is unavailable.";
  if (state.busy) return "Generating mask…";

  const positiveCount = state.promptPoints.filter((point) => point.label === 1).length;
  const negativeCount = state.promptPoints.filter((point) => point.label === 0).length;
  const hasExplicitBox = Array.isArray(state.promptBox) && state.promptBox.length === 4;
  const hasLastBox = Array.isArray(state.lastPromptBox) && state.lastPromptBox.length === 4;

  if (state.previewAnnotation) {
    return "Preview mask is ready for the current frame.";
  }

  if (!positiveCount && !negativeCount && !hasExplicitBox) {
    return hasLastBox
      ? "Left click to include, Shift+left click to exclude, draw a box, or reuse the last box."
      : "Left click to include, Shift+left click to exclude, or draw a box.";
  }

  const parts = [];
  if (positiveCount) parts.push(`include: ${positiveCount}`);
  if (negativeCount) parts.push(`exclude: ${negativeCount}`);
  if (hasExplicitBox) {
    parts.push("box");
  }
  const trackRangeSummary = summarizeTrackFrameRange(canvas);
  if (trackRangeSummary) {
    parts.push(trackRangeSummary);
  }
  if (state.keepPromptsAcrossFrames) {
    parts.push("keep");
  }
  return `Prompt ready: ${parts.join(" · ")}.`;
}

function updateUi(canvas) {
  const state = getSam2State(canvas);
  if (!state) return;

  const geometryKind = getLabelGeometryKind(canvas);
  const outputReady = isSam2SupportedGeometry(geometryKind);
  const promptBox = state.promptBox;
  const hasPrompt = state.promptPoints.length > 0 || !!promptBox;
  const hasPreview = !!state.previewAnnotation;
  const disabledBase = canvas.readOnly || !state.enabled || state.busy;

  if (state.summaryEl) {
    state.summaryEl.textContent = summarizePromptState(canvas);
  }

  const modeButtons = {
    none: state.btnModeNone,
    point: state.btnModePoint,
    box: state.btnModeBox,
  };
  Object.entries(modeButtons).forEach(([mode, button]) => {
    if (!button) return;
    button.classList.toggle("active", state.promptMode === mode);
    button.disabled = disabledBase || !state.configured;
  });

  if (state.btnClear) {
    state.btnClear.disabled = disabledBase || (!hasPrompt && !hasPreview);
  }
  if (state.btnReuseBox) {
    state.btnReuseBox.disabled =
      disabledBase || !state.configured || !Array.isArray(state.lastPromptBox);
  }
  if (state.btnPreview) {
    state.btnPreview.disabled = disabledBase || !state.configured || !outputReady || !hasPrompt;
  }
  if (state.btnApply) {
    state.btnApply.disabled =
      disabledBase || !state.configured || !outputReady || (!hasPrompt && !hasPreview);
  }
  if (state.btnTrack) {
    state.btnTrack.disabled =
      disabledBase ||
      !state.configured ||
      !outputReady ||
      canvas.kind !== "video" ||
      !hasPrompt;
  }

  if (state.keepPromptsEl) {
    state.keepPromptsEl.disabled = disabledBase || !state.configured;
    state.keepPromptsEl.checked = !!state.keepPromptsAcrossFrames;
  }
  if (state.trackStartEl) {
    state.trackStartEl.disabled =
      disabledBase || !state.configured || canvas.kind !== "video";
    state.trackStartEl.value = Number.isInteger(state.trackStartFrame)
      ? String(state.trackStartFrame)
      : "";
  }
  if (state.trackEndEl) {
    state.trackEndEl.disabled =
      disabledBase || !state.configured || canvas.kind !== "video";
    state.trackEndEl.value = Number.isInteger(state.trackEndFrame)
      ? String(state.trackEndFrame)
      : "";
  }

  if (state.labelHintEl) {
    if (!state.configured) {
      state.labelHintEl.textContent =
        "Object masking is not fully configured on the server yet.";
    } else if (!outputReady) {
      state.labelHintEl.textContent =
        "Select a bbox or polygon label class before previewing or applying an object mask.";
    } else if (geometryKind === "bbox") {
      state.labelHintEl.textContent =
        "Left click = include point, Shift+left click = exclude point. SAM2 previews the mask, then applies its bounds as a bbox for the selected class.";
    } else {
      state.labelHintEl.textContent =
        "Left click = include point, Shift+left click = exclude point. Enter = preview/apply, Ctrl/Cmd+Enter = queue a video batch, B = reuse last box, X = clear prompts.";
    }
  }
}

function drawPromptPoint(canvas, point) {
  const ctx = canvas.ctx;
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);
  const radius = 6 / scale;
  const borderWidth = 2 / scale;
  const color = point.label === 1 ? "#20c997" : "#dc3545";

  ctx.beginPath();
  ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff";
  ctx.fill();
  ctx.lineWidth = borderWidth;
  ctx.strokeStyle = color;
  ctx.stroke();
}

function drawPromptBox(canvas, box, color = "#f8c146") {
  const [rawX1, rawY1, rawX2, rawY2] = box;
  const x = Math.min(rawX1, rawX2);
  const y = Math.min(rawY1, rawY2);
  const w = Math.abs(rawX2 - rawX1);
  const h = Math.abs(rawY2 - rawY1);
  const ctx = canvas.ctx;
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);

  ctx.save();
  ctx.setLineDash([8 / scale, 6 / scale]);
  ctx.lineWidth = 2 / scale;
  ctx.strokeStyle = color;
  ctx.strokeRect(x, y, w, h);
  ctx.restore();
}

function drawPreviewBox(canvas, box, color = "#20c997") {
  const [rawX1, rawY1, rawX2, rawY2] = box;
  const x = Math.min(rawX1, rawX2);
  const y = Math.min(rawY1, rawY2);
  const w = Math.abs(rawX2 - rawX1);
  const h = Math.abs(rawY2 - rawY1);
  const ctx = canvas.ctx;
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);

  ctx.save();
  ctx.fillStyle = "rgba(32, 201, 151, 0.12)";
  ctx.fillRect(x, y, w, h);
  ctx.lineWidth = 2 / scale;
  ctx.strokeStyle = color;
  ctx.strokeRect(x, y, w, h);
  ctx.restore();
}

function drawPreviewAnnotation(canvas, annotation) {
  if (!annotation) return;
  if (
    canvas.kind === "video" &&
    annotation.frame_index != null &&
    annotation.frame_index !== canvas.currentFrameIndex
  ) {
    return;
  }

  const polygonPoints = clonePolygonPoints(annotation.polygon_points);
  const ctx = canvas.ctx;
  const scale = (canvas.baseScale || 1) * (canvas.zoom || 1);

  if (polygonPoints && polygonPoints.length >= 3) {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(polygonPoints[0][0], polygonPoints[0][1]);
    for (let pointIndex = 1; pointIndex < polygonPoints.length; pointIndex += 1) {
      const point = polygonPoints[pointIndex];
      ctx.lineTo(point[0], point[1]);
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(32, 201, 151, 0.18)";
    ctx.fill();
    ctx.lineWidth = 2 / scale;
    ctx.strokeStyle = "#20c997";
    ctx.stroke();
    ctx.restore();
    return;
  }

  drawPreviewBox(canvas, [annotation.x1, annotation.y1, annotation.x2, annotation.y2]);
}

function drawSam2Overlay(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled) return;

  const imgW = canvas.imageWidth || canvas.mediaEl.naturalWidth || canvas.mediaEl.videoWidth || 0;
  const imgH = canvas.imageHeight || canvas.mediaEl.naturalHeight || canvas.mediaEl.videoHeight || 0;
  if (!imgW || !imgH) return;

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

  drawPreviewAnnotation(canvas, state.previewAnnotation);
  const promptBox = state.boxDraft || state.promptBox;
  if (promptBox) {
    drawPromptBox(canvas, promptBox);
  }
  state.promptPoints.forEach((point) => drawPromptPoint(canvas, point));

  ctx.restore();
}

function normalizePromptBox(box) {
  if (!Array.isArray(box) || box.length !== 4) {
    return null;
  }

  const coords = box.map((value) => Number(value));
  if (!coords.every((value) => Number.isFinite(value))) {
    return null;
  }

  const [x1, y1, x2, y2] = coords;
  if (x1 === x2 || y1 === y2) {
    return null;
  }
  return [x1, y1, x2, y2];
}

function reuseLastPromptBox(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || canvas.readOnly || state.busy) {
    return;
  }

  const lastPromptBox = clonePromptBox(state.lastPromptBox);
  if (!lastPromptBox) {
    return;
  }

  const frameIndex = getSam2FrameIndex(canvas);
  if (state.promptFrameIndex !== frameIndex) {
    if (state.keepPromptsAcrossFrames) {
      state.promptPoints = clonePromptPoints(state.promptPoints);
      state.promptFrameIndex = frameIndex;
      state.boxDraft = null;
      clearPreview(canvas);
    } else {
      clearPrompts(canvas, { keepMode: true, silent: true });
      state.promptFrameIndex = frameIndex;
    }
  }

  state.promptMode = "box";
  state.promptBox = [...lastPromptBox];
  state.previewAnnotation = null;
  updateUi(canvas);
  canvas.redraw(false);
}

function buildPromptPayload(
  canvas,
  { includeTrackId = false, includeTrackRange = false } = {}
) {
  const state = getSam2State(canvas);
  if (!state) return null;
  const promptBox = normalizePromptBox(state.promptBox);
  const promptPoints = state.promptPoints.map((point) => ({
    x: Number(point.x),
    y: Number(point.y),
    label: point.label === 0 ? 0 : 1,
  }));

  if (!promptBox && !promptPoints.length) {
    return null;
  }

  const payload = {
    label_class_id: canvas.currentLabelClassId,
    frame_index: getSam2FrameIndex(canvas),
    box_xyxy: promptBox,
    prompt_points: promptPoints,
    include_reverse: true,
  };

  if (canvas.kind === "video" && includeTrackRange) {
    let trackStartFrame = Number.isInteger(state.trackStartFrame)
      ? state.trackStartFrame
      : null;
    const trackEndFrame = Number.isInteger(state.trackEndFrame)
      ? state.trackEndFrame
      : null;
    if (trackStartFrame == null && trackEndFrame != null) {
      trackStartFrame = getSam2FrameIndex(canvas);
    }
    payload.track_start_frame = trackStartFrame;
    payload.track_end_frame = trackEndFrame;
  }

  if (includeTrackId) {
    const activeTrackId =
      canvas.kind === "video" && canvas.activeAnnotation && Number.isInteger(canvas.activeAnnotation.track_id)
        ? canvas.activeAnnotation.track_id
        : null;
    payload.track_id = activeTrackId;
  }

  return payload;
}

async function parseErrorResponse(response) {
  const fallback = `HTTP ${response.status}`;
  try {
    const payload = await response.json();
    if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail.trim();
    }
  } catch (_error) {
    // Ignore JSON parsing failures.
  }

  try {
    const text = await response.text();
    if (text && text.trim()) {
      return text.trim();
    }
  } catch (_error) {
    // Ignore text parsing failures.
  }
  return fallback;
}

function trackStatusUrl(canvas) {
  return `${canvas.apiBase}/items/${canvas.itemId}/sam2/track-status`;
}

function annotationsUrl(canvas) {
  return `${canvas.apiBase}/items/${canvas.itemId}/annotations`;
}

function describeTrackJobRange(job) {
  if (!job) return null;
  const startFrame = Number.isInteger(job.track_start_frame) ? job.track_start_frame : null;
  const endFrame = Number.isInteger(job.track_end_frame) ? job.track_end_frame : null;
  if (startFrame != null && endFrame != null) {
    return `frames ${startFrame}-${endFrame}`;
  }
  if (startFrame != null) {
    return `from frame ${startFrame}`;
  }
  if (endFrame != null) {
    return `to frame ${endFrame}`;
  }
  if (Number.isInteger(job.frame_index)) {
    return `seed ${job.frame_index}`;
  }
  return null;
}

function setJobStatusText(canvas, text) {
  const state = getSam2State(canvas);
  if (!state?.jobStatusEl) return;
  state.jobStatusEl.textContent = text || "";
}

function summarizeTrackQueueStatus(canvas, statusData) {
  const runningCount = Number(statusData?.running_count || 0);
  const queuedCount = Number(statusData?.queued_count || 0);
  const itemJobs = Array.isArray(statusData?.item_jobs) ? statusData.item_jobs : [];
  const runningItemJob = itemJobs.find((job) => job?.status === "running") || null;
  const queuedItemJobs = itemJobs.filter((job) => job?.status === "queued");
  const latestFinishedJob = statusData?.latest_finished_job || null;

  if (runningItemJob) {
    const range = describeTrackJobRange(runningItemJob);
    const extraQueued = queuedItemJobs.length;
    return [
      "SAM2 batch is running for this item",
      range ? `(${range})` : "",
      extraQueued > 0 ? `with ${extraQueued} more waiting.` : ".",
    ]
      .filter(Boolean)
      .join(" ")
      .replace(" .", ".");
  }

  if (queuedItemJobs.length) {
    const nextJob = queuedItemJobs[0];
    const queuePosition = Number.isInteger(nextJob?.queue_position)
      ? `queue position ${nextJob.queue_position}`
      : "queued";
    const range = describeTrackJobRange(nextJob);
    const extraQueued = queuedItemJobs.length - 1;
    return [
      `SAM2 batch is queued for this item (${queuePosition})`,
      range ? `${range}.` : "",
      extraQueued > 0 ? `${extraQueued} more queued for this item.` : "",
    ]
      .filter(Boolean)
      .join(" ")
      .trim();
  }

  if (latestFinishedJob?.status === "failed") {
    return latestFinishedJob.error_message
      ? `Last SAM2 batch failed: ${latestFinishedJob.error_message}`
      : "Last SAM2 batch failed.";
  }

  if (latestFinishedJob?.status === "completed") {
    const resultCount = Number(latestFinishedJob.result_annotation_count || 0);
    if (resultCount > 0) {
      return `Last SAM2 batch applied ${resultCount} tracked masks.`;
    }
    return "Last SAM2 batch completed.";
  }

  if (runningCount > 0 || queuedCount > 0) {
    const parts = [];
    if (runningCount > 0) {
      parts.push(`${runningCount} running`);
    }
    if (queuedCount > 0) {
      parts.push(`${queuedCount} waiting`);
    }
    return `SAM2 queue busy elsewhere: ${parts.join(", ")}.`;
  }

  return "Apply across video runs as a background SAM2 batch.";
}

async function syncAnnotationsFromServer(canvas, statusData) {
  const state = getSam2State(canvas);
  if (!state || !statusData) {
    return;
  }

  const targetRevision = Number(statusData.item_annotation_revision || 0);
  if (!Number.isInteger(targetRevision) || targetRevision <= Number(canvas.annotationRevision || 0)) {
    return;
  }

  if (
    canvas.isSaving ||
    canvas.isDirty ||
    (typeof canvas.isInteractionActive === "function" && canvas.isInteractionActive())
  ) {
    return;
  }

  const response = await fetch(annotationsUrl(canvas));
  if (!response.ok) {
    throw new Error(await parseErrorResponse(response));
  }

  const annotations = await response.json();
  canvas.applyServerAnnotations(annotations, targetRevision);
  if (statusData.item_status && typeof canvas.updateStatusBadge === "function") {
    canvas.updateStatusBadge(statusData.item_status);
  }
}

function scheduleTrackStatusPoll(canvas, delayMs = null) {
  const state = getSam2State(canvas);
  if (!state || canvas.kind !== "video" || !state.enabled) {
    return;
  }

  if (state.trackStatusPollTimer) {
    window.clearTimeout(state.trackStatusPollTimer);
    state.trackStatusPollTimer = null;
  }

  const nextDelay =
    delayMs == null
      ? Math.max(1000, Number(state.trackStatusPollIntervalMs || 5000))
      : Math.max(250, Number(delayMs) || 0);

  state.trackStatusPollTimer = window.setTimeout(() => {
    state.trackStatusPollTimer = null;
    void refreshTrackStatus(canvas);
  }, nextDelay);
}

function stopTrackStatusPoll(canvas) {
  const state = getSam2State(canvas);
  if (!state) return;
  if (state.trackStatusPollTimer) {
    window.clearTimeout(state.trackStatusPollTimer);
    state.trackStatusPollTimer = null;
  }
}

async function refreshTrackStatus(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || canvas.kind !== "video" || state.trackStatusBusy) {
    return;
  }

  state.trackStatusBusy = true;
  try {
    const response = await fetch(trackStatusUrl(canvas), {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await parseErrorResponse(response));
    }

    const data = await response.json();
    state.lastTrackStatus = data;
    setJobStatusText(canvas, summarizeTrackQueueStatus(canvas, data));
    await syncAnnotationsFromServer(canvas, data);
  } catch (error) {
    console.error("Failed to refresh SAM2 track status", error);
  } finally {
    state.trackStatusBusy = false;
    scheduleTrackStatusPoll(canvas);
  }
}

async function requestCurrentFrameSuggestion(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled) {
    throw new Error("Object masking is unavailable.");
  }
  if (!state.configured) {
    throw new Error("Object masking is not configured on the server.");
  }
  if (!isSam2OutputLabelSelected(canvas)) {
    throw new Error("Select a bbox or polygon label class before using object masking.");
  }

  const payload = buildPromptPayload(canvas, { includeTrackId: false });
  if (!payload) {
    throw new Error("Add at least one include or exclude point, or provide a box prompt.");
  }

  const url = `${canvas.apiBase}/items/${canvas.itemId}/sam2/current-frame`;
  setBusy(canvas, true);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await parseErrorResponse(response));
    }
    const data = await response.json();
    const rawSuggestion = Array.isArray(data.annotations) ? data.annotations[0] || null : null;
    const suggestion = rawSuggestion
      ? normalizeSuggestionGeometry(canvas, rawSuggestion)
      : null;
    state.previewAnnotation = suggestion;
    updateUi(canvas);
    canvas.redraw(false);
    return suggestion;
  } finally {
    setBusy(canvas, false);
  }
}

function normalizeSuggestionGeometry(canvas, suggestion) {
  const geometryKind = getLabelGeometryKind(canvas, suggestion?.label_class_id);
  const polygonPoints = clonePolygonPoints(suggestion?.polygon_points);
  const normalized = {
    ...suggestion,
    frame_index:
      canvas.kind === "video"
        ? suggestion?.frame_index ?? canvas.currentFrameIndex
        : null,
    x1: Number(suggestion.x1),
    y1: Number(suggestion.y1),
    x2: Number(suggestion.x2),
    y2: Number(suggestion.y2),
    polygon_points: geometryKind === "polygon" ? polygonPoints : null,
  };
  if (geometryKind === "polygon") {
    syncPolygonBounds(normalized);
  }
  return normalized;
}

function buildAnnotationFromSuggestion(
  canvas,
  suggestion,
  trackId = null,
  { assignClientUid = true } = {}
) {
  const normalizedSuggestion = normalizeSuggestionGeometry(canvas, suggestion);
  const annotation = {
    client_uid: null,
    id: null,
    label_class_id: normalizedSuggestion.label_class_id,
    frame_index: normalizedSuggestion.frame_index,
    x1: normalizedSuggestion.x1,
    y1: normalizedSuggestion.y1,
    x2: normalizedSuggestion.x2,
    y2: normalizedSuggestion.y2,
    polygon_points: clonePolygonPoints(normalizedSuggestion.polygon_points),
    status: "pending",
    is_occluded: false,
    is_truncated: false,
    is_outside: false,
    is_lost: false,
    track_id: trackId,
    propagation_frames: 0,
  };
  if (assignClientUid && typeof canvas.ensureClientUid === "function") {
    canvas.ensureClientUid(annotation);
  }
  return annotation;
}

function replaceCurrentFrameAnnotation(canvas, suggestion) {
  const active = canvas.activeAnnotation;

  if (canvas.kind !== "video") {
    if (active) {
      active.label_class_id = suggestion.label_class_id;
      active.polygon_points = clonePolygonPoints(suggestion.polygon_points);
      active.x1 = Number(suggestion.x1);
      active.y1 = Number(suggestion.y1);
      active.x2 = Number(suggestion.x2);
      active.y2 = Number(suggestion.y2);
      syncPolygonBounds(active);
      canvas.markActiveAnnotation(active);
      return;
    }

    const annotation = buildAnnotationFromSuggestion(canvas, suggestion, null);
    canvas.annotations.push(annotation);
    canvas.frameAnnotations.set(null, canvas.annotations);
    canvas.markActiveAnnotation(annotation);
    return;
  }

  let trackId = null;
  if (active && Number.isInteger(active.track_id)) {
    trackId = active.track_id;
  }
  if (!Number.isInteger(trackId)) {
    trackId = canvas.nextTrackId++;
  }

  if (active && active.track_id == null && active._storedAnnotation) {
    canvas.removeSparseAnnotation(active._storedAnnotation);
  }

  const annotation = buildAnnotationFromSuggestion(canvas, suggestion, trackId);
  annotation.frame_index = canvas.currentFrameIndex;
  const propagatedRunLength = canvas.getPropagationRunLengthForTrackEdit(
    annotation,
    0
  );
  canvas.commitVideoAnnotation(annotation, propagatedRunLength);
  canvas.addManualKeyframe(trackId, canvas.currentFrameIndex);
  canvas.restoreActiveAnnotationForTrack(trackId);
}

async function applyCurrentFrameSuggestion(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled) return;

  let suggestion = state.previewAnnotation;
  if (!suggestion) {
    suggestion = await requestCurrentFrameSuggestion(canvas);
  }
  if (!suggestion) {
    throw new Error("The mask assistant did not return a mask for the current frame.");
  }

  replaceCurrentFrameAnnotation(canvas, suggestion);
  clearPrompts(canvas, { keepMode: true, silent: true });
  clearPreview(canvas);

  if (canvas.kind === "video" && canvas.timelineInitialized && canvas.totalFrames) {
    canvas.updateTimelineAnnotations();
    canvas.updateTimelinePlayhead();
  }

  updateUi(canvas);
  canvas.redraw();
  if (typeof canvas.pushHistoryCheckpoint === "function") {
    canvas.pushHistoryCheckpoint();
  }
  canvas.scheduleSave();
}

function replaceTrackFromSuggestions(canvas, trackId, suggestions) {
  const ordered = [...suggestions].sort(
    (left, right) => (left.frame_index ?? 0) - (right.frame_index ?? 0)
  );

  canvas.clearTrackSparseAnnotations(trackId);
  ordered.forEach((suggestion) => {
    const annotation = buildAnnotationFromSuggestion(canvas, suggestion, trackId);
    annotation.frame_index = suggestion.frame_index ?? canvas.currentFrameIndex;
    annotation.propagation_frames = 0;
    canvas.addSparseAnnotation(annotation);
  });

  const frames = ordered
    .map((annotation) => annotation.frame_index)
    .filter((value) => Number.isInteger(value));
  if (frames.length) {
    const uniqueFrames = [canvas.currentFrameIndex, Math.min(...frames), Math.max(...frames)];
    canvas.setTrackKeyframes(trackId, uniqueFrames);
  }

  canvas.annotations = canvas.buildAnnotationsForFrame(canvas.currentFrameIndex);
  canvas.restoreActiveAnnotationForTrack(trackId);
}

async function trackAcrossVideo(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || canvas.kind !== "video") {
    return;
  }
  if (!state.configured) {
    throw new Error("Object masking is not configured on the server.");
  }
  if (!isSam2OutputLabelSelected(canvas)) {
    throw new Error("Select a bbox or polygon label class before using object masking.");
  }

  const payload = buildPromptPayload(canvas, {
    includeTrackId: true,
    includeTrackRange: true,
  });
  if (!payload) {
    throw new Error("Add at least one include or exclude point, or provide a box prompt.");
  }

  const activeTrackId = Number.isInteger(payload.track_id) ? payload.track_id : null;
  const trackId = activeTrackId ?? canvas.nextTrackId++;
  payload.track_id = trackId;

  const url = `${canvas.apiBase}/items/${canvas.itemId}/sam2/track`;
  setBusy(canvas, true);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(await parseErrorResponse(response));
    }

    const data = await response.json();
    clearPrompts(canvas, { keepMode: true, silent: true });
    clearPreview(canvas);
    state.lastTrackStatus = null;
    setJobStatusText(canvas, summarizeTrackQueueStatus(canvas, {
      running_count: data.running_count || 0,
      queued_count: data.queued_count || 0,
      item_jobs: data.job ? [data.job] : [],
      latest_finished_job: null,
    }));
    updateUi(canvas);
    canvas.redraw();
    scheduleTrackStatusPoll(canvas, 250);
  } finally {
    setBusy(canvas, false);
  }
}

async function handleSam2KeyboardShortcut(canvas, evt) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || canvas.readOnly || state.busy) {
    return;
  }

  const activeTag = document.activeElement?.tagName;
  if (activeTag === "INPUT" || activeTag === "TEXTAREA" || activeTag === "SELECT") {
    return;
  }

  if (canvas.isPolygonDrawing) {
    return;
  }

  const hasPrompt = state.promptPoints.length > 0 || !!state.promptBox;
  const hasPreview = !!state.previewAnnotation;

  if (evt.key === "Enter") {
    if (!state.configured || !isSam2OutputLabelSelected(canvas) || (!hasPrompt && !hasPreview)) {
      return;
    }

    evt.preventDefault();
    try {
      if ((evt.ctrlKey || evt.metaKey) && canvas.kind === "video" && hasPrompt) {
        await trackAcrossVideo(canvas);
        return;
      }

      if (hasPreview) {
        await applyCurrentFrameSuggestion(canvas);
      } else {
        await requestCurrentFrameSuggestion(canvas);
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Mask action failed.");
    }
    return;
  }

  if (!evt.ctrlKey && !evt.metaKey && !evt.altKey && (evt.key === "b" || evt.key === "B")) {
    if (!state.configured) return;
    evt.preventDefault();
    reuseLastPromptBox(canvas);
    return;
  }

  if (!evt.ctrlKey && !evt.metaKey && !evt.altKey && (evt.key === "x" || evt.key === "X")) {
    if (!hasPrompt && !hasPreview) return;
    evt.preventDefault();
    clearPrompts(canvas, { keepMode: true });
    return;
  }

  if (evt.key === "Escape") {
    if (!hasPrompt && !hasPreview) return;
    evt.preventDefault();
    clearPrompts(canvas, { keepMode: true });
  }
}

function handleMouseDown(canvas, evt) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || canvas.readOnly || state.busy) {
    return false;
  }
  if (state.promptMode === "none") {
    return false;
  }
  if (evt.button === 1 || (evt.button === 0 && evt.ctrlKey)) {
    return false;
  }

  const isPointPromptClick = state.promptMode === "point" && evt.button === 0;
  const isBoxPromptClick = state.promptMode === "box" && evt.button === 0;
  if (!isPointPromptClick && !isBoxPromptClick) {
    return false;
  }

  if (canvas.mediaEl.tagName === "VIDEO" && !canvas.mediaEl.paused) {
    canvas.mediaEl.pause();
    if (typeof canvas.stopRenderLoop === "function") {
      canvas.stopRenderLoop();
    }
  }

  if (typeof canvas.blurActiveFormControl === "function") {
    canvas.blurActiveFormControl();
  }

  evt.preventDefault();

  if (isPointPromptClick) {
    const imgPt = canvas.toImageCoordinates(evt);
    addPromptPoint(canvas, evt.shiftKey ? 0 : 1, imgPt);
    return true;
  }

  if (isBoxPromptClick) {
    const imgPt = canvas.toImageCoordinates(evt);
    const frameIndex = getSam2FrameIndex(canvas);
    if (state.promptFrameIndex !== frameIndex) {
      if (state.keepPromptsAcrossFrames) {
        state.promptFrameIndex = frameIndex;
        state.boxDraft = null;
        clearPreview(canvas);
      } else {
        clearPrompts(canvas, { keepMode: true, silent: true });
        state.promptFrameIndex = frameIndex;
      }
    }

    state.isDrawingBox = true;
    state.boxStart = { x: Number(imgPt.x), y: Number(imgPt.y) };
    state.boxDraft = [imgPt.x, imgPt.y, imgPt.x, imgPt.y];
    state.previewAnnotation = null;
    updateUi(canvas);
    canvas.redraw(false);
    return true;
  }

  return false;
}

function handleMouseMove(canvas, evt) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled) {
    return false;
  }

  if (state.promptMode !== "none" && !state.isDrawingBox) {
    canvas.canvas.style.cursor = "crosshair";
  }

  if (!state.isDrawingBox) {
    return false;
  }

  evt.preventDefault();
  const imgPt = canvas.toImageCoordinates(evt);
  state.boxDraft = [state.boxStart.x, state.boxStart.y, imgPt.x, imgPt.y];
  canvas.canvas.style.cursor = "crosshair";
  canvas.redraw(false);
  return true;
}

function handleMouseUp(canvas, evt) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || !state.isDrawingBox) {
    return false;
  }

  evt.preventDefault();
  state.isDrawingBox = false;
  const nextBox = normalizePromptBox(state.boxDraft);
  state.promptBox = nextBox;
  state.lastPromptBox = nextBox ? [...nextBox] : state.lastPromptBox;
  state.boxDraft = null;
  state.previewAnnotation = null;
  canvas.canvas.style.cursor = "crosshair";
  updateUi(canvas);
  canvas.redraw(false);
  return true;
}

function handleFrameChange(canvas) {
  const state = getSam2State(canvas);
  if (!state || !state.enabled || canvas.kind !== "video") {
    return;
  }

  if (state.promptFrameIndex == null) {
    state.promptFrameIndex = canvas.currentFrameIndex;
    clearPreview(canvas);
    updateUi(canvas);
    return;
  }

  if (state.promptFrameIndex !== canvas.currentFrameIndex) {
    if (state.keepPromptsAcrossFrames) {
      state.promptFrameIndex = canvas.currentFrameIndex;
      state.boxDraft = null;
      clearPreview(canvas);
      updateUi(canvas);
      canvas.redraw(false);
      return;
    }

    clearPrompts(canvas, { keepMode: true, silent: true });
    state.promptFrameIndex = canvas.currentFrameIndex;
  }
  clearPreview(canvas);
  updateUi(canvas);
}

function bindPanelEvents(canvas) {
  const state = getSam2State(canvas);
  if (!state || state.bound) {
    return;
  }

  state.btnModeNone = document.getElementById("btn-sam2-mode-none");
  state.btnModePoint = document.getElementById("btn-sam2-mode-point");
  state.btnModeBox = document.getElementById("btn-sam2-mode-box");
  state.btnPreview = document.getElementById("btn-sam2-preview");
  state.btnApply = document.getElementById("btn-sam2-apply");
  state.btnTrack = document.getElementById("btn-sam2-track");
  state.btnClear = document.getElementById("btn-sam2-clear");
  state.btnReuseBox = document.getElementById("btn-sam2-reuse-box");
  state.keepPromptsEl = document.getElementById("sam2-keep-prompts");
  state.trackStartEl = document.getElementById("sam2-track-start");
  state.trackEndEl = document.getElementById("sam2-track-end");
  state.summaryEl = document.getElementById("sam2-prompt-summary");
  state.jobStatusEl = document.getElementById("sam2-job-status");
  state.labelHintEl = document.getElementById("sam2-label-hint");
  state.trackStartFrame = parseOptionalFrameValue(state.trackStartEl?.value);
  state.trackEndFrame = parseOptionalFrameValue(state.trackEndEl?.value);

  state.btnModeNone?.addEventListener("click", () => setPromptMode(canvas, "none"));
  state.btnModePoint?.addEventListener("click", () => setPromptMode(canvas, "point"));
  state.btnModeBox?.addEventListener("click", () => setPromptMode(canvas, "box"));
  state.btnClear?.addEventListener("click", () => clearPrompts(canvas, { keepMode: true }));
  state.btnPreview?.addEventListener("click", async () => {
    try {
      await requestCurrentFrameSuggestion(canvas);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Mask preview failed.");
    }
  });
  state.btnApply?.addEventListener("click", async () => {
    try {
      await applyCurrentFrameSuggestion(canvas);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Applying the mask failed.");
    }
  });
  state.btnTrack?.addEventListener("click", async () => {
    try {
      await trackAcrossVideo(canvas);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Queueing the SAM2 batch failed.");
    }
  });
  state.btnReuseBox?.addEventListener("click", () => reuseLastPromptBox(canvas));
  state.keepPromptsEl?.addEventListener("change", () => {
    state.keepPromptsAcrossFrames = !!state.keepPromptsEl.checked;
    updateUi(canvas);
  });
  state.trackStartEl?.addEventListener("change", () => {
    state.trackStartFrame = parseOptionalFrameValue(state.trackStartEl.value);
    clearPreview(canvas);
    updateUi(canvas);
    canvas.redraw(false);
  });
  state.trackEndEl?.addEventListener("change", () => {
    state.trackEndFrame = parseOptionalFrameValue(state.trackEndEl.value);
    clearPreview(canvas);
    updateUi(canvas);
    canvas.redraw(false);
  });
  document.addEventListener("keydown", (evt) => {
    handleSam2KeyboardShortcut(canvas, evt);
  });
  if (canvas.kind === "video") {
    const refreshSoon = () => scheduleTrackStatusPoll(canvas, 100);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        refreshSoon();
      }
    });
    window.addEventListener("focus", refreshSoon);
    window.addEventListener("beforeunload", () => stopTrackStatusPoll(canvas), {
      once: true,
    });
    refreshSoon();
  }

  state.bound = true;
  updateUi(canvas);
}

export function enhanceAnnotationCanvasWithSam2(canvas, config = {}) {
  canvas.sam2 = {
    enabled: !!config.sam2Enabled,
    configured: !!config.sam2Configured,
    promptMode: "none",
    promptFrameIndex: canvas.kind === "video" ? 0 : null,
    promptPoints: [],
    promptBox: null,
    previewAnnotation: null,
    isDrawingBox: false,
    boxStart: null,
    boxDraft: null,
    busy: false,
    bound: false,
    btnModeNone: null,
    btnModePoint: null,
    btnModeBox: null,
    btnPreview: null,
    btnApply: null,
    btnTrack: null,
    btnClear: null,
    btnReuseBox: null,
    keepPromptsEl: null,
    trackStartEl: null,
    trackEndEl: null,
    summaryEl: null,
    jobStatusEl: null,
    labelHintEl: null,
    lastPromptBox: null,
    keepPromptsAcrossFrames: false,
    trackStartFrame: null,
    trackEndFrame: null,
    trackStatusBusy: false,
    trackStatusPollTimer: null,
    trackStatusPollIntervalMs: Number(config.sam2JobPollIntervalMs || 5000),
    lastTrackStatus: null,
  };

  const originalAttachEvents = canvas.attachEvents.bind(canvas);
  const originalOnMouseDown = canvas.onMouseDown.bind(canvas);
  const originalOnMouseMove = canvas.onMouseMove.bind(canvas);
  const originalOnMouseUp = canvas.onMouseUp.bind(canvas);
  const originalRedraw = canvas.redraw.bind(canvas);
  const originalSetCurrentFrame = canvas.setCurrentFrame.bind(canvas);
  const originalUpdateHoverCursor = canvas.updateHoverCursor.bind(canvas);
  const originalApplyLabelSelection =
    typeof canvas.applyLabelSelection === "function"
      ? canvas.applyLabelSelection.bind(canvas)
      : null;

  canvas.attachEvents = function patchedAttachEvents() {
    originalAttachEvents();
    bindPanelEvents(canvas);
  };

  canvas.onMouseDown = function patchedOnMouseDown(evt) {
    if (handleMouseDown(canvas, evt)) {
      return;
    }
    return originalOnMouseDown(evt);
  };

  canvas.onMouseMove = function patchedOnMouseMove(evt) {
    if (handleMouseMove(canvas, evt)) {
      return;
    }
    return originalOnMouseMove(evt);
  };

  canvas.onMouseUp = function patchedOnMouseUp(evt) {
    if (handleMouseUp(canvas, evt)) {
      return;
    }
    return originalOnMouseUp(evt);
  };

  canvas.redraw = function patchedRedraw(withList = true) {
    originalRedraw(withList);
    drawSam2Overlay(canvas);
    updateUi(canvas);
  };

  canvas.setCurrentFrame = function patchedSetCurrentFrame(frameIndex, options = {}) {
    originalSetCurrentFrame(frameIndex, options);
    handleFrameChange(canvas);
  };

  if (originalApplyLabelSelection) {
    canvas.applyLabelSelection = function patchedApplyLabelSelection(labelClassId) {
      const previousLabelClassId = canvas.currentLabelClassId;
      originalApplyLabelSelection(labelClassId);
      if (previousLabelClassId !== canvas.currentLabelClassId) {
        clearPreview(canvas);
        updateUi(canvas);
        canvas.redraw(false);
      }
    };
  }

  canvas.updateHoverCursor = function patchedUpdateHoverCursor(clientX, clientY) {
    const state = getSam2State(canvas);
    if (state && state.enabled && state.promptMode !== "none") {
      canvas.canvas.style.cursor = "crosshair";
      return;
    }
    return originalUpdateHoverCursor(clientX, clientY);
  };
}
