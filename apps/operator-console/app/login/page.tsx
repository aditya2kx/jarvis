import { signIn } from "@/auth";
import { Button } from "@/components/ui/button";

/**
 * Minimal Auth.js Google entry (Issue #210). Brand-first; one CTA.
 * Design #27 / docs/contributing/ui-polish.md — shadcn Button, no card clutter.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const params = await searchParams;
  const error = params.error;
  const denied = error === "AccessDenied";
  const otherError = Boolean(error) && !denied;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-background px-6">
      <div className="flex w-full max-w-sm flex-col items-center gap-8 text-center">
        <div className="space-y-2">
          <p className="text-3xl font-semibold tracking-tight text-foreground">Palmetto</p>
          <p className="text-sm text-muted-foreground">Operator Console</p>
        </div>
        {denied ? (
          <p className="text-sm text-destructive" role="alert">
            That Google account is not allowed. Use an allowlisted work account.
          </p>
        ) : null}
        {otherError ? (
          <p className="text-sm text-destructive" role="alert">
            Sign-in failed ({error}). Try again, or check Cloud Run auth secrets if this persists.
          </p>
        ) : null}
        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/home" });
          }}
          className="w-full"
        >
          <Button type="submit" size="lg" className="w-full">
            Sign in with Google
          </Button>
        </form>
      </div>
    </main>
  );
}
