"use client";

import { useRouter } from "next/navigation";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { STORE_COOKIE, STORE_IDS, storeDisplayName, type StoreId } from "@/lib/config/stores";
import { writeFilterCookie } from "@/lib/filters/range";

const OPTIONS = STORE_IDS.map((id) => ({ value: id, label: storeDisplayName(id) }));

/**
 * Top-bar store switcher. The choice is a cookie (like Period), so it follows the
 * operator across every page; the refresh re-renders the current page for that store.
 */
export function StoreFilter({ store }: { store: StoreId }) {
  const router = useRouter();

  function onChange(next: string | null) {
    if (next == null || next === store) return;
    writeFilterCookie(STORE_COOKIE, next);
    router.refresh();
  }

  return (
    <Select value={store} onValueChange={onChange} items={OPTIONS}>
      <SelectTrigger size="sm" aria-label="Store" className="min-w-24 max-md:h-10 max-md:text-sm">
        <span className="size-1.5 shrink-0 rounded-full bg-emerald-500" aria-hidden />
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {OPTIONS.map((opt) => (
          <SelectItem key={opt.value} value={opt.value}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
