import { createContext, useContext, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";

type Memory = {
  values: Record<string, unknown>;
  set: Dispatch<SetStateAction<Record<string, unknown>>>;
};
const Context = createContext<Memory | null>(null);
export function Workspace({ children }: { children: ReactNode }) {
  const [values, set] = useState<Record<string, unknown>>({});
  return (
    <Context.Provider value={{ values, set }}>{children}</Context.Provider>
  );
}
export function useMemory<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const memory = useContext(Context);
  if (!memory) throw new Error("Workspace memory boundary missing");
  const value = (memory.values[key] ?? initial) as T;
  return [
    value,
    (next) =>
      memory.set((values) => ({
        ...values,
        [key]:
          typeof next === "function"
            ? (next as (previous: T) => T)((values[key] ?? initial) as T)
            : next,
      })),
  ];
}
