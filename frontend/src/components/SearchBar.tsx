"use client";

import { useState, useEffect, FormEvent } from "react";
import { LanguageOption, getLanguages } from "@/lib/api";
import LanguageSelect from "./LanguageSelect";

interface Props {
  onSearch: (query: string, language?: string) => void;
  loading: boolean;
  initialQuery?: string;
}

export default function SearchBar({ onSearch, loading, initialQuery }: Props) {
  const [query, setQuery] = useState(initialQuery || "");
  const [languages, setLanguages] = useState<LanguageOption[]>([]);
  const [selectedLangs, setSelectedLangs] = useState<string[]>([]);

  useEffect(() => {
    getLanguages()
      .then((langs) => {
        setLanguages(langs);
        // Restore from localStorage, or fall back to all enabled
        const saved = localStorage.getItem("lumina:searchLangs");
        if (saved) {
          try {
            const parsed = JSON.parse(saved) as string[];
            // Only keep codes that are still in the enabled list
            const enabled = new Set(langs.filter((l) => l.enabled).map((l) => l.code));
            const valid = parsed.filter((c) => enabled.has(c));
            if (valid.length > 0) {
              setSelectedLangs(valid);
              return;
            }
          } catch { /* ignore bad data */ }
        }
        setSelectedLangs(langs.filter((l) => l.enabled).map((l) => l.code));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (initialQuery) setQuery(initialQuery);
  }, [initialQuery]);

  const enabledLangs = languages.filter((l) => l.enabled);

  function handleLangChange(codes: string[]) {
    setSelectedLangs(codes);
    localStorage.setItem("lumina:searchLangs", JSON.stringify(codes));
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    // If all enabled are selected or only one, pass it for TMDB locale
    const lang = selectedLangs.length === 1 ? selectedLangs[0] : undefined;
    onSearch(trimmed, lang);
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2 sm:gap-3 w-full max-w-2xl items-center">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search movie or TV show..."
        className="flex-1 min-w-0 rounded-lg bg-zinc-800 border border-zinc-700 px-4 py-3 text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent"
      />
      {enabledLangs.length > 1 && (
        <LanguageSelect
          options={enabledLangs}
          selected={selectedLangs}
          onChange={handleLangChange}
          showAll
          compact
          placeholder="Lang"
        />
      )}
      <button
        type="submit"
        disabled={loading || !query.trim()}
        className="flex-shrink-0 rounded-lg bg-violet-600 px-4 sm:px-6 py-3 font-medium text-white hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "..." : "Search"}
      </button>
    </form>
  );
}
