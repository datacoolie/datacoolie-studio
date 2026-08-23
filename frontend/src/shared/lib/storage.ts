/** Presentation helpers for storage locations. Keep canonical URI handling in the backend. */

/**
 * Render storage locations in one preferred provider form.
 * Legacy DBFS paths retain their scheme so the provider boundary stays explicit.
 */
export function displayStorageUri(uri: string): string {
  if (/^dbfs:\/Volumes(?:\/|$)/i.test(uri)) return uri.slice("dbfs:".length);
  if (/^Volumes(?:\/|$)/i.test(uri)) return `/${uri}`;
  if (/^abfs:\/\//i.test(uri)) return `abfss://${uri.slice("abfs://".length)}`;
  if (/^(?:gcs):\/\//i.test(uri)) return `gs://${uri.slice(uri.indexOf("://") + 3)}`;
  if (/^(?:s3a|s3n):\/\//i.test(uri)) return `s3://${uri.slice(uri.indexOf("://") + 3)}`;
  return uri;
}
