"use client";

import { create } from "zustand";
import type { Me } from "@bower/api";

// The client-side auth state: the user projection plus whether a /me check
// has resolved. No token ever lives here, auth rides the HttpOnly bwr_session
// cookie. `checked` distinguishes "not looked yet" from "looked, logged out"
// (both have user === null), so useUser doesn't re-fetch /me on every mount.
export interface AuthState {
  user: Me | null;
  checked: boolean;
  setUser: (user: Me | null) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  checked: false,
  // A definitive result (a user OR a confirmed-null) marks the check resolved.
  setUser: (user) => set({ user, checked: true }),
  // Reset to unknown so the next mount re-checks (e.g. after logout).
  clear: () => set({ user: null, checked: false }),
}));
