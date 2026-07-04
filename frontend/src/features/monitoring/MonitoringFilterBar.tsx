import { Icon } from "@iconify/react";
import cleanIcon from "@iconify-icons/carbon/clean";
import { ChevronDown, FilterX, Search } from "lucide-react";
import type { MouseEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../shared/api/client";
import type { JobRecord, MonitoringFilterOptionsResponse, MonitoringRecord } from "../../shared/api/types";
import type { FilterOption, MonitoringFilters } from "./monitoringFilters";
import { DEFAULT_MONITORING_FILTERS, hasActiveFilters } from "./monitoringFilters";

interface MonitoringFilterBarProps {
  environmentId: number;
  options: MonitoringFilterOptionsResponse | null;
  filters: MonitoringFilters;
  searchOptions: MonitoringSearchOption[];
  grainWarning?: string;
  onChange: (filters: MonitoringFilters) => void;
}

const STATUS_OPTIONS = ["pending", "running", "succeeded", "failed", "skipped"];

export interface MonitoringSearchOption {
  key: string;
  label: string;
  detail: string;
  value: string;
  investigateKind: "job_id" | "dataflow" | "dataflow_run_id" | "destination_table";
  kind: "job" | "dataflow" | "dataflow run" | "table";
}

export function MonitoringFilterBar({ environmentId, options, filters, searchOptions, grainWarning = "", onChange }: MonitoringFilterBarProps) {
  const [customOpen, setCustomOpen] = useState(false);
  const [rangeDraft, setRangeDraft] = useState<MonitoringFilters["range"]>(filters.range);
  const [searchOpen, setSearchOpen] = useState(false);
  const [openFilter, setOpenFilter] = useState<keyof MonitoringFilters | null>(null);
  const [searchDraft, setSearchDraft] = useState(filters.search);
  const [remoteSearchOptions, setRemoteSearchOptions] = useState<MonitoringSearchOption[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [draftStart, setDraftStart] = useState(filters.startTime);
  const [draftEnd, setDraftEnd] = useState(filters.endTime);
  const customRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLLabelElement | null>(null);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (customRef.current && !customRef.current.contains(event.target as Node)) {
        closeCustomRange();
      }
      if (searchRef.current && !searchRef.current.contains(event.target as Node)) {
        setSearchOpen(false);
      }
      if (event.target instanceof Element && !event.target.closest(".monitoring-filter-dropdown")) {
        setOpenFilter(null);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  useEffect(() => {
    if (!customOpen) {
      setRangeDraft(filters.range);
      setDraftStart(filters.startTime);
      setDraftEnd(filters.endTime);
    }
  }, [customOpen, filters.range, filters.startTime, filters.endTime]);

  useEffect(() => {
    setSearchDraft(filters.investigateValue || filters.search);
  }, [filters.investigateValue, filters.search]);

  useEffect(() => {
    const query = searchDraft.trim();
    if (!searchOpen || query.length < 2) {
      setRemoteSearchOptions([]);
      setSearchLoading(false);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSearchLoading(true);
      const params = searchQueryParams(filters, query);
      Promise.all([
        api.getMonitoringJobs(environmentId, { ...params, limit: 8, offset: 0 }),
        api.getMonitoringDataflows(environmentId, { ...params, limit: 16, offset: 0 })
      ])
        .then(([jobs, dataflows]) => {
          if (cancelled) return;
          setRemoteSearchOptions(buildRemoteSearchOptions(jobs.records, dataflows.records));
        })
        .catch(() => {
          if (!cancelled) setRemoteSearchOptions([]);
        })
        .finally(() => {
          if (!cancelled) setSearchLoading(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [environmentId, filters, searchDraft, searchOpen]);

  function setFilter<Key extends keyof MonitoringFilters>(key: Key, value: MonitoringFilters[Key]) {
    onChange({ ...filters, [key]: value });
  }

  function setRange(value: MonitoringFilters["range"]) {
    if (value === "custom") {
      const now = new Date();
      const start = new Date(now.getTime() - 30 * 86400 * 1000);
      const startValue = filters.startTime || start.toISOString();
      const endValue = filters.endTime || now.toISOString();
      setRangeDraft("custom");
      setDraftStart(startValue);
      setDraftEnd(endValue);
      setCustomOpen(true);
      return;
    }
    setRangeDraft(value);
    setCustomOpen(false);
    onChange({ ...filters, range: value, startTime: "", endTime: "" });
  }

  function closeCustomRange() {
    setCustomOpen(false);
    setRangeDraft(filters.range);
    setDraftStart(filters.startTime);
    setDraftEnd(filters.endTime);
  }

  const statusOptions = optionsFor(options, "status", STATUS_OPTIONS);
  const operationOptions = optionsFor(options, "operation_type");
  const stageOptions = optionsFor(options, "stage");
  const connectionOptions = optionsFor(options, "connection");
  const searchMatches = uniqueOptions([
    ...remoteSearchOptions,
    ...(searchDraft.trim() ? searchOptions.filter((option) => optionMatches(option, searchDraft)) : searchOptions)
  ]).sort((left, right) => searchOptionRank(left, searchDraft) - searchOptionRank(right, searchDraft)).slice(0, 10);

  return (
    <div className="monitoring-filter-bar" aria-label="Monitoring filters">
      <div className="monitoring-filter-primary">
        <label className="monitoring-filter-field compact monitoring-time-filter">
          <span>Range</span>
          <div className="monitoring-time-control" ref={customRef}>
            <select value={customOpen ? rangeDraft : filters.range} onChange={(event) => setRange(event.target.value as MonitoringFilters["range"])}>
              <option value="24h">Last 24h</option>
              <option value="3d">Last 3 days</option>
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="90d">Last 90 days</option>
              <option value="custom">Custom</option>
              <option value="all">All time</option>
            </select>
            {(customOpen ? rangeDraft : filters.range) === "custom" ? (
              <button type="button" className="monitoring-custom-trigger" onClick={() => setCustomOpen((value) => !value)}>
                {customOpen ? customRangeLabel(draftStart, draftEnd) : customRangeLabel(filters.startTime, filters.endTime)}
              </button>
            ) : null}
            {customOpen && rangeDraft === "custom" ? (
              <div className="monitoring-time-popover">
                <label>
                  <span>Start</span>
                  <input type="datetime-local" value={toDateTimeLocal(draftStart)} onChange={(event) => setDraftStart(fromDateTimeLocal(event.target.value))} />
                </label>
                <label>
                  <span>End</span>
                  <input type="datetime-local" value={toDateTimeLocal(draftEnd)} onChange={(event) => setDraftEnd(fromDateTimeLocal(event.target.value))} />
                </label>
                <div className="monitoring-time-actions">
                  <button type="button" className="text-action" onClick={() => setQuickCustomRange(14)}>
                    14d
                  </button>
                  <button type="button" className="text-action" onClick={() => setQuickCustomRange(90)}>
                    90d
                  </button>
                  <button type="button" className="text-action" onClick={setCurrentMonthRange}>
                    This month
                  </button>
                  <button type="button" className="text-action" onClick={closeCustomRange}>
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="primary mini"
                    onClick={() => {
                      onChange({ ...filters, range: "custom", startTime: draftStart, endTime: draftEnd });
                      setCustomOpen(false);
                      setRangeDraft("custom");
                    }}
                  >
                    Apply
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </label>

        <label
          className={`monitoring-filter-field compact monitoring-grain-filter${grainWarning ? " filter-warning" : ""}`}
          title={grainWarning || undefined}
        >
          <span>Grain</span>
          <select
            value={filters.grain}
            aria-invalid={grainWarning ? "true" : undefined}
            onChange={(event) => setFilter("grain", event.target.value as never)}
          >
            <option value="auto">Auto</option>
            <option value="hour">Hour</option>
            <option value="day">Day</option>
            <option value="week">Week</option>
            <option value="month">Month</option>
          </select>
        </label>

        <FilterDropdown
          label="Status"
          value={filters.status}
          options={statusOptions}
          open={openFilter === "status"}
          onOpen={() => setOpenFilter(openFilter === "status" ? null : "status")}
          onChange={(value) => setFilter("status", value as never)}
          onClose={() => setOpenFilter(null)}
        />
        <FilterDropdown
          label="Operation"
          value={filters.operationType}
          options={operationOptions}
          open={openFilter === "operationType"}
          onOpen={() => setOpenFilter(openFilter === "operationType" ? null : "operationType")}
          onChange={(value) => setFilter("operationType", value as never)}
          onClose={() => setOpenFilter(null)}
        />
        <FilterDropdown
          label="Stage"
          value={filters.stage}
          options={stageOptions}
          open={openFilter === "stage"}
          onOpen={() => setOpenFilter(openFilter === "stage" ? null : "stage")}
          onChange={(value) => setFilter("stage", value as never)}
          onClose={() => setOpenFilter(null)}
        />
        <FilterDropdown
          label="Connection"
          value={filters.connection}
          options={connectionOptions}
          open={openFilter === "connection"}
          onOpen={() => setOpenFilter(openFilter === "connection" ? null : "connection")}
          onChange={(value) => setFilter("connection", value as never)}
          onClose={() => setOpenFilter(null)}
        />

        <label className="monitoring-filter-search monitoring-object-search" ref={searchRef}>
          <Search size={15} />
          <input
            value={searchDraft}
            onFocus={() => setSearchOpen(true)}
            onChange={(event) => {
              setSearchDraft(event.target.value);
              setSearchOpen(true);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                const first = searchMatches[0];
                if (first) {
                  applyInvestigation(first);
                  setSearchOpen(false);
                }
              }
              if (event.key === "Escape") {
                setSearchDraft(filters.search);
                setSearchOpen(false);
              }
            }}
            placeholder="Find job, dataflow, run id, table..."
          />
          {searchDraft ? (
            <MonitoringClearButton
              className="monitoring-search-clear"
              title="Clear search"
              ariaLabel="Clear search"
              onClick={() => {
                setSearchDraft("");
                onChange({ ...filters, search: "", investigateKind: "", investigateValue: "" });
                setSearchOpen(false);
              }}
            />
          ) : null}
          {searchOpen ? (
            <div className="monitoring-search-results">
              {searchLoading ? <div className="monitoring-search-empty">Searching monitoring objects...</div> : null}
              {searchMatches.length ? (
                searchMatches.map((option) => (
                  <button
                    key={`${option.key}:${option.detail}`}
                    type="button"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => {
                      applyInvestigation(option);
                      setSearchOpen(false);
                    }}
                    title={`${option.label}${option.detail ? ` - ${option.detail}` : ""}`}
                  >
                    <span>{option.kind}</span>
                    <strong title={option.label}>{option.label}</strong>
                    <small title={option.detail}>{option.detail}</small>
                  </button>
                ))
              ) : !searchLoading ? (
                <div className="monitoring-search-empty">No matching monitoring object</div>
              ) : null}
            </div>
          ) : null}
        </label>

        <button className="secondary icon-button" disabled={!hasActiveFilters(filters)} onClick={() => onChange(DEFAULT_MONITORING_FILTERS)} title="Clear filters">
          <FilterX size={15} />
        </button>
      </div>
    </div>
  );

  function setQuickCustomRange(days: number) {
    const now = new Date();
    const start = new Date(now.getTime() - days * 86400 * 1000);
    setDraftStart(start.toISOString());
    setDraftEnd(now.toISOString());
  }

  function setCurrentMonthRange() {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), 1);
    setDraftStart(start.toISOString());
    setDraftEnd(now.toISOString());
  }

  function applyInvestigation(option: MonitoringSearchOption) {
    setSearchDraft(option.value);
    onChange({
      ...filters,
      search: "",
      investigateKind: option.investigateKind,
      investigateValue: option.value
    });
  }
}

function optionMatches(option: MonitoringSearchOption, query: string) {
  const normalized = normalizeSearchText(query);
  return normalizeSearchText(`${option.label} ${option.detail} ${option.value} ${option.kind}`).includes(normalized);
}

function searchOptionRank(option: MonitoringSearchOption, query: string) {
  const normalized = normalizeSearchText(query);
  const label = normalizeSearchText(option.label);
  const value = normalizeSearchText(option.value);
  if (normalized && (label === normalized || value === normalized)) {
    return option.investigateKind === "destination_table" ? 0 : option.investigateKind === "dataflow" ? 1 : 2;
  }
  if (normalized && (label.startsWith(normalized) || value.startsWith(normalized))) {
    return option.investigateKind === "destination_table" ? 10 : option.investigateKind === "dataflow" ? 11 : 12;
  }
  const kindRank = option.investigateKind === "destination_table" ? 20 : option.investigateKind === "dataflow" ? 21 : option.investigateKind === "dataflow_run_id" ? 22 : 23;
  return kindRank;
}

function normalizeSearchText(value: unknown) {
  return String(value ?? "").trim().toLowerCase().replace(/`/g, "");
}

function uniqueOptions(options: MonitoringSearchOption[]) {
  const seen = new Set<string>();
  const result: MonitoringSearchOption[] = [];
  for (const option of options) {
    const key = `${option.kind}:${option.value}:${option.detail}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(option);
  }
  return result;
}

function searchQueryParams(filters: MonitoringFilters, search: string) {
  return {
    range: filters.range,
    startTime: filters.range === "custom" ? filters.startTime : undefined,
    endTime: filters.range === "custom" ? filters.endTime : undefined,
    status: filters.status,
    stage: filters.stage,
    connection: filters.connection,
    operationType: filters.operationType,
    search
  };
}

function buildRemoteSearchOptions(jobs: JobRecord[], dataflows: MonitoringRecord[]) {
  const options: MonitoringSearchOption[] = [];
  for (const row of dataflows) {
    options.push(...dataflowSearchOptions(row));
  }
  for (const row of jobs) {
    const jobId = String(row.job_id ?? "").trim();
    if (!jobId) continue;
    options.push({
      key: `job:${jobId}`,
      kind: "job",
      label: jobId,
      detail: compactDetail([
        row.status,
        row.engine_name,
        row.platform_name,
        row.metadata_provider_name
      ]),
      investigateKind: "job_id",
      value: jobId
    });
  }
  return uniqueOptions(options);
}

function dataflowSearchOptions(row: MonitoringRecord) {
  const options: MonitoringSearchOption[] = [];
  const runId = String(row.dataflow_run_id ?? "").trim();
  const dataflowId = String(row.dataflow_id ?? "").trim();
  const dataflowName = String(row.dataflow_name ?? "").trim();
  if (dataflowName || dataflowId) {
    options.push({
      key: `dataflow-name:${dataflowName || dataflowId}`,
      kind: "dataflow",
      label: dataflowName || dataflowId,
      detail: connectionFlowDetail(row),
      investigateKind: "dataflow",
      value: dataflowName || dataflowId
    });
  }
  if (dataflowId && dataflowName) {
    options.push({
      key: `dataflow-id:${dataflowId}`,
      kind: "dataflow",
      label: dataflowId,
      detail: dataflowName,
      investigateKind: "dataflow",
      value: dataflowId
    });
  }
  if (runId) {
    options.push({
      key: `dataflow-run:${runId}`,
      kind: "dataflow run",
      label: runId,
      detail: compactDetail([row.status, dataflowName || dataflowId || "dataflow"]),
      investigateKind: "dataflow_run_id",
      value: runId
    });
  }
  const tableIdentity = tableSearchIdentity(row, "destination");
  if (tableIdentity) {
    options.push({
      key: `destination-table:${tableIdentity}`,
      kind: "table",
      label: tableIdentity,
      detail: compactDetail(["dest", row.destination_name]),
      investigateKind: "destination_table",
      value: tableIdentity
    });
  }
  return options;
}

function compactDetail(values: unknown[]) {
  const detail = values.map((value) => String(value ?? "").trim()).filter(Boolean).join(" · ");
  return detail || "unknown";
}

function connectionFlowDetail(row: MonitoringRecord) {
  const source = String(row.source_name ?? "").trim() || "unknown source";
  const destination = String(row.destination_name ?? "").trim() || "unknown destination";
  return `${source} - ${destination}`;
}

function tableSearchIdentity(row: MonitoringRecord, direction: "source" | "destination") {
  const fullTable = firstString(row, [`${direction}_full_table`]).replace(/`/g, "");
  if (fullTable) return fullTable;
  const targetDisplay = direction === "destination" ? firstString(row, ["target_display"]) : "";
  if (targetDisplay) return targetDisplay;
  const catalog = firstString(row, [`${direction}_catalog`, `${direction}_catalog_name`]);
  const database = firstString(row, [`${direction}_database`, `${direction}_database_name`]);
  const schema = firstString(row, [`${direction}_schema`, `${direction}_schema_name`]);
  const table = firstString(row, [`${direction}_table`, `${direction}_table_name`]);
  const path = firstString(row, [`${direction}_path`, `${direction}_physical_path`, `${direction}_uri`]);
  const qualified = [catalog, database, schema, table].filter(Boolean).join(".");
  return qualified || path || "";
}

function firstString(row: MonitoringRecord, keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value !== null && value !== undefined && value !== "") return String(value);
  }
  return "";
}

function FilterDropdown({
  label,
  value,
  options,
  open,
  onOpen,
  onChange,
  onClose
}: {
  label: string;
  value: string;
  options: FilterOption[];
  open: boolean;
  onOpen: () => void;
  onChange: (value: string) => void;
  onClose: () => void;
}) {
  const selected = splitFilterValues(value);
  const selectedSet = new Set(selected);
  const isDefault = selected.length === 0;
  const labelText = isDefault ? "All" : selected.length === 1 ? selected[0] : `${selected.length} selected`;

  function chooseOption(optionValue: string, event: MouseEvent<HTMLButtonElement>) {
    if (optionValue === "all") {
      onChange("all");
      onClose();
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      const next = new Set(selected);
      if (next.has(optionValue)) {
        next.delete(optionValue);
      } else {
        next.add(optionValue);
      }
      onChange(next.size ? Array.from(next).join("|") : "all");
      return;
    }
    onChange(optionValue);
    onClose();
  }

  return (
    <div className="monitoring-filter-field monitoring-filter-dropdown">
      <span>{label}</span>
      <div className="monitoring-filter-dropdown-control">
        <button type="button" className="monitoring-filter-trigger" onClick={onOpen} title={selected.join(", ") || "All"}>
          <span>{labelText}</span>
          <ChevronDown size={13} />
        </button>
        {!isDefault ? (
          <MonitoringClearButton
            className="monitoring-filter-clear"
            title={`Clear ${label}`}
            ariaLabel={`Clear ${label}`}
            onClick={() => onChange("all")}
          />
        ) : null}
        {open ? (
          <div className="monitoring-filter-menu">
            {options.map((option) => {
              const active = option.value === "all" ? isDefault : selectedSet.has(option.value);
              return (
                <button
                  key={option.value}
                  type="button"
                  className={active ? "active" : ""}
                  onClick={(event) => chooseOption(option.value, event)}
                >
                  <span>{option.label}</span>
                  {option.count !== undefined ? <small>{formatCount(option.count)}</small> : null}
                  {active ? <strong>Selected</strong> : null}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MonitoringClearButton({
  className,
  title,
  ariaLabel,
  onClick
}: {
  className: string;
  title: string;
  ariaLabel: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={`monitoring-clear-button ${className}`} title={title} aria-label={ariaLabel} onClick={onClick}>
      <Icon className="monitoring-clear-button-icon" icon={cleanIcon} />
    </button>
  );
}

function splitFilterValues(value: string) {
  if (!value || value === "all") return [];
  return value.split("|").map((item) => item.trim()).filter(Boolean);
}

function optionsFor(response: MonitoringFilterOptionsResponse | null, field: string, fallback: string[] = []): FilterOption[] {
  const fromResponse = response?.options[field] ?? [];
  const options = fromResponse.length
    ? fromResponse.map((option) => ({
        value: String(option.value),
        label: String(option.label),
        count: option.count
      }))
    : fallback.map((value) => ({ value, label: value }));
  return [{ value: "all", label: "All" }, ...options];
}

function formatCount(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function toDateTimeLocal(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function fromDateTimeLocal(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toISOString() : "";
}

function customRangeLabel(startValue: string, endValue: string) {
  const start = shortDateTime(startValue);
  const end = shortDateTime(endValue);
  if (!start && !end) return "Pick range";
  return `${start || "..."} - ${end || "..."}`;
}

function shortDateTime(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}
