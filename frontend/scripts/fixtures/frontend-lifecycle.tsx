import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { metadataEditorWorkspaceOptions, useEnvironmentMetadataEditor } from "../../src/features/metadata-explorer/metadataEditorQueries";
import { installEnvironmentVersionReconciliation, useEnvironmentContextQuery } from "../../src/features/environments/environmentQueries";
import { useMetadataDataflowEditor } from "../../src/shared/metadata/useMetadataDataflowEditor";
import { useStudioRouter } from "../../src/app/useStudioRouter";
import { filtersFromSearch, filtersToSearch } from "../../src/features/monitoring/monitoringFilters";
import { useProjectReferenceRegistry } from "../../src/features/projects/useProjectReferenceRegistry";
import { createProjectMutations } from "../../src/features/projects/projectMutations";
import { projectRegistryOptions } from "../../src/features/projects/projectQueries";
import { useStudioWorkspace } from "../../src/app/useStudioWorkspace";
import { MetadataEditorLoadingDrawer } from "../../src/features/dataflows/MetadataEditorLoadingDrawer";
import { App } from "../../src/App";
import { NotificationCenterProvider } from "../../src/shared/notifications/NotificationCenter";
import { useLineageGraph } from "../../src/features/lineage/lineageQueries";
import { LineageView } from "../../src/features/lineage/LineageView";
import "@xyflow/react/dist/style.css";
import "../../src/styles/tokens.css";
import "../../src/styles.css";
import "../../src/styles/components.css";

const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
let editor: ReturnType<typeof useEnvironmentMetadataEditor>;
let setEnabled: (value: boolean) => void;
let followUp: () => Promise<void> = async () => {};
let controller: ReturnType<typeof useMetadataDataflowEditor>;
let router: ReturnType<typeof useStudioRouter>;
let registry: ReturnType<typeof useProjectReferenceRegistry>;
let registryMutation: ReturnType<typeof createProjectMutations>;
let setProject: (id: number) => void;
let setSelection: (value: object | null) => void;
let setEnvironment: (id: number | null) => void;
let install: () => void;
const mode = new URLSearchParams(location.search).get("fixture");
let stopReconciliation: (() => void) | undefined;

function EnvironmentFixture() {
  const [id, updateId] = useState<number | null>(null);
  setEnvironment = updateId;
  useEnvironmentContextQuery(id, 100_000, id);
  return null;
}
function RouterFixture() {
  router = useStudioRouter();
  return <><output data-testid="route">{JSON.stringify(router.route)}</output><output data-testid="status">{filtersFromSearch(router.route.search ?? "").status}</output></>;
}
function RegistryFixture() {
  const [id, updateId] = useState(3);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  setProject = updateId;
  registry = useProjectReferenceRegistry(id, true);
  registryMutation = createProjectMutations({ projectId: id, environmentId: null, queryClient: client, setStudioRoute: () => {}, setBusy, setError, clearEnvironmentSources: () => {}, isActiveProject: projectId => projectId === window.lifecycle.projectId });
  Object.assign(window.lifecycle, { projectId: id });
  return <><output data-testid="registry">{JSON.stringify(registry.data)}</output><output data-testid="error">{error}</output><output data-testid="busy">{String(busy)}</output></>;
}
function WorkspaceFixture() {
  router = useStudioRouter();
  const workspace = useStudioWorkspace(router);
  return <output data-testid="workspace">{String(workspace.loading)}</output>;
}
function GraphFixture() {
  useEnvironmentContextQuery(7, 100_000);
  const graph = useLineageGraph(7);
  useEffect(() => installEnvironmentVersionReconciliation(client), []);
  return <output data-testid="graph">{graph.data?.assets[0]?.label}</output>;
}

function Fixture() {
  const [enabled, updateEnabled] = useState(false);
  setEnabled = updateEnabled;
  editor = useEnvironmentMetadataEditor(7, { enabled, onCatalogChanged: () => followUp() });
  const [selection, updateSelection] = useState<object | null>(null);
  setSelection = updateSelection;
  controller = useMetadataDataflowEditor({ environmentId: 7, sourceDocument: editor.workspace?.document ?? null, draftDocument: editor.workspace?.draft ?? null, selection, onEnsureEditor: async () => { await editor.ensureContext(); }, onSaveDraft: editor.saveDraft, onPrepareSave: editor.prepareDocumentWrite, onApplySave: editor.applyPreparedDocumentWrite, onAbandonSave: editor.abandonDocumentWrite });
  useEffect(() => { install = () => { stopReconciliation?.(); stopReconciliation = installEnvironmentVersionReconciliation(client); }; }, []);
  return <><output data-testid="busy">{String(editor.busy)}</output><output data-testid="record">{controller.selectedRecord?.row.name ?? ""}</output>
    {selection && !controller.selectedRecord ? <MetadataEditorLoadingDrawer loading={controller.loading} error={controller.loadError} onRetry={controller.retryEditor} onClose={() => updateSelection(null)} /> : null}
  </>;
}

const root = createRoot(document.getElementById("root")!);
root.render(<React.StrictMode><QueryClientProvider client={client}><NotificationCenterProvider>{mode === "app" ? <App /> : mode === "graph" ? <GraphFixture /> : mode === "router" ? <RouterFixture /> : mode === "registry" ? <RegistryFixture /> : mode === "workspace" ? <WorkspaceFixture /> : <><Fixture /><EnvironmentFixture /></>}</NotificationCenterProvider></QueryClientProvider></React.StrictMode>);

Object.assign(window, {
  lifecycle: {
    client,
    get editor() { return editor; },
    setEnabled(value: boolean) { setEnabled(value); },
    get controller() { return controller; },
    get router() { return router; },
    get registry() { return registry; },
    get registryMutation() { return registryMutation; },
    LineageView,
    setProject(id: number) { setProject(id); },
    setSelection(value: object | null) { setSelection(value); },
    setEnvironment(id: number | null) { setEnvironment(id); },
    installReconciliation() { install(); },
    replaceFilters(filters: object) { router.replaceSearch(filtersToSearch({ ...filtersFromSearch(router.route.search ?? ""), ...filters }, router.route.search)); },
    readRegistry(id: number) { return client.fetchQuery({ ...projectRegistryOptions(id), staleTime: 0 }); },
    setFollowUp(callback: () => Promise<void>) { followUp = callback; },
    readWorkspace() { return client.fetchQuery({ ...metadataEditorWorkspaceOptions(7), staleTime: 0, retry: false }); },
    workspaceKey: metadataEditorWorkspaceOptions(7).queryKey,
    dispose() { root.unmount(); stopReconciliation?.(); client.clear(); },
  },
});
