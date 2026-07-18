export interface ResourceLoadResult<Value> {
  data: Value;
  fetchedAt: number;
  fromCache: boolean;
  current: boolean;
}

interface ResourceEntry<Value> {
  data: Value;
  fetchedAt: number;
}

/**
 * Small cache for client-owned server resources. It deduplicates in-flight
 * reads and uses generations so invalidated responses cannot repopulate stale
 * data after a mutation or route change.
 */
export class ResourceCache<Key, Value> {
  private readonly entries = new Map<Key, ResourceEntry<Value>>();
  private readonly inFlight = new Map<Key, Promise<ResourceLoadResult<Value>>>();
  private readonly generations = new Map<Key, number>();

  constructor(
    private readonly now: () => number = Date.now,
    private readonly options: { maxEntries?: number } = {}
  ) {}

  peek(key: Key): ResourceLoadResult<Value> | null {
    const entry = this.entries.get(key);
    if (!entry) return null;
    this.touch(key, entry);
    return {
      data: entry.data,
      fetchedAt: entry.fetchedAt,
      fromCache: true,
      current: true
    };
  }

  isFresh(key: Key, ttlMs: number) {
    const entry = this.entries.get(key);
    return Boolean(entry && this.now() - entry.fetchedAt < Math.max(0, ttlMs));
  }

  load(
    key: Key,
    loader: () => Promise<Value>,
    options: { ttlMs: number; force?: boolean }
  ): Promise<ResourceLoadResult<Value>> {
    const cached = this.entries.get(key);
    if (!options.force && cached && this.isFresh(key, options.ttlMs)) {
      this.touch(key, cached);
      return Promise.resolve({
        data: cached.data,
        fetchedAt: cached.fetchedAt,
        fromCache: true,
        current: true
      });
    }

    const pending = this.inFlight.get(key);
    if (pending) return pending;

    const generation = this.generation(key);
    const request = loader()
      .then((data) => {
        const current = this.generation(key) === generation;
        const fetchedAt = this.now();
        if (current) {
          this.entries.set(key, { data, fetchedAt });
          this.trim();
        }
        return { data, fetchedAt, fromCache: false, current };
      })
      .finally(() => {
        if (this.inFlight.get(key) === request) this.inFlight.delete(key);
      });
    this.inFlight.set(key, request);
    return request;
  }

  invalidate(key: Key) {
    this.generations.set(key, this.generation(key) + 1);
    this.entries.delete(key);
    this.inFlight.delete(key);
  }

  clear() {
    for (const key of new Set([...this.entries.keys(), ...this.inFlight.keys(), ...this.generations.keys()])) {
      this.invalidate(key);
    }
  }

  private generation(key: Key) {
    return this.generations.get(key) ?? 0;
  }

  private touch(key: Key, entry: ResourceEntry<Value>) {
    this.entries.delete(key);
    this.entries.set(key, entry);
  }

  private trim() {
    const maxEntries = Math.max(1, this.options.maxEntries ?? Number.MAX_SAFE_INTEGER);
    while (this.entries.size > maxEntries) {
      const oldest = this.entries.keys().next().value as Key | undefined;
      if (oldest === undefined) return;
      this.entries.delete(oldest);
    }
  }
}
