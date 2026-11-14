import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import App from "../App";
import * as client from "../api/client";
import { libraryPage, libraryDelta } from "./libraryFixtures";

test("renders the app shell with the wordmark", async () => {
  vi.spyOn(client.api, "libraryContext").mockResolvedValue({ scope: "test-library" });
  vi.spyOn(client.api, "library").mockResolvedValue(libraryPage([]));
  vi.spyOn(client.api, "libraryChanges").mockResolvedValue(libraryDelta());
  render(<App />);
  expect(screen.getByRole("img", { name: /Novel OS/i })).toBeInTheDocument();
});
