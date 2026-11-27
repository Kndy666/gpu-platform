import { describe, it, expect } from "vitest";
import { resolveAllocationResource, shareMaximum, type AllocationResourceDef } from "@/lib/allocation";

describe("Resource allocation accounting", () => {
  it("distributes remaining quota evenly across max-allocated devices", () => {
    expect(shareMaximum(["max", "max"], [80, 80], 100, 1)).toEqual([50, 50]);
    expect(shareMaximum([20, "max", "max"], [80, 40, 80], 120, 1)).toEqual([20, 40, 60]);
  });

  it("rejects impossible allocations exceeding quota or per-device caps", () => {
    expect(shareMaximum([60, 60], [80, 80], 100, 1)).toBeNull();
    expect(shareMaximum([90], [80], 100, 1)).toBeNull();
    expect(shareMaximum(["max", "max"], [80, 80], 1, 1)).toBeNull();
  });

  it("resolves GPU memory allocations respecting heterogeneous cards and per-card quotas", () => {
    const def: AllocationResourceDef = {
      key: "gpu_memory",
      label_text: "GPU Memory",
      unit: "GiB",
      allocation_group: "gpu",
      allocation_field: "memory_gib",
      minimum: 1,
      maximum: 80,
      step: 1,
      limit: 64,
      per_allocation_limit: 40,
    };

    const gpus = [
      { index: 0, memory_total_gib: 80 },
      { index: 1, memory_total_gib: 24 },
    ];

    const resolved = resolveAllocationResource(
      [{ gpu_memory: "max" }, { gpu_memory: "max" }],
      def,
      gpus,
    );
    expect(resolved).toEqual([40, 24]);

    const overQuota = resolveAllocationResource(
      [{ gpu_memory: 40 }, { gpu_memory: 30 }],
      def,
      gpus,
    );
    expect(overQuota).toBeNull();
  });
});
