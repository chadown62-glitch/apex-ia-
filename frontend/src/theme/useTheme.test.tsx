import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAppearance, motionAllowed, readCachedAppearance, resetAppearanceFromCache, setAppearance } from "./appearance";
import { ThemeToggle } from "./ThemeToggle";
import { useTheme } from "./useTheme";

function fakeMediaQueryList(query: string, matches: boolean): MediaQueryList {
  return {
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  };
}

function TestConsumer() {
  const { theme, toggleTheme } = useTheme();
  return (
    <div>
      <span data-testid="current-theme">{theme}</span>
      <button onClick={() => void toggleTheme()}>toggle</button>
    </div>
  );
}

function okSettingsFetch() {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ status: "real", data: { appearance: { theme: "dune" } } }),
  });
}

describe("appearance store / useTheme", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    localStorage.clear();
    resetAppearanceFromCache();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    localStorage.clear();
    vi.unstubAllGlobals();
    resetAppearanceFromCache();
  });

  it("defaults to Night when nothing is stored and the system does not prefer light", () => {
    render(<TestConsumer />);
    expect(screen.getByTestId("current-theme")).toHaveTextContent("night");
    expect(document.documentElement.getAttribute("data-theme")).toBe("night");
  });

  it("resolves to Dune when the system prefers light and nothing is stored", () => {
    vi.stubGlobal("matchMedia", (query: string) => fakeMediaQueryList(query, query.includes("light")));
    expect(readCachedAppearance().theme).toBe("dune");
  });

  it("keeps the previous version's explicit choice (dark/light) instead of losing it", () => {
    localStorage.setItem("apex-theme", "light");
    expect(readCachedAppearance().theme).toBe("dune");
    localStorage.setItem("apex-theme", "dark");
    expect(readCachedAppearance().theme).toBe("night");
  });

  it("ignores a corrupted cache and falls back to defaults, never to an invalid value", () => {
    localStorage.setItem("apex-appearance", "{not json");
    expect(readCachedAppearance()).toEqual({ theme: "night", animations: true, particles: true, reduce_motion: "system" });
    localStorage.setItem("apex-appearance", JSON.stringify({ theme: "neon", animations: "yes", reduce_motion: "sometimes" }));
    expect(readCachedAppearance()).toEqual({ theme: "night", animations: true, particles: true, reduce_motion: "system" });
  });

  it("toggling applies the theme, caches it and saves it to the backend settings", async () => {
    const fetchMock = okSettingsFetch();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<TestConsumer />);
    act(() => {
      fireEvent.click(screen.getByText("toggle"));
    });
    expect(screen.getByTestId("current-theme")).toHaveTextContent("dune");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dune");
    expect(JSON.parse(localStorage.getItem("apex-appearance") ?? "{}").theme).toBe("dune");
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/assistant/settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ appearance: { theme: "dune" } });
  });

  it("writes animation, particle and motion settings to <html> for the CSS and the backdrop", () => {
    setAppearance({ animations: false, particles: false, reduce_motion: "always" });
    const root = document.documentElement;
    expect(root.getAttribute("data-animations")).toBe("off");
    expect(root.getAttribute("data-particles")).toBe("off");
    expect(root.getAttribute("data-reduce-motion")).toBe("always");
    expect(motionAllowed(getAppearance())).toBe(false);
  });

  it("follows the system reduced-motion preference only in 'system' mode", () => {
    vi.stubGlobal("matchMedia", (query: string) => fakeMediaQueryList(query, query.includes("reduce")));
    expect(motionAllowed({ theme: "night", animations: true, particles: true, reduce_motion: "system" })).toBe(false);
    expect(motionAllowed({ theme: "night", animations: true, particles: true, reduce_motion: "never" })).toBe(true);
  });
});

describe("ThemeToggle", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    localStorage.clear();
    resetAppearanceFromCache();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    resetAppearanceFromCache();
  });

  it("renders an accessible toggle button that flips between Night and Dune", async () => {
    globalThis.fetch = okSettingsFetch() as unknown as typeof fetch;
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: /passer au thème apex dune/i }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dune");
    expect(await screen.findByRole("button", { name: /passer au thème apex night/i })).toBeInTheDocument();
  });

  it("says when the theme could not be saved instead of pretending it was", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("connexion refusée")) as unknown as typeof fetch;
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /passer au thème apex dune/i });
    fireEvent.click(button);
    await waitFor(() => expect(button.getAttribute("title")).toMatch(/non enregistré/));
  });
});
