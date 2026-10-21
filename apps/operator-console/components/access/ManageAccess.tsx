import "server-only";
import { getAccess } from "@/lib/access/server";
import type { PageId } from "@/lib/access/registry";
import { PageAccessDrawer } from "./PageAccessDrawer";

/** Page-header button for owners (hidden while they use View as); renders nothing for anyone else. */
export async function ManageAccess({ page }: { page: PageId }) {
  const a = await getAccess();
  if (!a.realIsOwner || a.viewingAs) return null;
  return <PageAccessDrawer page={page} />;
}
