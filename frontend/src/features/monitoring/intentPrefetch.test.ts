import { afterEach, describe, expect, it, vi } from "vitest";
import { IntentPrefetchController } from "./intentPrefetch";

describe("IntentPrefetchController", () => {
  afterEach(() => vi.useRealTimers());

  it("waits for stable hover intent", () => {
    vi.useFakeTimers();
    const load = vi.fn();
    const controller = new IntentPrefetchController(load, 150);

    controller.schedule("performance");
    vi.advanceTimersByTime(149);
    expect(load).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(load).toHaveBeenCalledWith("performance");
  });

  it("cancels intent that ends before the delay", () => {
    vi.useFakeTimers();
    const load = vi.fn();
    const controller = new IntentPrefetchController(load, 150);

    controller.schedule("volume");
    controller.cancel("volume");
    vi.runAllTimers();

    expect(load).not.toHaveBeenCalled();
  });

  it("prefetches immediately on pointer down without a delayed duplicate", () => {
    vi.useFakeTimers();
    const load = vi.fn();
    const controller = new IntentPrefetchController(load, 150);

    controller.schedule("jobs");
    controller.immediately("jobs");
    vi.runAllTimers();

    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("jobs");
  });
});
