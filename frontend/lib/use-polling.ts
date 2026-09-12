"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";

// Лёгкий polling с остановкой при размонтировании (R3 research 002).
export function usePolling<T>(fn: () => Promise<T>, ms = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });

  const reload = useCallback(async () => {
    try {
      const next = await fnRef.current();
      setData(next);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError("Ошибка сети", 0));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const tick = async () => {
      if (active) await reload();
    };
    tick();
    const timer = setInterval(tick, ms);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [ms, reload]);

  return { data, error, loading, updatedAt, reload };
}
