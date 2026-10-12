import { describe, expect, it } from "vitest";
import { runTone } from "@/lib/run-status";

describe("runTone", () => {
  it("separates a partial run from success and failure", () => {
    expect(runTone("success")).toBe("ok");
    expect(runTone("partial")).toBe("warn");
    expect(runTone("failed")).toBe("bad");
    expect(runTone("halted")).toBe("bad");
    expect(runTone("otp_pending")).toBe("bad");
    expect(runTone(null)).toBe("unknown");
    expect(runTone(undefined)).toBe("unknown");
  });
});
