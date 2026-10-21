import Link from "next/link";
import { LockKeyholeIcon, RefreshCwIcon } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/** Full-page refusal for a page the signed-in person can't open. */
export function NoAccess({
  pageLabel,
  email,
  nextHref,
  nextLabel,
  error,
}: {
  pageLabel: string;
  email: string;
  nextHref: string | null;
  nextLabel: string | null;
  error: string | null;
}) {
  return (
    <div className="flex min-h-[60dvh] items-center justify-center" data-testid="no-access">
      <Card className="w-full max-w-md">
        <CardContent className="flex flex-col items-center gap-4 py-8 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
            {error ? <RefreshCwIcon className="size-5" aria-hidden /> : <LockKeyholeIcon className="size-5" aria-hidden />}
          </span>
          <div className="flex flex-col gap-1.5">
            <h1 className="text-lg font-semibold tracking-tight">
              {error ? "Access check unavailable" : `You don't have access to ${pageLabel}`}
            </h1>
            <p className="text-sm text-muted-foreground">
              {error
                ? `${error} Reload in a moment — if it keeps happening, tell the console owner.`
                : "Ask the console owner to give you access to this page."}
            </p>
            {email ? (
              <p className="text-xs text-muted-foreground">
                Signed in as <span className="font-medium text-foreground">{email}</span>
              </p>
            ) : null}
          </div>
          {nextHref && nextLabel && !error ? (
            <Link href={nextHref} className={buttonVariants({ variant: "outline" })}>
              Go to {nextLabel}
            </Link>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
