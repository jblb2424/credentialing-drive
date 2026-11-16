import { useEffect, useRef, useState } from "react";

import { entityPath, fetchJson } from "./api.js";

const MAX_FILES_PER_BATCH = 50;

function entryFor(file) {
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID()}`,
    file,
    status: "ready",
    error: "",
  };
}

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

export function NativeUploader({ entityId, onClose }) {
  const inputRef = useRef(null);
  const [entries, setEntries] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [batchError, setBatchError] = useState("");
  const busy = entries.some((entry) => entry.status === "uploading");
  const readyCount = entries.filter((entry) => entry.status === "ready").length;

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);

  const addFiles = (fileList) => {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    setBatchError("");
    setEntries((current) => {
      const remaining = Math.max(0, MAX_FILES_PER_BATCH - current.length);
      if (incoming.length > remaining) setBatchError(`A batch can include up to ${MAX_FILES_PER_BATCH} files.`);
      const seen = new Set(current.map((entry) => `${entry.file.name}:${entry.file.size}:${entry.file.lastModified}`));
      const additions = incoming
        .filter((file) => {
          const key = `${file.name}:${file.size}:${file.lastModified}`;
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        })
        .slice(0, remaining)
        .map(entryFor);
      return [...current, ...additions];
    });
  };

  const updateEntry = (id, changes) => setEntries((current) => current.map((entry) => entry.id === id ? { ...entry, ...changes } : entry));

  const queueFile = async (entry) => {
    updateEntry(entry.id, { status: "uploading", error: "" });
    try {
      const created = await fetchJson(entityPath(entityId, "/uploads"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_name: entry.file.name, mime_type: entry.file.type, size_bytes: entry.file.size }),
      });
      const upload = created.upload;
      const storageResponse = await fetch(upload.upload_url, {
        method: "PUT",
        headers: { "Content-Type": upload.mime_type },
        body: entry.file,
      });
      if (!storageResponse.ok) throw new Error("The file could not be securely uploaded.");
      await fetchJson(entityPath(entityId, `/uploads/${encodeURIComponent(upload.id)}/complete`), { method: "POST" });
      updateEntry(entry.id, { status: "queued" });
    } catch (error) {
      updateEntry(entry.id, { status: "error", error: error.message || "The file could not be uploaded." });
    }
  };

  const queueReadyFiles = async () => {
    const ready = entries.filter((entry) => entry.status === "ready");
    if (!ready.length || busy) return;
    setBatchError("");
    // A small client-side concurrency limit makes batches responsive without
    // overwhelming the browser or creating a burst of storage requests.
    const pending = [...ready];
    await Promise.all(Array.from({ length: Math.min(3, pending.length) }, async () => {
      while (pending.length) await queueFile(pending.shift());
    }));
  };

  const removeEntry = (id) => {
    if (busy) return;
    setEntries((current) => current.filter((entry) => entry.id !== id));
  };

  return <div className="native-upload-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="native-upload-dialog" role="dialog" aria-modal="true" aria-labelledby="native-upload-title">
      <header className="native-upload-header">
        <div>
          <p className="eyebrow">Document intake</p>
          <h2 id="native-upload-title">Upload credentialing documents</h2>
        </div>
        <button className="dialog-close" type="button" onClick={onClose} disabled={busy} aria-label="Close upload documents">×</button>
      </header>

      <input ref={inputRef} className="native-upload-input" type="file" multiple onChange={(event) => { addFiles(event.target.files); event.target.value = ""; }} />
      <div
        className={`native-upload-dropzone${dragging ? " is-dragging" : ""}`}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => { if (event.target === event.currentTarget) setDragging(false); }}
        onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files); }}
      >
        <span className="native-upload-icon" aria-hidden="true">↥</span>
        <strong>Drag and drop documents here</strong>
        <span>PDF, images, Word documents, text files, and spreadsheets</span>
        <button className="secondary-button" type="button" onClick={() => inputRef.current?.click()} disabled={busy}>Choose files</button>
      </div>

      {batchError && <p className="native-upload-error" role="alert">{batchError}</p>}
      {entries.length > 0 && <div className="native-upload-queue" aria-live="polite">
        <div className="native-upload-queue-heading"><strong>{entries.length} {entries.length === 1 ? "document" : "documents"}</strong><button className="text-button" type="button" onClick={() => setEntries([])} disabled={busy}>Clear list</button></div>
        <ul>
          {entries.map((entry) => <li key={entry.id} className={`native-upload-row is-${entry.status}`}>
            <span className="native-upload-file-icon" aria-hidden="true">▤</span>
            <span className="native-upload-file-name"><strong>{entry.file.name}</strong><small>{formatSize(entry.file.size)}{entry.error ? ` · ${entry.error}` : entry.status === "queued" ? " · Queued" : entry.status === "uploading" ? " · Uploading" : ""}</small></span>
            {entry.status === "uploading" ? <span className="spinner" aria-label="Uploading" /> : entry.status === "queued" ? <span className="native-upload-check" aria-label="Queued">✓</span> : <button className="native-upload-remove" type="button" onClick={() => removeEntry(entry.id)} aria-label={`Remove ${entry.file.name}`}>×</button>}
          </li>)}
        </ul>
      </div>}

      <footer className="native-upload-actions">
        <button className="secondary-button" type="button" onClick={onClose} disabled={busy}>Close</button>
        <button className="primary-button" type="button" onClick={queueReadyFiles} disabled={!readyCount || busy}>{busy ? "Uploading..." : `Upload ${readyCount || ""} ${readyCount === 1 ? "document" : "documents"}`}</button>
      </footer>
    </section>
  </div>;
}
