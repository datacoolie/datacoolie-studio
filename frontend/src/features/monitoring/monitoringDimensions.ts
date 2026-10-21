/** Display opaque backend dimension keys without using labels as identities. */
const UNKNOWN_KEY = "__monitoring_unknown__";
const VALUE_PREFIX = "__monitoring_value__:";
const CONTEXT_PREFIX = "__monitoring_context__:";

export function monitoringDimensionLabel(value: unknown): string {
  if (value == null || String(value).replace(/[\s\u001c-\u001f\u0085]/gu, "") === "" || value === UNKNOWN_KEY) return "Unknown";
  const key = String(value);
  if (key.startsWith(CONTEXT_PREFIX)) {
    try {
      const parts: unknown = JSON.parse(key.slice(CONTEXT_PREFIX.length));
      if (Array.isArray(parts)) return parts.map(monitoringDimensionLabel).join(" · ");
    } catch { /* Preserve malformed source text. */ }
  }
  if (key.startsWith(VALUE_PREFIX)) {
    const hex = key.slice(VALUE_PREFIX.length);
    if (/^(?:[a-f\d]{2})*$/i.test(hex)) {
      try {
        const bytes = Uint8Array.from(hex.match(/.{2}/g) ?? [], (part) => parseInt(part, 16));
        return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch { /* Preserve malformed source text. */ }
    }
  }
  return key.toLowerCase() === "unknown" ? `${key} (value)` : key;
}

/** Raw log/identity dimensions have not been encoded by the SQL projection. */
export function monitoringRawDimensionLabel(value: unknown): string {
  const text = value == null ? "" : String(value).replace(/^[\s\u001c-\u001f\u0085]+|[\s\u001c-\u001f\u0085]+$/gu, "");
  if (!text) return "Unknown";
  return text.toLowerCase() === "unknown" ? `${text} (value)` : text;
}

/** Drawer value convention: missing raw dimensions are compacted without changing literal values. */
export function monitoringDrawerRawDimensionLabel(value: unknown): string {
  const label = monitoringRawDimensionLabel(value);
  return label === "Unknown" ? "unk" : label;
}
