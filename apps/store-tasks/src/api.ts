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
export const shopDevice = {
  get: () => localStorage.getItem(DEVICE_KEY),
  set: (token: string | null) => (token ? localStorage.setItem(DEVICE_KEY, token) : localStorage.removeItem(DEVICE_KEY)),
};

function authHeaders(token?: string): Record<string, string> {
  // X-Requested-With makes Google sign-in answer 401 instead of redirecting a background request.
  const headers: Record<string, string> = { "X-Requested-With": "XMLHttpRequest" };
  const device = shopDevice.get();
  if (device) headers["X-Shop-Device"] = device;
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
  try {
    const res = await fetch(path, {
      method,
      headers,
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
      signal: ctl.signal,
    });
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
  photoUrl: (token: string, path: string) => {
    let url = photoUrls.get(path);
    if (!url) {
      url = fetch(`/v1/photos/${path}`, { headers: authHeaders(token) }).then(async (res) => {
        if (!res.ok) throw new ApiError("Photo not available", res.status);
        return URL.createObjectURL(await res.blob());
      });
      url.catch(() => photoUrls.delete(path));
      photoUrls.set(path, url);
    }
    return url;
  },
  addTask: (token: string, body: { title: string; shift: string; date: string; instructions?: string; assignee_id?: string | null; needs_photo?: boolean }) =>
    call<Task>("POST", "/v1/tasks", { token, body }),
  count: (token: string, date: string) => call<{ count: Count | null }>("GET", `/v1/counts/${date}`, { token }),
  submitCount: (token: string, date: string, body: Pick<Count, "items" | "notes" | "tips">) =>
    call<{ count: Count }>("PUT", `/v1/counts/${date}`, { token, body }),
  templates: (token: string) => call<{ templates: Template[] }>("GET", "/v1/templates", { token }),
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
  history: (token: string, from: string, to: string) =>
    call<{ days: HistoryDay[] }>("GET", `/v1/history?from=${from}&to=${to}`, { token }),
};
