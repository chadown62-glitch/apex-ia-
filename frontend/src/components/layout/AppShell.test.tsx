import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

function renderShell(initialPath = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AppShell>
          <div>page content</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell / Sidebar / Topbar", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "unknown", data: null, detail: "no handler in this test" }),
    }) as unknown as typeof fetch;
    localStorage.clear();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("groups navigation entries under real section labels, never a flat list", () => {
    renderShell();
    expect(screen.getByText("Workspace")).toBeInTheDocument();
    expect(screen.getByText("Intelligence")).toBeInTheDocument();
    expect(screen.getByText("Cognition")).toBeInTheDocument();
    expect(screen.getByText("Creative")).toBeInTheDocument();
    expect(screen.getByText("Data")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("marks the current route's link active via NavLink, never by a fabricated flag", () => {
    renderShell("/mission-center");
    const link = screen.getByRole("link", { name: /missions/i });
    expect(link.className).toMatch(/sidebar__link--active/);
  });

  it("collapses the sidebar and persists the choice across remounts", () => {
    const { unmount } = renderShell();
    expect(screen.getByText("APEX")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /réduire la navigation/i }));
    expect(screen.queryByText("APEX")).not.toBeInTheDocument();
    expect(localStorage.getItem("apex-sidebar-collapsed")).toBe("1");

    unmount();
    renderShell();
    expect(screen.queryByText("APEX")).not.toBeInTheDocument();
  });

  it("opens the mobile sidebar via the hamburger and closes it when a link is clicked", () => {
    renderShell();
    const sidebar = screen.getByRole("navigation", { name: /navigation principale/i });
    expect(sidebar.className).not.toMatch(/sidebar--mobile-open/);

    fireEvent.click(screen.getByRole("button", { name: /ouvrir la navigation/i }));
    expect(sidebar.className).toMatch(/sidebar--mobile-open/);

    fireEvent.click(screen.getByRole("link", { name: /home/i }));
    expect(sidebar.className).not.toMatch(/sidebar--mobile-open/);
  });

  it("shows the real APEX status derived from health/missions, never a hardcoded READY", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByText("WAITING")).toBeInTheDocument());
  });

  it("renders the routed page content inside the shell", () => {
    renderShell();
    expect(screen.getByText("page content")).toBeInTheDocument();
  });
});
