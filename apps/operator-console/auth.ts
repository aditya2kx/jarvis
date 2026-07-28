import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { isAllowlisted } from "@/lib/auth/allowlist";

/**
 * App-owned Google OAuth (Auth.js) — replaces Cloud Run browser IAP (Issue #210).
 * Allowlist is the sole access gate; Cloud Run is --allow-unauthenticated.
 * Route gating lives in `proxy.ts` (explicit redirect) + console layout session check.
 */
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Google],
  trustHost: true,
  pages: {
    signIn: "/login",
    error: "/login",
  },
  callbacks: {
    async signIn({ user }) {
      if (!user.email) return false;
      return isAllowlisted(user.email);
    },
  },
});
