import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { isAllowlisted } from "@/lib/auth/allowlist";

/**
 * App-owned Google OAuth (Auth.js) — replaces Cloud Run browser IAP (Issue #210).
 * Allowlist is the sole access gate; Cloud Run is --allow-unauthenticated.
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
    authorized({ auth: session, request }) {
      const path = request.nextUrl.pathname;
      if (
        path.startsWith("/api/auth") ||
        path === "/login" ||
        path.startsWith("/login/")
      ) {
        return true;
      }
      return !!session;
    },
  },
});
