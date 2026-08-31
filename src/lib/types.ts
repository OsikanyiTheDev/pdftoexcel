export type Statement = {
  id: number;
  bank_name: string;
  account_number: string;
  account_name: string;
  currency: string;
  period_start: string | null;
  period_end: string | null;
  opening_balance: number | null;
  closing_balance: number | null;
  total_debit: number | null;
  total_credit: number | null;
  row_count: number;
  pages: number | null;
  status: string;
  verification: { issues: string[]; stats: Record<string, number> } | null;
  original_filename: string;
  storage_path: string;
  created_at: string;
};

export type Txn = {
  id: number;
  trans_date: string;
  value_date: string;
  raw_details: string;
  category: string;
  party: string;
  ref_number: string | null;
  reference_id: string | null;
  withdrawal: number;
  deposit: number;
  balance: number;
};

export const fmt = (n: number | null | undefined, ccy = "GHS") =>
  n === null || n === undefined
    ? "—"
    : `${ccy} ${n.toLocaleString("en-GH", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const fmtDate = (d: string | null) =>
  d ? new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) : "—";
