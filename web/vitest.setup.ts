import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  // Pending chat questions live in localStorage; one test's must not surface in the next.
  localStorage.clear();
});
