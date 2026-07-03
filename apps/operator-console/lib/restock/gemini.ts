import "server-only";
import { ACTIVE_BASES } from "./parse";

// Direct REST call (no SDK) per repo convention — see .cursor/rules/
// user-preferences.mdc #11 ("prefer direct API calls over third-party
// packages with low maintenance signal"). GEMINI_API_KEY comes from Secret
// Manager at runtime (see .env.example), never committed.
const GEMINI_MODEL = "gemini-2.5-flash";
const GEMINI_URL = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;

export interface ParsedImageRow {
  item: string;
  quantity_tubs: number;
  confidence: number;
}

/**
 * Vision-parse a restock order photo into candidate (item, quantity) rows.
 * Never writes to BQ — caller runs these through validateParsedRows() and
 * shows an editable confirmation step before any write (EXECUTION.md §M3).
 */
export async function parseRestockImage(imageBase64: string, mimeType: string): Promise<ParsedImageRow[]> {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) throw new Error("parseRestockImage: GEMINI_API_KEY not configured");

  const prompt =
    `This is a photo of a restock order sheet for a smoothie/bowl shop. Extract each ` +
    `line item and its ordered quantity (in tubs). Valid item names are exactly one of: ` +
    `${ACTIVE_BASES.join(", ")}. Map any abbreviation or misspelling to the closest valid ` +
    `name. Respond with ONLY a JSON array, no prose: ` +
    `[{"item": "<one of the valid names>", "quantity_tubs": <number>, "confidence": <0-1>}]`;

  const res = await fetch(`${GEMINI_URL}?key=${apiKey}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contents: [
        {
          parts: [{ text: prompt }, { inline_data: { mime_type: mimeType, data: imageBase64 } }],
        },
      ],
      generationConfig: { responseMimeType: "application/json" },
    }),
  });

  if (!res.ok) {
    throw new Error(`parseRestockImage: Gemini API ${res.status}: ${await res.text()}`);
  }
  const body = await res.json();
  const text: string | undefined = body.candidates?.[0]?.content?.parts?.[0]?.text;
  if (!text) throw new Error("parseRestockImage: empty Gemini response");

  const parsed = JSON.parse(text);
  if (!Array.isArray(parsed)) throw new Error("parseRestockImage: Gemini did not return an array");
  return parsed;
}
