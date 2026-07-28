import { auth } from "@/auth";
import { redirect } from "next/navigation";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import { IapLoginBeacon } from "@/components/shell/IapLoginBeacon";

export default async function ConsoleLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const session = await auth();
  if (!session?.user?.email) {
    redirect("/login");
  }

  return (
    <>
      <IapLoginBeacon />
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Topbar />
          <main className="flex-1 overflow-y-auto p-4 md:p-6">{children}</main>
        </div>
      </div>
    </>
  );
}
