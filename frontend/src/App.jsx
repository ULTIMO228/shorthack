import { useState } from "react";

export default function App() {
  const [url, setUrl] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  async function shorten(e) {
    e.preventDefault();
    setError("");
    setResult(null);
    try {
      const r = await fetch("/api/shorten", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!r.ok) throw new Error("Не удалось сократить ссылку");
      const data = await r.json();
      setResult(`${window.location.origin}${data.short_url}`);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="container">
      <h1>⚡ Shorthack</h1>
      <p className="subtitle">Короткие ссылки за секунду</p>
      <form onSubmit={shorten} className="form">
        <input
          type="url"
          required
          placeholder="https://очень-длинная-ссылка.ru/..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button type="submit">Сократить</button>
      </form>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="result">
          <a href={result} target="_blank" rel="noreferrer">{result}</a>
        </div>
      )}
    </main>
  );
}
