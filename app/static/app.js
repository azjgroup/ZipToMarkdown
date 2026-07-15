"use strict";

const MAX_UPLOAD_BYTES = 1024 ** 3;
const POLL_INTERVAL_MS = 750;
const panels = [
  "ready-panel",
  "upload-panel",
  "conversion-panel",
  "complete-panel",
  "failure-panel",
].map((id) => document.getElementById(id));

const archiveInput = document.getElementById("archive-input");
const dropZone = document.getElementById("drop-zone");
const chooseButton = document.getElementById("choose-button");
const uploadFilename = document.getElementById("upload-filename");
const uploadProgress = document.getElementById("upload-progress");
const uploadPercent = document.getElementById("upload-percent");
const conversionStage = document.getElementById("conversion-stage");
const conversionFilename = document.getElementById("conversion-filename");
const conversionProgress = document.getElementById("conversion-progress");
const conversionPercent = document.getElementById("conversion-percent");
const convertedCount = document.getElementById("converted-count");
const skippedCount = document.getElementById("skipped-count");
const failedCount = document.getElementById("failed-count");
const emptyCount = document.getElementById("empty-count");
const pollingNote = document.getElementById("polling-note");
const completeSummary = document.getElementById("complete-summary");
const issuesNotice = document.getElementById("issues-notice");
const downloadButton = document.getElementById("download-button");
const deleteButton = document.getElementById("delete-button");
const retryButton = document.getElementById("retry-button");
const failureMessage = document.getElementById("failure-message");
const errorMessage = document.getElementById("error-message");

let activeJobId = null;
let pollTimer = null;
let pollFailures = 0;

const stageLabels = {
  queued: "Queued",
  extracting: "Unpacking ZIP",
  converting: "Converting files",
  packaging: "Preparing download",
};

function showPanel(id) {
  panels.forEach((panel) => {
    panel.hidden = panel.id !== id;
  });
  errorMessage.hidden = true;
}

function showReadyError(message) {
  showPanel("ready-panel");
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}

function resetInterface() {
  window.clearTimeout(pollTimer);
  pollTimer = null;
  pollFailures = 0;
  activeJobId = null;
  archiveInput.value = "";
  uploadProgress.value = 0;
  uploadPercent.textContent = "0%";
  conversionProgress.value = 0;
  conversionPercent.textContent = "0%";
  pollingNote.hidden = true;
  showPanel("ready-panel");
}

function validateFile(file) {
  if (!file) {
    return "Choose one ZIP archive.";
  }
  if (!file.name.toLowerCase().endsWith(".zip")) {
    return "Choose a file whose name ends in .zip.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "That ZIP is larger than the 1 GB limit.";
  }
  return null;
}

function startUpload(file) {
  const validationError = validateFile(file);
  if (validationError) {
    showReadyError(validationError);
    return;
  }

  showPanel("upload-panel");
  uploadFilename.textContent = file.name;
  uploadProgress.value = 0;
  uploadPercent.textContent = "0%";

  const request = new XMLHttpRequest();
  const formData = new FormData();
  formData.append("archive", file, file.name);
  request.open("POST", "/api/jobs");
  request.setRequestHeader("Accept", "application/json");

  request.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) {
      uploadProgress.removeAttribute("value");
      uploadPercent.textContent = "Uploading";
      return;
    }
    const progress = Math.min(1, event.loaded / event.total);
    uploadProgress.value = progress;
    uploadPercent.textContent = `${Math.round(progress * 100)}%`;
  });

  request.addEventListener("load", () => {
    let payload = {};
    try {
      payload = JSON.parse(request.responseText || "{}");
    } catch (_) {
      payload = {};
    }
    if (request.status !== 202 || !payload.job_id) {
      showReadyError(payload.detail || "The ZIP could not be uploaded. Try again.");
      return;
    }
    activeJobId = payload.job_id;
    showPanel("conversion-panel");
    pollJob();
  });

  request.addEventListener("error", () => {
    showReadyError("The local app could not receive the ZIP. Try again.");
  });
  request.send(formData);
}

function renderProgress(job) {
  const progress = Math.max(0, Math.min(1, Number(job.progress) || 0));
  conversionStage.textContent = stageLabels[job.status] || "Working";
  conversionFilename.textContent = job.current_file || job.original_name || "Preparing files";
  conversionProgress.value = progress;
  conversionPercent.textContent = `${Math.round(progress * 100)}%`;
  convertedCount.textContent = String(job.converted || 0);
  skippedCount.textContent = String(job.skipped || 0);
  failedCount.textContent = String(job.failed || 0);
  const empty = Number(job.empty) || 0;
  emptyCount.textContent = `${empty} converted file${empty === 1 ? "" : "s"} contained no extracted text.`;
  emptyCount.hidden = empty === 0;
}

function renderComplete(job) {
  showPanel("complete-panel");
  const converted = Number(job.converted) || 0;
  const issueTotal = (Number(job.skipped) || 0) + (Number(job.failed) || 0);
  completeSummary.textContent = `${converted} file${converted === 1 ? "" : "s"} converted${issueTotal ? `, with ${issueTotal} item${issueTotal === 1 ? "" : "s"} explained in the report` : ""}.`;
  issuesNotice.hidden = !job.has_issues;
  downloadButton.href = `/api/jobs/${encodeURIComponent(activeJobId)}/download`;
}

function renderFailure(message) {
  showPanel("failure-panel");
  failureMessage.textContent = message || "Review the ZIP and try again.";
}

async function pollJob() {
  if (!activeJobId) {
    return;
  }
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(activeJobId)}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(response.status === 404 ? "This conversion has expired." : "Status unavailable.");
    }
    const job = await response.json();
    pollFailures = 0;
    pollingNote.hidden = true;
    renderProgress(job);
    if (job.status === "complete") {
      renderComplete(job);
      return;
    }
    if (job.status === "failed") {
      renderFailure(job.error || "The ZIP could not be converted.");
      return;
    }
    pollTimer = window.setTimeout(pollJob, POLL_INTERVAL_MS);
  } catch (error) {
    pollFailures += 1;
    pollingNote.textContent = `${error.message} Retrying locally…`;
    pollingNote.hidden = false;
    pollTimer = window.setTimeout(pollJob, Math.min(2000, POLL_INTERVAL_MS * pollFailures));
  }
}

async function deleteActiveJob() {
  window.clearTimeout(pollTimer);
  if (!activeJobId) {
    resetInterface();
    return;
  }
  const jobId = activeJobId;
  try {
    await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
  } finally {
    resetInterface();
  }
}

archiveInput.addEventListener("change", () => {
  startUpload(archiveInput.files[0]);
});

chooseButton.addEventListener("click", () => {
  archiveInput.click();
});

["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("is-dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
  });
});

dropZone.addEventListener("drop", (event) => {
  startUpload(event.dataTransfer.files[0]);
});

deleteButton.addEventListener("click", async () => {
  deleteButton.disabled = true;
  await deleteActiveJob();
  deleteButton.disabled = false;
});

retryButton.addEventListener("click", deleteActiveJob);
