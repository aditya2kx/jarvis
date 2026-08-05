import "server-only";

// Same direct-REST Gemini pattern as lib/restock/gemini.ts (Issue #216).
const GEMINI_MODEL = "gemini-2.5-flash";
const GEMINI_URL = `https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent`;

/**
 * Lightly paraphrase motivational framing around a fixed leaderboard block.
 * If GEMINI_TOKEN is missing or the model drops/changes the leaderboard, returns
 * the original message unchanged (never invent numbers/names).
 */
export async function varyMotivationalCopy(
  message: string,
  leaderboardMd: string,
): Promise<{ text: string; varied: boolean }> {
  const token = (process.env.GEMINI_TOKEN ?? "").trim();
  const lb = leaderboardMd.trim();
  if (!token || !lb) return { text: message, varied: false };

  const prompt =
    `You rewrite a short ClickUp team chat message for a smoothie shop.\n` +
    `Rules:\n` +
    `1. Keep the LEADERBOARD block below EXACTLY character-for-character — do not change names, dollars, bullets, or markdown.\n` +
    `2. Vary only the greeting / intro and the closing motivational lines (one-team energy, keep momentum, collaborative) so each post feels fresh.\n` +
    `3. Stay short, warm, professional. No emojis overload. No new employee names or dollar amounts.\n` +
    `4. Return ONLY the full message markdown, nothing else.\n\n` +
    `LEADERBOARD (must appear verbatim):\n${lb}\n\n` +
    `CURRENT MESSAGE:\n${message}`;

  try {
    const res = await fetch(`${GEMINI_URL}?key=${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0.9 },
      }),
    });
    if (!res.ok) {
      console.warn(`varyMotivationalCopy: Gemini ${res.status}`);
      return { text: message, varied: false };
    }
    const body = (await res.json()) as {
      candidates?: { content?: { parts?: { text?: string }[] } }[];
    };
    const text = body.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ?? "";
    if (!text || !text.includes(lb)) {
      console.warn("varyMotivationalCopy: leaderboard not preserved — falling back");
      return { text: message, varied: false };
    }
    return { text, varied: true };
  } catch (e) {
    console.warn("varyMotivationalCopy failed:", e);
    return { text: message, varied: false };
  }
}
