import { ResourceCache } from "../shared/data/resourceCache";

export const ENVIRONMENT_RESOURCE_NAMES = [
  "lineage",
  "assets",
  "latest-status",
  "editor-document",
  "editor-draft",
  "sources",
  "overview",
] as const;

export type EnvironmentResourceName = typeof ENVIRONMENT_RESOURCE_NAMES[number];

const SESSION_RESOURCE_TTL_MS = Number.MAX_SAFE_INTEGER;
const STRUCTURAL_RESOURCE_NAMES = [
  "lineage",
  "assets",
  "editor-document",
  "editor-draft",
  "overview",
] as const satisfies readonly EnvironmentResourceName[];

/** Owns module-resource identity, request deduplication and revision-driven invalidation. */
export class EnvironmentResourceStore {
  private readonly cache: ResourceCache<string, unknown>;

  constructor(now: () => number = Date.now) {
    this.cache = new ResourceCache<string, unknown>(now);
  }

  async load<T>(
    environmentId: number,
    resource: EnvironmentResourceName,
    fetcher: () => Promise<T>,
    options?: { force?: boolean },
  ) {
    const result = await this.cache.load(this.key(environmentId, resource), fetcher, {
      ttlMs: SESSION_RESOURCE_TTL_MS,
      force: options?.force,
    });
    return result.data as T;
  }

  invalidateResource(environmentId: number, resource: EnvironmentResourceName) {
    this.cache.invalidate(this.key(environmentId, resource));
  }

  invalidateEnvironment(environmentId: number) {
    for (const resource of ENVIRONMENT_RESOURCE_NAMES) {
      this.cache.invalidate(this.key(environmentId, resource));
    }
  }

  invalidateStructural(environmentId: number) {
    for (const resource of STRUCTURAL_RESOURCE_NAMES) {
      this.cache.invalidate(this.key(environmentId, resource));
    }
  }

  private key(environmentId: number, resource: EnvironmentResourceName) {
    return `${environmentId}:${resource}`;
  }
}
