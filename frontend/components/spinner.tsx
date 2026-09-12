export default function Spinner({ text = "Агент разбирает обращение…" }: { text?: string }) {
  return (
    <div className="spinner-block" role="status" aria-live="polite">
      <span className="spinner-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {text}
    </div>
  );
}
