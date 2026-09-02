import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Switch } from "./Field";
import { Dropdown } from "./Dropdown";
import { Modal } from "./Modal";
import { Tabs } from "./Tabs";
import { ToastProvider, useToast } from "./Toast";

describe("Modal", () => {
  function Harness() {
    const [open, setOpen] = useState(true);
    return (
      <div>
        <button onClick={() => setOpen(true)}>reopen</button>
        <Modal open={open} onClose={() => setOpen(false)} title="Test dialog">
          <p>modal body</p>
        </Modal>
      </div>
    );
  }

  it("renders its content only while open, with the right dialog semantics", () => {
    render(<Harness />);
    expect(screen.getByRole("dialog", { name: "Test dialog" })).toBeInTheDocument();
    expect(screen.getByText("modal body")).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    render(<Harness />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes when clicking the backdrop, not when clicking inside the panel", () => {
    render(<Harness />);
    fireEvent.mouseDown(screen.getByText("modal body"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.mouseDown(document.querySelector(".modal-overlay") as Element);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes via the explicit close button", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Fermer" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Tabs", () => {
  const items = [
    { id: "a", label: "Tab A", content: <p>content A</p> },
    { id: "b", label: "Tab B", content: <p>content B</p> },
  ];

  it("shows the first tab's content by default and switches on click", () => {
    render(<Tabs items={items} />);
    expect(screen.getByText("content A")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Tab B" }));
    expect(screen.getByText("content B")).toBeInTheDocument();
    expect(screen.queryByText("content A")).not.toBeInTheDocument();
  });

  it("supports arrow-key navigation between tabs (roving tabindex)", () => {
    render(<Tabs items={items} />);
    const tabA = screen.getByRole("tab", { name: "Tab A" });
    tabA.focus();
    fireEvent.keyDown(tabA, { key: "ArrowRight" });
    expect(screen.getByText("content B")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Tab B" })).toHaveFocus();
  });
});

describe("Dropdown", () => {
  it("opens the menu, calls onSelect, and closes afterwards", () => {
    const onSelect = vi.fn();
    render(<Dropdown trigger="Actions" items={[{ id: "x", label: "Do X", onSelect }]} />);

    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("menuitem", { name: "Do X" }));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes when clicking outside", () => {
    render(
      <div>
        <Dropdown trigger="Actions" items={[{ id: "x", label: "Do X", onSelect: () => {} }]} />
        <button>outside</button>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("button", { name: "outside" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

describe("Switch", () => {
  it("is a real controlled toggle — never flips without onChange being called", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Enabled" />);
    fireEvent.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe("ToastProvider / useToast", () => {
  function Trigger() {
    const { push } = useToast();
    return <button onClick={() => push("Agent créé", "success")}>notify</button>;
  }

  it("renders a pushed toast and lets it be dismissed manually", () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "notify" }));
    expect(screen.getByText("Agent créé")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Fermer la notification" }));
    return waitFor(() => expect(screen.queryByText("Agent créé")).not.toBeInTheDocument());
  });
});
