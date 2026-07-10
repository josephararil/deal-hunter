import { useEffect, useMemo, useState } from "react";

const TIER_LABEL = { diamond: "💎 Diamond", good: "👍 Good find", skip: "· Skipped" };
const TIER_ORDER = { diamond: 0, good: 1, skip: 2 };

// Local dev reads the synced copy in public/ (npm run dev syncs it from ../state).
// The deployed build fetches straight from GitHub's raw content each page load, so the
// site is always current the moment CI commits a new deals_history.json — no rebuild
// or redeploy needed when the data changes, only when the UI code itself changes.
const DATA_URL = import.meta.env.DEV
  ? "/data.json"
  : "https://raw.githubusercontent.com/josephararil/deal-hunter/main/state/deals_history.json";

function sign(n) {
  if (n === null || n === undefined) return "?";
  return n >= 0 ? `+${n}` : `${n}`;
}

function firstOption(entry) {
  return (entry.options || [])[0] || {};
}

function entryKey(e) {
  return `${e.date}|${e.deal_id}|${e.destination}`;
}

export default function App() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [tiers, setTiers] = useState({ diamond: true, good: true, skip: true });
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("date");
  const [selectedKey, setSelectedKey] = useState(null);

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => r.json())
      .then((d) => setEntries(d.entries || []))
      .catch((e) => setError(String(e)));
  }, []);

  const visible = useMemo(() => {
    if (!entries) return [];
    const q = query.trim().toLowerCase();
    let list = entries.filter((e) => tiers[e.tier] !== false);
    if (q) {
      list = list.filter((e) =>
        `${e.destination} ${e.window}`.toLowerCase().includes(q)
      );
    }
    list = [...list].sort((a, b) => {
      if (sortBy === "date") return b.date.localeCompare(a.date);
      if (sortBy === "score") return (b.final_score ?? -1) - (a.final_score ?? -1);
      if (sortBy === "price")
        return (firstOption(a).price_per_night_eur ?? Infinity) -
          (firstOption(b).price_per_night_eur ?? Infinity);
      return 0;
    });
    return list;
  }, [entries, tiers, query, sortBy]);

  const selected = useMemo(
    () => visible.find((e) => entryKey(e) === selectedKey) || visible[0] || null,
    [visible, selectedKey]
  );

  if (error) return <div className="center-msg">Failed to load data.json: {error}</div>;
  if (!entries) return <div className="center-msg">Loading…</div>;

  return (
    <div className="app">
      <header className="header">
        <h1>Diamond Finder</h1>
        <span className="count">{visible.length} of {entries.length} deals</span>
      </header>

      <div className="toolbar">
        <input
          className="search"
          placeholder="Search destination or window…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {Object.keys(TIER_LABEL).map((t) => (
          <label key={t} className="tier-toggle">
            <input
              type="checkbox"
              checked={tiers[t]}
              onChange={(e) => setTiers({ ...tiers, [t]: e.target.checked })}
            />
            {TIER_LABEL[t]}
          </label>
        ))}
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="date">Sort: newest first</option>
          <option value="score">Sort: highest score</option>
          <option value="price">Sort: lowest price</option>
        </select>
      </div>

      <div className="body">
        <ul className="list">
          {visible.map((e) => {
            const opt = firstOption(e);
            const key = entryKey(e);
            return (
              <li
                key={key}
                className={`row tier-${e.tier}` + (key === selectedKey || (!selectedKey && e === visible[0]) ? " active" : "")}
                onClick={() => setSelectedKey(key)}
              >
                <div className="row-top">
                  <span className="badge">{TIER_LABEL[e.tier] || e.tier}</span>
                  <span className="score">{e.final_score ?? "?"}/100</span>
                </div>
                <div className="row-dest">{e.destination}</div>
                <div className="row-meta">
                  {e.window} · {opt.price_per_night_eur != null ? `€${opt.price_per_night_eur}/night` : "no price"} · {e.date}
                </div>
              </li>
            );
          })}
          {visible.length === 0 && <li className="empty">No deals match this filter.</li>}
        </ul>

        <div className="detail">
          {selected ? <Detail entry={selected} /> : <div className="empty">Select a deal</div>}
        </div>
      </div>
    </div>
  );
}

function Detail({ entry: e }) {
  const opt = firstOption(e);
  return (
    <div className="detail-card">
      <div className="detail-header">
        <span className={`badge tier-${e.tier}`}>{TIER_LABEL[e.tier] || e.tier}</span>
        <h2>{e.destination}</h2>
        <div className="sub">{e.type} · {e.window} · emailed {e.date}</div>
      </div>

      {e.summary && <p className="summary">{e.summary}</p>}
      {e.about && <p className="about">{e.about}</p>}
      {e.value_case && (
        <div className="value-case"><b>Why it's a deal:</b> {e.value_case}</div>
      )}

      <div className="score-line">
        Score: {e.llm_score ?? "?"} desirability {sign(e.price_adj)} price {sign(e.transit_adj)} transit = <b>{e.final_score ?? "?"}</b>/100 ({e.tier})
      </div>
      {e.baseline_note && <div className="baseline">{e.baseline_note}</div>}

      {e.options && e.options.length > 0 && (
        <ul className="options">
          {e.options.map((o, i) => (
            <li key={i}>
              {o.dates} · {o.price_per_night_eur != null ? `€${o.price_per_night_eur}/night` : ""}
              {o.total_eur != null ? ` · €${o.total_eur} total` : ""}
              {o.booking_url && (
                <> · <a href={o.booking_url} target="_blank" rel="noreferrer">Book now</a></>
              )}
              {o.source && <span className="src"> ({o.source})</span>}
            </li>
          ))}
        </ul>
      )}
      {!e.options?.length && e.how_to_book && (
        <div className="how-to-book"><b>How to book:</b> {e.how_to_book}</div>
      )}

      {e.child_price_caveat && (
        <div className="warning">
          ⚠ Live rate is a base room price — reconfirm the child is included at this price before booking.
        </div>
      )}
      {e.grounding && <div className="grounding">Source: {e.grounding}</div>}
      {e.red_flags && <div className="red-flags">Red flags: {e.red_flags}</div>}
      {e.confidence && <div className="confidence">Grounding confidence: {e.confidence}</div>}
    </div>
  );
}
