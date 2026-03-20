"use client";

import { useState, useEffect, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface DirEntry {
  name: string;
  path: string;
}

interface BrowseResult {
  path: string;
  parent: string | null;
  dirs: DirEntry[];
}

interface Props {
  initialPath: string;
  onSelect: (path: string) => void;
  onCancel: () => void;
}

export default function FolderBrowser({ initialPath, onSelect, onCancel }: Props) {
  const [currentPath, setCurrentPath] = useState(initialPath || "/");
  const [dirs, setDirs] = useState<DirEntry[]>([]);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [newFolderName, setNewFolderName] = useState("");

  const browse = useCallback(async (path: string) => {
    setLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/settings/browse?path=${encodeURIComponent(path)}`
      );
      if (!res.ok) return;
      const data: BrowseResult = await res.json();
      setCurrentPath(data.path);
      setParentPath(data.parent);
      setDirs(data.dirs);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    browse(initialPath || "/");
  }, [initialPath, browse]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <h3 className="text-sm font-semibold text-zinc-200">Select folder</h3>
          <button
            onClick={onCancel}
            className="text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Current path */}
        <div className="px-4 py-2 border-b border-zinc-800/50">
          <div className="flex items-center gap-2 text-xs font-mono text-zinc-400 bg-zinc-800/50 rounded px-3 py-2 overflow-x-auto">
            <svg className="w-4 h-4 shrink-0 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
            <span className="truncate">{currentPath}</span>
          </div>
        </div>

        {/* Directory list */}
        <div className="max-h-72 overflow-y-auto">
          {loading ? (
            <div className="px-4 py-8 text-center text-zinc-500 text-sm animate-pulse">
              Loading...
            </div>
          ) : (
            <>
              {/* Parent dir */}
              {parentPath && (
                <button
                  onClick={() => browse(parentPath)}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-zinc-800/50 transition-colors text-left border-b border-zinc-800/30"
                >
                  <svg className="w-4 h-4 text-zinc-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 17l-5-5m0 0l5-5m-5 5h12" />
                  </svg>
                  <span className="text-zinc-400">..</span>
                </button>
              )}

              {/* Subdirectories */}
              {dirs.map((dir) => (
                <button
                  key={dir.path}
                  onClick={() => browse(dir.path)}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-zinc-800/50 transition-colors text-left"
                >
                  <svg className="w-4 h-4 text-amber-500/70 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                  </svg>
                  <span className="text-zinc-200">{dir.name}</span>
                </button>
              ))}

              {dirs.length === 0 && !parentPath && (
                <div className="px-4 py-6 text-center text-zinc-600 text-sm">
                  Empty directory
                </div>
              )}

              {dirs.length === 0 && parentPath && (
                <div className="px-4 py-4 text-center text-zinc-600 text-xs">
                  No subdirectories
                </div>
              )}
            </>
          )}
        </div>

        {/* New folder */}
        <div className="px-4 py-2 border-t border-zinc-800/50">
          <div className="flex gap-2">
            <input
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              placeholder="New folder name..."
              className="flex-1 rounded bg-zinc-800 border border-zinc-700 px-3 py-1.5 text-zinc-100 placeholder-zinc-600 text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter" && newFolderName.trim()) {
                  const newPath = `${currentPath}/${newFolderName.trim()}`.replace(/\/+/g, "/");
                  setNewFolderName("");
                  onSelect(newPath);
                }
              }}
            />
            <button
              onClick={() => {
                if (newFolderName.trim()) {
                  const newPath = `${currentPath}/${newFolderName.trim()}`.replace(/\/+/g, "/");
                  setNewFolderName("");
                  onSelect(newPath);
                }
              }}
              disabled={!newFolderName.trim()}
              className="rounded bg-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-600 disabled:opacity-30 transition-colors"
            >
              Create & select
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3">
          <button
            onClick={onCancel}
            className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => onSelect(currentPath)}
            className="rounded-lg bg-violet-600 px-5 py-2 text-sm font-medium text-white hover:bg-violet-500 transition-colors"
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );
}
