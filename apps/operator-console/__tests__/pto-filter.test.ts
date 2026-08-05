import { describe, expect, it } from "vitest";
import {
  parsePtoFilter,
  serializePtoFilter,
} from "@/lib/filters/pto-filter";

describe("pto filter", () => {
  it("defaults to include", () => {
    expect(parsePtoFilter(undefined)).toBe("include");
    expect(parsePtoFilter("")).toBe("include");
    expect(parsePtoFilter("include")).toBe("include");
  });

  it("parses exclude", () => {
    expect(parsePtoFilter("exclude")).toBe("exclude");
  });

  it("only serializes exclude into the URL", () => {
    expect(serializePtoFilter("include")).toBe("");
    expect(serializePtoFilter("exclude")).toBe("exclude");
  });
});
