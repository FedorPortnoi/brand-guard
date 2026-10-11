import type { Check, Signal } from "../src/brandguard.js";

export const sig = (tone: Signal["tone"], detail: string, impact = 0): Signal => ({ label: detail, detail, tone, impact });

export const check = (verdict: Check["verdict"], signals: Signal[], found: Partial<Check["found"]> = {}): Check => ({
  verdict, score: 0, signals, url: "https://luxe-kicks.store/p", domain: "luxe-kicks.store",
  found: { how: "link", what: "luxe-kicks.store", ...found },
});
