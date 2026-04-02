// static/js/upload.js
// Multi-file drag & drop uploader for project items.

function bootMultiFileUpload() {
  const cfg = window.FRAME_PIN_UPLOAD_CONFIG || {};
  const uploadUrl = cfg.uploadUrl;
  if (!uploadUrl) {
    return;
  }

  const dropzone = document.getElementById("upload-dropzone");
  const fileInput = document.getElementById("file-input");
  const form = document.getElementById("upload-form");
  const statusList = document.getElementById("upload-status-list");

  if (!dropzone || !fileInput || !form || !statusList) {
    return;
  }

  if (dropzone.dataset.uploadBooted === "true") {
    return;
  }
  dropzone.dataset.uploadBooted = "true";

  let pendingCount = 0;
  let pendingVideoConversion = false;

  function createStatusItem(fileName, initialMessage, kind) {
    const row = document.createElement("div");
    row.className = "upload-status-item";

    const label = document.createElement("div");
    label.className = "status-label";
    label.innerHTML =
      '<span class="filename"></span><span class="message"></span>';

    const nameSpan = label.querySelector(".filename");
    const msgSpan = label.querySelector(".message");
    nameSpan.textContent = fileName;
    msgSpan.textContent = initialMessage || "";

    const badge = document.createElement("span");
    badge.className = "status-badge badge";

    if (kind === "pending") {
      badge.classList.add("text-bg-secondary");
      badge.textContent = "Uploading";
    } else if (kind === "processing") {
      badge.classList.add("text-bg-primary");
      badge.textContent = "Converting";
    } else if (kind === "success") {
      badge.classList.add("text-bg-success");
      badge.textContent = "Done";
    } else if (kind === "error") {
      badge.classList.add("text-bg-danger");
      badge.textContent = "Error";
    }

    row.appendChild(label);
    row.appendChild(badge);
    statusList.appendChild(row);

    return { row, badge, nameSpan, msgSpan };
  }

  async function uploadSingleFile(file) {
    const entry = createStatusItem(file.name, "", "pending");
    pendingCount += 1;

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(uploadUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Frame-Pin-Upload": "1",
        },
        body: formData,
      });

      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
          const text = await res.text();
          if (text && text.trim()) {
            msg = text.trim();
          }
        } catch {
          // ignore
        }
        entry.badge.className = "status-badge badge text-bg-danger";
        entry.badge.textContent = "Error";
        entry.msgSpan.textContent = ` - ${msg}`;
      } else {
        let payload = null;
        try {
          payload = await res.json();
        } catch {
          payload = null;
        }

        const mediaConversion = payload?.media_conversion || null;
        if (
          mediaConversion &&
          (mediaConversion.status === "pending" ||
            mediaConversion.status === "processing")
        ) {
          pendingVideoConversion = true;
          entry.badge.className = "status-badge badge text-bg-primary";
          entry.badge.textContent = "Converting";
          entry.msgSpan.textContent = mediaConversion.message
            ? ` - ${mediaConversion.message}`
            : "";
        } else {
          entry.badge.className = "status-badge badge text-bg-success";
          entry.badge.textContent = "Done";
          entry.msgSpan.textContent = "";
        }
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Upload failed unexpectedly";
      entry.badge.className = "status-badge badge text-bg-danger";
      entry.badge.textContent = "Error";
      entry.msgSpan.textContent = ` - ${message}`;
    } finally {
      pendingCount -= 1;
      if (pendingCount === 0) {
        window.setTimeout(() => {
          window.location.reload();
        }, pendingVideoConversion ? 1500 : 400);
      }
    }
  }

  function handleFiles(fileList) {
    if (!fileList || !fileList.length) return;

    const accepted = [];
    for (const file of fileList) {
      const type = file.type || "";
      if (type.startsWith("image/") || type.startsWith("video/")) {
        accepted.push(file);
      } else {
        createStatusItem(
          file.name,
          " - Unsupported file type (only image/video allowed).",
          "error",
        );
      }
    }

    if (!accepted.length) return;

    accepted.forEach((acceptedFile) => {
      uploadSingleFile(acceptedFile);
    });
  }

  dropzone.addEventListener("dragover", (evt) => {
    evt.preventDefault();
    dropzone.classList.add("dragover");
  });

  dropzone.addEventListener("dragleave", (evt) => {
    if (evt.target === dropzone) {
      dropzone.classList.remove("dragover");
    }
  });

  dropzone.addEventListener("drop", (evt) => {
    evt.preventDefault();
    dropzone.classList.remove("dragover");
    if (evt.dataTransfer && evt.dataTransfer.files) {
      handleFiles(evt.dataTransfer.files);
    }
  });

  dropzone.addEventListener("click", () => {
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files.length) {
      handleFiles(fileInput.files);
    }
    fileInput.value = "";
  });

  form.addEventListener("submit", (evt) => {
    evt.preventDefault();
    if (fileInput.files && fileInput.files.length) {
      handleFiles(fileInput.files);
      fileInput.value = "";
    }
  });
}

if (typeof window !== "undefined") {
  const initUpload = () => {
    try {
      bootMultiFileUpload();
    } catch (e) {
      console.error("Failed to initialize multi-file upload", e);
    }
  };

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", initUpload, { once: true });
  } else {
    initUpload();
  }

  window.addEventListener("htmx:load", initUpload);
}
