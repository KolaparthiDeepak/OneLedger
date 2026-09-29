"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { QuickAdd, type QuickAddPreset } from "./quick-add";

const Ctx = createContext<(preset?: QuickAddPreset) => void>(() => {});

/** One Add sheet for the whole app: the header button, the phone "+" and page buttons all open it. */
export function QuickAddProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ open: boolean; preset?: QuickAddPreset }>({ open: false });
  const open = useCallback((preset?: QuickAddPreset) => setState({ open: true, preset }), []);
  return (
    <Ctx.Provider value={open}>
      {children}
      <QuickAdd open={state.open} preset={state.preset} onClose={() => setState({ open: false })} />
    </Ctx.Provider>
  );
}

export function useQuickAdd() {
  return useContext(Ctx);
}
