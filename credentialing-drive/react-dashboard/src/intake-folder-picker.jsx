import { useState } from "react";
import { entityPath, fetchJson } from "./api.js";
import { shortDate } from "./utils.js";

export const INTAKE_FOLDER_SELECTION_COPY = "Select the folder whose new files should be processed for this client.";

export function useIntakeFolderPicker(entityId, onSelected) {
  const [isOpen, setIsOpen] = useState(false);
  const [folders, setFolders] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [selectingFolderId, setSelectingFolderId] = useState("");
  const visibleFolders = folders.filter((folder) => folder.name?.toLowerCase().includes(query.trim().toLowerCase()));

  const openFolderPicker = () => {
    setIsOpen(true);
    setIsLoading(true);
    setQuery("");
    setError("");
    fetchJson(entityPath(entityId, "/drive/folders"))
      .then((result) => setFolders(result.folders || []))
      .catch((requestError) => setError(requestError.message || "We could not load Google Drive folders."))
      .finally(() => setIsLoading(false));
  };

  const selectFolder = (folder) => {
    setSelectingFolderId(folder.id);
    setError("");
    fetchJson(entityPath(entityId, "/drive/intake-folder"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folder.id }),
    })
      .then(({ folder: selectedFolder, intake_ready: intakeReady }) => {
        if (!intakeReady) throw new Error("Google Drive did not confirm document monitoring.");
        setIsOpen(false);
        onSelected?.(selectedFolder);
      })
      .catch((requestError) => setError(requestError.message || "We could not save the intake folder."))
      .finally(() => setSelectingFolderId(""));
  };

  const folderPicker = isOpen && <div className="folder-picker-backdrop" role="presentation">
    <section className="folder-picker" role="dialog" aria-modal="true" aria-labelledby="folder-picker-title">
      <div className="folder-picker-header">
        <div>
          <p className="eyebrow">Google Drive</p>
          <h2 id="folder-picker-title">Choose intake folder</h2>
          <p>{INTAKE_FOLDER_SELECTION_COPY}</p>
        </div>
        <button className="dialog-close" type="button" onClick={() => setIsOpen(false)} aria-label="Close folder picker">×</button>
      </div>
      <label className="folder-picker-search">Search folders<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter accessible Google Drive folders" autoFocus /></label>
      {error && <p className="connection-error folder-picker-error" role="alert">{error}</p>}
      <div className="folder-picker-list">
        {isLoading ? <span className="loading-inline"><span className="spinner" />Loading Google Drive folders...</span> : visibleFolders.length ? visibleFolders.map((folder) => <button className="folder-picker-option" type="button" key={folder.id} onClick={() => selectFolder(folder)} disabled={Boolean(selectingFolderId)}><span className="folder-picker-icon" aria-hidden="true">□</span><span><strong>{folder.name}</strong><small>{folder.modifiedTime ? `Modified ${shortDate(folder.modifiedTime)}` : "Google Drive folder"}</small></span>{selectingFolderId === folder.id && <span className="spinner" />}</button>) : <p className="folder-picker-empty">No accessible folders match that search.</p>}
      </div>
    </section>
  </div>;

  return { openFolderPicker, folderPicker };
}
