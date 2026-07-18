export interface ProjectReferenceMappingsLoadRequest {
  id: number;
  projectId: number | null;
  projectChanged: boolean;
  ownsBusy: boolean;
}

/**
 * Keeps the project-wide mapping cache from accepting an older request after a
 * project switch or a newer refresh. The workspace hook owns state updates;
 * this small stateful guard makes that ordering rule explicit and testable.
 */
export function createProjectReferenceMappingsLoadGuard() {
  let latestRequestId = 0;
  let latestProjectId: number | null = null;
  let busyRequestId: number | null = null;

  function begin(projectId: number | null, showBusy = false): ProjectReferenceMappingsLoadRequest {
    // A quiet refresh inherits a pending mapping load's busy state. This lets
    // the latest request clear it after the earlier request is invalidated.
    const ownsBusy = showBusy || busyRequestId !== null;
    const request = {
      id: ++latestRequestId,
      projectId,
      projectChanged: latestProjectId !== projectId,
      ownsBusy,
    };
    latestProjectId = projectId;
    if (ownsBusy) busyRequestId = request.id;
    return request;
  }

  function isCurrent(request: ProjectReferenceMappingsLoadRequest) {
    return request.id === latestRequestId && request.projectId === latestProjectId;
  }

  function finish(request: ProjectReferenceMappingsLoadRequest) {
    if (busyRequestId !== request.id) return false;
    busyRequestId = null;
    return true;
  }

  return { begin, isCurrent, finish };
}
