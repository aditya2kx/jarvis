import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NoAccess } from "@/components/access/NoAccess";
import { AccessManager } from "@/components/access/AccessManager";
import { AccessHistoryList } from "@/components/access/AccessHistoryList";
import { getAccess } from "@/lib/access/server";
import { accessDirectory, type AccessDirectory } from "@/lib/access/people";
import { accessSandboxWritable, isConsolePreview } from "@/lib/config/preview";

export const dynamic = "force-dynamic";

export default async function AccessPage() {
  const me = await getAccess();
  if (!me.realIsOwner) {
    return <NoAccess pageLabel="Access" email={me.realEmail} nextHref="/" nextLabel="the console" error={me.error} />;
  }

  let dir: AccessDirectory | null = null;
  let error: string | null = null;
  try {
    dir = await accessDirectory();
  } catch (e) {
    console.error(`console_access_directory_failed: ${e instanceof Error ? e.message : String(e)}`);
    error = "Couldn't load access right now. Nothing has changed — reload in a moment.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Access"
        subtitle="Who can sign in, and which pages and sections each person sees. Only owners see this page."
      />

      {isConsolePreview() && accessSandboxWritable() ? (
        <p className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-4 py-2 text-sm text-sky-900 dark:text-sky-200">
          Sandbox: changes here are saved to this preview&apos;s own throwaway table and never touch the live console.
        </p>
      ) : null}

      {error || !dir ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</p>
      ) : (
        <>
          {dir.signInError ? (
            <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-900 dark:text-amber-200">
              Couldn&apos;t read Google sign-in: {dir.signInError}. Page and section access still saves.
            </p>
          ) : null}

          <AccessManager people={dir.people} history={dir.history} />

          <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
            <Card>
              <CardHeader>
                <CardTitle>Recent changes</CardTitle>
                <CardDescription>Every change is kept — older versions are never overwritten.</CardDescription>
              </CardHeader>
              <CardContent>
                {dir.history.length ? (
                  <AccessHistoryList entries={dir.history.slice(0, 20)} />
                ) : (
                  <p className="text-sm text-muted-foreground">No changes yet.</p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>How access works</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-2 text-sm text-muted-foreground">
                <p>
                  <span className="font-medium text-foreground">Not set</span> — signed in before access was managed here;
                  they {dir.defaultPolicy === "full" ? "still see everything" : "see nothing"} until you edit them.
                </p>
                <p>Seeing a section means they can use its buttons too. Hidden sections never reach their browser.</p>
                <p>Adding someone also lets their Google account sign in; removing them takes that away.</p>
                <p>On any page, use <span className="font-medium text-foreground">Manage access</span> to change who sees that page.</p>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
