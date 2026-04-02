export default function Card({ title, subtitle, right, children, className = "" }) {
  return (
    <section
      className={`rounded-3xl bg-white/90 ring-1 ring-black/[0.04] shadow-[0_12px_40px_-20px_rgba(15,23,42,0.28)] backdrop-blur ${className}`}
    >
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 border-b border-black/[0.04] px-6 py-5">
          <div>
            {title ? <h2 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h2> : null}
          </div>
          {right}
        </header>
      )}
      <div className="px-6 py-5">{children}</div>
    </section>
  );
}
