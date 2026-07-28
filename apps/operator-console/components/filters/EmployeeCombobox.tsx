"use client";

import { useMemo, useState } from "react";
import { Popover } from "@base-ui/react/popover";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Searchable single-select employee picker (recognition drawer). */
export function EmployeeCombobox({
  id,
  value,
  options,
  onChange,
  placeholder = "Select employee…",
}: {
  id?: string;
  value: string;
  options: string[];
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((opt) => opt.toLowerCase().includes(q));
  }, [options, query]);

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <Popover.Trigger
        id={id}
        nativeButton
        type="button"
        aria-label="Employee"
        className={cn(
          "flex h-9 min-h-9 w-full items-center justify-between gap-1 rounded-md border border-border bg-background px-3 text-left text-sm outline-none transition-colors",
          "hover:bg-muted/60 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40",
          value ? "text-foreground" : "text-muted-foreground",
        )}
      >
        <span className="truncate">{value || placeholder}</span>
        <ChevronDownIcon className="size-3.5 shrink-0 opacity-60" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="start" sideOffset={4} className="z-50">
          <Popover.Popup
            className={cn(
              "flex w-[var(--anchor-width)] min-w-64 max-w-[min(24rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-lg outline-none",
              "origin-[var(--transform-origin)] transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
            )}
          >
            <div className="border-b border-border p-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search employees…"
                className="h-9 px-2 text-sm"
                aria-label="Search employees"
                autoFocus
              />
            </div>
            <ul className="max-h-64 overflow-y-auto p-1" role="listbox">
              {filtered.length === 0 ? (
                <li className="px-2 py-3 text-center text-xs text-muted-foreground">No matches</li>
              ) : (
                filtered.map((opt) => {
                  const checked = opt === value;
                  return (
                    <li key={opt}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={checked}
                        className={cn(
                          "flex min-h-9 w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                          checked && "bg-muted/70",
                        )}
                        onClick={() => {
                          onChange(opt);
                          setOpen(false);
                        }}
                      >
                        <span
                          className={cn(
                            "flex size-3.5 shrink-0 items-center justify-center rounded-sm border",
                            checked
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-border",
                          )}
                          aria-hidden
                        >
                          {checked ? <CheckIcon className="size-2.5" strokeWidth={3} /> : null}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{opt}</span>
                      </button>
                    </li>
                  );
                })
              )}
            </ul>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
