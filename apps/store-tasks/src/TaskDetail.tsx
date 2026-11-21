import { CalendarDays, Check, Copy, FileText, Film, Pencil, Repeat2, Send, Trash2, UserPlus } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, isOffline, type Photo, type TaskFile, type Session, type Shift, type Task, type TaskDetail as Detail, type Template } from "./api";
import { AddPhotos, PhotoGrid } from "./Photos";
import { repeatLabel } from "./Repeat";
import type { TaskDraft } from "./TaskForm";
import { Avatar, Button, cx, firstName, inputClass, Notice, Tick, time } from "./ui";

const URL_PATTERN = /(https?:\/\/[^\s)]+)/g;

interface Props {
  task: Task;
  subtasks: Task[];
  shift: Shift | undefined;
  names: Map<string, string>;
  session: Session;
  readOnly: boolean;
  onToggle: (t: Task) => void;
  onAssign: (t: Task) => void;
  onAssignMe: (t: Task) => void;
  onChanged: (t: Task) => void;
  onOpen: (id: string) => void;
  /** Start a new task from this one. `once` is false when the original repeats and can be copied as such. */
  onDuplicate: (draft: TaskDraft, once: boolean) => void;
  /** A manager changes when a repeating task comes back, its shift or its place in the list. */
  onEditDefinition: (definition: Template) => void;
  /** The task was deleted: close it and reload the list. */
  onRemoved: () => void;
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex min-h-7 items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

const offlineOr = (e: unknown, what: string) => (isOffline(e) ? `No connection. ${what} — try again when Wi-Fi is back.` : (e as Error).message);

/** Everything about one task: how to do it, who is doing it, photos and notes. */
export function TaskDetail({ task, subtasks, shift, names, session, readOnly, onToggle, onAssign, onAssignMe, onChanged, onOpen, onDuplicate, onEditDefinition, onRemoved }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");
  const isManager = session.staff.role === "manager";
  const me = session.staff.id;

  useEffect(() => {
    let live = true;
    setDetail(null);
    setError("");
    setEditing(null);
    setNote("");
    api
      .task(session.token, task.id)
      .then((d) => live && setDetail(d))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [session.token, task.id]);

  const run = async (what: string, work: () => Promise<void>) => {
    setError("");
    try {
      await work();
    } catch (e) {
      setError(offlineOr(e, what));
    }
  };

  const done = task.status === "done";
  const photos = task.photos ?? [];
  const comments = task.comments ?? [];
  const files = task.files ?? [];
  const checklists = task.checklists ?? [];
  const blocked = !done && task.needs_photo && !photos.length;
  const assignee = task.assignee_id ? names.get(task.assignee_id) : undefined;
  const reference = detail?.reference_photos ?? [];
  const earlier = detail?.recent_photos ?? [];
  const definition = detail?.definition ?? null;
  const duplicate = () => {
    const { id: _id, ...copy } = definition ?? ({} as Partial<Template>);
    onDuplicate(
      { ...copy, title: task.title, instructions: task.instructions, shift: task.shift, needs_photo: task.needs_photo, order: undefined, copy_from: definition?.id },
      !(definition && isManager),
    );
  };

  const saveInstructions = () =>
    run("The change was not saved", async () => {
      setSaving(true);
      try {
        onChanged(await api.setInstructions(session.token, task.id, editing ?? ""));
        setEditing(null);
      } finally {
        setSaving(false);
      }
    });

  const sendNote = () =>
    run("The note was not saved", async () => {
      const text = note.trim();
      if (!text) return;
      onChanged(await api.comment(session.token, task.id, text));
      setNote("");
    });

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>{[shift?.label, task.due_at && `due ${time(task.due_at)}`].filter(Boolean).join(" · ")}</span>
          {task.source !== "clickup" && detail && (
            <span className="fade flex items-center gap-1.5">
              {definition ? <Repeat2 className="size-3.5" /> : <CalendarDays className="size-3.5" />}
              {definition ? repeatLabel(definition) : "This day only"}
              {definition && isManager && !readOnly && (
                <button onClick={() => onEditDefinition(definition)} className="rounded font-medium text-primary underline-offset-2 hover:underline">
                  Change
                </button>
              )}
            </span>
          )}
        </div>
        {done ? (
          <div className="pop flex items-center justify-between gap-3 rounded-xl bg-success/10 px-4 py-3">
            <p className="flex items-center gap-2 font-medium text-success">
              <Check className="size-5" strokeWidth={3} />
              <span>
                Done{task.completed_by && ` by ${firstName(names.get(task.completed_by)) || "someone"}`}
                {task.completed_at && ` at ${time(task.completed_at)}`}
                {task.pending && " (not sent yet)"}
              </span>
            </p>
            {!readOnly && (
              <Button variant="outline" onClick={() => onToggle(task)}>
                Undo
              </Button>
            )}
          </div>
        ) : (
          !readOnly && (
            <Button disabled={blocked} onClick={() => onToggle(task)} className="min-h-13 w-full text-base">
              <Check className="size-5" />
              Mark done
            </Button>
          )
        )}
        {blocked && <Notice>Add a photo below first — this one needs a photo to be marked done.</Notice>}
        {error && <Notice tone="error">{error}</Notice>}
      </div>

      <Section title="Who is doing it">
        <div className="flex gap-2">
          <button
            disabled={readOnly}
            onClick={() => onAssign(task)}
            className="flex min-h-12 min-w-0 flex-1 items-center gap-3 rounded-lg border border-border bg-card px-3 text-left transition enabled:hover:border-ring enabled:hover:bg-accent"
          >
            {task.assignee_id && assignee ? (
              <span key={task.assignee_id} className="pop inline-flex">
                <Avatar id={task.assignee_id} name={assignee} />
              </span>
            ) : (
              <span className="flex size-8 items-center justify-center rounded-full border border-dashed border-ring text-muted-foreground">
                <UserPlus className="size-4" />
              </span>
            )}
            <span className={cx("min-w-0 flex-1 truncate text-[16px]", !assignee && "text-muted-foreground")}>{assignee ?? (readOnly ? "No one" : "Pick a person")}</span>
            {!readOnly && assignee && <span className="text-sm text-muted-foreground">Change</span>}
          </button>
          {!readOnly && task.assignee_id !== me && session.staff.assignable && (
            <Button variant="outline" onClick={() => onAssignMe(task)} className="min-h-12 shrink-0">
              I'll do it
            </Button>
          )}
        </div>
      </Section>

      <Section
        title="How to do it"
        action={
          isManager && !readOnly && editing === null ? (
            <button onClick={() => setEditing(task.instructions)} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-primary hover:bg-accent">
              <Pencil className="size-3.5" />
              {task.instructions ? "Edit" : "Add"}
            </button>
          ) : undefined
        }
      >
        {editing !== null ? (
          <div className="space-y-2">
            <textarea autoFocus rows={5} value={editing} onChange={(e) => setEditing(e.target.value)} className={cx(inputClass, "py-2")} placeholder="Steps, amounts, where things are kept…" />
            <p className="text-sm text-muted-foreground">{task.template_id ? "Shows on this task every day from now on." : "Shows on this task only."}</p>
            <div className="flex gap-2">
              <Button disabled={saving} onClick={saveInstructions}>
                {saving ? "Saving…" : "Save"}
              </Button>
              <Button variant="ghost" onClick={() => setEditing(null)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : task.instructions ? (
          <p className="whitespace-pre-wrap break-words text-[16px] leading-relaxed">
            {task.instructions.split(URL_PATTERN).map((part, i) =>
              i % 2 ? (
                <a key={i} href={part} target="_blank" rel="noreferrer" className="underline">
                  {part}
                </a>
              ) : (
                part
              ),
            )}
          </p>
        ) : (
          <p className="text-muted-foreground">
            {readOnly ? "No instructions." : isManager ? "No instructions yet. Add some so anyone can do this task." : "No instructions yet. Ask a manager if you are unsure."}
          </p>
        )}
      </Section>

      {(reference.length > 0 || (isManager && !readOnly)) && (
        <Section title="How it should look">
          <div className="space-y-3">
            <PhotoGrid
              token={session.token}
              photos={reference}
              names={names}
              canRemove={() => isManager && !readOnly}
              onRemove={(p: Photo) => run("The photo was not removed", async () => setDetail(await api.removeReferencePhoto(session.token, task.id, p.path)))}
            />
            {isManager && !readOnly && (
              <>
                {!reference.length && <p className="text-muted-foreground">Add an example photo so everyone knows what "done right" looks like.</p>}
                <AddPhotos label="Add example photo" onFile={(f) => run("The photo was not saved", async () => setDetail(await api.addReferencePhoto(session.token, task.id, f)))} />
              </>
            )}
          </div>
        </Section>
      )}

      <Section title={task.needs_photo ? "Photos · needed for this task" : "Photos"}>
        <div className="space-y-3">
          <PhotoGrid
            token={session.token}
            photos={photos}
            names={names}
            canRemove={(p) => !readOnly && (isManager || p.by === me)}
            onRemove={(p) => run("The photo was not removed", async () => onChanged(await api.removePhoto(session.token, task.id, p.path)))}
          />
          {!photos.length && readOnly && <p className="text-muted-foreground">No photos.</p>}
          {!readOnly && <AddPhotos label="Add photo" onFile={(f) => run("The photo was not saved", async () => onChanged(await api.photo(session.token, task.id, f)))} />}
        </div>
      </Section>

      {earlier.length > 0 && (
        <Section title="Photos from earlier days">
          <PhotoGrid token={session.token} photos={earlier} names={names} />
        </Section>
      )}

      {files.length > 0 && (
        <Section title={`Files · ${files.length}`}>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
            {files.map((f) => (
              <li key={f.path}>
                <FileLink token={session.token} file={f} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {checklists.map((c, i) => (
        <Section key={i} title={`${c.name} · ${c.items.filter((it) => it.done).length}/${c.items.length}`}>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
            {c.items.map((it, j) => (
              <li key={j} className="flex min-h-11 items-center gap-3 px-3 py-2">
                <Tick done={it.done} small />
                <span className="strike" data-done={it.done}>{it.name}</span>
              </li>
            ))}
          </ul>
        </Section>
      ))}

      {subtasks.length > 0 && (
        <Section title={`Steps · ${subtasks.filter((s) => s.status === "done").length}/${subtasks.length}`}>
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border">
            {subtasks.map((s) => (
              <li key={s.id} className="flex items-stretch">
                <button
                  aria-label={s.status === "done" ? `Undo ${s.title}` : `Done: ${s.title}`}
                  disabled={readOnly}
                  onClick={() => onToggle(s)}
                  className="flex w-12 shrink-0 items-center justify-center hover:bg-muted"
                >
                  <Tick done={s.status === "done"} dim={s.pending} small />
                </button>
                <button onClick={() => onOpen(s.id)} className="min-h-12 flex-1 py-2 pr-3 text-left hover:bg-muted">
                  <span className="strike" data-done={s.status === "done"}>
                    {s.title}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title={comments.length ? `Notes · ${comments.length}` : "Notes"}>
        <div className="space-y-3">
          {comments.length > 0 && (
            <ul className="space-y-3">
              {comments.map((c) => (
                <li key={c.id} className={cx("flex gap-3", c.reply_to && "pl-11")}>
                  <Avatar id={c.by} name={names.get(c.by) ?? "?"} />
                  <div className="rise min-w-0 flex-1 rounded-lg bg-muted px-3 py-2">
                    <p className="text-sm text-muted-foreground">
                      {firstName(names.get(c.by)) || "Someone"} · {time(c.at)}
                    </p>
                    <p className="whitespace-pre-wrap break-words">{c.text}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {!comments.length && readOnly && <p className="text-muted-foreground">No notes.</p>}
          {!readOnly && (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                sendNote();
              }}
            >
              <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note for the team…" className={inputClass} />
              <Button type="submit" aria-label="Send note" disabled={!note.trim()} className="shrink-0 px-3">
                <Send className="size-4" />
              </Button>
            </form>
          )}
        </div>
      </Section>

      {!readOnly && (
        <div className="flex flex-wrap gap-2 border-t border-border pt-5">
          <Button variant="outline" disabled={!detail} onClick={duplicate}>
            <Copy className="size-4" />
            Duplicate
          </Button>
          {!task.template_id && (isManager || task.created_by === me) && (
            <Button
              variant="danger"
              onClick={() =>
                window.confirm("Delete this task?") &&
                run("The task was not deleted", async () => {
                  await api.removeTask(session.token, task.id);
                  onRemoved();
                })
              }
            >
              <Trash2 className="size-4" />
              Delete task
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

/** Opens a stored video or document; the session header is needed, so it is fetched first. */
function FileLink({ token, file }: { token: string; file: TaskFile }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const Icon = file.type.startsWith("video/") ? Film : FileText;
  const open = async () => {
    setBusy(true);
    setFailed(false);
    // Opened before the fetch so Safari does not treat it as a pop-up.
    const tab = window.open("", "_blank");
    try {
      const url = await api.photoUrl(token, file.path);
      if (tab) tab.location.href = url;
      else window.location.href = url;
    } catch {
      tab?.close();
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button onClick={open} disabled={busy} className="flex min-h-12 w-full items-center gap-3 px-3 py-2 text-left transition hover:bg-muted">
      <Icon className="size-5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate">{file.name}</span>
      <span className="shrink-0 text-sm text-muted-foreground">
        {failed ? "Could not open" : busy ? "Opening…" : `${Math.max(1, Math.round(file.size / 1e6))} MB`}
      </span>
    </button>
  );
}
