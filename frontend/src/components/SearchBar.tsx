"use client";

import { useState, useEffect, FormEvent } from "react";

interface Props {
  onSearch: (query: string, language?: string) => void;
  loading: boolean;
  initialQuery?: string;
}

/** The language of titles / overviews is a setting ("Jazyk názvů a popisů"); file search finds every language. */
export default function SearchBar({ onSearch, loading, initialQuery }: Props) {
  const [query, setQuery] = useState(initialQuery || "");

  useEffect(() => {
    if (initialQuery) setQuery(initialQuery);
  }, [initialQuery]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    onSearch(trimmed);
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
