import type { Metadata, Viewport } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar } from "@/components/shell/Topbar";
import { MobileTabBar } from "@/components/shell/MobileTabBar";
import { HealthBanner } from "@/components/shell/HealthBanner";
import { IapLoginBeacon } from "@/components/shell/IapLoginBeacon";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ActionToastProvider } from "@/lib/actions/ActionToast";
import { ViewAsBanner } from "@/components/access/ViewAsBanner";
import { getAccess } from "@/lib/access/server";
import { allowedHrefs } from "@/lib/access/policy";

// Inter's optical-size axis gives 12–13px table and chart text the open "text" cut and
// page titles the tighter "display" cut; figures are tabular where numbers stack.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  axes: ["opsz"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Palmetto, Texas — Operator Console",
  description: "Operator console — replaces the Grafana dashboard.",
  applicationName: "Operator Console",
  appleWebApp: {
    capable: true,
    title: "Palmetto",
    statusBarStyle: "black-translucent",
  },
  formatDetection: { telephone: false },
};

// viewport-fit=cover lets the shell paint under the notch / home indicator; Topbar and
// MobileTabBar pad by env(safe-area-inset-*) so nothing interactive sits there.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0a0a0a" },
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
  ],
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const access = await getAccess();
  const hrefs = access.error ? [] : allowedHrefs(access.grant);
  const navHrefs = access.realIsOwner && !access.viewingAs ? [...hrefs, "/access"] : hrefs;
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        {/* Apply saved theme before paint to avoid a light flash (default = dark). */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('oc_theme');if(t==='light')document.documentElement.classList.remove('dark');else document.documentElement.classList.add('dark');}catch(e){document.documentElement.classList.add('dark');}})();`,
          }}
        />
      </head>
      <body className="min-h-full">
        <TooltipProvider>
          <ActionToastProvider>
            <IapLoginBeacon />
            <div className="flex h-dvh overflow-hidden">
              <Sidebar allowedHrefs={navHrefs} />
              <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
                <Topbar />
                {access.viewingAs ? <ViewAsBanner email={access.viewingAs} /> : null}
                <HealthBanner />
                <main className="flex-1 overflow-y-auto overscroll-contain px-4 pt-4 pb-[calc(var(--tabbar-h)+env(safe-area-inset-bottom)+1.5rem)] md:p-6">
                  {children}
                </main>
              </div>
            </div>
            <MobileTabBar allowedHrefs={navHrefs} />
          </ActionToastProvider>
        </TooltipProvider>
      </body>
    </html>
  );
}
