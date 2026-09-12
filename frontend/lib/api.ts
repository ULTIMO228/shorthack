// Обёртка над fetch для API ядра (контракт 001):
// cookie-сессия (credentials: "include"), JSON, ошибки ядра { detail } пробрасываются как есть.

export class ApiError extends Error {
  status: number;

  constructor(detail: string, status: number) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

type ApiInit = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
};

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const res = await fetch(path, {
    method: init.method ?? "GET",
    credentials: "include",
    headers: init.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });

  if (res.status === 204) return undefined as T;

  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    // не-JSON ответ — обрабатывается ниже по статусу
  }

  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : `Ошибка сервера (${res.status})`;
    throw new ApiError(detail, res.status);
  }

  return data as T;
}
