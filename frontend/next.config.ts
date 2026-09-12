import type { NextConfig } from "next";

// Адрес backend (Core API). Переопределяется через BACKEND_URL в frontend/.env.local
// или переменной окружения; по умолчанию — локальный uvicorn на :8000.
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
