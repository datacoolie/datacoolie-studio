import { describe, expect, it, vi } from "vitest";
import { ResourceCache } from "./resourceCache";

describe("ResourceCache", () => {
  it("serves fresh data without calling the loader again", async () => {
    let now = 1_000;
    const cache = new ResourceCache<string, number>(() => now);
    const loader = vi.fn().mockResolvedValue(7);

    expect((await cache.load("env:1", loader, { ttlMs: 30_000 })).data).toBe(7);
    now += 29_999;
    const cached = await cache.load("env:1", loader, { ttlMs: 30_000 });

    expect(cached.fromCache).toBe(true);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("revalidates when the entry reaches its TTL", async () => {
    let now = 1_000;
    const cache = new ResourceCache<string, number>(() => now);
    const loader = vi.fn().mockResolvedValueOnce(7).mockResolvedValueOnce(8);

    await cache.load("env:1", loader, { ttlMs: 30_000 });
    now += 30_000;

    expect((await cache.load("env:1", loader, { ttlMs: 30_000 })).data).toBe(8);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("deduplicates concurrent reads", async () => {
    let resolve!: (value: number) => void;
    const cache = new ResourceCache<string, number>();
    const loader = vi.fn(() => new Promise<number>((done) => { resolve = done; }));

    const first = cache.load("env:1", loader, { ttlMs: 30_000 });
    const second = cache.load("env:1", loader, { ttlMs: 30_000 });
    resolve(9);

    expect((await first).data).toBe(9);
    expect((await second).data).toBe(9);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("does not restore a response invalidated while in flight", async () => {
    let resolve!: (value: number) => void;
    const cache = new ResourceCache<string, number>();
    const pending = cache.load(
      "env:1",
      () => new Promise<number>((done) => { resolve = done; }),
      { ttlMs: 30_000 }
    );

    cache.invalidate("env:1");
    resolve(10);

    expect((await pending).current).toBe(false);
    expect(cache.peek("env:1")).toBeNull();
  });

  it("evicts the least recently used entry when bounded", async () => {
    const cache = new ResourceCache<string, number>(Date.now, { maxEntries: 2 });
    await cache.load("one", async () => 1, { ttlMs: 30_000 });
    await cache.load("two", async () => 2, { ttlMs: 30_000 });
    expect(cache.peek("one")?.data).toBe(1);

    await cache.load("three", async () => 3, { ttlMs: 30_000 });

    expect(cache.peek("one")?.data).toBe(1);
    expect(cache.peek("two")).toBeNull();
    expect(cache.peek("three")?.data).toBe(3);
  });
});
