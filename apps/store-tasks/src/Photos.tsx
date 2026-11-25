import { ChevronLeft, ChevronRight, ImagePlus, Play, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Photo, type PhotoSize } from "./api";
import { Button, cx, dayLabel, firstName, time, useEscape } from "./ui";

const MAX_PHOTO_EDGE = 1600;

/** Phone photos are several MB; shrink before sending. A format the browser cannot read is sent as it is. */
export async function shrink(file: File): Promise<Blob> {
  if (file.type.startsWith("video/")) return file;
  try {
    const img = await createImageBitmap(file);
    const scale = Math.min(1, MAX_PHOTO_EDGE / Math.max(img.width, img.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not read photo"))), "image/jpeg", 0.82));
  } catch {
    return file;
  }
}

function useUrl(token: string, photo: Photo, size: PhotoSize) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const path = photo.path;
  const sized = photo.type ? undefined : size; // videos are served as they are
  useEffect(() => {
    let live = true;
    setUrl(null);
    setFailed(false);
    api
      .photoUrl(token, path, sized)
      .then((u) => live && setUrl(u))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [token, path, sized]);
  return { url, failed };
}

function Thumb({ token, photo, onOpen }: { token: string; photo: Photo; onOpen: () => void }) {
  const { url, failed } = useUrl(token, photo, "thumb");
  return (
    <button onClick={onOpen} className="group relative aspect-square overflow-hidden rounded-lg bg-muted" aria-label={photo.type ? "Play video" : "Open photo"}>
      {url && !photo.type && <img src={url} alt="" className="size-full object-cover transition group-hover:scale-105" />}
      {url && photo.type && (
        <>
          <video src={`${url}#t=0.1`} muted playsInline preload="metadata" className="size-full object-cover" />
          <span className="absolute inset-0 flex items-center justify-center">
            <span className="rounded-full bg-black/55 p-2.5 text-white transition group-hover:scale-110">
              <Play className="size-5 fill-current" />
            </span>
          </span>
        </>
      )}
      {!url && !failed && <span className="absolute inset-0 animate-pulse bg-muted" />}
      {failed && <span className="absolute inset-0 flex items-center justify-center p-1 text-center text-xs text-muted-foreground">Not available</span>}
      {photo.date && <span className="absolute inset-x-0 bottom-0 bg-black/55 px-1.5 py-0.5 text-left text-[11px] text-white">{dayLabel(photo.date)}</span>}
    </button>
  );
}

interface GridProps {
  token: string;
  photos: Photo[];
  names: Map<string, string>;
  /** Given when photos can be removed here; returns whether this person may remove that one. */
  canRemove?: (p: Photo) => boolean;
  onRemove?: (p: Photo) => Promise<void>;
}

export function PhotoGrid({ token, photos, names, canRemove, onRemove }: GridProps) {
  const [open, setOpen] = useState<number | null>(null);
  if (!photos.length) return null;
  const shown = open !== null ? photos[Math.min(open, photos.length - 1)] : null;
  return (
    <>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
        {photos.map((p, i) => (
          <Thumb key={p.path} token={token} photo={p} onOpen={() => setOpen(i)} />
        ))}
      </div>
      {shown && open !== null && (
        <Viewer
          token={token}
          photo={shown}
          caption={[firstName(names.get(shown.by ?? "")), shown.date ? dayLabel(shown.date) : "", time(shown.at)].filter(Boolean).join(" · ")}
          position={photos.length > 1 ? `${Math.min(open, photos.length - 1) + 1} of ${photos.length}` : ""}
          onClose={() => setOpen(null)}
          onStep={photos.length > 1 ? (n) => setOpen((open + n + photos.length) % photos.length) : undefined}
          onRemove={
            onRemove && canRemove?.(shown)
              ? async () => {
                  await onRemove(shown);
                  setOpen(null);
                }
              : undefined
          }
        />
      )}
    </>
  );
}

interface ViewerProps {
  token: string;
  photo: Photo;
  caption: string;
  position: string;
  onClose: () => void;
  onStep?: (n: number) => void;
  onRemove?: () => Promise<void>;
}

function Viewer({ token, photo, caption, position, onClose, onStep, onRemove }: ViewerProps) {
  const { url } = useUrl(token, photo, "view");
  const [busy, setBusy] = useState(false);

  useEscape(onClose);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") onStep?.(-1);
      if (e.key === "ArrowRight") onStep?.(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onStep]);

  const round = "rounded-full bg-white/10 p-2.5 text-white hover:bg-white/20";
  return (
    <div className="fixed inset-x-0 top-0 bottom-[calc(var(--vp-gap,0px)*-1)] z-50 flex flex-col bg-black/95" role="dialog" aria-modal="true" aria-label="Photo" onClick={onClose}>
      <div className="flex items-center justify-between gap-3 p-3 pt-[max(0.75rem,env(safe-area-inset-top))] text-white" onClick={(e) => e.stopPropagation()}>
        <p className="min-w-0 truncate text-sm">{[caption, position].filter(Boolean).join("  ·  ")}</p>
        <div className="flex shrink-0 gap-2">
          {onRemove && (
            <button
              aria-label="Remove photo"
              disabled={busy}
              className={cx(round, "disabled:opacity-40")}
              onClick={() => {
                setBusy(true);
                onRemove().finally(() => setBusy(false));
              }}
            >
              <Trash2 className="size-5" />
            </button>
          )}
          <button aria-label="Close" className={round} onClick={onClose}>
            <X className="size-5" />
          </button>
        </div>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        {!url ? (
          <p className="text-white/70">Loading…</p>
        ) : photo.type ? (
          <video key={url} src={url} controls autoPlay playsInline className="max-h-full max-w-full rounded-lg" onClick={(e) => e.stopPropagation()} />
        ) : (
          <img src={url} alt="" className="max-h-full max-w-full rounded-lg object-contain" onClick={(e) => e.stopPropagation()} />
        )}
        {onStep && (
          <>
            <button aria-label="Previous photo" className={cx(round, "absolute left-3")} onClick={(e) => (e.stopPropagation(), onStep(-1))}>
              <ChevronLeft className="size-6" />
            </button>
            <button aria-label="Next photo" className={cx(round, "absolute right-3")} onClick={(e) => (e.stopPropagation(), onStep(1))}>
              <ChevronRight className="size-6" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * One button for every device: a phone offers its camera or photo library, a computer its files.
 * `onFile` is called once per chosen photo (or video, with `videos`), in order.
 */
export function AddPhotos({ label, onFile, className, videos }: { label: string; onFile: (file: Blob) => Promise<void>; className?: string; videos?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [left, setLeft] = useState(0);

  const send = async (files: FileList | null) => {
    const list = [...(files ?? [])];
    setLeft(list.length);
    try {
      for (const f of list) {
        await onFile(await shrink(f));
        setLeft((n) => n - 1);
      }
    } finally {
      setLeft(0);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <>
      <input ref={input} type="file" accept={videos ? "image/*,video/*" : "image/*"} multiple hidden onChange={(e) => send(e.target.files)} />
      <Button variant="outline" disabled={left > 0} onClick={() => input.current?.click()} className={className}>
        <ImagePlus className="size-4" />
        {left > 0 ? `Saving ${left}…` : label}
      </Button>
    </>
  );
}
