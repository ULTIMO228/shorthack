"use client";

import { createContext, ReactNode, useCallback, useContext, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";

type Toast = { id: number; text: string; kind: "err" | "ok" | "info" };

const TOAST_ICON = { err: AlertTriangle, ok: CheckCircle2, info: Info } as const;

const ToastContext = createContext<{ push: (text: string, kind?: Toast["kind"]) => void }>({
  push: () => {},
});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const push = useCallback((text: string, kind: Toast["kind"] = "err") => {
    seq.current += 1;
    const id = seq.current;
    setToasts((list) => [...list, { id, text, kind }]);
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), 5000);
  }, []);

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="toast-zone" aria-live="polite">
        {toasts.map((t) => {
          const Icon = TOAST_ICON[t.kind] ?? AlertTriangle;
          return (
            <div key={t.id} className={`toast ${t.kind === "err" ? "" : t.kind}`} role="alert">
              <Icon />
              {t.text}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
