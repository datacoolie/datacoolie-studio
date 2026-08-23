import {
  Bell,
  Check,
  CheckCheck,
  CheckCircle2,
  Trash2,
  X,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useDrawerEscape } from "../hooks/useDrawerEscape";
import { formatRelativeTime } from "../time";
import {
  addNotificationRecord,
  applyNotificationEvent,
  createNotificationRecord,
  isNotificationRecord,
  mergeNotificationRecords,
  NOTIFICATION_CHANNEL_NAME,
  NOTIFICATION_STORAGE_KEY,
  notificationListsEqual,
  parsePersistedNotifications,
  pruneNotifications,
  serializeNotifications,
  unreadNotificationCount,
  type NotificationChannelEvent,
  type NotificationInput,
  type NotificationRecord,
} from "./notificationStore";

interface NotificationCenterContextValue {
  notifications: NotificationRecord[];
  unreadCount: number;
  publish: (input: NotificationInput) => string | null;
  markRead: (id: string) => void;
  markAllRead: () => void;
  clearRead: () => void;
  clearAll: () => void;
}

const NotificationCenterContext = createContext<NotificationCenterContextValue | null>(null);
const EMPTY_NOTIFICATION_CENTER: NotificationCenterContextValue = {
  notifications: [],
  unreadCount: 0,
  publish: () => null,
  markRead: () => undefined,
  markAllRead: () => undefined,
  clearRead: () => undefined,
  clearAll: () => undefined,
};

export function NotificationCenterProvider({ children }: { children: ReactNode }) {
  const [notifications, setNotifications] = useState<NotificationRecord[]>(() => readStoredNotifications());
  const notificationsRef = useRef(notifications);
  const tabIdRef = useRef(makeTabId());
  const channelRef = useRef<BroadcastChannel | null>(null);

  const commit = useCallback((next: NotificationRecord[], event?: NotificationChannelEvent) => {
    const pruned = pruneNotifications(next);
    notificationsRef.current = pruned;
    setNotifications(pruned);
    writeStoredNotifications(pruned);
    if (event) channelRef.current?.postMessage(event);
  }, []);

  const latestRecords = useCallback(() => {
    const now = Date.now();
    return mergeNotificationRecords(notificationsRef.current, readStoredNotifications(now), now);
  }, []);

  const publish = useCallback((input: NotificationInput) => {
    const now = Date.now();
    const base = latestRecords();
    const record = createNotificationRecord(input, now);
    const result = addNotificationRecord(base, record, now);
    if (!result.added) {
      if (!notificationListsEqual(notificationsRef.current, result.records)) commit(result.records);
      return null;
    }
    commit(result.records, { type: "upsert", sourceId: tabIdRef.current, record });
    return record.id;
  }, [commit, latestRecords]);

  const markRead = useCallback((id: string) => {
    const now = Date.now();
    const base = latestRecords();
    const next = applyNotificationEvent(base, {
      type: "mark-read",
      sourceId: tabIdRef.current,
      id,
      readAt: now,
    }, now);
    if (notificationListsEqual(notificationsRef.current, next)) return;
    commit(next, { type: "mark-read", sourceId: tabIdRef.current, id, readAt: now });
  }, [commit, latestRecords]);

  const markAllRead = useCallback(() => {
    const now = Date.now();
    const base = latestRecords();
    const next = applyNotificationEvent(base, {
      type: "mark-all-read",
      sourceId: tabIdRef.current,
      readAt: now,
    }, now);
    if (notificationListsEqual(notificationsRef.current, next)) return;
    commit(next, { type: "mark-all-read", sourceId: tabIdRef.current, readAt: now });
  }, [commit, latestRecords]);

  const clearRead = useCallback(() => {
    const base = latestRecords();
    const next = applyNotificationEvent(base, { type: "clear-read", sourceId: tabIdRef.current });
    if (notificationListsEqual(notificationsRef.current, next)) return;
    commit(next, { type: "clear-read", sourceId: tabIdRef.current });
  }, [commit, latestRecords]);

  const clearAll = useCallback(() => {
    if (!notificationsRef.current.length && !readStoredNotifications().length) return;
    commit([], { type: "clear-all", sourceId: tabIdRef.current });
  }, [commit]);

  const applyRemoteEvent = useCallback((event: NotificationChannelEvent) => {
    if (event.sourceId === tabIdRef.current) return;
    const now = Date.now();
    const base = mergeNotificationRecords(notificationsRef.current, readStoredNotifications(now), now);
    const next = applyNotificationEvent(base, event, now);
    if (notificationListsEqual(notificationsRef.current, next)) return;
    // Persist the converged snapshot, but do not broadcast the remote event again.
    commit(next);
  }, [commit]);

  useEffect(() => {
    writeStoredNotifications(notificationsRef.current);

    function handleStorage(event: StorageEvent) {
      if (event.key !== NOTIFICATION_STORAGE_KEY) return;
      const next = parsePersistedNotifications(event.newValue);
      if (notificationListsEqual(notificationsRef.current, next)) return;
      notificationsRef.current = next;
      setNotifications(next);
    }

    window.addEventListener("storage", handleStorage);
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel(NOTIFICATION_CHANNEL_NAME);
      channel.onmessage = (event: MessageEvent<unknown>) => {
        if (!isNotificationChannelEvent(event.data)) return;
        applyRemoteEvent(event.data);
      };
      channelRef.current = channel;
    }

    return () => {
      window.removeEventListener("storage", handleStorage);
      channelRef.current?.close();
      channelRef.current = null;
    };
  }, [applyRemoteEvent]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const next = pruneNotifications(notificationsRef.current);
      if (notificationListsEqual(notificationsRef.current, next)) return;
      commit(next);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [commit]);

  const value = useMemo<NotificationCenterContextValue>(() => ({
    notifications,
    unreadCount: unreadNotificationCount(notifications),
    publish,
    markRead,
    markAllRead,
    clearRead,
    clearAll,
  }), [clearAll, clearRead, markAllRead, markRead, notifications, publish]);

  return <NotificationCenterContext.Provider value={value}>{children}</NotificationCenterContext.Provider>;
}

export function useNotificationCenter(): NotificationCenterContextValue {
  return useContext(NotificationCenterContext) ?? EMPTY_NOTIFICATION_CENTER;
}

export function NotificationCenter() {
  const { notifications, unreadCount, markRead, markAllRead, clearRead } = useNotificationCenter();
  const [open, setOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => {
    setOpen(false);
    setExpandedId(null);
  }, []);

  useDrawerEscape(close, open);

  useEffect(() => {
    if (!open) return undefined;
    function closeOnOutsideClick(event: PointerEvent) {
      if (containerRef.current && event.target instanceof Node && !containerRef.current.contains(event.target)) {
        close();
      }
    }
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [close, open]);

  return (
    <div className="notification-center" ref={containerRef}>
      <button
        type="button"
        className="notification-center-trigger"
        aria-label={unreadCount ? `Notifications, ${unreadCount} unread` : "Notifications"}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((current) => !current)}
        title="Notifications"
      >
        <Bell size={16} aria-hidden="true" />
        {unreadCount ? <span className="notification-center-badge">{unreadCount > 99 ? "99+" : unreadCount}</span> : null}
      </button>

      {open ? (
        <section className="notification-center-popover" role="dialog" aria-label="Notifications">
          <header className="notification-center-header">
            <div>
              <strong>Notifications</strong>
              <span>{unreadCount ? `${unreadCount} unread` : "No unread notifications"}</span>
            </div>
            <button type="button" className="notification-center-close" onClick={close} aria-label="Close notifications" title="Close notifications">
              <X size={15} aria-hidden="true" />
            </button>
          </header>
          <div className="notification-center-actions">
            <button type="button" className="text-action" onClick={markAllRead} disabled={!unreadCount}>
              <CheckCheck size={13} aria-hidden="true" /> Mark all read
            </button>
            <button type="button" className="text-action" onClick={clearRead} disabled={!notifications.some((record) => record.readAt !== null)}>
              <Trash2 size={13} aria-hidden="true" /> Clear read
            </button>
          </div>
          <div className="notification-center-list" role="list" aria-label="Recent notifications">
            {notifications.length ? notifications.map((record) => {
              const expanded = expandedId === record.id;
              return (
                <div className="notification-center-list-item" role="listitem" key={record.id}>
                  <button
                    type="button"
                    className={`notification-center-row tone-${record.tone}${record.readAt ? " is-read" : " is-unread"}`}
                    aria-expanded={expanded}
                    onClick={() => {
                      markRead(record.id);
                      setExpandedId((current) => current === record.id ? null : record.id);
                    }}
                  >
                    <NotificationToneIcon tone={record.tone} />
                    <span className="notification-center-row-body">
                      <span className="notification-center-row-meta">
                        <strong>{record.location}</strong>
                        <span title={notificationAbsoluteTime(record.occurredAt)}>{notificationRelativeTime(record.occurredAt)}</span>
                      </span>
                      <span className="notification-center-row-title">{record.title}</span>
                      <span className={`notification-center-row-message${expanded ? " is-expanded" : ""}`} title={expanded ? undefined : record.message}>
                        {record.message}
                      </span>
                    </span>
                    {record.readAt === null ? <span className="notification-center-unread-dot" aria-label="Unread" /> : <Check size={13} aria-hidden="true" />}
                  </button>
                </div>
              );
            }) : (
              <div className="notification-center-empty">
                <Bell size={20} aria-hidden="true" />
                <span>No notifications in the last 24 hours.</span>
              </div>
            )}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function NotificationToneIcon({ tone }: { tone: NotificationRecord["tone"] }) {
  if (tone === "success") return <CheckCircle2 className="notification-center-row-icon" size={15} aria-hidden="true" />;
  if (tone === "warning") return <AlertTriangle className="notification-center-row-icon" size={15} aria-hidden="true" />;
  return <XCircle className="notification-center-row-icon" size={15} aria-hidden="true" />;
}

function readStoredNotifications(now = Date.now()): NotificationRecord[] {
  if (typeof window === "undefined") return [];
  try {
    return parsePersistedNotifications(window.localStorage.getItem(NOTIFICATION_STORAGE_KEY), now);
  } catch {
    return [];
  }
}

function writeStoredNotifications(records: NotificationRecord[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(NOTIFICATION_STORAGE_KEY, serializeNotifications(records));
  } catch {
    // Storage is an optional enhancement. Private mode and quota failures must not break Studio.
  }
}

function isNotificationChannelEvent(value: unknown): value is NotificationChannelEvent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as {
    sourceId?: unknown;
    type?: unknown;
    record?: unknown;
    id?: unknown;
    readAt?: unknown;
  };
  if (typeof candidate.sourceId !== "string" || typeof candidate.type !== "string") return false;
  if (candidate.type === "upsert") return isNotificationRecord(candidate.record);
  if (candidate.type === "mark-read") {
    return typeof candidate.id === "string" && typeof candidate.readAt === "number" && Number.isFinite(candidate.readAt);
  }
  if (candidate.type === "mark-all-read") {
    return typeof candidate.readAt === "number" && Number.isFinite(candidate.readAt);
  }
  return candidate.type === "clear-read" || candidate.type === "clear-all";
}

function notificationRelativeTime(timestamp: number) {
  return formatRelativeTime(new Date(timestamp).toISOString()) ?? "unknown time";
}

function notificationAbsoluteTime(timestamp: number) {
  return new Date(timestamp).toLocaleString();
}

function makeTabId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
