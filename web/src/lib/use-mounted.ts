"use client";

import { useSyncExternalStore } from "react";

const emptySubscribe = () => () => {};

/** False during SSR/hydration, true after mount — without a setState-in-effect. */
export function useMounted(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  );
}
