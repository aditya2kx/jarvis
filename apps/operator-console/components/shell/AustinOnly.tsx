import "server-only";
import type { ReactNode } from "react";
import { StoreIcon } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { currentStore } from "@/lib/config/current-store";
import { DEFAULT_STORE_ID, storeDisplayName } from "@/lib/config/stores";

/**
 * Call after the access check on a page that is not built for every store yet:
 * `const only = await austinOnly("Order Quality"); if (only) return only;`
 * so the page never loads or shows another store's data under the selected store's name.
 */
export async function austinOnly(pageLabel: string): Promise<ReactNode | null> {
  const store = await currentStore();
  if (store === DEFAULT_STORE_ID) return null;
  return (
    <div className="flex min-h-[60dvh] items-center justify-center" data-testid="austin-only">
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
            <StoreIcon className="size-5" aria-hidden />
          </span>
          <div className="flex flex-col gap-1.5">
            <h1 className="text-lg font-semibold tracking-tight">
              {pageLabel} is {storeDisplayName(DEFAULT_STORE_ID)} only for now
            </h1>
            <p className="text-sm text-muted-foreground">
              Switch the store to {storeDisplayName(DEFAULT_STORE_ID)} in the top bar to use this page. It is not set
              up for {storeDisplayName(store)} yet.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
