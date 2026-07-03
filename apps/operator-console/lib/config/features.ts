// Read screens ship in M1/M2 and stay on; write paths flip on per-milestone
// as their MERGE contracts land (M3/M4). See docs/operator-console/EXECUTION.md §4.
export const FEATURES = {
  sales: true,
  labor: true,
  forecast: true,
  orderQuality: true,
  inventory: true,
  payroll: true,
  pipeline: true,
  writeGoals: false,
  writeTraining: false,
  writeRecognition: false,
  writeRestock: false,
} as const;
