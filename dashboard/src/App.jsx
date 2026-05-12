import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Cell,
  PieChart,
  Pie,
  CartesianGrid,
  Area,
  AreaChart,
} from "recharts";

const API = "http://localhost:8000/api";

const T = {
  bg: "#08080d",
  surface: "#101018",
  surface2: "#16161f",
  hover: "#1c1c28",
  border: "#1e1e2d",
  border2: "#282840",
  text: "#e4e4ec",
  dim: "#7a7a92",
  muted: "#4e4e66",
  green: "#22c55e",
  greenBg: "rgba(34,197,94,0.06)",
  red: "#ef4444",
  redBg: "rgba(239,68,68,0.06)",
  accent: "#818cf8",
  accentDim: "#6366f1",
  session: {
    asian: "#34d399",
    london: "#60a5fa",
    new_york: "#f472b6",
    off_hours: "#a1a1aa",
  },
  mono: "'JetBrains Mono','SF Mono','Cascadia Code',monospace",
  sans: "'Geist','Satoshi',-apple-system,BlinkMacSystemFont,sans-serif",
};

function useFetch(url) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const refetch = useCallback(() => {
    setLoading(true);
    fetch(url)
      .then((r) => r.json())
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [url]);
  useEffect(() => {
    refetch();
  }, [refetch]);
  return { data, loading, refetch };
}

function PnlText({ value, size = 13 }) {
  if (value == null) return <span style={{ color: T.muted }}>—</span>;
  const c = value > 0 ? T.green : value < 0 ? T.red : T.dim;
  return (
    <span
      style={{ color: c, fontSize: size, fontFamily: T.mono, fontWeight: 600 }}
    >
      {value > 0 ? "+" : ""}
      {value.toFixed(2)}
    </span>
  );
}

function Pill({ children, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: active ? T.accentDim : "transparent",
        color: active ? "#fff" : T.dim,
        border: `1px solid ${active ? "transparent" : T.border}`,
        borderRadius: 6,
        padding: "5px 13px",
        fontSize: 12,
        cursor: "pointer",
        fontWeight: active ? 600 : 400,
        fontFamily: T.sans,
      }}
    >
      {children}
    </button>
  );
}

function Card({ children, style }) {
  return (
    <div
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: 10,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function StatCard({ label, value, sub, color }) {
  return (
    <Card style={{ padding: "16px 18px", flex: "1 1 155px", minWidth: 140 }}>
      <div
        style={{
          fontSize: 10,
          color: T.muted,
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 24,
          fontWeight: 700,
          color: color || T.text,
          fontFamily: T.mono,
        }}
      >
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: T.dim, marginTop: 4 }}>{sub}</div>
      )}
    </Card>
  );
}

function Section({ title, children, right }) {
  return (
    <div style={{ marginTop: 28 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 14,
        }}
      >
        <h2 style={{ fontSize: 14, fontWeight: 600, color: T.text, margin: 0 }}>
          {title}
        </h2>
        {right}
      </div>
      {children}
    </div>
  );
}

function Empty({ msg }) {
  return (
    <div
      style={{
        color: T.dim,
        padding: "40px 20px",
        textAlign: "center",
        fontSize: 13,
      }}
    >
      {msg}
    </div>
  );
}

function formatDuration(ms) {
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${mins % 60}m`;
  return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
}

// ─── Stats Panel ─────────────────────────────────────────────
function StatsPanel({ period }) {
  const { data: s } = useFetch(`${API}/stats?period=${period}`);
  if (!s) return null;
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <StatCard
        label="Net P&L"
        value={s.net_pnl?.toFixed(2) ?? "0.00"}
        color={s.net_pnl >= 0 ? T.green : T.red}
      />
      <StatCard
        label="Win Rate"
        value={`${s.win_rate ?? 0}%`}
        sub={`${s.winners ?? 0}W · ${s.losers ?? 0}L`}
        color={(s.win_rate ?? 0) >= 50 ? T.green : T.red}
      />
      <StatCard label="Profit Factor" value={s.profit_factor ?? "—"} />
      <StatCard
        label="Avg Win"
        value={s.avg_winner?.toFixed(2) ?? "0"}
        color={T.green}
      />
      <StatCard
        label="Avg Loss"
        value={s.avg_loser?.toFixed(2) ?? "0"}
        color={T.red}
      />
      <StatCard
        label="Trades"
        value={s.total_trades ?? 0}
        sub={s.open_trades ? `${s.open_trades} open` : null}
      />
      {s.avg_rr && <StatCard label="Avg R:R" value={s.avg_rr} />}
    </div>
  );
}

// ─── Equity Curve ────────────────────────────────────────────
function EquityCurve({ period }) {
  const { data } = useFetch(`${API}/stats/equity?period=${period}`);
  if (!data?.length)
    return (
      <Card>
        <Empty msg="No closed trades yet" />
      </Card>
    );
  const final = data[data.length - 1].cumulative;
  const color = final >= 0 ? T.green : T.red;
  return (
    <Card style={{ padding: "16px 6px 6px 0" }}>
      <ResponsiveContainer width="100%" height={250}>
        <AreaChart
          data={data}
          margin={{ top: 8, right: 16, bottom: 4, left: 8 }}
        >
          <defs>
            <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.15} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={T.border} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: T.muted }}
            tickFormatter={(v) =>
              v
                ? new Date(v).toLocaleDateString("en", {
                    month: "short",
                    day: "numeric",
                  })
                : ""
            }
            interval="preserveStartEnd"
          />
          <YAxis tick={{ fontSize: 10, fill: T.muted }} />
          <Tooltip
            contentStyle={{
              background: T.surface,
              border: `1px solid ${T.border}`,
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v, name) => [
              v?.toFixed(2),
              name === "cumulative" ? "Cumulative" : "Trade",
            ]}
            labelFormatter={(v) => (v ? new Date(v).toLocaleString() : "")}
          />
          <Area
            type="monotone"
            dataKey="cumulative"
            stroke={color}
            strokeWidth={2}
            fill="url(#eqGrad)"
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
}

// ─── Session Breakdown ───────────────────────────────────────
function SessionBreakdown({ period }) {
  const { data } = useFetch(`${API}/stats/sessions?period=${period}`);
  if (!data?.length) return null;
  const total = data.reduce((a, s) => a + s.count, 0);
  return (
    <Card style={{ padding: 20 }}>
      <div
        style={{
          display: "flex",
          gap: 24,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <div style={{ width: 160, height: 160, flexShrink: 0 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie
                data={data}
                dataKey="count"
                nameKey="session"
                cx="50%"
                cy="50%"
                innerRadius={46}
                outerRadius={70}
                strokeWidth={2}
                stroke={T.surface}
              >
                {data.map((s) => (
                  <Cell key={s.session} fill={T.session[s.session] || T.dim} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          {data.map((s) => (
            <div
              key={s.session}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "7px 0",
                borderBottom: `1px solid ${T.border}`,
              }}
            >
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 2,
                  background: T.session[s.session] || T.dim,
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  flex: 1,
                  fontSize: 13,
                  color: T.text,
                  textTransform: "capitalize",
                }}
              >
                {s.session?.replace("_", " ")}
              </span>
              <span style={{ fontSize: 11, color: T.dim, fontFamily: T.mono }}>
                {s.count}
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: T.muted,
                  width: 36,
                  textAlign: "right",
                }}
              >
                {total > 0 ? Math.round((s.count / total) * 100) : 0}%
              </span>
              <PnlText value={s.pnl} size={12} />
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// ─── Symbol Performance ──────────────────────────────────────
function SymbolPerformance({ period }) {
  const { data } = useFetch(`${API}/stats/symbols?period=${period}`);
  if (!data?.length) return null;
  return (
    <Card style={{ padding: "14px 6px 6px 0" }}>
      <ResponsiveContainer
        width="100%"
        height={Math.max(180, data.length * 34)}
      >
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 20, bottom: 4, left: 56 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke={T.border}
            horizontal={false}
          />
          <XAxis type="number" tick={{ fontSize: 10, fill: T.muted }} />
          <YAxis
            type="category"
            dataKey="symbol"
            tick={{ fontSize: 11, fill: T.text }}
            width={50}
          />
          <Tooltip
            contentStyle={{
              background: T.surface,
              border: `1px solid ${T.border}`,
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(v) => [v.toFixed(2), "P&L"]}
            labelFormatter={(sym) => {
              const s = data.find((d) => d.symbol === sym);
              return s ? `${sym} · ${s.count} trades · ${s.win_rate}% WR` : sym;
            }}
          />
          <Bar dataKey="pnl" radius={[0, 4, 4, 0]}>
            {data.map((s) => (
              <Cell
                key={s.symbol}
                fill={s.pnl >= 0 ? T.green : T.red}
                fillOpacity={0.7}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  );
}

// ─── Calendar Heatmap ────────────────────────────────────────
function CalendarHeatmap({ period }) {
  const { data } = useFetch(`${API}/stats/calendar?period=${period}`);
  const [tooltip, setTooltip] = useState(null);

  const dayMap = useMemo(() => {
    if (!data?.length) return {};
    const m = {};
    data.forEach((d) => {
      m[d.date] = d;
    });
    return m;
  }, [data]);

  const grid = useMemo(() => {
    if (!data?.length) return [];
    const dates = data.map((d) => new Date(d.date));
    const minDate = new Date(Math.min(...dates));
    const maxDate = new Date(Math.max(...dates));
    const start = new Date(minDate);
    start.setDate(start.getDate() - start.getDay());
    const end = new Date(maxDate);
    end.setDate(end.getDate() + (6 - end.getDay()));
    const weeks = [];
    let current = new Date(start);
    let week = [];
    while (current <= end) {
      week.push(current.toISOString().split("T")[0]);
      if (week.length === 7) {
        weeks.push(week);
        week = [];
      }
      current.setDate(current.getDate() + 1);
    }
    if (week.length) weeks.push(week);
    return weeks;
  }, [data]);

  if (!data?.length)
    return (
      <Card>
        <Empty msg="No closed trades for this period" />
      </Card>
    );

  const maxAbs = Math.max(...data.map((d) => Math.abs(d.pnl)), 1);
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];

  const getColor = (pnl) => {
    if (pnl == null) return "transparent";
    const i = Math.min(Math.abs(pnl) / maxAbs, 1);
    if (pnl > 0) return `rgba(34,197,94,${0.12 + i * 0.65})`;
    if (pnl < 0) return `rgba(239,68,68,${0.12 + i * 0.65})`;
    return T.border;
  };

  return (
    <Card style={{ padding: 20, overflowX: "auto", position: "relative" }}>
      <div style={{ display: "flex", gap: 2 }}>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            marginRight: 4,
            paddingTop: 18,
          }}
        >
          {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
            <div
              key={i}
              style={{
                height: 14,
                width: 14,
                fontSize: 9,
                color: T.muted,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {i % 2 === 1 ? d : ""}
            </div>
          ))}
        </div>
        {grid.map((week, wi) => {
          const firstDay = new Date(week[0]);
          const showMonth = wi === 0 || firstDay.getDate() <= 7;
          return (
            <div
              key={wi}
              style={{ display: "flex", flexDirection: "column", gap: 2 }}
            >
              <div
                style={{
                  height: 14,
                  fontSize: 9,
                  color: T.dim,
                  textAlign: "center",
                }}
              >
                {showMonth ? months[firstDay.getMonth()] : ""}
              </div>
              {week.map((dateStr) => {
                const d = dayMap[dateStr];
                return (
                  <div
                    key={dateStr}
                    onMouseEnter={() => d && setTooltip(d)}
                    onMouseLeave={() => setTooltip(null)}
                    style={{
                      width: 14,
                      height: 14,
                      borderRadius: 2,
                      background: d ? getColor(d.pnl) : T.surface2,
                      border: `1px solid ${d ? "transparent" : T.border}`,
                    }}
                  />
                );
              })}
            </div>
          );
        })}
      </div>
      {tooltip && (
        <div
          style={{
            position: "absolute",
            top: 8,
            right: 16,
            background: T.surface2,
            border: `1px solid ${T.border2}`,
            borderRadius: 8,
            padding: "10px 14px",
            fontSize: 12,
            zIndex: 10,
            pointerEvents: "none",
          }}
        >
          <div style={{ color: T.text, fontWeight: 600, marginBottom: 4 }}>
            {tooltip.date}
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <span style={{ color: T.dim }}>
              P&L: <PnlText value={tooltip.pnl} size={12} />
            </span>
            <span style={{ color: T.dim }}>
              {tooltip.count} trade{tooltip.count !== 1 ? "s" : ""}
            </span>
            <span style={{ color: T.dim }}>
              {tooltip.winners}W · {tooltip.losers}L
            </span>
          </div>
        </div>
      )}
      <div
        style={{ display: "flex", gap: 6, marginTop: 14, alignItems: "center" }}
      >
        <span style={{ fontSize: 10, color: T.muted }}>Loss</span>
        {[0.7, 0.4, 0.15].map((o) => (
          <div
            key={o}
            style={{
              width: 12,
              height: 12,
              borderRadius: 2,
              background: `rgba(239,68,68,${o})`,
            }}
          />
        ))}
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: 2,
            background: T.border,
            margin: "0 2px",
          }}
        />
        {[0.15, 0.4, 0.7].map((o) => (
          <div
            key={o}
            style={{
              width: 12,
              height: 12,
              borderRadius: 2,
              background: `rgba(34,197,94,${o})`,
            }}
          />
        ))}
        <span style={{ fontSize: 10, color: T.muted }}>Profit</span>
      </div>
    </Card>
  );
}

// ─── Trade Detail Modal ──────────────────────────────────────
function TradeDetail({ tradeId, onClose }) {
  const { data: trade, refetch } = useFetch(`${API}/trades/${tradeId}`);
  const [noteText, setNoteText] = useState("");
  const [noteType, setNoteType] = useState("general");
  const [noteFilter, setNoteFilter] = useState("all");
  const [uploading, setUploading] = useState(false);
  const [screenshotCaption, setScreenshotCaption] = useState("");
  const [screenshotTf, setScreenshotTf] = useState("");

  if (!trade) return null;

  const addNote = async () => {
    if (!noteText.trim()) return;
    const form = new FormData();
    form.append("content", noteText);
    form.append("note_type", noteType);
    await fetch(`${API}/trades/${tradeId}/notes`, {
      method: "POST",
      body: form,
    });
    setNoteText("");
    refetch();
  };

  const deleteNote = async (nid) => {
    await fetch(`${API}/trades/${tradeId}/notes/${nid}`, { method: "DELETE" });
    refetch();
  };

  const uploadScreenshot = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    form.append("caption", screenshotCaption);
    form.append("chart_timeframe", screenshotTf);
    await fetch(`${API}/trades/${tradeId}/screenshots`, {
      method: "POST",
      body: form,
    });
    setUploading(false);
    setScreenshotCaption("");
    setScreenshotTf("");
    refetch();
  };

  const deleteScreenshot = async (sid) => {
    await fetch(`${API}/trades/${tradeId}/screenshots/${sid}`, {
      method: "DELETE",
    });
    refetch();
  };

  const filteredNotes =
    trade.notes?.filter(
      (n) => noteFilter === "all" || n.note_type === noteFilter,
    ) || [];
  const timeframes = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"];

  const row = (label, val) => (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "6px 0",
        borderBottom: `1px solid ${T.border}`,
      }}
    >
      <span style={{ fontSize: 12, color: T.dim }}>{label}</span>
      <span style={{ fontSize: 12, color: T.text, fontFamily: T.mono }}>
        {val ?? "—"}
      </span>
    </div>
  );

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.75)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: T.surface,
          border: `1px solid ${T.border}`,
          borderRadius: 14,
          width: "100%",
          maxWidth: 640,
          maxHeight: "90vh",
          overflowY: "auto",
          padding: "24px 28px",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: 20,
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              <span
                style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color: T.text,
                  fontFamily: T.mono,
                }}
              >
                {trade.symbol}
              </span>
              <span
                style={{
                  fontSize: 14,
                  fontWeight: 700,
                  color: trade.side === "BUY" ? T.green : T.red,
                }}
              >
                {trade.side}
              </span>
              <span style={{ fontSize: 12, color: T.dim }}>
                {trade.lots?.toFixed(2)} lots
              </span>
            </div>
            <div style={{ fontSize: 11, color: T.muted, marginTop: 4 }}>
              {trade.session && (
                <span
                  style={{
                    color: T.session[trade.session] || T.dim,
                    textTransform: "capitalize",
                  }}
                >
                  {trade.session.replace("_", " ")} session
                </span>
              )}
              {" · "}#{trade.id} · {trade.status}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: T.surface2,
              border: `1px solid ${T.border}`,
              borderRadius: 6,
              color: T.dim,
              fontSize: 16,
              cursor: "pointer",
              padding: "4px 10px",
            }}
          >
            ✕
          </button>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "0 28px",
            marginBottom: 20,
          }}
        >
          <div>
            {row("Entry Price", trade.entry_price?.toFixed(5))}
            {row("Exit Price", trade.exit_price?.toFixed(5))}
            {row("Stop Loss", trade.stop_loss?.toFixed(5))}
            {row("Take Profit", trade.take_profit?.toFixed(5))}
          </div>
          <div>
            {row(
              "Entry Time",
              trade.entry_time
                ? new Date(trade.entry_time).toLocaleString()
                : null,
            )}
            {row(
              "Exit Time",
              trade.exit_time
                ? new Date(trade.exit_time).toLocaleString()
                : null,
            )}
            {row(
              "Duration",
              trade.entry_time && trade.exit_time
                ? formatDuration(
                    new Date(trade.exit_time) - new Date(trade.entry_time),
                  )
                : null,
            )}
            {row("Status", trade.status)}
          </div>
        </div>

        {trade.status === "closed" && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr 1fr",
              gap: 1,
              borderRadius: 10,
              overflow: "hidden",
              marginBottom: 24,
              background: T.border,
            }}
          >
            {[
              { label: "Net P&L", val: trade.net_profit, big: true },
              { label: "Gross", val: trade.gross_profit },
              { label: "Commission", val: trade.commission, plain: true },
              { label: "Swap", val: trade.swap, plain: true },
            ].map(({ label, val, big, plain }) => (
              <div
                key={label}
                style={{
                  background: big
                    ? trade.net_profit >= 0
                      ? T.greenBg
                      : T.redBg
                    : T.surface2,
                  padding: "12px 14px",
                }}
              >
                <div
                  style={{
                    fontSize: 10,
                    color: T.muted,
                    textTransform: "uppercase",
                    letterSpacing: "0.08em",
                    marginBottom: 4,
                  }}
                >
                  {label}
                </div>
                {plain ? (
                  <span
                    style={{ fontSize: 14, fontFamily: T.mono, color: T.text }}
                  >
                    {val?.toFixed(2) ?? "0"}
                  </span>
                ) : (
                  <PnlText value={val} size={big ? 20 : 14} />
                )}
              </div>
            ))}
          </div>
        )}

        {/* Screenshots */}
        <Section title="Screenshots">
          {trade.screenshots?.length > 0 && (
            <div
              style={{
                display: "flex",
                gap: 10,
                flexWrap: "wrap",
                marginBottom: 14,
              }}
            >
              {trade.screenshots.map((s) => (
                <div
                  key={s.id}
                  style={{
                    position: "relative",
                    borderRadius: 8,
                    overflow: "hidden",
                    border: `1px solid ${T.border}`,
                  }}
                >
                  <img
                    src={`http://localhost:8000${s.file_path}`}
                    alt={s.caption || "chart"}
                    style={{
                      width: 200,
                      height: 130,
                      objectFit: "cover",
                      display: "block",
                    }}
                  />
                  <div
                    style={{
                      position: "absolute",
                      bottom: 0,
                      left: 0,
                      right: 0,
                      background:
                        "linear-gradient(transparent, rgba(0,0,0,0.8))",
                      padding: "16px 8px 6px",
                    }}
                  >
                    {s.chart_timeframe && (
                      <span
                        style={{
                          fontSize: 10,
                          color: T.accent,
                          fontFamily: T.mono,
                          marginRight: 6,
                        }}
                      >
                        {s.chart_timeframe}
                      </span>
                    )}
                    {s.caption && (
                      <span style={{ fontSize: 10, color: T.dim }}>
                        {s.caption}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => deleteScreenshot(s.id)}
                    style={{
                      position: "absolute",
                      top: 4,
                      right: 4,
                      background: "rgba(0,0,0,0.7)",
                      color: "#fff",
                      border: "none",
                      borderRadius: 4,
                      width: 20,
                      height: 20,
                      cursor: "pointer",
                      fontSize: 11,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "flex-end",
              flexWrap: "wrap",
            }}
          >
            <div style={{ flex: 1, minWidth: 140 }}>
              <label
                style={{
                  fontSize: 10,
                  color: T.muted,
                  display: "block",
                  marginBottom: 4,
                }}
              >
                Caption
              </label>
              <input
                value={screenshotCaption}
                onChange={(e) => setScreenshotCaption(e.target.value)}
                placeholder="e.g. H1 setup before entry"
                style={{
                  width: "100%",
                  background: T.bg,
                  border: `1px solid ${T.border}`,
                  borderRadius: 6,
                  padding: "7px 10px",
                  color: T.text,
                  fontSize: 12,
                  fontFamily: T.sans,
                  boxSizing: "border-box",
                }}
              />
            </div>
            <div style={{ width: 80 }}>
              <label
                style={{
                  fontSize: 10,
                  color: T.muted,
                  display: "block",
                  marginBottom: 4,
                }}
              >
                Timeframe
              </label>
              <select
                value={screenshotTf}
                onChange={(e) => setScreenshotTf(e.target.value)}
                style={{
                  width: "100%",
                  background: T.bg,
                  border: `1px solid ${T.border}`,
                  borderRadius: 6,
                  padding: "7px 8px",
                  color: T.text,
                  fontSize: 12,
                  fontFamily: T.sans,
                }}
              >
                <option value="">—</option>
                {timeframes.map((tf) => (
                  <option key={tf} value={tf}>
                    {tf}
                  </option>
                ))}
              </select>
            </div>
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "7px 14px",
                background: T.bg,
                border: `1px dashed ${T.border2}`,
                borderRadius: 6,
                cursor: "pointer",
                fontSize: 12,
                color: T.dim,
              }}
            >
              {uploading ? "Uploading…" : "📎 Upload"}
              <input
                type="file"
                accept="image/*"
                onChange={uploadScreenshot}
                style={{ display: "none" }}
              />
            </label>
          </div>
        </Section>

        {/* Notes */}
        <Section
          title="Notes"
          right={
            <div style={{ display: "flex", gap: 4 }}>
              {["all", "general", "entry_reason", "exit_reason", "lesson"].map(
                (t) => (
                  <Pill
                    key={t}
                    active={noteFilter === t}
                    onClick={() => setNoteFilter(t)}
                  >
                    {t === "all" ? "All" : t.replace("_", " ")}
                  </Pill>
                ),
              )}
            </div>
          }
        >
          {filteredNotes.length > 0 && (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                marginBottom: 14,
              }}
            >
              {filteredNotes.map((n) => (
                <div
                  key={n.id}
                  style={{
                    background: T.bg,
                    border: `1px solid ${T.border}`,
                    borderRadius: 8,
                    padding: "10px 14px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      marginBottom: 4,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 10,
                        color: T.accent,
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                        fontWeight: 600,
                      }}
                    >
                      {n.note_type.replace("_", " ")}
                    </span>
                    <button
                      onClick={() => deleteNote(n.id)}
                      style={{
                        background: "none",
                        border: "none",
                        color: T.muted,
                        fontSize: 13,
                        cursor: "pointer",
                        padding: 0,
                      }}
                    >
                      ×
                    </button>
                  </div>
                  <div style={{ fontSize: 13, color: T.text, lineHeight: 1.6 }}>
                    {n.content}
                  </div>
                  <div style={{ fontSize: 10, color: T.muted, marginTop: 6 }}>
                    {n.created_at
                      ? new Date(n.created_at).toLocaleString()
                      : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
            {["general", "entry_reason", "exit_reason", "lesson"].map((t) => (
              <Pill
                key={t}
                active={noteType === t}
                onClick={() => setNoteType(t)}
              >
                {t.replace("_", " ")}
              </Pill>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <textarea
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder="Why did you take this trade? What did you learn?"
              rows={3}
              style={{
                flex: 1,
                background: T.bg,
                border: `1px solid ${T.border}`,
                borderRadius: 8,
                padding: "10px 14px",
                color: T.text,
                fontSize: 13,
                resize: "vertical",
                fontFamily: T.sans,
                lineHeight: 1.5,
              }}
            />
            <button
              onClick={addNote}
              disabled={!noteText.trim()}
              style={{
                background: noteText.trim() ? T.accentDim : T.surface2,
                color: noteText.trim() ? "#fff" : T.muted,
                border: "none",
                borderRadius: 8,
                padding: "0 20px",
                cursor: noteText.trim() ? "pointer" : "default",
                fontSize: 13,
                fontWeight: 600,
                fontFamily: T.sans,
              }}
            >
              Add
            </button>
          </div>
        </Section>
      </div>
    </div>
  );
}

// ─── Trade Table ─────────────────────────────────────────────
function TradeTable({ period, filters, onSelectTrade }) {
  const params = new URLSearchParams({ period, limit: "500" });
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.side) params.set("side", filters.side);
  if (filters.session) params.set("session", filters.session);
  if (filters.status) params.set("status", filters.status);
  const { data, loading } = useFetch(`${API}/trades?${params}`);

  const exportCsv = () => {
    if (!data?.trades?.length) return;
    const headers = [
      "ID",
      "Symbol",
      "Side",
      "Lots",
      "Entry",
      "Exit",
      "Entry Time",
      "Exit Time",
      "SL",
      "TP",
      "Gross",
      "Commission",
      "Swap",
      "Net P&L",
      "Session",
      "Status",
    ];
    const rows = data.trades.map((t) => [
      t.id,
      t.symbol,
      t.side,
      t.lots?.toFixed(2),
      t.entry_price?.toFixed(5),
      t.exit_price?.toFixed(5) ?? "",
      t.entry_time ?? "",
      t.exit_time ?? "",
      t.stop_loss ?? "",
      t.take_profit ?? "",
      t.gross_profit ?? "",
      t.commission ?? "",
      t.swap ?? "",
      t.net_profit ?? "",
      t.session ?? "",
      t.status,
    ]);
    const csv = [headers, ...rows].map((r) => r.join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trades_${period}_${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <Empty msg="Loading trades…" />;
  if (!data?.trades?.length)
    return (
      <Card>
        <Empty msg="No trades match your filters" />
      </Card>
    );

  const h = {
    fontSize: 10,
    color: T.muted,
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    padding: "10px 12px",
    textAlign: "left",
    borderBottom: `1px solid ${T.border}`,
    position: "sticky",
    top: 0,
    background: T.surface,
    zIndex: 2,
  };
  const c = {
    padding: "9px 12px",
    fontSize: 12,
    borderBottom: `1px solid ${T.border}`,
    fontFamily: T.mono,
  };

  return (
    <Card style={{ overflow: "hidden" }}>
      <div style={{ maxHeight: 520, overflowY: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {[
                "Symbol",
                "Side",
                "Lots",
                "Entry",
                "Exit",
                "Net P&L",
                "Session",
                "Time",
                "",
              ].map((col) => (
                <th key={col} style={h}>
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.trades.map((t) => (
              <tr
                key={t.id}
                onClick={() => onSelectTrade(t.id)}
                style={{ cursor: "pointer" }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.background = T.hover)
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.background = "transparent")
                }
              >
                <td style={{ ...c, color: T.text, fontWeight: 600 }}>
                  {t.symbol}
                </td>
                <td style={{ ...c, color: t.side === "BUY" ? T.green : T.red }}>
                  {t.side}
                </td>
                <td style={{ ...c, color: T.text }}>{t.lots?.toFixed(2)}</td>
                <td style={{ ...c, color: T.text }}>
                  {t.entry_price?.toFixed(5)}
                </td>
                <td style={{ ...c, color: T.text }}>
                  {t.exit_price?.toFixed(5) || "—"}
                </td>
                <td style={c}>
                  <PnlText value={t.net_profit} />
                </td>
                <td style={c}>
                  <span
                    style={{
                      color: T.session[t.session] || T.dim,
                      fontSize: 11,
                      textTransform: "capitalize",
                    }}
                  >
                    {t.session?.replace("_", " ") || "—"}
                  </span>
                </td>
                <td
                  style={{
                    ...c,
                    color: T.dim,
                    fontSize: 11,
                    fontFamily: T.sans,
                  }}
                >
                  {t.entry_time
                    ? new Date(t.entry_time).toLocaleString("en", {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : "—"}
                </td>
                <td style={{ ...c, color: T.muted, fontFamily: T.sans }}>
                  {t.notes?.length ? `📝${t.notes.length}` : ""}
                  {t.screenshots?.length ? ` 📸${t.screenshots.length}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 14px",
          borderTop: `1px solid ${T.border}`,
        }}
      >
        <span style={{ fontSize: 11, color: T.dim }}>
          {data.trades.length} of {data.total} trades
        </span>
        <button
          onClick={exportCsv}
          style={{
            background: T.surface2,
            border: `1px solid ${T.border}`,
            borderRadius: 6,
            padding: "5px 12px",
            fontSize: 11,
            color: T.dim,
            cursor: "pointer",
            fontFamily: T.sans,
          }}
        >
          ⬇ Export CSV
        </button>
      </div>
    </Card>
  );
}

// ─── Filter Bar ──────────────────────────────────────────────
function FilterBar({ filters, setFilters, filterOptions }) {
  const s = {
    background: T.bg,
    color: T.text,
    border: `1px solid ${T.border}`,
    borderRadius: 6,
    padding: "6px 10px",
    fontSize: 12,
    fontFamily: T.sans,
  };
  return (
    <div
      style={{
        display: "flex",
        gap: 8,
        flexWrap: "wrap",
        alignItems: "center",
      }}
    >
      <select
        value={filters.symbol || ""}
        onChange={(e) =>
          setFilters((f) => ({ ...f, symbol: e.target.value || null }))
        }
        style={s}
      >
        <option value="">All Symbols</option>
        {filterOptions.symbols?.map((sym) => (
          <option key={sym} value={sym}>
            {sym}
          </option>
        ))}
      </select>
      <select
        value={filters.side || ""}
        onChange={(e) =>
          setFilters((f) => ({ ...f, side: e.target.value || null }))
        }
        style={s}
      >
        <option value="">All Sides</option>
        <option value="BUY">BUY</option>
        <option value="SELL">SELL</option>
      </select>
      <select
        value={filters.session || ""}
        onChange={(e) =>
          setFilters((f) => ({ ...f, session: e.target.value || null }))
        }
        style={s}
      >
        <option value="">All Sessions</option>
        {filterOptions.sessions?.map((sess) => (
          <option key={sess} value={sess}>
            {sess}
          </option>
        ))}
      </select>
      <select
        value={filters.status || ""}
        onChange={(e) =>
          setFilters((f) => ({ ...f, status: e.target.value || null }))
        }
        style={s}
      >
        <option value="">All Status</option>
        <option value="open">Open</option>
        <option value="closed">Closed</option>
      </select>
      {Object.values(filters).some(Boolean) && (
        <button
          onClick={() =>
            setFilters({
              symbol: null,
              side: null,
              session: null,
              status: null,
            })
          }
          style={{
            background: "none",
            border: "none",
            color: T.accent,
            fontSize: 12,
            cursor: "pointer",
            fontFamily: T.sans,
          }}
        >
          Clear
        </button>
      )}
    </div>
  );
}

// ─── App ─────────────────────────────────────────────────────
export default function App() {
  const [period, setPeriod] = useState("month");
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [filters, setFilters] = useState({
    symbol: null,
    side: null,
    session: null,
    status: null,
  });
  const [tab, setTab] = useState("overview");
  const { data: filterOptions } = useFetch(`${API}/filters`);

  return (
    <div
      style={{
        background: T.bg,
        color: T.text,
        minHeight: "100vh",
        fontFamily: T.sans,
      }}
    >
      <div
        style={{
          borderBottom: `1px solid ${T.border}`,
          padding: "14px 28px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span
          style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.02em" }}
        >
          Trading Journal
        </span>
        <div style={{ display: "flex", gap: 3 }}>
          {[
            ["today", "Today"],
            ["week", "Week"],
            ["month", "Month"],
            ["3months", "3M"],
            ["year", "Year"],
            ["all", "All"],
          ].map(([k, l]) => (
            <Pill key={k} active={period === k} onClick={() => setPeriod(k)}>
              {l}
            </Pill>
          ))}
        </div>
      </div>
      <div
        style={{
          borderBottom: `1px solid ${T.border}`,
          padding: "0 28px",
          display: "flex",
        }}
      >
        {[
          ["overview", "Overview"],
          ["trades", "Trade Log"],
          ["calendar", "Calendar"],
        ].map(([k, l]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            style={{
              background: "none",
              border: "none",
              borderBottom:
                tab === k ? `2px solid ${T.accent}` : "2px solid transparent",
              color: tab === k ? T.text : T.dim,
              padding: "11px 18px",
              fontSize: 13,
              cursor: "pointer",
              fontFamily: T.sans,
              fontWeight: tab === k ? 600 : 400,
            }}
          >
            {l}
          </button>
        ))}
      </div>
      <div style={{ padding: "18px 28px 40px", maxWidth: 1200 }}>
        {tab === "overview" && (
          <>
            <StatsPanel period={period} />
            <Section title="Equity Curve">
              <EquityCurve period={period} />
            </Section>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 20,
              }}
            >
              <Section title="Sessions">
                <SessionBreakdown period={period} />
              </Section>
              <Section title="Symbols">
                <SymbolPerformance period={period} />
              </Section>
            </div>
          </>
        )}
        {tab === "trades" && (
          <>
            <div style={{ marginBottom: 14 }}>
              <FilterBar
                filters={filters}
                setFilters={setFilters}
                filterOptions={filterOptions || {}}
              />
            </div>
            <TradeTable
              period={period}
              filters={filters}
              onSelectTrade={setSelectedTrade}
            />
          </>
        )}
        {tab === "calendar" && (
          <Section title="Trading Calendar">
            <CalendarHeatmap period={period} />
          </Section>
        )}
      </div>
      {selectedTrade && (
        <TradeDetail
          tradeId={selectedTrade}
          onClose={() => setSelectedTrade(null)}
        />
      )}
    </div>
  );
}
