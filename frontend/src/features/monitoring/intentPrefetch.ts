export class IntentPrefetchController<Key> {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private scheduledKey: Key | null = null;

  constructor(
    private readonly load: (key: Key) => void,
    private readonly delayMs = 150
  ) {}

  schedule(key: Key) {
    this.cancel();
    this.scheduledKey = key;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.scheduledKey = null;
      this.load(key);
    }, this.delayMs);
  }

  immediately(key: Key) {
    this.cancel();
    this.load(key);
  }

  cancel(key?: Key) {
    if (key !== undefined && this.scheduledKey !== key) return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.scheduledKey = null;
  }

  dispose() {
    this.cancel();
  }
}
