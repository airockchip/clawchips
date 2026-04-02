import logo from "../assets/claw-logo.png";

/** `navHidden`: 不在侧栏显示，App 内对应页面与逻辑仍保留（例如 Providers）。 */
const NAV_ITEMS = [
  { id: "home", label: "Overview" },
  { id: "routing", label: "Routing" },
  { id: "providers", label: "Providers", navHidden: true },
  { id: "feedback", label: "Tasks" },
  { id: "memory", label: "Memory" },
];

function LogoMark() {
  return (
    <img
      src={logo}
      alt="CLAWCHIPS logo"
      className="h-12 w-12 shrink-0 object-contain"
    />
  );
}

export default function Sidebar({ page, setPage, health, hostLabel, connected }) {
  return (
    <aside className="fixed left-0 top-0 flex h-screen w-[240px] flex-col border-r border-rose-100/80 bg-white/90 px-4 py-5 backdrop-blur">
      <div className="px-3">
        <div className="flex items-center gap-3">
          <LogoMark />
          <div className="text-[18px] font-semibold tracking-tight text-slate-900">ClawChips</div>
        </div>
      </div>

      <nav className="mt-7 flex flex-1 flex-col gap-1">
        {NAV_ITEMS.filter((item) => !item.navHidden).map((item) => {
          const active = item.id === page;
          return (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              className={`rounded-2xl px-4 py-3 text-left text-sm font-medium transition ${
                active
                  ? "bg-rose-50/80 text-rose-700 shadow-sm ring-1 ring-rose-200/80"
                  : "text-slate-500 hover:bg-rose-50/55 hover:text-rose-700"
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </nav>

      <div className="rounded-2xl bg-white/95 px-4 py-4 text-sm shadow-sm ring-1 ring-rose-100/80">
        <div className="break-all font-mono text-[13px] text-slate-700">{hostLabel}</div>
        <div
          className={`mt-3 inline-flex items-center gap-2 font-medium ${
            connected ? "text-emerald-600" : "text-rose-500"
          }`}
        >
          <span className={`h-2.5 w-2.5 rounded-full ${connected ? "bg-emerald-500" : "bg-rose-500"}`} />
          <span>{connected ? "Connected" : "Disconnected"}</span>
        </div>
      </div>
    </aside>
  );
}
