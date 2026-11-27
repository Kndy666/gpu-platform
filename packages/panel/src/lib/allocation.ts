export interface AllocationResourceDef {
  key: string;
  label_text: string;
  unit: string;
  allocation_group?: string | null;
  allocation_field?: string | null;
  default?: number | string | null;
  minimum?: number | string | null;
  maximum?: number | string | null;
  step?: number | string | null;
  integral?: boolean;
  limit?: number | string | null;
  per_allocation_limit?: number | string | null;
}

export interface CapacityGpu {
  index?: number | null;
  ready?: boolean | null;
  compute_percent?: number | null;
  memory_gib?: number | null;
  memory_total_gib?: number | null;
}

export type AllocationValue = number | string | "max";

export function initialAllocationValue(resource: AllocationResourceDef): number {
  const amount = Number(resource.default ?? resource.minimum ?? 1);
  const limit = resource.per_allocation_limit ?? resource.maximum;
  return limit == null ? amount : Math.min(amount, Number(limit));
}

export function shareMaximum(
  values: (number | "max")[],
  caps: number[],
  total: number | null,
  minimum: number,
): number[] | null {
  const fixed = values.reduce<number>((sum, value) => sum + (value === "max" ? 0 : Number(value)), 0);
  const capSum = caps.reduce((sum, value) => sum + value, 0);
  let available = Math.floor(Math.min(total ?? capSum, capSum) - fixed);
  const result = values.map((value) => (value === "max" ? 0 : Number(value)));
  let pending = values
    .map((value, index) => (value === "max" ? index : -1))
    .filter((index) => index >= 0);

  if (
    available < pending.length * minimum ||
    result.some((value, index) => values[index] !== "max" && (value < minimum || value > caps[index]))
  ) {
    return null;
  }

  while (available > 0 && pending.length) {
    const share = Math.max(1, Math.floor(available / pending.length));
    for (const index of pending) {
      const amount = Math.min(share, caps[index] - result[index], available);
      result[index] += amount;
      available -= amount;
    }
    pending = pending.filter((index) => result[index] < caps[index]);
  }
  return result.every((value) => value >= minimum) ? result : null;
}

export function resolveAllocationResource(
  rows: Record<string, AllocationValue>[],
  resource: AllocationResourceDef,
  gpus: CapacityGpu[] = [],
): number[] | null {
  if (!rows.length) return [];
  const increment = Math.min(1, Number(resource.step ?? 1) || 1);
  const toUnits = (value: number | string) => Number(value) / increment;
  const capacities = gpus
    .map((device) =>
      Math.floor(
        toUnits(
          Math.min(
            Number(resource.per_allocation_limit ?? resource.maximum ?? Number.MAX_SAFE_INTEGER),
            Number(
              resource.allocation_field === "memory_gib"
                ? (device.memory_total_gib ?? resource.maximum ?? Number.MAX_SAFE_INTEGER)
                : (resource.maximum ?? Number.MAX_SAFE_INTEGER),
            ),
          ),
        ),
      ),
    )
    .sort((a, b) => b - a);

  const fallbackCap = Math.floor(
    toUnits(resource.maximum ?? resource.limit ?? initialAllocationValue(resource)),
  );
  const caps = rows.map((_, index) => capacities[index] ?? fallbackCap);
  const values: (number | "max")[] = rows.map((row) =>
    row[resource.key] === "max" ? "max" : toUnits(row[resource.key] ?? initialAllocationValue(resource)),
  );
  const resolved = shareMaximum(
    values,
    caps,
    resource.limit == null ? null : Math.floor(toUnits(resource.limit)),
    Math.ceil(toUnits(resource.minimum ?? 1)),
  );
  return resolved ? resolved.map((amount) => Math.round(amount * increment * 10000) / 10000) : null;
}
