import { redirect } from "next/navigation";
import { getAccess } from "@/lib/access/server";
import { allowedHrefs } from "@/lib/access/policy";

export default async function RootPage() {
  const a = await getAccess();
  redirect((!a.error && allowedHrefs(a.grant)[0]) || "/home");
}
