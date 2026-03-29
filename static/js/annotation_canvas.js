// static/js/annotation_canvas.js
// Thin entrypoint: wire DOM to core AnnotationCanvas logic.

import { AnnotationCanvas, lfDebug } from "./annotation_core.js?v=20260328_object_label_toggle";
import { enhanceAnnotationCanvasWithComments } from "./annotation_comments.js?v=20260328_object_label_toggle";
import { enhanceAnnotationCanvasWithSam2 } from "./annotation_sam2.js?v=20260326_annotation_audit";
import { enhanceAnnotationCanvasWithCollaboration } from "./annotation_collaboration.js?v=20260326_annotation_audit";

function parseJsonScript(scriptId) {
  const el = document.getElementById(scriptId);
  if (!el) return [];
  try {
    return JSON.parse(el.textContent.trim() || "[]");
  } catch (e) {
    console.error("Failed to parse JSON script", scriptId, e);
    return [];
  }
}

export function bootAnnotationCanvas() {
  const image = document.getElementById("item-image");
  const video = document.getElementById("item-video");
  const mediaEl = image || video;
  const canvas = document.getElementById("annotation-canvas");
  const cfg = window.LABEL_FORGE_CONFIG || {};
  if (!mediaEl || !canvas || !cfg.itemId) return;
  if (canvas.dataset.annotationBooted === "true") return;

  canvas.dataset.annotationBooted = "true";

  const annotations = parseJsonScript("initial-annotations");
  const regionComments = parseJsonScript("initial-region-comments");
  const labelClasses = parseJsonScript("label-classes-data");

  lfDebug("boot", {
    itemId: cfg.itemId,
    kind: cfg.kind,
    fps: cfg.fps,
    durationSec: cfg.durationSec,
    readyState: mediaEl.readyState,
    videoWidth: mediaEl.videoWidth,
    videoHeight: mediaEl.videoHeight,
  });

  const ac = new AnnotationCanvas(canvas, mediaEl, {
    itemId: cfg.itemId,
    apiBase: cfg.apiBase || "/api",
    annotations,
    labelClasses,
    regionComments,
    kind: cfg.kind,
    fps: cfg.fps,
    durationSec: cfg.durationSec,
    annotationRevision: cfg.annotationRevision ?? 0,
    prevItemId: cfg.prevItemId ?? null,
    prevItemUrl: cfg.prevItemUrl || null,
    nextItemUrl: cfg.nextItemUrl || null,
    readOnly: !!cfg.readOnly,
  });
  enhanceAnnotationCanvasWithComments(ac, {
    initialComments: regionComments,
    currentUser: cfg.currentUser || null,
  });
  enhanceAnnotationCanvasWithSam2(ac, {
    sam2Enabled: !!cfg.sam2Enabled,
    sam2Configured: !!cfg.sam2Configured,
    sam2JobPollIntervalMs: cfg.sam2JobPollIntervalMs,
  });
  enhanceAnnotationCanvasWithCollaboration(ac, {
    enabled: !!cfg.collaborationEnabled,
    wsPath: cfg.collaborationWsPath || null,
    currentUser: cfg.currentUser || null,
  });
  ac.init(annotations);
}

if (typeof window !== "undefined") {
  const initAnnotationCanvas = () => {
    try {
      bootAnnotationCanvas();
    } catch (error) {
      console.error("Failed to initialize annotation canvas", error);
    }
  };

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", initAnnotationCanvas, {
      once: true,
    });
  } else {
    initAnnotationCanvas();
  }

  window.addEventListener("htmx:load", initAnnotationCanvas);
}
