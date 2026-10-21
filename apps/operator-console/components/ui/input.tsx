import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

/** Phone keypad for number fields: digits only for non-negative whole numbers, digits + "."
 *  for non-negative decimals; fields that may go negative keep the full keyboard (minus key). */
function numberInputMode(
  props: React.ComponentProps<"input">
): React.HTMLAttributes<HTMLInputElement>["inputMode"] {
  if (props.type !== "number" || props.inputMode) return props.inputMode
  if (props.min == null || Number(props.min) < 0) return undefined
  const step = props.step == null || props.step === "any" ? props.step : Number(props.step)
  return step == null || (typeof step === "number" && Number.isInteger(step)) ? "numeric" : "decimal"
}

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      inputMode={numberInputMode({ type, ...props })}
      enterKeyHint={props.enterKeyHint ?? (type === "search" ? "search" : "done")}
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Input }
