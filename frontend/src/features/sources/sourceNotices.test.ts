import { describe, expect, it } from "vitest";

import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import type { SourcePath } from "../../shared/api/domainTypes";
import {
  batchNotice,
  logSyncNotice,
  toBatchEntries,
} from "./sourceNotices";

describe("source notices", () => {
  it("maps source entries to typed batch inputs", () => {
    expect(toBatchEntries([
      { kind: "metadata", source: { id: 7 } as SourcePath },
      { kind: "sql", source: { id: 8 } as SourcePath },
    ])).toEqual([
      { kind: "metadata", id: 7 },
      { kind: "sql", id: 8 },
    ]);
  });

  it("summarizes mixed batch outcomes", () => {
    expect(batchNotice("sync", {
      total: 3,
      succeeded: 1,
      warnings: 1,
      failed: 1,
      errors: ["SQL source failed"],
    }, "Incremental")).toMatchObject({
      tone: "warning",
      title: "Sync completed with issues",
      detail: "Incremental · 2/3 sources processed · 1 synced · 1 completed with warning · 1 failed",
      errors: ["SQL source failed"],
    });
  });

  it("reads log result counts from nested sync execution", () => {
    const status = {
      status: "ok",
      message: "Log refresh completed",
      sync_execution: {
        result: {
          candidate_files: 4,
          replaced_files: 2,
          inserted_rows: 10,
          unchanged_files: 1,
        },
      },
    } as unknown as SourceSyncStatus;

    expect(logSyncNotice(status, { mode: "incremental" })).toMatchObject({
      tone: "success",
      title: "Log sync complete",
      detail: "Incremental · 4 candidate files · 2 replaced · 10 rows · 1 unchanged · Log refresh completed",
    });
  });
});
