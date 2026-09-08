export const NOTIFICATION_READ_ON_DWELL_DELAY_MS = 1_200;

type TimerId = ReturnType<typeof setTimeout>;

export interface NotificationReadDwellController {
  schedule: (notificationId: string) => void;
  cancel: () => void;
  pendingId: () => string | null;
  dispose: () => void;
}

export function createNotificationReadDwellController(
  onDwell: (notificationId: string) => void,
  delayMs = NOTIFICATION_READ_ON_DWELL_DELAY_MS,
): NotificationReadDwellController {
  let timer: TimerId | null = null;
  let pendingNotificationId: string | null = null;

  const cancel = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    pendingNotificationId = null;
  };

  const schedule = (notificationId: string) => {
    cancel();
    pendingNotificationId = notificationId;
    timer = setTimeout(() => {
      const completedNotificationId = pendingNotificationId;
      cancel();
      if (completedNotificationId !== null) onDwell(completedNotificationId);
    }, delayMs);
  };

  return {
    schedule,
    cancel,
    pendingId: () => pendingNotificationId,
    dispose: cancel,
  };
}
