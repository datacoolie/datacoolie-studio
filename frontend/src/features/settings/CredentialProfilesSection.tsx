import {
  CheckCircle2,
  KeyRound,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../../shared/api/client";
import type {
  CredentialCapabilities,
  CredentialProfile,
  CredentialProfileDetail,
  StorageProvider,
} from "../../shared/api/domainTypes";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { toErrorMessage } from "../../shared/lib/errors";
import { OperationConfirmationDialog } from "../../shared/components/OperationConfirmationDialog";
import { StorageProviderIcon } from "../../shared/components/StorageProviderIcon";

type CloudProvider = Exclude<StorageProvider, "local">;
type FieldSpec = { key: string; label: string; optional?: boolean; placeholder?: string };

const PROVIDERS: Array<{ value: CloudProvider; label: string; detail: string }> = [
  { value: "s3", label: "Amazon S3", detail: "AWS buckets" },
  { value: "minio", label: "MinIO", detail: "S3-compatible storage" },
  { value: "adls", label: "ADLS Gen2", detail: "Azure data lake" },
  { value: "onelake", label: "Microsoft OneLake", detail: "Fabric Lakehouse Files" },
  { value: "gcs", label: "Google Cloud", detail: "Cloud Storage buckets" },
  { value: "dbfs", label: "Databricks", detail: "DBFS and Unity Catalog Volumes" },
];

const CONFIG_FIELDS: Record<string, FieldSpec[]> = {
  "s3/aws_shared_profile": [{ key: "profile_name", label: "AWS profile name", placeholder: "default" }],
  "s3/access_key": [{ key: "access_key_id", label: "Access key ID" }],
  "minio/access_key": [{ key: "access_key_id", label: "Access key ID" }],
  "adls/service_principal": [
    { key: "tenant_id", label: "Tenant ID" },
    { key: "client_id", label: "Client ID" },
    { key: "account_name", label: "Storage account" },
  ],
  "adls/sas": [{ key: "account_name", label: "Storage account" }],
  "adls/account_key": [{ key: "account_name", label: "Storage account" }],
  "onelake/service_principal": [
    { key: "tenant_id", label: "Tenant ID" },
    { key: "client_id", label: "Client ID" },
  ],
  "dbfs/databricks_profile": [
    { key: "profile", label: "Databricks profile", placeholder: "DEFAULT" },
    { key: "host", label: "Workspace host", optional: true, placeholder: "https://workspace.cloud.databricks.com" },
  ],
  "dbfs/pat": [
    { key: "host", label: "Workspace host", placeholder: "https://workspace.cloud.databricks.com" },
  ],
  "dbfs/oauth_m2m": [
    { key: "host", label: "Workspace host", placeholder: "https://workspace.cloud.databricks.com" },
    { key: "client_id", label: "Client ID" },
  ],
};

const SECRET_FIELDS: Record<string, FieldSpec[]> = {
  "s3/access_key": [
    { key: "secret_access_key", label: "Secret access key" },
    { key: "session_token", label: "Session token", optional: true },
  ],
  "minio/access_key": [
    { key: "secret_access_key", label: "Secret access key" },
    { key: "session_token", label: "Session token", optional: true },
  ],
  "adls/service_principal": [{ key: "client_secret", label: "Client secret" }],
  "adls/sas": [{ key: "sas_token", label: "SAS token" }],
  "adls/account_key": [{ key: "account_key", label: "Account key" }],
  "onelake/service_principal": [{ key: "client_secret", label: "Client secret" }],
  "dbfs/pat": [{ key: "token", label: "Personal access token" }],
  "dbfs/oauth_m2m": [{ key: "client_secret", label: "Client secret" }],
};

export function CredentialProfilesSection() {
  const [profiles, setProfiles] = useState<CredentialProfile[]>([]);
  const [capabilities, setCapabilities] = useState<CredentialCapabilities | null>(null);
  const [drawer, setDrawer] = useState<"create" | "edit" | null>(null);
  const [provider, setProvider] = useState<CloudProvider>("s3");
  const [authType, setAuthType] = useState("access_key");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<CredentialProfileDetail | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [replaceSecret, setReplaceSecret] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<CredentialProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; message: string } | null>(null);

  async function load() {
    const [nextProfiles, nextCapabilities] = await Promise.all([
      api.listCredentialProfiles(),
      api.getCredentialCapabilities(),
    ]);
    setProfiles(nextProfiles);
    setCapabilities(nextCapabilities);
  }

  useEffect(() => {
    void load().catch((error) => setFeedback({ tone: "error", message: toErrorMessage(error) }));
  }, []);

  const authTypes = useMemo(
    () => capabilities?.providers[provider] ?? [],
    [capabilities, provider],
  );
  useEffect(() => {
    if (authTypes.length && !authTypes.includes(authType)) {
      setAuthType(authTypes[0]);
      setValues({});
    }
  }, [authType, authTypes]);

  const key = `${provider}/${authType}`;
  const configFields = CONFIG_FIELDS[key] ?? [];
  const secretFields = SECRET_FIELDS[key] ?? [];
  const requiresSecret = secretFields.some((field) => !field.optional) || key === "gcs/service_account";
  const canSubmit = Boolean(
    name.trim()
    && configFields.filter((field) => !field.optional).every((field) => values[field.key]?.trim())
    && (!requiresSecret || (
      key === "gcs/service_account"
        ? values.service_account_json?.trim()
        : secretFields.filter((field) => !field.optional).every((field) => values[field.key]?.trim())
    )),
  );

  function closeDrawer(force = false) {
    if (busy && !force) return;
    setDrawer(null);
    setEditing(null);
    setDrawerLoading(false);
    setReplaceSecret(false);
    setName("");
    setValues({});
  }

  useDrawerEscape(closeDrawer, drawer !== null);

  function openCreate() {
    setFeedback(null);
    setEditing(null);
    setName("");
    setValues({});
    setDrawer("create");
  }

  async function openEdit(profile: CredentialProfile) {
    setFeedback(null);
    setDrawer("edit");
    setDrawerLoading(true);
    setReplaceSecret(false);
    setName(profile.name);
    setValues({});
    try {
      const detail = await api.getCredentialProfile(profile.id);
      setEditing(detail);
      setProvider(detail.provider);
      setAuthType(detail.auth_type);
      setName(detail.name);
      setValues(
        Object.fromEntries(
          Object.entries(detail.config).map(([field, value]) => [field, String(value ?? "")]),
        ),
      );
    } catch (error) {
      closeDrawer(true);
      setFeedback({ tone: "error", message: toErrorMessage(error) });
    } finally {
      setDrawerLoading(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setFeedback(null);
    try {
      const config = Object.fromEntries(
        configFields
          .filter((field) => values[field.key]?.trim())
          .map((field) => [field.key, values[field.key].trim()]),
      );
      const secret = key === "gcs/service_account"
        ? { service_account_json: values.service_account_json }
        : Object.fromEntries(
            secretFields
              .filter((field) => values[field.key]?.trim())
              .map((field) => [field.key, values[field.key].trim()]),
          );
      await api.createCredentialProfile({
        name: name.trim(),
        provider,
        auth_type: authType,
        config,
        ...(Object.keys(secret).length ? { secret } : {}),
      });
      closeDrawer(true);
      setFeedback({ tone: "success", message: "Credential profile created. Secret values remain write-only." });
      await load();
    } catch (error) {
      setFeedback({ tone: "error", message: toErrorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function editProfile(event: FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const editKey = `${editing.provider}/${editing.auth_type}`;
    const editConfigFields = CONFIG_FIELDS[editKey] ?? [];
    const editSecretFields = SECRET_FIELDS[editKey] ?? [];
    const config = Object.fromEntries(
      editConfigFields
        .filter((field) => values[field.key]?.trim())
        .map((field) => [field.key, values[field.key].trim()]),
    );
    const nameChanged = name.trim() !== editing.name;
    const configChanged = editableConfigChanged(
      editConfigFields, values, editing.config,
    );
    if (!nameChanged && !configChanged && !replaceSecret) return;
    const secret = editKey === "gcs/service_account"
      ? { service_account_json: values.service_account_json }
      : Object.fromEntries(
          editSecretFields
            .filter((field) => values[field.key]?.trim())
            .map((field) => [field.key, values[field.key].trim()]),
        );
    setBusy(true);
    setFeedback(null);
    try {
      await api.updateCredentialProfile(editing.id, {
        ...(nameChanged ? { name: name.trim() } : {}),
        ...(configChanged ? { config } : {}),
        ...(replaceSecret ? { secret } : {}),
      });
      closeDrawer(true);
      setFeedback({
        tone: "success",
        message: replaceSecret
          ? "Credential profile updated and secret replaced."
          : editing.secret_state === "present"
            ? "Credential profile updated. Existing secret was preserved."
            : "Credential profile updated. Its secret still needs replacement.",
      });
      await load();
    } catch (error) {
      setFeedback({ tone: "error", message: toErrorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function remove(profile: CredentialProfile) {
    setBusy(true);
    setFeedback(null);
    try {
      await api.deleteCredentialProfile(profile.id);
      setPendingDelete(null);
      setFeedback({ tone: "success", message: `${profile.name} deleted.` });
      await load();
    } catch (error) {
      setFeedback({ tone: "error", message: toErrorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-section credential-profiles-section" aria-labelledby="credential-profiles-heading">
      <div className="settings-section-heading">
        <div className="settings-section-heading-copy">
          <h3 id="credential-profiles-heading">Credential Profiles</h3>
          <span>Reusable identities for cloud and Databricks sources.</span>
        </div>
        <button type="button" className="settings-section-action" onClick={openCreate}>
          <Plus size={14} /> Add profile
        </button>
      </div>

      <div className={`credential-store-status ${capabilities?.secret_store_available ? "available" : "unavailable"}`}>
        {capabilities?.secret_store_available ? <ShieldCheck size={15} /> : <KeyRound size={15} />}
        <div>
          <strong>{capabilities?.secret_store_available ? "Protected by OS credential storage" : "Credential storage unavailable"}</strong>
          <span>
            {capabilities?.secret_store_available
              ? `Secrets are write-only · ${capabilities.secret_store_backend}`
              : capabilities?.remediation ?? "Checking credential storage…"}
          </span>
        </div>
      </div>

      {feedback ? (
        <div className={`credential-feedback ${feedback.tone}`} role={feedback.tone === "error" ? "alert" : "status"}>
          {feedback.tone === "success" ? <CheckCircle2 size={14} /> : null}
          <span>{feedback.message}</span>
        </div>
      ) : null}

      <div className="credential-profile-list">
        {profiles.map((profile) => {
          const providerInfo = PROVIDERS.find((item) => item.value === profile.provider);
          return (
            <article className="credential-profile-row" key={profile.id}>
              <div className={`credential-provider-mark provider-${profile.provider}`} aria-hidden="true">
                <StorageProviderIcon provider={profile.provider} />
              </div>
              <div className="credential-profile-identity">
                <strong>{profile.name}</strong>
                <span>{summaryText(profile)}</span>
              </div>
              <div className="credential-profile-badges">
                <span>{providerInfo?.label ?? profile.provider.toUpperCase()}</span>
                <span>{humanize(profile.auth_type)}</span>
              </div>
              <div className="credential-profile-usage">
                <strong>{profile.reference_count}</strong>
                <span>{profile.reference_count === 1 ? "source" : "sources"}</span>
              </div>
              <div className="credential-profile-state">
                <span className={`credential-secret-state is-${profile.secret_state}`}>{humanize(profile.secret_state)}</span>
                <small>v{profile.version}</small>
              </div>
              <div className="credential-profile-actions">
                <button type="button" disabled={busy} onClick={() => void openEdit(profile)}>
                  <Pencil size={13} /> Edit
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={busy || profile.reference_count > 0}
                  title={profile.reference_count > 0 ? "Remove source references before deleting" : undefined}
                  onClick={() => setPendingDelete(profile)}
                >
                  <Trash2 size={13} /> Delete
                </button>
              </div>
            </article>
          );
        })}
        {!profiles.length ? (
          <div className="credential-empty-state">
            <div><KeyRound size={17} /></div>
            <strong>No saved credentials</strong>
            <span>Ambient provider authentication remains available in Sources.</span>
            <button type="button" onClick={openCreate}>Add your first profile</button>
          </div>
        ) : null}
      </div>

      {drawer ? (
        <div className="metadata-drawer-backdrop" onMouseDown={() => closeDrawer()}>
          <aside
            className="metadata-drawer credential-profile-drawer"
            aria-label={drawer === "create" ? "Add credential profile" : "Edit credential profile"}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="metadata-drawer-header">
              <div>
                <span className="eyebrow">{drawer === "create" ? "Cloud identity" : "Profile configuration"}</span>
                <h2>{drawer === "create" ? "Add credential profile" : `Edit ${editing?.name ?? name}`}</h2>
                <small>
                  {drawer === "create"
                    ? "Create once, then select this identity from any compatible Source."
                    : "Update visible profile fields. Existing secrets stay masked unless you replace them."}
                </small>
              </div>
              <button className="icon-action small" type="button" onClick={() => closeDrawer()} aria-label="Close credential profile drawer">
                <X size={16} />
              </button>
            </header>
            <div className="metadata-drawer-body credential-profile-drawer-body">
              {drawer === "create" ? (
                <CredentialForm
                  authType={authType}
                  authTypes={authTypes}
                  busy={busy}
                  canSubmit={canSubmit}
                  capabilities={capabilities}
                  configFields={configFields}
                  keyName={key}
                  name={name}
                  onAuthTypeChange={(value) => { setAuthType(value); setValues({}); }}
                  onCancel={closeDrawer}
                  onNameChange={setName}
                  onProviderChange={(value) => { setProvider(value); setValues({}); }}
                  onSubmit={submit}
                  onValueChange={(field, value) => setValues((current) => ({ ...current, [field]: value }))}
                  provider={provider}
                  secretFields={secretFields}
                  values={values}
                />
              ) : drawerLoading || !editing ? (
                <div className="credential-drawer-loading" role="status">Loading profile…</div>
              ) : (
                <EditCredentialForm
                  busy={busy}
                  configFields={CONFIG_FIELDS[`${editing.provider}/${editing.auth_type}`] ?? []}
                  name={name}
                  onCancel={closeDrawer}
                  onNameChange={setName}
                  onReplaceSecretChange={(enabled) => {
                    setReplaceSecret(enabled);
                    if (!enabled) {
                      const configKeys = new Set(
                        (CONFIG_FIELDS[`${editing.provider}/${editing.auth_type}`] ?? []).map((field) => field.key),
                      );
                      setValues((current) => Object.fromEntries(
                        Object.entries(current).filter(([field]) => configKeys.has(field)),
                      ));
                    }
                  }}
                  onSubmit={editProfile}
                  onValueChange={(field, value) => setValues((current) => ({ ...current, [field]: value }))}
                  profile={editing}
                  replaceSecret={replaceSecret}
                  secretFields={SECRET_FIELDS[`${editing.provider}/${editing.auth_type}`] ?? []}
                  secretStoreAvailable={capabilities?.secret_store_available !== false}
                  values={values}
                />
              )}
            </div>
          </aside>
        </div>
      ) : null}
      {pendingDelete ? (
        <OperationConfirmationDialog
          title={`Delete ${pendingDelete.name}?`}
          description="This removes the profile configuration and its protected OS credential entry."
          icon={<Trash2 size={18} />}
          confirmIcon={<Trash2 size={14} />}
          confirmLabel={busy ? "Deleting…" : "Delete profile"}
          tone="danger"
          busy={busy}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void remove(pendingDelete)}
        >
          <p>Source files are not changed. A profile referenced by a Source cannot be deleted.</p>
        </OperationConfirmationDialog>
      ) : null}
    </section>
  );
}

function CredentialForm({
  authType,
  authTypes,
  busy,
  canSubmit,
  capabilities,
  configFields,
  keyName,
  name,
  onAuthTypeChange,
  onCancel,
  onNameChange,
  onProviderChange,
  onSubmit,
  onValueChange,
  provider,
  secretFields,
  values,
}: {
  authType: string;
  authTypes: string[];
  busy: boolean;
  canSubmit: boolean;
  capabilities: CredentialCapabilities | null;
  configFields: FieldSpec[];
  keyName: string;
  name: string;
  onAuthTypeChange: (value: string) => void;
  onCancel: () => void;
  onNameChange: (value: string) => void;
  onProviderChange: (value: CloudProvider) => void;
  onSubmit: (event: FormEvent) => void;
  onValueChange: (field: string, value: string) => void;
  provider: CloudProvider;
  secretFields: FieldSpec[];
  values: Record<string, string>;
}) {
  return (
    <form className="credential-drawer-form" onSubmit={onSubmit}>
      <section>
        <div className="credential-form-section-heading"><span>1</span><div><strong>Profile</strong><small>A recognizable name and provider.</small></div></div>
        <label>Profile name<input autoFocus value={name} onChange={(event) => onNameChange(event.target.value)} placeholder="e.g. Production Databricks" /></label>
        <div className="credential-form-grid">
          <label>Provider<select value={provider} onChange={(event) => onProviderChange(event.target.value as CloudProvider)}>
            {PROVIDERS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
          </select></label>
          <label>Authentication<select value={authType} onChange={(event) => onAuthTypeChange(event.target.value)}>
            {authTypes.map((item) => <option value={item} key={item}>{humanize(item)}</option>)}
          </select></label>
        </div>
        <p className="credential-provider-detail">{PROVIDERS.find((item) => item.value === provider)?.detail}</p>
      </section>
      <section>
        <div className="credential-form-section-heading"><span>2</span><div><strong>Connection identity</strong><small>Non-secret values stay visible for troubleshooting.</small></div></div>
        <div className="credential-form-grid">
          {configFields.map((field) => (
            <SecretField
              key={field.key}
              label={`${field.label}${field.optional ? " (optional)" : ""}`}
              value={values[field.key] ?? ""}
              onChange={(value) => onValueChange(field.key, value)}
              placeholder={field.placeholder}
              secret={false}
            />
          ))}
          {!configFields.length ? <p className="credential-form-note">No additional identity fields are required.</p> : null}
        </div>
      </section>
      <section>
        <div className="credential-form-section-heading"><span>3</span><div><strong>Secret</strong><small>Stored by the operating system and never returned by the API.</small></div></div>
        {keyName === "gcs/service_account" ? (
          <label>Service-account JSON<textarea value={values.service_account_json ?? ""} onChange={(event) => onValueChange("service_account_json", event.target.value)} rows={7} spellCheck={false} /></label>
        ) : (
          <div className="credential-form-grid">
            {secretFields.map((field) => (
              <SecretField
                key={field.key}
                label={`${field.label}${field.optional ? " (optional)" : ""}`}
                value={values[field.key] ?? ""}
                onChange={(value) => onValueChange(field.key, value)}
                secret
              />
            ))}
            {!secretFields.length ? <p className="credential-form-note"><ShieldCheck size={14} /> This authentication method uses your local provider configuration and stores no secret.</p> : null}
          </div>
        )}
      </section>
      <div className="credential-drawer-actions">
        <button type="submit" disabled={busy || !canSubmit || (secretFields.length > 0 && capabilities?.secret_store_available === false)}>
          {busy ? "Creating…" : "Create profile"}
        </button>
        <button type="button" className="text-action" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

function EditCredentialForm({
  busy,
  configFields,
  name,
  onCancel,
  onNameChange,
  onReplaceSecretChange,
  onSubmit,
  onValueChange,
  profile,
  replaceSecret,
  secretFields,
  secretStoreAvailable,
  values,
}: {
  busy: boolean;
  configFields: FieldSpec[];
  name: string;
  onCancel: () => void;
  onNameChange: (value: string) => void;
  onReplaceSecretChange: (enabled: boolean) => void;
  onSubmit: (event: FormEvent) => void;
  onValueChange: (field: string, value: string) => void;
  profile: CredentialProfileDetail;
  replaceSecret: boolean;
  secretFields: FieldSpec[];
  secretStoreAvailable: boolean;
  values: Record<string, string>;
}) {
  const editKey = `${profile.provider}/${profile.auth_type}`;
  const hasStoredSecret = editKey === "gcs/service_account" || secretFields.length > 0;
  const configReady = configFields
    .filter((field) => !field.optional)
    .every((field) => values[field.key]?.trim());
  const replacementReady = !replaceSecret || (
    editKey === "gcs/service_account"
      ? Boolean(values.service_account_json?.trim())
      : secretFields
          .filter((field) => !field.optional)
          .every((field) => values[field.key]?.trim())
  );
  const changed = name.trim() !== profile.name
    || editableConfigChanged(configFields, values, profile.config)
    || replaceSecret;
  const ready = Boolean(name.trim() && configReady && replacementReady && changed);
  return (
    <form className="credential-drawer-form" onSubmit={onSubmit}>
      <div className="credential-rotate-summary">
        <span className={`credential-provider-mark provider-${profile.provider}`} aria-hidden="true">
          <StorageProviderIcon provider={profile.provider} />
        </span>
        <div>
          <strong>{PROVIDERS.find((item) => item.value === profile.provider)?.label ?? profile.provider.toUpperCase()}</strong>
          <span>{humanize(profile.auth_type)} · {profile.reference_count} referencing source{profile.reference_count === 1 ? "" : "s"}</span>
        </div>
      </div>
      <section>
        <div className="credential-form-section-heading"><span>1</span><div><strong>Profile</strong><small>Provider and authentication type stay fixed for referenced Sources.</small></div></div>
        <label>Profile name<input autoFocus value={name} onChange={(event) => onNameChange(event.target.value)} /></label>
      </section>
      <section>
        <div className="credential-form-section-heading"><span>2</span><div><strong>Connection identity</strong><small>These non-secret values remain editable.</small></div></div>
        <div className="credential-form-grid">
          {configFields.map((field) => (
            <SecretField
              key={field.key}
              label={`${field.label}${field.optional ? " (optional)" : ""}`}
              value={values[field.key] ?? ""}
              onChange={(value) => onValueChange(field.key, value)}
              placeholder={field.placeholder}
              secret={false}
            />
          ))}
          {!configFields.length ? <p className="credential-form-note">No non-secret connection fields are required.</p> : null}
        </div>
      </section>
      <section>
        <div className="credential-form-section-heading"><span>3</span><div><strong>Secret</strong><small>The current value can never be viewed or copied.</small></div></div>
        {hasStoredSecret ? (
          <>
            <div className={`credential-masked-secret is-${profile.secret_state}`}>
              <KeyRound size={15} />
              <div>
                <strong>••••••••</strong>
                <span>{humanize(profile.secret_state)} · stored by the operating system</span>
              </div>
              <button
                type="button"
                disabled={busy || !secretStoreAvailable}
                onClick={() => onReplaceSecretChange(!replaceSecret)}
              >
                {replaceSecret ? "Keep existing" : profile.secret_state === "present" ? "Replace secret" : "Add replacement"}
              </button>
            </div>
            {replaceSecret ? (
              <div className="credential-secret-replacement">
                <p>Enter a complete replacement. Blank fields never reuse or reveal the current secret.</p>
                {editKey === "gcs/service_account" ? (
                  <label>New service-account JSON<textarea value={values.service_account_json ?? ""} onChange={(event) => onValueChange("service_account_json", event.target.value)} rows={8} spellCheck={false} /></label>
                ) : (
                  <div className="credential-form-grid">
                    {secretFields.map((field) => (
                      <SecretField
                        key={field.key}
                        label={`New ${field.label.toLowerCase()}${field.optional ? " (optional)" : ""}`}
                        value={values[field.key] ?? ""}
                        onChange={(value) => onValueChange(field.key, value)}
                        secret
                      />
                    ))}
                  </div>
                )}
              </div>
            ) : null}
          </>
        ) : (
          <p className="credential-form-note"><ShieldCheck size={14} /> This authentication method stores no secret.</p>
        )}
      </section>
      <div className="credential-drawer-actions">
        <button type="submit" disabled={busy || !ready}>{busy ? "Saving…" : "Save changes"}</button>
        <button type="button" className="text-action" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

function SecretField({
  autoFocus = false,
  label,
  onChange,
  placeholder,
  secret,
  value,
}: {
  autoFocus?: boolean;
  label: string;
  onChange: (value: string) => void;
  placeholder?: string;
  secret: boolean;
  value: string;
}) {
  return (
    <label>
      {label}
      <input
        autoFocus={autoFocus}
        type={secret ? "password" : "text"}
        value={value}
        autoComplete="off"
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function humanize(value: string) {
  const special: Record<string, string> = {
    oauth_m2m: "OAuth M2M",
    pat: "PAT",
    aws_shared_profile: "AWS shared profile",
  };
  if (special[value]) return special[value];
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function editableConfigChanged(
  fields: FieldSpec[],
  values: Record<string, string>,
  original: Record<string, unknown>,
) {
  return fields.some((field) => (
    (values[field.key] ?? "").trim() !== String(original[field.key] ?? "").trim()
  ));
}

function summaryText(profile: CredentialProfile) {
  const ignored = new Set(["provider", "auth_type", "secret_fields"]);
  const entry = Object.entries(profile.masked_summary).find(([key]) => !ignored.has(key));
  if (!entry) return profile.secret_state === "present" ? "Secret protected" : "No stored secret";
  return `${humanize(entry[0])}: ${String(entry[1])}`;
}
