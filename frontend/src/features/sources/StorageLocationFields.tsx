import {
  CheckCircle2,
  ChevronDown,
  LoaderCircle,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../../shared/api/client";
import { StorageProviderIcon } from "../../shared/components/StorageProviderIcon";
import type {
  CredentialProfile,
  StorageBinding,
  StorageProvider,
} from "../../shared/api/domainTypes";
import { toErrorMessage } from "../../shared/lib/errors";

export const LOCAL_STORAGE_BINDING: StorageBinding = {
  provider: "local",
  auth_mode: "none",
  credential_profile_id: null,
  options: {},
};

const PROVIDERS: Array<{
  value: StorageProvider;
  label: string;
  shortLabel: string;
  example: string;
}> = [
  { value: "local", label: "Local filesystem", shortLabel: "Local", example: "D:\\projects\\datacoolie" },
  { value: "s3", label: "Amazon S3", shortLabel: "S3", example: "s3://bucket/project" },
  { value: "minio", label: "MinIO", shortLabel: "MinIO", example: "s3://bucket/project" },
  { value: "adls", label: "Azure Data Lake Gen2", shortLabel: "ADLS", example: "abfs://container@account.dfs.core.windows.net/project" },
  { value: "onelake", label: "Microsoft OneLake", shortLabel: "OneLake", example: "abfss://workspace@onelake.dfs.fabric.microsoft.com/lakehouse.Lakehouse/Files/project" },
  { value: "gcs", label: "Google Cloud Storage", shortLabel: "GCS", example: "gs://bucket/project" },
  { value: "dbfs", label: "Databricks DBFS", shortLabel: "Databricks", example: "/Volumes/catalog/schema/volume/project" },
];

interface TestFeedback {
  tone: "success" | "error";
  message: string;
  detail?: string;
}

export function StorageLocationFields({
  binding,
  uri,
  sourceConfig,
  disabled,
  onChange,
}: {
  binding: StorageBinding;
  uri: string;
  sourceConfig?: Record<string, unknown>;
  disabled: boolean;
  onChange: (binding: StorageBinding) => void;
}) {
  const [profiles, setProfiles] = useState<CredentialProfile[]>([]);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestFeedback | null>(null);
  useEffect(() => {
    void api.listCredentialProfiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);
  const compatibleProfiles = useMemo(
    () => profiles.filter((profile) => profile.provider === binding.provider),
    [binding.provider, profiles],
  );
  const provider = PROVIDERS.find((item) => item.value === binding.provider) ?? PROVIDERS[0];
  const showAdvanced = binding.provider !== "local"
    && (
      binding.provider === "minio"
      || binding.provider === "gcs"
      || binding.provider === "s3"
      || binding.provider === "adls"
      || binding.provider === "dbfs"
    );
  const profileRequired = binding.auth_mode === "credential_profile";
  const dbfsPath = uri.trim();
  const unityCatalogVolume = dbfsPath.startsWith("dbfs:/Volumes/")
    || dbfsPath.startsWith("/Volumes/")
    || dbfsPath.startsWith("Volumes/");
  const legacyDbfs = binding.provider === "dbfs"
    && Boolean(dbfsPath)
    && !unityCatalogVolume;

  function setProvider(providerValue: StorageProvider) {
    onChange({
      provider: providerValue,
      auth_mode: providerValue === "local" ? "none" : "ambient",
      credential_profile_id: null,
      options: {},
    });
    setTestResult(null);
  }

  function setOption(key: string, value: unknown) {
    const options = { ...binding.options };
    if (value === "" || value === undefined) delete options[key];
    else options[key] = value;
    onChange({ ...binding, options });
    setTestResult(null);
  }

  function setDatabricksOption(key: "host" | "profile", value: string) {
    const options = { ...binding.options };
    const other = key === "host" ? "profile" : "host";
    if (value) {
      options[key] = value;
      delete options[other];
    } else {
      delete options[key];
    }
    onChange({ ...binding, options });
    setTestResult(null);
  }

  async function testConnection() {
    if (!uri.trim()) return;
    const pathIssue = binding.provider === "onelake" ? oneLakePathIssue(uri) : null;
    if (pathIssue) {
      setTestResult({
        tone: "error",
        message: "Invalid OneLake path",
        detail: pathIssue,
      });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.validateStorageConnection({
        uri: uri.trim(),
        storage: binding,
        source_config: sourceConfig,
      });
      setTestResult({
        tone: result.status === "ok" ? "success" : "error",
        message: result.status === "ok" ? "Connection ready" : "Connection failed",
        detail: [
          result.message,
          result.status === "ok" && !result.metadata_write_back_supported
            ? "Metadata write-back unavailable"
            : null,
          typeof result.error?.message === "string" ? result.error.message : null,
          result.error?.code ? String(result.error.code) : null,
        ].filter((value, index, values): value is string => Boolean(value) && values.indexOf(value) === index).join(" · "),
      });
    } catch (error) {
      setTestResult({ tone: "error", message: "Connection failed", detail: toErrorMessage(error) });
    } finally {
      setTesting(false);
    }
  }

  const authOptions = binding.provider === "dbfs" || binding.provider === "adls" || binding.provider === "onelake"
    ? [
        {
          value: "ambient",
          label: binding.provider === "onelake" ? "Azure sign-in (default)" : "Default credentials",
        },
        { value: "credential_profile", label: "Credential Profile" },
      ]
    : [
        { value: "ambient", label: "Default credentials" },
        { value: "credential_profile", label: "Credential Profile" },
        { value: "anonymous", label: "Anonymous" },
      ];

  return (
    <fieldset className="storage-location-fields" disabled={disabled}>
      <legend>Storage & access</legend>
      <div className="storage-location-heading">
        <div className={`storage-provider-icon provider-${binding.provider}`}>
          <StorageProviderIcon provider={binding.provider} />
        </div>
        <div>
          <strong>{provider.label}</strong>
          <span>
            {binding.provider === "adls" ? (
              <>Accepts: <code>abfs:// · abfss:// · Azure DFS HTTPS</code></>
            ) : binding.provider === "onelake" ? (
              <>ABFSS or HTTPS · <code>Lakehouse Files only</code></>
            ) : binding.provider === "dbfs" ? (
              legacyDbfs
                ? <>Legacy path · Prefer <code>/Volumes/…</code></>
                : <>Preferred: <code>/Volumes/catalog/schema/volume</code></>
            ) : (
              <>Example: <code>{provider.example}</code></>
            )}
          </span>
        </div>
      </div>

      <div className="storage-primary-fields">
        <label>
          Provider
          <select value={binding.provider} onChange={(event) => setProvider(event.target.value as StorageProvider)}>
            {PROVIDERS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
          </select>
        </label>
        {binding.provider !== "local" ? (
          <label>
            Authentication
            <select
              value={binding.auth_mode}
              onChange={(event) => {
                onChange({
                  ...binding,
                  auth_mode: event.target.value as StorageBinding["auth_mode"],
                  credential_profile_id: null,
                });
                setTestResult(null);
              }}
            >
              {authOptions.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
            </select>
          </label>
        ) : (
          <div className="storage-local-note"><ShieldCheck size={14} /><span>No credentials required</span></div>
        )}
        {binding.provider === "minio" ? (
          <label className="storage-profile-field">
            MinIO endpoint
            <input
              value={String(binding.options.endpoint_url ?? "")}
              onChange={(event) => setOption("endpoint_url", event.target.value)}
              placeholder="https://minio.example.com"
              required
            />
          </label>
        ) : null}
        {profileRequired ? (
          <label className="storage-profile-field">
            Credential Profile
            <select
              value={binding.credential_profile_id ?? ""}
              onChange={(event) => {
                onChange({ ...binding, credential_profile_id: event.target.value || null });
                setTestResult(null);
              }}
            >
              <option value="">Select a {provider.shortLabel} profile…</option>
              {compatibleProfiles.map((profile) => <option value={profile.id} key={profile.id}>{profile.name} · {profile.auth_type.replace(/_/g, " ")}</option>)}
            </select>
            {!compatibleProfiles.length ? <small>No compatible profiles. Add one in Studio Settings.</small> : null}
          </label>
        ) : null}
      </div>

      {showAdvanced ? (
        <details className="storage-advanced">
          <summary><ChevronDown size={13} /><span>Advanced settings</span><small>{advancedSummary(binding)}</small></summary>
          <div className="storage-advanced-grid">
            {binding.provider === "s3" || binding.provider === "minio" ? (
              <label>Region<input value={String(binding.options.region ?? "")} onChange={(event) => setOption("region", event.target.value)} placeholder="Optional" /></label>
            ) : null}
            {binding.provider === "minio" ? (
              <>
                <label>Addressing<select value={String(binding.options.addressing_style ?? "path")} onChange={(event) => setOption("addressing_style", event.target.value)}><option value="path">Path style</option><option value="virtual">Virtual host</option></select></label>
                <label className="storage-checkbox"><input type="checkbox" checked={binding.options.verify_tls !== false} onChange={(event) => setOption("verify_tls", event.target.checked)} /> Verify TLS</label>
              </>
            ) : null}
            {binding.provider === "adls" ? (
              <label>Account name<input value={String(binding.options.account_name ?? "")} onChange={(event) => setOption("account_name", event.target.value)} placeholder="If not encoded in URI" /></label>
            ) : null}
            {binding.provider === "gcs" ? (
              <>
                <label>Project ID<input value={String(binding.options.project_id ?? "")} onChange={(event) => setOption("project_id", event.target.value)} placeholder="Optional" /></label>
                <label>Billing project<input value={String(binding.options.billing_project ?? "")} onChange={(event) => setOption("billing_project", event.target.value)} placeholder="Optional" /></label>
              </>
            ) : null}
            {binding.provider === "dbfs" && binding.auth_mode !== "credential_profile" ? (
              <>
                <label>Workspace host<input value={String(binding.options.host ?? "")} onChange={(event) => setDatabricksOption("host", event.target.value)} placeholder="https://workspace.cloud.databricks.com" /></label>
                <label>Databricks profile<input value={String(binding.options.profile ?? "")} onChange={(event) => setDatabricksOption("profile", event.target.value)} placeholder="Optional profile name" /></label>
              </>
            ) : null}
          </div>
        </details>
      ) : null}

      <div className="storage-test-row">
        <button
          type="button"
          className="storage-test-button"
          disabled={
            testing
            || !uri.trim()
            || (profileRequired && !binding.credential_profile_id)
            || (binding.provider === "minio" && !binding.options.endpoint_url)
          }
          onClick={() => void testConnection()}
        >
          {testing ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />}
          {testing ? "Testing…" : "Test connection"}
        </button>
        {testResult ? (
          <div className={`storage-test-result ${testResult.tone}`} role={testResult.tone === "error" ? "alert" : "status"}>
            {testResult.tone === "success" ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            <div><strong>{testResult.message}</strong>{testResult.detail ? <span>{testResult.detail}</span> : null}</div>
          </div>
        ) : <span className="storage-test-hint">Read-only check; no files are changed.</span>}
      </div>
    </fieldset>
  );
}

export function oneLakePathIssue(uri: string): string | null {
  let decoded = uri.trim();
  try {
    decoded = decodeURIComponent(decoded);
  } catch {
    return null;
  }
  if (/(?:^|\/)Tables(?:\/|$)/i.test(decoded)) {
    return "Use a Lakehouse Files path; OneLake Tables is not supported.";
  }
  return null;
}

function advancedSummary(binding: StorageBinding) {
  const configured = Object.values(binding.options).filter((value) => value !== "" && value !== undefined).length;
  return configured ? `${configured} configured` : "Optional";
}
