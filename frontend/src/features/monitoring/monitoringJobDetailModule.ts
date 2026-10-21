import { lazy } from "react";

let jobDetailsPromise: Promise<typeof import("./details/JobDetails")> | undefined;

function loadJobDetails() {
  return jobDetailsPromise ??= import("./details/JobDetails").catch(error => {
    jobDetailsPromise = undefined;
    throw error;
  });
}

export const JobDetailSections = lazy(() => loadJobDetails().then(module => ({ default: module.JobDetailSections })));

export function preloadJobDetailSections() {
  void loadJobDetails().catch(() => undefined);
}
