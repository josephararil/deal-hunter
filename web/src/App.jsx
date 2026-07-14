import { useEffect, useMemo, useState, useCallback } from "react";

const TIER = {
  diamond: { label: "Diamond", glyph: "◆" },
  good: { label: "Good find", glyph: "●" },
  skip: { label: "Skipped", glyph: "·" },
};
const TIER_ORDER = { diamond: 0, good: 1, skip: 2 };

// Local dev reads the synced copy in public/ (npm run dev syncs it from ../state).
// The deployed build fetches straight from GitHub's raw content each page load, so the
// site is always current the moment CI commits a new deals_history.json — no rebuild
// or redeploy needed when the data changes, only when the UI code itself changes.
const DATA_URL = import.meta.env.DEV
  ? "/data.json"
  : "https://raw.githubusercontent.com/josephararil/deal-hunter/main/state/deals_history.json";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function sign(n) {
  if (n === null || n === undefined) return "0";
  return n >= 0 ? `+${n}` : `${n}`;
}

function fmtEur(n) {
  if (n === null || n === undefined) return null;
  return Number.isInteger(n) ? `${n}` : n.toFixed(0);
}

function firstOption(entry) {
  return (entry.options || [])[0] || {};
}

function entryKey(e) {
  return `${e.date}|${e.deal_id}|${e.destination}`;
}

function parseISO(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((s || "").trim());
  if (!m) return null;
  return { y: +m[1], mo: +m[2] - 1, d: +m[3] };
}

// "2026-09-10 - 2026-09-13" -> "10–13 Sep 2026"; "2026-08-28 - 2026-09-02" -> "28 Aug – 2 Sep 2026"
function prettyWindow(win) {
  const parts = (win || "").split(/\s+-\s+/);
  const a = parseISO(parts[0]);
  const b = parseISO(parts[1]);
  if (!a || !b) return win || "";
  const nights = Math.round((Date.UTC(b.y, b.mo, b.d) - Date.UTC(a.y, a.mo, a.d)) / 864e5);
  const suffix = nights > 0 ? ` · ${nights} night${nights > 1 ? "s" : ""}` : "";
  if (a.y === b.y && a.mo === b.mo) return `${a.d}–${b.d} ${MONTHS[a.mo]} ${a.y}${suffix}`;
  if (a.y === b.y) return `${a.d} ${MONTHS[a.mo]} – ${b.d} ${MONTHS[b.mo]} ${a.y}${suffix}`;
  return `${a.d} ${MONTHS[a.mo]} ${a.y} – ${b.d} ${MONTHS[b.mo]} ${b.y}${suffix}`;
}

function fmtDate(s) {
  const p = parseISO(s);
  return p ? `${p.d} ${MONTHS[p.mo]} ${p.y}` : s || "";
}

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem("df-theme") || "system");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    localStorage.setItem("df-theme", theme);
  }, [theme]);
  const toggle = useCallback(() => {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setTheme((t) => {
      const current = t === "system" ? (prefersDark ? "dark" : "light") : t;
      return current === "dark" ? "light" : "dark";
    });
  }, []);
  return [theme, toggle];
}

export default function App() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [tiers, setTiers] = useState({ diamond: true, good: true, skip: true });
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("date");
  const [selectedKey, setSelectedKey] = useState(null);
  const [theme, toggleTheme] = useTheme();

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => r.json())
      .then((d) => setEntries(d.entries || []))
      .catch((e) => setError(String(e)));
  }, []);

  const stats = useMemo(() => {
    if (!entries || !entries.length) return null;
    const prices = entries
      .map((e) => firstOption(e).price_per_night_eur)
      .filter((p) => p != null);
    return {
      total: entries.length,
      diamonds: entries.filter((e) => e.tier === "diamond").length,
      best: prices.length ? Math.min(...prices) : null,
      latest: entries.map((e) => e.date).sort().pop(),
    };
  }, [entries]);

  const visible = useMemo(() => {
    if (!entries) return [];
    const q = query.trim().toLowerCase();
    let list = entries.filter((e) => tiers[e.tier] !== false);
    if (q)
      list = list.filter((e) => `${e.destination} ${e.window}`.toLowerCase().includes(q));
    list = [...list].sort((a, b) => {
      if (sortBy === "date")
        return b.date.localeCompare(a.date) || TIER_ORDER[a.tier] - TIER_ORDER[b.tier];
      if (sortBy === "score") return (b.final_score ?? -1) - (a.final_score ?? -1);
      if (sortBy === "price")
        return (
          (firstOption(a).price_per_night_eur ?? Infinity) -
          (firstOption(b).price_per_night_eur ?? Infinity)
        );
      return 0;
    });
    return list;
  }, [entries, tiers, query, sortBy]);

  const selected = useMemo(
    () => entries?.find((e) => entryKey(e) === selectedKey) || null,
    [entries, selectedKey]
  );

  const counts = useMemo(() => {
    const c = { diamond: 0, good: 0, skip: 0 };
    (entries || []).forEach((e) => { if (c[e.tier] != null) c[e.tier]++; });
    return c;
  }, [entries]);

  if (error)
    return (
      <div className="center-msg">
        <div className="center-card">
          <div className="center-glyph">⚠</div>
          Couldn’t load the deals feed.
          <code>{error}</code>
        </div>
      </div>
    );
  if (!entries)
    return (
      <div className="center-msg">
        <div className="loader" aria-label="Loading">
          <span className="gem-spin">◆</span>
          <p>Polishing the latest finds…</p>
        </div>
      </div>
    );

  return (
    <div className="app">
      <GradientDefs />
      <div className="aurora" aria-hidden="true" />

      <header className="hero">
        <div className="hero-top">
          <div className="brand">
            <span className="brand-gem">◆</span>
            <div>
              <h1>Diamond Finder</h1>
              <p className="tagline">Rare family travel deals, found while you sleep.</p>
            </div>
          </div>
          <button className="theme-btn" onClick={toggleTheme} aria-label="Toggle colour theme">
            <span className="theme-icon">{theme === "dark" ? "☀" : "☾"}</span>
          </button>
        </div>

        {stats && (
          <div className="stat-row">
            <Stat value={stats.total} label="deals tracked" />
            <Stat value={stats.diamonds} label="diamonds" accent="diamond" />
            <Stat value={stats.best != null ? `€${fmtEur(stats.best)}` : "—"} label="lowest / night" />
            <Stat value={fmtDate(stats.latest)} label="last run" small />
          </div>
        )}
      </header>

      <div className="toolbar">
        <div className="search-wrap">
          <span className="search-icon">⌕</span>
          <input
            className="search"
            placeholder="Search a hotel or destination…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button className="search-clear" onClick={() => setQuery("")} aria-label="Clear search">
              ✕
            </button>
          )}
        </div>

        <div className="segmented" role="group" aria-label="Filter by tier">
          {Object.keys(TIER).map((t) => (
            <button
              key={t}
              className={`seg seg-${t}` + (tiers[t] ? " on" : "")}
              onClick={() => setTiers({ ...tiers, [t]: !tiers[t] })}
              aria-pressed={tiers[t]}
            >
              <span className="seg-glyph">{TIER[t].glyph}</span>
              {TIER[t].label}
              <span className="seg-count">{counts[t]}</span>
            </button>
          ))}
        </div>

        <div className="select-wrap">
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="Sort deals">
            <option value="date">Newest first</option>
            <option value="score">Highest score</option>
            <option value="price">Lowest price</option>
          </select>
          <span className="select-caret">▾</span>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="empty-grid">
          <span className="empty-gem">◇</span>
          <p>No deals match these filters.</p>
        </div>
      ) : (
        <div className="grid">
          {visible.map((e, i) => (
            <DealCard
              key={entryKey(e)}
              entry={e}
              index={i}
              onOpen={() => setSelectedKey(entryKey(e))}
            />
          ))}
        </div>
      )}

      <footer className="site-foot">
        <span className="brand-gem small">◆</span>
        Everything that ever made it to the inbox — nothing more, nothing less.
      </footer>

      <Drawer entry={selected} onClose={() => setSelectedKey(null)} />
    </div>
  );
}

function Stat({ value, label, accent, small }) {
  return (
    <div className={`stat${accent ? ` stat-${accent}` : ""}`}>
      <div className={`stat-value${small ? " sm" : ""}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function ScoreRing({ score, tier, size = 56 }) {
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score ?? 0));
  const off = c * (1 - pct / 100);
  return (
    <svg className="ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle className="ring-track" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} fill="none" />
      <circle
        className="ring-value"
        cx={size / 2}
        cy={size / 2}
        r={r}
        strokeWidth={stroke}
        fill="none"
        stroke={`url(#grad-${tier})`}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={off}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text className="ring-text" x="50%" y="50%" dominantBaseline="central" textAnchor="middle">
        {score ?? "—"}
      </text>
    </svg>
  );
}

function DealCard({ entry: e, index, onOpen }) {
  const opt = firstOption(e);
  const ppn = fmtEur(opt.price_per_night_eur);
  const blurb = e.value_case || e.about || e.summary || "";
  return (
    <button
      className={`card tier-${e.tier}`}
      style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}
      onClick={onOpen}
    >
      <div className="card-sheen" aria-hidden="true" />
      <div className="card-head">
        <span className={`badge tier-${e.tier}`}>
          <span className="badge-glyph">{TIER[e.tier]?.glyph}</span>
          {TIER[e.tier]?.label || e.tier}
        </span>
        <ScoreRing score={e.final_score} tier={e.tier} />
      </div>

      <h3 className="card-dest">{e.destination}</h3>
      <div className="card-window">
        <span className="ico">🗓</span>
        {prettyWindow(e.window)}
      </div>

      <div className="card-price">
        {ppn ? (
          <>
            <span className="price-eur">€{ppn}</span>
            <span className="price-unit">/ night</span>
            {opt.total_eur != null && <span className="price-total">€{fmtEur(opt.total_eur)} total</span>}
          </>
        ) : (
          <span className="price-none">Price on request</span>
        )}
      </div>

      {blurb && <p className="card-blurb">{blurb}</p>}

      <div className="card-foot">
        {e.baseline_note ? (
          <span className="pill pill-baseline">{e.baseline_note}</span>
        ) : (
          <span className="pill pill-muted">emailed {fmtDate(e.date)}</span>
        )}
        <span className="card-open">Details →</span>
      </div>
    </button>
  );
}

function Drawer({ entry: e, onClose }) {
  useEffect(() => {
    if (!e) return;
    const onKey = (ev) => ev.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [e, onClose]);

  if (!e) return null;
  const total = fmtEur(firstOption(e).total_eur);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className={`drawer tier-${e.tier}`} onClick={(ev) => ev.stopPropagation()} role="dialog" aria-modal="true">
        <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>

        <div className="drawer-hero">
          <span className={`badge tier-${e.tier}`}>
            <span className="badge-glyph">{TIER[e.tier]?.glyph}</span>
            {TIER[e.tier]?.label || e.tier}
          </span>
          <h2>{e.destination}</h2>
          <div className="drawer-sub">
            {e.type} · {prettyWindow(e.window)} · emailed {fmtDate(e.date)}
          </div>
        </div>

        <div className="scorebar">
          <ScoreRing score={e.final_score} tier={e.tier} size={72} />
          <div className="scorebar-break">
            <div className="score-formula">
              <Chip n={e.llm_score} label="desirability" />
              <span className="op">{sign(e.price_adj)[0] === "-" ? "−" : "+"}</span>
              <Chip n={Math.abs(e.price_adj ?? 0)} label="price" tone={(e.price_adj ?? 0) >= 0 ? "up" : "down"} />
              <span className="op">{sign(e.transit_adj)[0] === "-" ? "−" : "+"}</span>
              <Chip n={Math.abs(e.transit_adj ?? 0)} label="transit" tone={(e.transit_adj ?? 0) >= 0 ? "up" : "down"} />
            </div>
            <div className="score-final">
              = <b>{e.final_score ?? "—"}</b><span>/100 · {e.tier}</span>
            </div>
          </div>
        </div>

        {e.summary && <p className="d-summary">{e.summary}</p>}
        {e.about && <p className="d-about">{e.about}</p>}
        {e.value_case && (
          <div className="value-case">
            <span className="vc-label">Why it’s a deal</span>
            {e.value_case}
          </div>
        )}
        {e.why && <p className="d-about">{e.why}</p>}

        {e.baseline_note && <div className="d-note baseline">📉 {e.baseline_note}</div>}

        {firstOption(e).price_per_night_eur != null && (
          <div className="price-banner">
            <div>
              <span className="pb-eur">€{fmtEur(firstOption(e).price_per_night_eur)}</span>
              <span className="pb-unit"> / night</span>
            </div>
            {total && <div className="pb-total">€{total} all-in</div>}
          </div>
        )}

        {e.options?.length > 0 && (
          <div className="opts">
            <div className="opts-title">Availability</div>
            {e.options.map((o, i) => (
              <div className="opt" key={i}>
                <div className="opt-main">
                  <div className="opt-dates">{o.dates}</div>
                  <div className="opt-src">{o.source}</div>
                </div>
                <div className="opt-right">
                  <div className="opt-price">
                    {o.price_per_night_eur != null && <span>€{fmtEur(o.price_per_night_eur)}<small>/night</small></span>}
                    {o.total_eur != null && <span className="opt-total">€{fmtEur(o.total_eur)} total</span>}
                  </div>
                  {o.booking_url && (
                    <a className="book-btn" href={o.booking_url} target="_blank" rel="noreferrer">
                      Book ↗
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {!e.options?.length && e.how_to_book && (
          <div className="d-note"><b>How to book:</b> {e.how_to_book}</div>
        )}

        {e.child_price_caveat && (
          <div className="d-note warning">
            ⚠ Live rate is a base room price — reconfirm the 4-year-old is included before booking.
          </div>
        )}
        {e.red_flags && <div className="d-note flag">🚩 {e.red_flags}</div>}

        <div className="drawer-meta">
          {e.confidence && (
            <span className={`pill conf conf-${e.confidence}`}>Grounding: {e.confidence}</span>
          )}
          {e.grounding && <p className="grounding">{e.grounding}</p>}
        </div>
      </aside>
    </div>
  );
}

function Chip({ n, label, tone }) {
  return (
    <span className={`chip${tone ? ` chip-${tone}` : ""}`}>
      <b>{n ?? "—"}</b>
      <span>{label}</span>
    </span>
  );
}

function GradientDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <linearGradient id="grad-diamond" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#67e8f9" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
        <linearGradient id="grad-good" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fbbf24" />
          <stop offset="1" stopColor="#f97316" />
        </linearGradient>
        <linearGradient id="grad-skip" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#cbd5e1" />
          <stop offset="1" stopColor="#94a3b8" />
        </linearGradient>
      </defs>
    </svg>
  );
}
