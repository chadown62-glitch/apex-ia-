import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DataEnvelope, Gate } from "../api/types";
import { SecurityGatePage } from "./SecurityGatePage";

function renderWithQueryClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("SecurityGatePage", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("renders a real Gate report end-to-end through fetchEnvelope, hooks, and the badge component", async () => {
    const envelope: DataEnvelope<Gate> = {
      status: "real",
      data: {
        gate_status: "PASS",
        phase_2_authorized: true,
        machine_label: "dev-box",
        fingerprint: "abc123",
        measured_on_target_machine: true,
        checks: [{ check_id: "cpu", title: "CPU", status: "PASS", message: "ok" }],
      },
      detail: null,
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => envelope,
    }) as unknown as typeof fetch;

    renderWithQueryClient(<SecurityGatePage />);

    await waitFor(() => expect(screen.getAllByText("PASS")[0]).toBeInTheDocument());
    expect(screen.getByText("dev-box")).toBeInTheDocument();
    expect(screen.getByText("abc123")).toBeInTheDocument();
  });

  it("shows an honest ErrorState instead of fabricated data when the API is unreachable", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED")) as unknown as typeof fetch;

    renderWithQueryClient(<SecurityGatePage />);

    await waitFor(() => expect(screen.getByText("Donnée indisponible")).toBeInTheDocument());
    expect(screen.queryByText("PASS")).not.toBeInTheDocument();
  });
});
