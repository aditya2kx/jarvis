import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ActionToastProvider } from "@/lib/actions/ActionToast";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Palmetto, Texas — Operator Console",
  description: "Operator console — replaces the Grafana dashboard.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
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
          <ActionToastProvider>{children}</ActionToastProvider>
        </TooltipProvider>
      </body>
    </html>
  );
}
