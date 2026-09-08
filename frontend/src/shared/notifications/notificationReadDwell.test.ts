import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createNotificationReadDwellController,
  NOTIFICATION_READ_ON_DWELL_DELAY_MS,
} from "./notificationReadDwell";

describe("notificationReadDwell", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("marks the hovered notification after the dwell delay", () => {
    vi.useFakeTimers();
    const onDwell = vi.fn();
    const controller = createNotificationReadDwellController(onDwell);

    controller.schedule("notification-1");
    vi.advanceTimersByTime(NOTIFICATION_READ_ON_DWELL_DELAY_MS - 1);
    expect(onDwell).not.toHaveBeenCalled();
    expect(controller.pendingId()).toBe("notification-1");

    vi.advanceTimersByTime(1);
    expect(onDwell).toHaveBeenCalledOnce();
    expect(onDwell).toHaveBeenCalledWith("notification-1");
    expect(controller.pendingId()).toBeNull();
  });

  it("cancels a dwell when the pointer or focus leaves the row", () => {
    vi.useFakeTimers();
    const onDwell = vi.fn();
    const controller = createNotificationReadDwellController(onDwell);

    controller.schedule("notification-1");
    vi.advanceTimersByTime(500);
    controller.cancel();
    vi.advanceTimersByTime(NOTIFICATION_READ_ON_DWELL_DELAY_MS);

    expect(onDwell).not.toHaveBeenCalled();
    expect(controller.pendingId()).toBeNull();
  });

  it("keeps only the latest row scheduled", () => {
    vi.useFakeTimers();
    const onDwell = vi.fn();
    const controller = createNotificationReadDwellController(onDwell);

    controller.schedule("notification-1");
    vi.advanceTimersByTime(400);
    controller.schedule("notification-2");
    vi.advanceTimersByTime(NOTIFICATION_READ_ON_DWELL_DELAY_MS - 1);
    expect(onDwell).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onDwell).toHaveBeenCalledOnce();
    expect(onDwell).toHaveBeenCalledWith("notification-2");
  });

  it("disposes the pending timer", () => {
    vi.useFakeTimers();
    const onDwell = vi.fn();
    const controller = createNotificationReadDwellController(onDwell);

    controller.schedule("notification-1");
    controller.dispose();
    vi.advanceTimersByTime(NOTIFICATION_READ_ON_DWELL_DELAY_MS);

    expect(onDwell).not.toHaveBeenCalled();
    expect(controller.pendingId()).toBeNull();
  });
});
