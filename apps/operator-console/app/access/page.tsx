import { EyeIcon, FlaskConicalIcon, KeyRoundIcon, LayersIcon, LogInIcon, TriangleAlertIcon } from "lucide-react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NoAccess } from "@/components/access/NoAccess";
import { AccessManager } from "@/components/access/AccessManager";
import { AccessHistoryList } from "@/components/access/AccessHistoryList";
import { getAccess } from "@/lib/access/server";
import { accessDirectory, type AccessDirectory } from "@/lib/access/people";
import { accessSandboxWritable, isConsolePreview } from "@/lib/config/preview";

export const dynamic = "force-dynamic";

const HOW = [
  { icon: LayersIcon, text: "Give a whole page, or only some of its sections. Seeing a section means they can use its buttons too." },
  { icon: LogInIcon, text: "Adding someone lets their Google account sign in; removing them takes that away." },
  { icon: EyeIcon, text: "View as shows the console exactly as that person sees it." },
  { icon: KeyRoundIcon, text: "On any page, Manage access changes who sees just that page." },
];

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
      <PageHeader title="Access" subtitle="Who can sign in, and which pages and sections each person sees. Only owners see this page." />

      {isConsolePreview() && accessSandboxWritable() ? (
        <p className="flex items-center gap-2 rounded-lg border border-sky-500/30 bg-sky-500/[0.07] px-4 py-2.5 text-sm">
          <FlaskConicalIcon className="size-4 shrink-0 text-sky-600 dark:text-sky-400" aria-hidden />
          <span>
            <span className="font-medium">Sandbox.</span>{" "}
            <span className="text-muted-foreground">Changes here go to this preview&apos;s own table and never touch the live console.</span>
          </span>
        </p>
      ) : null}

      {error || !dir ? (
        <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</p>
      ) : (
        <>
          {dir.signInError ? (
            <p className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-4 py-2.5 text-sm">
              <TriangleAlertIcon className="size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
              <span>
                Couldn&apos;t read Google sign-in: {dir.signInError}.{" "}
                <span className="text-muted-foreground">Page and section access still saves.</span>
              </span>
            </p>
          ) : null}

          <AccessManager people={dir.people} history={dir.history} />

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
            <Card>
              <CardHeader>
                <CardTitle>Recent changes</CardTitle>
                <CardDescription>Every change is kept — older versions are never overwritten.</CardDescription>
              </CardHeader>
              <CardContent>
                {dir.history.length ? (
                  <AccessHistoryList entries={dir.history.slice(0, 12)} />
                ) : (
                  <p className="text-sm text-muted-foreground">No changes yet.</p>
                )}
              </CardContent>
            </Card>
            <Card className="h-fit">
              <CardHeader>
                <CardTitle>How access works</CardTitle>
                <CardDescription>
                  Unreviewed people signed in before access was managed here and{" "}
                  {dir.defaultPolicy === "full" ? "still see everything" : "see nothing"} until you edit them.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-col gap-3">
                  {HOW.map(({ icon: Icon, text }) => (
                    <li key={text} className="flex gap-3 text-sm text-muted-foreground">
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-foreground">
                        <Icon className="size-3.5" aria-hidden />
                      </span>
                      <span className="pt-1 leading-snug">{text}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
