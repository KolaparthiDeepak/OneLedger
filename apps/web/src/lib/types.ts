export type Loan = {
  id: string; account_id: string; lender: string; loan_type: string; currency: string; original_principal: string;
  outstanding_principal: string | null; outstanding_as_of: string | null; interest_rate_percent: string; emi_amount: string;
  tenure_months: number; principal_paid: string; interest_paid: string; fees_paid: string; prepayments: string; payments_recorded: number;
  components_all_actual: boolean | null;
  projection: { remaining_emis?: number; projected_interest?: string; projected_payoff_date?: string; next_emi_date?: string; assumptions?: string[]; error?: string } | null;
};
