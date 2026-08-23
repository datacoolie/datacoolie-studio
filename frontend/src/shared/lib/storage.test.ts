import { describe, expect, it } from "vitest";
import { displayStorageUri } from "./storage";

describe("displayStorageUri", () => {
  it("uses the preferred secure ADLS form", () => {
    expect(displayStorageUri("abfs://container@account.dfs.core.windows.net/project/file.json"))
      .toBe("abfss://container@account.dfs.core.windows.net/project/file.json");
    expect(displayStorageUri("abfss://container@account.dfs.core.windows.net/project/file.json"))
      .toBe("abfss://container@account.dfs.core.windows.net/project/file.json");
  });

  it("uses the Databricks Volume path form for canonical DBFS URIs", () => {
    expect(displayStorageUri("dbfs:/Volumes/catalog/schema/volume/project/file.json"))
      .toBe("/Volumes/catalog/schema/volume/project/file.json");
    expect(displayStorageUri("DBFS:/Volumes/catalog/schema/volume"))
      .toBe("/Volumes/catalog/schema/volume");
  });

  it("normalizes the accepted relative Volume alias", () => {
    expect(displayStorageUri("Volumes/catalog/schema/volume/project"))
      .toBe("/Volumes/catalog/schema/volume/project");
    expect(displayStorageUri("/Volumes/catalog/schema/volume/project"))
      .toBe("/Volumes/catalog/schema/volume/project");
  });

  it("uses preferred schemes for GCS and S3 aliases", () => {
    expect(displayStorageUri("gcs://bucket/project/file.json")).toBe("gs://bucket/project/file.json");
    expect(displayStorageUri("s3a://bucket/project/file.json")).toBe("s3://bucket/project/file.json");
    expect(displayStorageUri("s3n://bucket/project/file.json")).toBe("s3://bucket/project/file.json");
  });

  it("keeps legacy DBFS and unrelated providers explicit", () => {
    expect(displayStorageUri("dbfs:/mnt/project/file.json")).toBe("dbfs:/mnt/project/file.json");
    expect(displayStorageUri("s3://bucket/project/file.json")).toBe("s3://bucket/project/file.json");
    expect(displayStorageUri("C:\\workspace\\metadata.json")).toBe("C:\\workspace\\metadata.json");
  });
});
