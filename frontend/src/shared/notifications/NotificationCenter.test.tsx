import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { NotificationCenter, NotificationCenterProvider } from "./NotificationCenter";

describe("NotificationCenter", () => {
  it("renders an accessible header trigger", () => {
    const markup = renderToStaticMarkup(
      <NotificationCenterProvider>
        <NotificationCenter />
      </NotificationCenterProvider>,
    );

    expect(markup).toContain('aria-label="Notifications"');
    expect(markup).toContain('aria-haspopup="dialog"');
    expect(markup).toContain("notification-center-trigger");
  });
});
