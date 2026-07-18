import { describe, expect, it, vi } from "vitest";
import { subscribeToEnvironmentHeaderRevalidation } from "./environmentHeaderRevalidation";

class PageActivationTarget {
  private readonly listeners = new Map<string, Set<() => void>>();

  addEventListener(type: string, listener: () => void) {
    const registered = this.listeners.get(type) ?? new Set<() => void>();
    registered.add(listener);
    this.listeners.set(type, registered);
  }

  removeEventListener(type: string, listener: () => void) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type: string) {
    for (const listener of this.listeners.get(type) ?? []) listener();
  }
}

class PageActivationDocument extends PageActivationTarget {
  visibilityState: DocumentVisibilityState = "visible";
}

describe("Environment header page-activation revalidation", () => {
  it("revalidates only when the page is visible", () => {
    const pageDocument = new PageActivationDocument();
    const pageWindow = new PageActivationTarget();
    const onRevalidate = vi.fn();
    const unsubscribe = subscribeToEnvironmentHeaderRevalidation(onRevalidate, pageDocument, pageWindow);

    pageDocument.visibilityState = "hidden";
    pageDocument.dispatch("visibilitychange");
    pageWindow.dispatch("focus");
    expect(onRevalidate).not.toHaveBeenCalled();

    pageDocument.visibilityState = "visible";
    pageDocument.dispatch("visibilitychange");
    pageWindow.dispatch("focus");
    expect(onRevalidate).toHaveBeenCalledTimes(2);

    unsubscribe();
    pageDocument.dispatch("visibilitychange");
    pageWindow.dispatch("focus");
    expect(onRevalidate).toHaveBeenCalledTimes(2);
  });
});
