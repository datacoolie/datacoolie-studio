import { Search, X } from "lucide-react";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import type { LineageSearchResult } from "../model/types";

export function LineageSearch({
  query,
  results,
  onQueryChange,
  onSelect
}: {
  query: string;
  results: LineageSearchResult[];
  onQueryChange: (query: string) => void;
  onSelect: (result: LineageSearchResult) => void;
}) {
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!results.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => {
        const offset = event.key === "ArrowDown" ? 1 : -1;
        return (current + offset + results.length) % results.length;
      });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      onSelect(results[activeIndex] ?? results[0]);
      setOpen(false);
    }
  }

  return (
    <div className="lineage-search">
      <label htmlFor={`${listboxId}-input`}>Find an entity</label>
      <div className="lineage-search-control">
        <Search size={15} aria-hidden="true" />
        <input
          id={`${listboxId}-input`}
          role="combobox"
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={open && Boolean(query.trim())}
          value={query}
          placeholder="Asset, reference, dataflow, dependency"
          onChange={(event) => {
            onQueryChange(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
        />
        {query ? (
          <button
            type="button"
            aria-label="Clear lineage search"
            onClick={() => {
              onQueryChange("");
              setOpen(false);
            }}
          >
            <X size={14} />
          </button>
        ) : null}
      </div>
      {open && query.trim() ? (
        <div
          id={listboxId}
          className="lineage-search-results"
          role="listbox"
          onMouseDown={(event) => event.preventDefault()}
        >
          {results.length ? results.map((result, index) => (
            <button
              key={`${result.kind}:${result.id}`}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              className={index === activeIndex ? "active" : ""}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => {
                onSelect(result);
                setOpen(false);
              }}
            >
              <span>
                <strong>{result.title}</strong>
                <small>{result.subtitle}</small>
                <code title={result.identity}>{result.identity}</code>
              </span>
              <i className={`lineage-search-kind kind-${result.kind}`}>{searchKindLabel(result.kind)}</i>
            </button>
          )) : (
            <div className="lineage-search-empty">No matching lineage entities</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function searchKindLabel(kind: LineageSearchResult["kind"]) {
  if (kind === "asset") return "Asset";
  if (kind === "dataflow") return "Dataflow";
  if (kind === "dependency") return "Dependency";
  return "Reference";
}
