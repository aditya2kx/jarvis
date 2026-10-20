import type { MetadataRoute } from "next";

// "Add to Home Screen" on iOS opens the console full-screen like an app (behind the same
// IAP sign-in). Icons come from app/icon.tsx and app/apple-icon.tsx.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Palmetto, Texas — Operator Console",
    short_name: "Palmetto",
    start_url: "/home",
    scope: "/",
    display: "standalone",
    background_color: "#0a0a0a",
    theme_color: "#0a0a0a",
    icons: [{ src: "/icon", sizes: "512x512", type: "image/png", purpose: "any" }],
  };
}
