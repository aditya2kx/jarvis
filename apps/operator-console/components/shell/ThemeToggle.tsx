"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

const STORAGE_KEY = "oc_theme";

export type ConsoleTheme = "dark" | "light";

function applyTheme(theme: ConsoleTheme) {
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

/** Reads / toggles `dark` on <html>; persists to localStorage (`oc_theme`). */
export function ThemeToggle() {
  const [theme, setTheme] = useState<ConsoleTheme>("dark");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      const next: ConsoleTheme = stored === "light" ? "light" : "dark";
      setTheme(next);
      applyTheme(next);
    } catch {
      applyTheme("dark");
    }
  }, []);

  function toggle() {
    const next: ConsoleTheme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore quota / private mode
    }
  }

  const isDark = theme === "dark";
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={toggle}
      aria-label={isDark ? "Switch to light background" : "Switch to dark background"}
      title={isDark ? "Light background" : "Dark background"}
      className="gap-1.5"
    >
      {isDark ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
      <span className="hidden sm:inline">{isDark ? "Light" : "Dark"}</span>
    </Button>
  );
}
