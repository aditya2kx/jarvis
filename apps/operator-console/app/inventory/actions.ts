"use server";

import { revalidatePath } from "next/cache";
import { operatorEmail, DEFAULT_STORE } from "@/lib/auth/identity";
import { submitRestock, setConfig, type RestockAction } from "@/lib/bq/writes";
import type { RestockRow } from "@/lib/restock/parse";

export async function submitRestockAction(deliveryDate: string, action: RestockAction, rows: RestockRow[]) {
  const by = await operatorEmail(); // IAP identity or throw — nothing writes until confirmed
  await submitRestock(DEFAULT_STORE, deliveryDate, action, rows, by);
  revalidatePath("/inventory");
}

export async function setCapacityAction(maxTubs: number) {
  const by = await operatorEmail();
  await setConfig(DEFAULT_STORE, "order_reco_max_tubs", String(maxTubs), by);
  revalidatePath("/inventory");
}
