export const lifecycleStatuses = ["succeeded", "failed", "skipped", "running", "pending"] as const;

export type LifecycleStatus = typeof lifecycleStatuses[number];

type LifecycleStatusPresentation = {
  chartColor: string;
  textColor: string;
  pillBackground: string;
  drawerSurface: string;
  drawerBorder: string;
};

export const lifecycleStatusPresentations: Record<LifecycleStatus, LifecycleStatusPresentation> = {
  succeeded: {
    chartColor: "#2f8f72",
    textColor: "#17735c",
    pillBackground: "#e5f5ef",
    drawerSurface: "#f4fbf8",
    drawerBorder: "#d7ebe4",
  },
  failed: {
    chartColor: "#c94a4f",
    textColor: "#b92d32",
    pillBackground: "#fde7e8",
    drawerSurface: "#fff6f6",
    drawerBorder: "#f3d6d8",
  },
  skipped: {
    chartColor: "#d89b42",
    textColor: "#996312",
    pillBackground: "#fff1dc",
    drawerSurface: "#fffaf0",
    drawerBorder: "#f0dfbd",
  },
  running: {
    chartColor: "#4e78bb",
    textColor: "#335fbc",
    pillBackground: "#e8efff",
    drawerSurface: "#f4f7ff",
    drawerBorder: "#dbe4f7",
  },
  pending: {
    chartColor: "#8a6fd1",
    textColor: "#7055b5",
    pillBackground: "#f0ebff",
    drawerSurface: "#f8f5ff",
    drawerBorder: "#e4dcf7",
  },
};

export function lifecycleStatusPresentation(value: unknown) {
  const status = String(value ?? "").trim().toLowerCase() as LifecycleStatus;
  return lifecycleStatuses.includes(status) ? lifecycleStatusPresentations[status] : null;
}

export function lifecycleStatusFromField(field: string | undefined): LifecycleStatus | null {
  const normalized = String(field ?? "").toLowerCase();
  return lifecycleStatuses.find((status) => normalized.includes(status)) ?? null;
}
