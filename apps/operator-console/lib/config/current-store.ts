import "server-only";
import { cookies } from "next/headers";
import { DEFAULT_STORE_ID, parseStore, STORE_COOKIE, type StoreId } from "./stores";

/**
 * The store this request is for: the `oc_store` cookie set by the top-bar store
 * switcher, else the default store. Outside a request (scripts, tests) there is
 * no cookie jar, which also means the default store.
 */
export async function currentStore(): Promise<StoreId> {
  try {
    return parseStore((await cookies()).get(STORE_COOKIE)?.value);
  } catch {
    return DEFAULT_STORE_ID;
  }
}
