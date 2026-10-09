import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Markdown, parseBlocks } from "./Markdown";

describe("Markdown (safe renderer for model answers)", () => {
  it("renders headings, lists, tables, quotes and code blocks", () => {
    const text = [
      "## Plan",
      "Voici **trois** étapes et du `code` :",
      "1. Lire",
      "2. Écrire",
      "- point a",
      "- point b",
      "",
      "| Année | Solde |",
      "|---|---:|",
      "| 1 | 1 200,00 |",
      "",
      "> Hypothèse : taux constant.",
      "",
      "```python",
      "print(1 + 1)",
      "```",
    ].join("\n");
    const { container } = render(<Markdown text={text} />);
    expect(screen.getByRole("heading", { name: "Plan" })).toBeInTheDocument();
    expect(container.querySelector("strong")?.textContent).toBe("trois");
    expect(container.querySelector("ol")?.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("ul")?.querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByRole("columnheader", { name: "Solde" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1 200,00" })).toBeInTheDocument();
    expect(container.querySelector("blockquote")?.textContent).toContain("taux constant");
    expect(container.querySelector("pre code")?.textContent).toBe("print(1 + 1)");
    expect(screen.getByText("python")).toBeInTheDocument();
  });

  it("never injects HTML written by the model: tags stay text", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> <script>alert(2)</script> **ok**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it("only links http(s) targets, opened safely", () => {
    const { container } = render(<Markdown text="[doc](https://example.org/a) et [piège](javascript:alert(1))" />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.org/a");
    expect(links[0].getAttribute("rel")).toContain("noopener");
    expect(container.textContent).toContain("[piège](javascript:alert(1))");
  });

  it("shows a swatch for each cited colour", () => {
    const { container } = render(<Markdown text="Fond #0f172a et accent #A78BFA, pas l'issue #12." />);
    const swatches = container.querySelectorAll(".md-color__swatch");
    expect(swatches).toHaveLength(2);
    expect((swatches[1] as HTMLElement).style.background).toBe("rgb(167, 139, 250)");
  });

  it("keeps an unterminated code block instead of dropping it", () => {
    expect(parseBlocks("```js\nconst a = 1;")).toEqual([{ kind: "code", lang: "js", text: "const a = 1;" }]);
  });

  it("copies a code block to the clipboard and says so", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<Markdown text={"```\nx = 1\n```"} />);
    fireEvent.click(screen.getByRole("button", { name: "Copier" }));
    expect(await screen.findByRole("button", { name: "Copié" })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith("x = 1");
  });
});
