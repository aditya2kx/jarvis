/**
 * Short operator-facing explanations for Plaid Personal Finance Categories (v2).
 * Source: Plaid PFC taxonomy (primary intent). Detailed codes inherit the primary
 * blurb plus a one-line nuance when we know it.
 */

export interface PfcDefinition {
  code: string;
  title: string;
  summary: string;
  /** What usually shows up for a restaurant / franchise ops ledger. */
  opsNote?: string;
}

const PRIMARY: Record<string, PfcDefinition> = {
  INCOME: {
    code: "INCOME",
    title: "Income",
    summary: "Money coming into the account — payroll, deposits, refunds classified as income.",
    opsNote: "Square settlements often land here (or as TRANSFER_IN).",
  },
  TRANSFER_IN: {
    code: "TRANSFER_IN",
    title: "Transfer in",
    summary: "Money moved into this account from another account (yours or someone else's).",
    opsNote: "Owner contributions and external bank transfers often appear here.",
  },
  TRANSFER_OUT: {
    code: "TRANSFER_OUT",
    title: "Transfer out",
    summary: "Money moved out of this account to another account, app, or person.",
    opsNote: "Payroll ACH, Zelle, and account-to-account moves are common. Internal card payments may be better marked Internal.",
  },
  LOAN_PAYMENTS: {
    code: "LOAN_PAYMENTS",
    title: "Loan payments",
    summary: "Payments toward credit cards, loans, or other debt.",
    opsNote: "Paying your own Chase card from checking is often an internal transfer — mark Internal so Money out is not double-counted.",
  },
  LOAN_DISBURSEMENTS: {
    code: "LOAN_DISBURSEMENTS",
    title: "Loan disbursements",
    summary: "Credit extended or applied on a loan/credit product (including card payment thank-yous).",
    opsNote: "On a credit card, 'Payment Thank You' is usually the other leg of a checking→card payment.",
  },
  FOOD_AND_DRINK: {
    code: "FOOD_AND_DRINK",
    title: "Food and drink",
    summary: "Restaurants, groceries, coffee, and related food purchases.",
  },
  GENERAL_MERCHANDISE: {
    code: "GENERAL_MERCHANDISE",
    title: "General merchandise",
    summary: "Retail goods — Amazon, Walmart, wholesale, and similar.",
  },
  GENERAL_SERVICES: {
    code: "GENERAL_SERVICES",
    title: "General services",
    summary: "Non-merchandise services — SaaS, professional services, auto, etc.",
  },
  TRANSPORTATION: {
    code: "TRANSPORTATION",
    title: "Transportation",
    summary: "Fuel, rideshare, tolls, parking, transit.",
  },
  TRAVEL: {
    code: "TRAVEL",
    title: "Travel",
    summary: "Flights, hotels, and travel booking.",
  },
  RENT_AND_UTILITIES: {
    code: "RENT_AND_UTILITIES",
    title: "Rent and utilities",
    summary: "Rent/mortgage and utility bills.",
  },
  BANK_FEES: {
    code: "BANK_FEES",
    title: "Bank fees",
    summary: "Account fees, overdraft, wire fees, and similar bank charges.",
  },
  ENTERTAINMENT: {
    code: "ENTERTAINMENT",
    title: "Entertainment",
    summary: "Streaming, events, hobbies, gambling.",
  },
  PERSONAL_CARE: {
    code: "PERSONAL_CARE",
    title: "Personal care",
    summary: "Gym, salon, pharmacy, and personal wellness.",
  },
  MEDICAL: {
    code: "MEDICAL",
    title: "Medical",
    summary: "Healthcare providers, dental, vision, medical supplies.",
  },
  GOVERNMENT_AND_NON_PROFIT: {
    code: "GOVERNMENT_AND_NON_PROFIT",
    title: "Government and non-profit",
    summary: "Taxes, licenses, donations, and government payments.",
  },
  HOME_IMPROVEMENT: {
    code: "HOME_IMPROVEMENT",
    title: "Home improvement",
    summary: "Hardware, furniture, and home services.",
  },
  OTHER: {
    code: "OTHER",
    title: "Other",
    summary: "Plaid could not map a more specific primary category.",
  },
};

const DETAILED_HINTS: Record<string, string> = {
  LOAN_PAYMENTS_CREDIT_CARD_PAYMENT:
    "Detailed: credit-card payment. If this is paying your own linked card, mark Internal.",
  TRANSFER_OUT_ACCOUNT_TRANSFER: "Detailed: account-to-account transfer out.",
  TRANSFER_IN_ACCOUNT_TRANSFER: "Detailed: account-to-account transfer in.",
  TRANSFER_OUT_TRANSFER_OUT_FROM_APPS: "Detailed: transfer via an app (Zelle, Venmo, etc.).",
  INCOME_CONTRACTOR: "Detailed: contractor / merchant settlement style income.",
  GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT: "Detailed: tax payment.",
  TRANSPORTATION_TOLLS: "Detailed: tolls / toll-road charges.",
};

export function pfcPrimaryDefinition(code: string | null | undefined): PfcDefinition {
  const key = (code || "").trim() || "OTHER";
  return (
    PRIMARY[key] || {
      code: key,
      title: key.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()),
      summary: "Plaid personal-finance category (PFC v2). No custom Palmetto definition yet.",
    }
  );
}

export function pfcDetailedHint(code: string | null | undefined): string | null {
  if (!code) return null;
  return DETAILED_HINTS[code] || null;
}
