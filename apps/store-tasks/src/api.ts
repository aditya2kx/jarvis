import { track, type Step } from "./steps";

export type Role = "crew" | "manager";

export interface Staff {
  id: string;
  name: string;
  role: Role;
  active: boolean;
  has_pin: boolean;
  adp_name: string;
  /** Active in ADP: the only people a task can be given to. */
  assignable: boolean;
}

export interface Shift {
  id: string;
  label: string;
  due: string;
  team_from: string;
  team_to: string;
  due_at?: string;
}

export interface CountItem {
  id: string;
  name: string;
  kind: "text" | "number";
  /** The ClickUp form's section heading (Bases, Granola…). */
  section?: string | null;
}

export interface Bootstrap {
  store: { id: string; name: string; tz: string; cutover_date: string | null; shifts: Shift[]; count_items: CountItem[] };
  /** `shop`: a shop device signed in with the store's code, not a Google account. */
  account: { email: string; label: string; shop: boolean };
  /** Set on a person's own Google account (signed in without a PIN) and on a shop device (signed in as the shop). */
  own: { session: string; staff: Staff } | null;
  /** A shop device's renewed token. */
  device: string | null;
  staff: Staff[];
  on_today: string[];
  today: string;
}

export interface Photo {
  path: string;
  by: string | null;
  at: string | null;
  /** Set on photos from earlier days: the day they were taken for. */
  date?: string;
  /** Set on an example video (its content type); photos have none. */
  type?: string;
}

export interface Comment {
  id: string;
  by: string;
  at: string;
  text: string;
  /** Set on a reply (copied from ClickUp): the comment it answers. */
  reply_to?: string | null;
}

/** A video or document attached to a task (photos are in `photos`). */
export interface TaskFile extends Photo {
  name: string;
  type: string;
  size: number;
}

export interface Checklist {
  name: string;
  items: { name: string; done: boolean }[];
}

export interface Task {
  id: string;
  date: string;
  shift: string;
  title: string;
  instructions: string;
  order: number;
  parent_id: string | null;
  needs_photo: boolean;
  due_at: string;
  status: "open" | "done";
  assignee_id: string | null;
  completed_by: string | null;
  /** Who pressed the tick: differs from `completed_by` when the shop device ticks for the assignee. */
  ticked_by?: string | null;
  completed_at: string | null;
  note?: string;
  photos?: Photo[];
  /** How many "how it should look" photos and videos the task has (sent with the day's list). */
  examples?: { photos: number; videos: number };
  comments?: Comment[];
  files?: TaskFile[];
  /** ClickUp checklists, kept as they were. */
  checklists?: Checklist[];
  template_id?: string | null;
  created_by?: string;
  source: string;
  pending?: boolean;
}

/** What a task needs beyond the list: example photos and what earlier days' photos looked like. */
export interface TaskDetail {
  task: Task;
  reference_photos: Photo[];
  recent_photos: Photo[];
  /** The repeating task this one comes from; null for a task added for one day. */
  definition: Template | null;
}

export interface Day {
  store: string;
  date: string;
  today: string;
  generated: boolean;
  shifts: Shift[];
  teams: Record<string, { id: string | null; name: string }[]>;
  tasks: Task[];
  count_submitted: boolean;
}

export interface Count {
  items: Record<string, string>;
  notes: { shift: string; low: string; out: string };
  tips: string | number | null;
  submitted_by: string | null;
  /** Null while only photos have been added and the count itself is not sent yet. */
  submitted_at: string | null;
  photos?: Photo[];
}

/** When a repeating task is on the list. Weekday 0 is Monday. */
export interface Repeat {
  /** Empty = every day of the week. */
  weekdays: number[];
  /** 2 = every other week, counted from the week of `start_date`. */
  every_weeks: number;
  /** Days of the month (-1 = the last day). When set, weekdays do not apply. */
  month_days: number[];
  start_date: string | null;
  end_date: string | null;
}

export type FeedbackKind = "problem" | "idea";

export interface FeedbackNew {
  kind: FeedbackKind;
  text: string;
  context: Record<string, unknown>;
  events: Step[];
}

export interface Feedback extends FeedbackNew {
  id: string;
  at: string;
  status: "new" | "done";
  staff_name: string;
  device: string;
  photos: Photo[];
}

export interface Template extends Repeat {
  id: string;
  shift: string;
  title: string;
  instructions: string;
  order: number;
  parent_id: string | null;
  needs_photo: boolean;
  active: boolean;
}

export interface Account {
  email: string;
  label: string;
  staff_id: string | null;
  updated_at: string;
}

export interface ShopCode {
  set: boolean;
  updated_at: string | null;
  /** Where the shop device opens the app. */
  url: string;
}

export interface HistoryDay {
  date: string;
  shifts: Record<string, { total: number; done: number; late: number }>;
  people: Record<string, number>;
}

export interface Session {
  token: string;
  staff: Staff;
}

export interface TrackerNote {
  id: string;
  by: string;
  at: string;
  text: string;
}

/** What a person fills in for an action item. */
export interface TrackerDraft {
  title: string;
  /** Sub-points, one per line. */
  detail: string;
  owners: string[];
  /** Which stores it is about. */
  tags: string[];
  due: string | null;
}

export interface TrackerItem extends TrackerDraft {
  id: string;
  status: "open" | "done";
  created_by: string;
  created_at: string;
  done_by: string | null;
  done_at: string | null;
  notes: TrackerNote[];
}

/** Someone on a project. `emails` (their Google accounts) is sent only to the project's admin. */
export interface ProjectMember {
  id: string;
  name: string;
  emails?: string[];
}

/** A private project: apart from the stores, for the people on it, each signed in with their own Google account. */
export interface Project {
  id: string;
  name: string;
  /** This person's member id. */
  me: string;
  /** This person runs the project: they choose who is on it. */
  admin: boolean;
  admins: string[];
  members: ProjectMember[];
  tags: string[];
  items: TrackerItem[];
  /** The renewed sign-in for this browser. */
  device: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export const SHOP = "shop";
const DEVICE_KEY = "st.device";
/** This browser's sign-in: the shop's (from the shop code) or a Google account's (from the sign-in hop). */
export const device = {
  get: () => localStorage.getItem(DEVICE_KEY),
  set: (token: string | null) => (token ? localStorage.setItem(DEVICE_KEY, token) : localStorage.removeItem(DEVICE_KEY)),
};

function authHeaders(token?: string): Record<string, string> {
  // X-Requested-With makes Google sign-in answer 401 instead of redirecting a background request.
  const headers: Record<string, string> = { "X-Requested-With": "XMLHttpRequest" };
  const signedIn = device.get();
  if (signedIn) headers["X-Shop-Device"] = signedIn;
  if (token) headers["X-Staff-Session"] = token;
  return headers;
}

/** True when the request never reached the server (offline, timeout) — safe to retry later. */
export const isOffline = (e: unknown) => !(e instanceof ApiError);

async function call<T>(method: string, path: string, opts: { token?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const headers = authHeaders(opts.token);
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.form ? 60000 : 15000);
  const started = performance.now();
  const step = (status: number) => track("api", `${method} ${path.split("?")[0]}`, { status, ms: Math.round(performance.now() - started) });
  try {
    const res = await fetch(path, {
      method,
      headers,
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
      signal: ctl.signal,
    }).catch((e: unknown) => {
      step(0);
      throw e;
    });
    step(res.status);
    if (!res.ok) {
      const data = (await res.json().catch(() => null)) as { error?: string } | null;
      if (res.status === 401 && !data?.error) {
        // The Google session ran out (the API's own 401s carry a message). Reloading signs in again.
        window.location.reload();
      }
      throw new ApiError(data?.error ?? `Request failed (${res.status})`, res.status);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

const photoForm = (file: Blob) => {
  const form = new FormData();
  form.append("photo", file, "photo.jpg");
  return form;
};

const photoUrls = new Map<string, Promise<string>>();
/** A thumbnail for grids, screen size for the viewer; the original only for downloads. */
export type PhotoSize = "thumb" | "view";

export const api = {
  bootstrap: () => call<Bootstrap>("GET", "/v1/bootstrap"),
  signIn: (staff_id: string, pin: string) =>
    call<{ session: string; staff: Staff }>("POST", "/v1/sessions", { body: { staff_id, pin } }),
  enrollShop: (code: string) => call<{ device: string }>("POST", "/v1/shop/enroll", { body: { code } }),
  shopCode: (token: string) => call<ShopCode>("GET", "/v1/shop-code", { token }),
  setShopCode: (token: string, code: string) => call<ShopCode & { device: string | null }>("PUT", "/v1/shop-code", { token, body: { code } }),
  day: (token: string, date: string) => call<Day>("GET", `/v1/days/${date}`, { token }),
  complete: (token: string, id: string, at: string, note = "") =>
    call<Task>("POST", `/v1/tasks/${id}/complete`, { token, body: { at, note } }),
  reopen: (token: string, id: string) => call<Task>("POST", `/v1/tasks/${id}/reopen`, { token, body: {} }),
  removeTask: (token: string, id: string) => call<{ ok: true }>("DELETE", `/v1/tasks/${id}`, { token }),
  task: (token: string, id: string) => call<TaskDetail>("GET", `/v1/tasks/${id}`, { token }),
  assign: (token: string, task_ids: string[], assignee_id: string | null) =>
    call<{ tasks: Task[] }>("POST", "/v1/assign", { token, body: { task_ids, assignee_id } }),
  comment: (token: string, id: string, text: string) => call<Task>("POST", `/v1/tasks/${id}/comments`, { token, body: { text } }),
  setInstructions: (token: string, id: string, instructions: string) =>
    call<Task>("POST", `/v1/tasks/${id}/instructions`, { token, body: { instructions } }),
  photo: (token: string, id: string, file: Blob) => call<Task>("POST", `/v1/tasks/${id}/photo`, { token, form: photoForm(file) }),
  removePhoto: (token: string, id: string, path: string) => call<Task>("DELETE", `/v1/tasks/${id}/photos/${path}`, { token }),
  addReferencePhoto: (token: string, id: string, file: Blob) =>
    call<TaskDetail>("POST", `/v1/tasks/${id}/reference-photo`, { token, form: photoForm(file) }),
  removeReferencePhoto: (token: string, id: string, path: string) =>
    call<TaskDetail>("DELETE", `/v1/tasks/${id}/reference-photos/${path}`, { token }),
  countPhoto: (token: string, date: string, file: Blob) =>
    call<{ count: Count }>("POST", `/v1/counts/${date}/photo`, { token, form: photoForm(file) }),
  /** Photos need the session header, so they are fetched once and shown from memory. */
  photoUrl: (token: string, path: string, size?: PhotoSize) => {
    const key = size ? `${path}?size=${size}` : path;
    let url = photoUrls.get(key);
    if (!url) {
      url = fetch(`/v1/photos/${key}`, { headers: authHeaders(token) }).then(async (res) => {
        if (!res.ok) throw new ApiError("Photo not available", res.status);
        return URL.createObjectURL(await res.blob());
      });
      url.catch(() => photoUrls.delete(key));
      photoUrls.set(key, url);
    }
    return url;
  },
  addTask: (token: string, body: { title: string; shift: string; date: string; instructions?: string; assignee_id?: string | null; needs_photo?: boolean }) =>
    call<Task>("POST", "/v1/tasks", { token, body }),
  count: (token: string, date: string) => call<{ count: Count | null }>("GET", `/v1/counts/${date}`, { token }),
  submitCount: (token: string, date: string, body: Pick<Count, "items" | "notes" | "tips">) =>
    call<{ count: Count }>("PUT", `/v1/counts/${date}`, { token, body }),
  templates: (token: string) => call<{ templates: Template[] }>("GET", "/v1/templates", { token }),
  sendFeedback: (token: string, body: FeedbackNew, photo?: Blob) => {
    if (!photo) return call<{ id: string }>("POST", "/v1/feedback", { token, body });
    const form = photoForm(photo);
    form.append("data", JSON.stringify(body));
    return call<{ id: string }>("POST", "/v1/feedback", { token, form });
  },
  feedback: (token: string) => call<{ feedback: Feedback[] }>("GET", "/v1/feedback", { token }),
  closeFeedback: (token: string, id: string, done: boolean) => call<Feedback>("POST", `/v1/feedback/${id}/done`, { token, body: { done } }),
  reorderTemplates: (token: string, shift: string, ids: string[]) =>
    call<{ changed: number }>("POST", "/v1/templates/order", { token, body: { shift, ids } }),
  /** `copy_from` starts a new task with another one's example photos. */
  saveTemplate: (token: string, body: Partial<Template> & { copy_from?: string }) =>
    call<{ template: Template }>("POST", "/v1/templates", { token, body }),
  staff: (token: string) => call<{ staff: Staff[] }>("GET", "/v1/staff", { token }),
  saveStaff: (token: string, body: Partial<Staff>) => call<{ staff: Staff }>("POST", "/v1/staff", { token, body }),
  setPin: (token: string, id: string, pin: string) => call<{ ok: true }>("POST", `/v1/staff/${id}/pin`, { token, body: { pin } }),
  accounts: (token: string) => call<{ accounts: Account[] }>("GET", "/v1/accounts", { token }),
  saveAccount: (token: string, body: { email: string; label?: string; staff_id?: string | null }) =>
    call<{ account: Account }>("POST", "/v1/accounts", { token, body }),
  removeAccount: (token: string, email: string) =>
    call<{ ok: true }>("DELETE", `/v1/accounts/${encodeURIComponent(email)}`, { token }),
  /** Where this Google account can go: its store's tasks, if any, and its projects. */
  projects: () => call<{ store: { id: string; name: string } | null; projects: { id: string; name: string }[] }>("GET", "/v1/projects"),
  project: (id: string) => call<Project>("GET", `/v1/projects/${id}`),
  projectParse: (id: string, text: string) => call<{ items: TrackerDraft[] }>("POST", `/v1/projects/${id}/parse`, { body: { text } }),
  projectAdd: (id: string, items: TrackerDraft[]) => call<{ items: TrackerItem[] }>("POST", `/v1/projects/${id}/items`, { body: { items } }),
  projectUpdate: (id: string, item: string, body: Partial<TrackerDraft> & { status?: TrackerItem["status"] }) =>
    call<TrackerItem>("PATCH", `/v1/projects/${id}/items/${item}`, { body }),
  projectNote: (id: string, item: string, text: string) => call<TrackerItem>("POST", `/v1/projects/${id}/items/${item}/notes`, { body: { text } }),
  projectRemove: (id: string, item: string) => call<{ ok: true }>("DELETE", `/v1/projects/${id}/items/${item}`),
  projectFeedback: (id: string, body: FeedbackNew, photo?: Blob) => {
    if (!photo) return call<{ id: string }>("POST", `/v1/projects/${id}/feedback`, { body });
    const form = photoForm(photo);
    form.append("data", JSON.stringify(body));
    return call<{ id: string }>("POST", `/v1/projects/${id}/feedback`, { form });
  },
  projectMembers: (id: string, members: { id?: string; name: string; emails: string[] }[]) =>
    call<Project>("PUT", `/v1/projects/${id}/members`, { body: { members } }),
  history: (token: string, from: string, to: string) =>
    call<{ days: HistoryDay[] }>("GET", `/v1/history?from=${from}&to=${to}`, { token }),
};
