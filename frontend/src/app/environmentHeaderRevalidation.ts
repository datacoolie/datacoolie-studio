type PageActivationListener = () => void;

interface PageActivationDocument {
  visibilityState: DocumentVisibilityState;
  addEventListener(type: "visibilitychange", listener: PageActivationListener): void;
  removeEventListener(type: "visibilitychange", listener: PageActivationListener): void;
}

interface PageActivationWindow {
  addEventListener(type: "focus", listener: PageActivationListener): void;
  removeEventListener(type: "focus", listener: PageActivationListener): void;
}

/** Revalidates only when a user returns to a visible Studio page. */
export function subscribeToEnvironmentHeaderRevalidation(
  onRevalidate: () => void,
  pageDocument: PageActivationDocument = document,
  pageWindow: PageActivationWindow = window,
) {
  const revalidateWhenVisible = () => {
    if (pageDocument.visibilityState === "visible") onRevalidate();
  };
  pageDocument.addEventListener("visibilitychange", revalidateWhenVisible);
  pageWindow.addEventListener("focus", revalidateWhenVisible);
  return () => {
    pageDocument.removeEventListener("visibilitychange", revalidateWhenVisible);
    pageWindow.removeEventListener("focus", revalidateWhenVisible);
  };
}
