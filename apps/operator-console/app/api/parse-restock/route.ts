import { NextRequest, NextResponse } from "next/server";
import { operatorEmail } from "@/lib/auth/identity";
import { parseRestockCsv, validateParsedRows, type RestockRow } from "@/lib/restock/parse";
import { parseRestockImage } from "@/lib/restock/gemini";

// Parses a restock CSV or photo into editable (item, quantity) rows. Never
// writes to BQ — the client shows these for operator confirmation, then
// submits via the submitRestockAction server action (EXECUTION.md §M3 step 5).
export async function POST(req: NextRequest) {
  try {
    await operatorEmail(); // throws if IAP's identity headers are absent/unverifiable
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "unauthorized" }, { status: 403 });
  }

  const form = await req.formData();
  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ error: "missing 'file' field" }, { status: 400 });
  }

  let rows: RestockRow[];
  let errors: string[];

  if (file.type === "text/csv" || file.name.toLowerCase().endsWith(".csv") || file.type === "text/plain") {
    const text = await file.text();
    ({ rows, errors } = parseRestockCsv(text));
  } else if (file.type.startsWith("image/")) {
    const buf = Buffer.from(await file.arrayBuffer());
    try {
      const candidates = await parseRestockImage(buf.toString("base64"), file.type);
      ({ rows, errors } = validateParsedRows(candidates));
    } catch (e) {
      return NextResponse.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502 });
    }
  } else {
    return NextResponse.json({ error: `unsupported file type: ${file.type || "unknown"}` }, { status: 400 });
  }

  return NextResponse.json({ rows, errors });
}
