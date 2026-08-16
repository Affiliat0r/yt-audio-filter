import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Each test gets a fresh DOM; a leaked component from the previous test makes
// `getByRole` ambiguous in ways that look like unrelated failures.
afterEach(cleanup);
