import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
      <button onClick={toggleTheme}>toggle</button>
    </div>
  );
}

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    vi.unstubAllGlobals();
  });

  it("defaults to dark when no stored choice and system does not prefer light", () => {
    render(<TestConsumer />);
    expect(screen.getByTestId("current-theme")).toHaveTextContent("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("resolves to light when the system prefers light and nothing is stored", () => {
    vi.stubGlobal("matchMedia", (query: string) => fakeMediaQueryList(query, query.includes("light")));
    render(<TestConsumer />);
    expect(screen.getByTestId("current-theme")).toHaveTextContent("light");
  });

  it("an explicit stored choice always wins over the system preference", () => {
    localStorage.setItem("apex-theme", "light");
    vi.stubGlobal("matchMedia", (query: string) => fakeMediaQueryList(query, false));
    render(<TestConsumer />);
    expect(screen.getByTestId("current-theme")).toHaveTextContent("light");
  });

  it("toggling persists the choice to localStorage and updates the DOM attribute", () => {
    render(<TestConsumer />);
    act(() => {
      fireEvent.click(screen.getByText("toggle"));
    });
    expect(screen.getByTestId("current-theme")).toHaveTextContent("light");
    expect(localStorage.getItem("apex-theme")).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("renders an accessible toggle button that flips the theme on click", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", { name: /passer au thème clair/i });
    fireEvent.click(button);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(screen.getByRole("button", { name: /passer au thème sombre/i })).toBeInTheDocument();
  });
});
