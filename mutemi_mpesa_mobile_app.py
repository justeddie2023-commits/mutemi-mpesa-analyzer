# MUTEMI M-PESA Statement Analyzer - Website Version
# Correct ONLINE version for Render. This file does NOT use tkinter.
# Render start command:
# streamlit run mutemi_mpesa_mobile_app.py --server.port $PORT --server.address 0.0.0.0

from collections import defaultdict
from datetime import datetime
import html
import re

import fitz
import pandas as pd
import streamlit as st

APP_TITLE = "MUTEMI M-PESA Statement Analyzer"

DEFAULT_LOAN_COMPANIES = [
    "CITYFIED CAPITAL", "GOLDSTEP CAPITAL", "fourth generation", "newark",
    "ELLEGANT CREDIT LTD", "OCL BUSINESS CREDIT LIMITED", "ASA LIMITED -GITHUNGURI",
    "UMOJA UFANISI", "INSPIRE CREDIT", "PEMBENI VENTURES", "SAMAWATI", "SIMPLEPAY",
    "OYA CREDIT", "TICK CREDIT", "PALLA", "CHEREHANI", "MWENYEJI INVESTMENT",
    "EDENBRIDGE", "BUSINESS CASH ADVANCE", "SASA PAY", "BIDII CREDIT", "INUKA",
    "ECLOF", "THIKA FAHALI EDEN INVESTMENT LTD", "PREMIER KENYA", "Premier SuperKwik",
]

ELLEGANT_TERMS = ["ELLEGANT CREDIT LTD", "ELEGANT CREDIT LTD"]

AMOUNT_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})*(?:\.\d{2})|[+-]?\d+(?:\.\d{2})")
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
DATETIME_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def money(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "0.00"


def format_month_label(month_str):
    try:
        return datetime.strptime(month_str, "%Y-%m").strftime("%b %Y")
    except Exception:
        return month_str


def human_file_size(size_bytes):
    try:
        size_bytes = float(size_bytes)
    except Exception:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes:.0f} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def transaction_sort_key(tx):
    return (tx.get("date", ""), tx.get("time", ""), tx.get("receipt", ""))


def parse_amount(text):
    if not text:
        return 0.0
    text = text.replace("−", "-")
    matches = AMOUNT_RE.findall(text)
    if not matches:
        return 0.0
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return 0.0


def amount_paid_value(value):
    return abs(value) if value < 0 else value


def keyword_pattern(term):
    escaped = re.escape(term.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"\s*-\s*")
    return re.compile(escaped, re.IGNORECASE)


def build_term_patterns(loan_companies):
    return {term: keyword_pattern(term) for term in loan_companies if term.strip()}


def term_matches_text(text, patterns):
    return [term for term, pattern in patterns.items() if pattern.search(text or "")]


def is_ellegant_credit_text(text):
    text = (text or "").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    words = set(text.split())
    has_name = "ELLEGANT" in words or "ELEGANT" in words
    return has_name and "CREDIT" in words and "LTD" in words


def make_text_table(headers, rows):
    if not rows:
        return ""
    all_rows = [headers] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]

    def fmt(row):
        return " │ ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers)))

    line = "─┼─".join("─" * width for width in widths)
    output = [fmt(headers), line]
    for row in rows:
        output.append(fmt(row))
    return "\n".join(output)


def risk_rating_from_percentage(percentage):
    if percentage <= 10:
        return "Very Good", 5
    if percentage <= 25:
        return "Good", 4
    if percentage <= 50:
        return "Fair", 3
    if percentage <= 75:
        return "Risky", 2
    return "Very Risky", 1


def risk_colors(rating):
    rating = (rating or "").lower()
    if rating == "very good":
        return "#DCFCE7", "#166534"
    if rating == "good":
        return "#ECFDF5", "#047857"
    if rating == "fair":
        return "#FEF9C3", "#854D0E"
    if rating == "risky":
        return "#FFEDD5", "#9A3412"
    if rating == "very risky":
        return "#FEE2E2", "#991B1B"
    return "#E2E8F0", "#334155"


def open_pdf_from_bytes(pdf_bytes, password=""):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.needs_pass:
        if not password:
            doc.close()
            return None, "password_required"
        if not doc.authenticate(password):
            doc.close()
            return None, "wrong_password"
    return doc, None


def extract_customer_name_from_pdf(doc):
    if doc is None or doc.page_count == 0:
        return "N/A"

    page = doc[0]
    text = clean_text(page.get_text("text"))
    patterns = [
        r"Customer\s+Name\s*:?\s*(.+?)\s+Mobile\s+Number",
        r"Customer\s+Name\s*:?\s*(.+?)\s+Email\s+Address",
        r"Customer\s+Name\s*:?\s*(.+?)\s+Statement\s+Period",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = clean_text(match.group(1))
            if name:
                return name

    words = page.get_text("words")
    if not words:
        return "N/A"

    sorted_words = sorted(words, key=lambda w: (round(w[1], 1), w[0]))
    for i in range(len(sorted_words) - 1):
        w1 = str(sorted_words[i][4]).strip().lower().rstrip(":")
        w2 = str(sorted_words[i + 1][4]).strip().lower().rstrip(":")
        if w1 == "customer" and w2 == "name":
            label_y = sorted_words[i][1]
            label_end_x = sorted_words[i + 1][2]
            page_width = page.rect.width
            value_words = []
            for w in sorted_words:
                word = str(w[4]).strip()
                if not word:
                    continue
                same_row = abs(w[1] - label_y) <= 6
                to_right = w[0] > label_end_x + 25
                before_qr_area = w[0] < page_width * 0.75
                if same_row and to_right and before_qr_area:
                    lower = word.lower().rstrip(":")
                    if lower in {"mobile", "number", "email", "address", "statement", "period", "request", "date"}:
                        break
                    value_words.append(word)
            name = clean_text(" ".join(value_words))
            if name:
                return name
    return "N/A"


def detect_column_ranges(words, page_width, page_height):
    top_words = [w for w in words if w[1] < page_height * 0.35]
    header_targets = {
        "receipt": ["receipt"], "completion": ["completion"], "details": ["details"],
        "status": ["transaction"], "paid": ["paid"], "withdrawn": ["withdrawn"],
        "balance": ["balance"],
    }
    starts = {}
    for col, options in header_targets.items():
        candidates = []
        for w in top_words:
            word = str(w[4]).strip().lower()
            if word in options:
                candidates.append((w[1], w[0]))
        if candidates:
            candidates.sort()
            starts[col] = candidates[0][1]

    fallback = {
        "receipt": page_width * 0.03, "completion": page_width * 0.16,
        "details": page_width * 0.31, "status": page_width * 0.49,
        "paid": page_width * 0.63, "withdrawn": page_width * 0.75,
        "balance": page_width * 0.88,
    }
    for col, value in fallback.items():
        starts.setdefault(col, value)

    ordered_cols = ["receipt", "completion", "details", "status", "paid", "withdrawn", "balance"]
    x = [starts[col] for col in ordered_cols]
    if any(x[i] >= x[i + 1] for i in range(len(x) - 1)):
        x = [fallback[col] for col in ordered_cols]

    return {
        "receipt": (0, (x[0] + x[1]) / 2),
        "completion": ((x[0] + x[1]) / 2, (x[1] + x[2]) / 2),
        "details": ((x[1] + x[2]) / 2, (x[2] + x[3]) / 2),
        "status": ((x[2] + x[3]) / 2, (x[3] + x[4]) / 2),
        "paid": ((x[3] + x[4]) / 2, (x[4] + x[5]) / 2),
        "withdrawn": ((x[4] + x[5]) / 2, (x[5] + x[6]) / 2),
        "balance": ((x[5] + x[6]) / 2, page_width + 20),
    }


def cell_text(row_words, x_min, x_max):
    cell = [w for w in row_words if x_min <= w[0] < x_max]
    cell.sort(key=lambda w: (round(w[1], 1), w[0]))
    return clean_text(" ".join(str(w[4]) for w in cell))


def parse_transactions_from_page(page, page_number, patterns):
    words = page.get_text("words")
    if not words:
        return []

    page_width = page.rect.width
    page_height = page.rect.height
    columns = detect_column_ranges(words, page_width, page_height)

    completion_x_min, completion_x_max = columns["completion"]
    row_start_candidates = []

    for w in words:
        word_text = str(w[4]).strip()
        x0, y0 = w[0], w[1]
        if DATE_RE.fullmatch(word_text) and completion_x_min <= x0 < completion_x_max:
            row_start_candidates.append(y0)

    row_starts = []
    for y in sorted(row_start_candidates):
        if not row_starts or abs(y - row_starts[-1]) > 3:
            row_starts.append(y)

    transactions = []
    for i, start_y in enumerate(row_starts):
        end_y = row_starts[i + 1] if i + 1 < len(row_starts) else page_height + 20
        row_words = [w for w in words if start_y - 2 <= w[1] < end_y - 1]
        if not row_words:
            continue

        row_text = clean_text(" ".join(str(w[4]) for w in sorted(row_words, key=lambda w: (round(w[1], 1), w[0]))))
        dt_match = DATETIME_RE.search(row_text)
        if not dt_match:
            date_match = DATE_RE.search(row_text)
            if not date_match:
                continue
            date_str = date_match.group(0)
            time_str = ""
        else:
            date_str = dt_match.group(1)
            time_str = dt_match.group(2)

        month = date_str[:7]
        paid_text = cell_text(row_words, *columns["paid"])
        withdrawn_text = cell_text(row_words, *columns["withdrawn"])
        details_text = cell_text(row_words, *columns["details"])
        receipt_text = cell_text(row_words, *columns["receipt"])

        paid_in = parse_amount(paid_text)
        withdrawn = parse_amount(withdrawn_text)
        search_blob = clean_text(f"{details_text} {row_text}")
        matched_terms = term_matches_text(search_blob, patterns)

        if is_ellegant_credit_text(search_blob):
            for term in patterns:
                if "ELLEGANT" in term.upper() and "CREDIT" in term.upper() and term not in matched_terms:
                    matched_terms.append(term)

        transactions.append({
            "page": page_number, "receipt": receipt_text, "date": date_str, "time": time_str,
            "month": month, "details": details_text, "paid_in": paid_in, "withdrawn": withdrawn,
            "amount_paid": amount_paid_value(withdrawn), "matched_terms": matched_terms,
            "row_text": row_text, "search_blob": search_blob,
        })
    return transactions


def parse_all_transactions(doc, loan_companies):
    patterns = build_term_patterns(loan_companies)
    transactions = []
    for page_number, page in enumerate(doc, start=1):
        transactions.extend(parse_transactions_from_page(page, page_number, patterns))
    return transactions


def find_loan_companies_by_page(doc, loan_companies):
    patterns = build_term_patterns(loan_companies)
    results = {term: {"count": 0, "pages": set()} for term in loan_companies}
    for page_number, page in enumerate(doc, start=1):
        text = clean_text(page.get_text("text"))
        for term, pattern in patterns.items():
            matches = list(pattern.finditer(text))
            if matches:
                results[term]["count"] += len(matches)
                results[term]["pages"].add(page_number)
    return results


def monthly_paid_in_summary(transactions):
    monthly = defaultdict(lambda: {
        "paid_in_excluding_matches": 0.0, "paid_in_excluded_matched": 0.0,
        "counted_paid_in_rows": 0, "excluded_paid_in_rows": 0,
    })
    for tx in transactions:
        paid_in = tx["paid_in"]
        if paid_in <= 0:
            continue
        month = tx["month"]
        if tx["matched_terms"]:
            monthly[month]["paid_in_excluded_matched"] += paid_in
            monthly[month]["excluded_paid_in_rows"] += 1
        else:
            monthly[month]["paid_in_excluding_matches"] += paid_in
            monthly[month]["counted_paid_in_rows"] += 1
    return monthly


def ellegant_credit_transactions(transactions):
    patterns = build_term_patterns(ELLEGANT_TERMS)
    rows = []
    for tx in transactions:
        search_blob = clean_text(f"{tx.get('details', '')} {tx.get('row_text', '')} {tx.get('search_blob', '')}")
        if term_matches_text(search_blob, patterns) or is_ellegant_credit_text(search_blob):
            amount = tx.get("amount_paid", 0.0)
            if amount <= 0:
                amount = amount_paid_value(tx.get("withdrawn", 0.0))
            rows.append({
                "Month": tx["month"], "Date Paid": tx["date"],
                "Transaction ID / Receipt No.": tx["receipt"],
                "Amount Paid": amount, "Page": tx["page"],
            })
    rows.sort(key=lambda r: (r["Month"], r["Date Paid"], r["Transaction ID / Receipt No."]))
    return rows


def loan_repayment_transactions(transactions):
    rows = []
    for tx in transactions:
        amount = tx.get("amount_paid", 0.0)
        if amount <= 0:
            continue
        matched_terms = tx.get("matched_terms", [])
        if not matched_terms:
            continue
        rows.append({
            "Month": tx["month"], "Date Paid": tx["date"], "Loan Company": ", ".join(matched_terms),
            "Transaction ID / Receipt No.": tx["receipt"], "Amount Paid": amount, "Page": tx["page"],
        })
    rows.sort(key=lambda r: (r["Month"], r["Date Paid"], r["Loan Company"], r["Transaction ID / Receipt No."]))
    return rows


def loan_company_cashflow_summary(transactions):
    summary = defaultdict(lambda: {"loan_received": 0.0, "installments_paid": 0.0, "matched_transactions": 0})
    monthly = defaultdict(lambda: {"paid_in_excluding_matches": 0.0, "loan_received": 0.0, "installments_paid": 0.0})
    selected_transactions = defaultdict(list)

    for tx in transactions:
        if tx.get("paid_in", 0.0) > 0 and not tx.get("matched_terms"):
            monthly[tx["month"]]["paid_in_excluding_matches"] += tx["paid_in"]

    for tx in sorted(transactions, key=transaction_sort_key):
        matched_terms = tx.get("matched_terms", [])
        if not matched_terms:
            continue

        for company in matched_terms:
            received = tx.get("paid_in", 0.0) if tx.get("paid_in", 0.0) > 0 else 0.0
            paid = tx.get("amount_paid", 0.0) if tx.get("amount_paid", 0.0) > 0 else 0.0
            if received <= 0 and paid <= 0:
                continue

            summary[company]["loan_received"] += received
            summary[company]["installments_paid"] += paid
            summary[company]["matched_transactions"] += 1
            monthly[tx["month"]]["loan_received"] += received
            monthly[tx["month"]]["installments_paid"] += paid

            tx_type = "Loan Received" if received > 0 and paid <= 0 else "Installment Paid" if paid > 0 and received <= 0 else "Received/Paid"
            selected_transactions[company].append({
                "Date": tx.get("date", ""), "Time": tx.get("time", ""), "Type": tx_type,
                "Receipt No.": tx.get("receipt", ""), "Loan Received": received,
                "Installment Paid": paid, "Page": tx.get("page", ""), "Details": tx.get("details", ""),
            })

    rows = []
    for company, values in summary.items():
        received = values["loan_received"]
        paid = values["installments_paid"]
        rows.append({
            "Loan Company": company, "Loan Received": received, "Installments Paid": paid,
            "Net Position": received - paid, "Matched Txns": values["matched_transactions"],
        })
    rows.sort(key=lambda r: r["Installments Paid"], reverse=True)
    return rows, monthly, dict(selected_transactions)


def loan_cycle_summary(company_transactions):
    cycles = []
    for company, events in company_transactions.items():
        ordered = sorted(events, key=lambda e: (e.get("Date", ""), e.get("Time", ""), e.get("Receipt No.", "")))
        current = None
        cycle_no = 0
        for event in ordered:
            received = event.get("Loan Received", 0.0)
            paid = event.get("Installment Paid", 0.0)
            if received > 0:
                if current is not None:
                    cycles.append(current)
                cycle_no += 1
                current = {
                    "Loan Company": company, "Cycle": cycle_no, "Loan Date": event.get("Date", ""),
                    "Loan Received": received, "Installments Paid": 0.0, "Installment Count": 0,
                }
            if paid > 0:
                if current is None:
                    cycle_no += 1
                    current = {
                        "Loan Company": company, "Cycle": cycle_no, "Loan Date": "Before first matched loan",
                        "Loan Received": 0.0, "Installments Paid": 0.0, "Installment Count": 0,
                    }
                current["Installments Paid"] += paid
                current["Installment Count"] += 1
        if current is not None:
            cycles.append(current)

    for cycle in cycles:
        cycle["Net Position"] = cycle["Loan Received"] - cycle["Installments Paid"]
    return cycles


def risk_profile_summary(monthly, loan_rows):
    total_paid_in = sum(values["paid_in_excluding_matches"] for values in monthly.values())
    total_loan = sum(row["Amount Paid"] for row in loan_rows)
    percentage = (total_loan / total_paid_in) * 100 if total_paid_in > 0 else 0.0
    rating, score = risk_rating_from_percentage(percentage)
    return {"total_paid_in": total_paid_in, "total_loan": total_loan, "percentage": percentage, "rating": rating, "score": score}


def build_report(customer_name, filename, loan_companies, search_results, transactions, monthly, ellegant_rows, loan_rows, risk_profile, cashflow_rows, cycle_rows):
    total_loan_received = sum(row["Loan Received"] for row in cashflow_rows)
    total_installments_paid = sum(row["Installments Paid"] for row in cashflow_rows)
    net_position = total_loan_received - total_installments_paid

    lines = []
    lines.append("MUTEMI M-PESA STATEMENT ANALYSIS SUMMARY")
    lines.append("=" * 72)
    lines.append(f"Statement file: {filename}")
    lines.append(f"Customer Name: {customer_name}")
    lines.append("")
    lines.append("EXECUTIVE SUMMARY")
    lines.append("─" * 72)
    lines.append(f"Transactions parsed: {len(transactions)}")
    lines.append(f"Normal Paid In Total, excluding matched loan companies: {money(risk_profile['total_paid_in'])}")
    lines.append(f"Loan received from matched loan companies: {money(total_loan_received)}")
    lines.append(f"Installments paid to matched loan companies: {money(total_installments_paid)}")
    lines.append(f"Net loan position, received less repaid: {money(net_position)}")
    lines.append(f"Risk rating: {risk_profile['rating']} | Score: {risk_profile['score']} | Loan repayment percentage: {risk_profile['percentage']:.2f}%")
    lines.append("")

    matched_rows = []
    for term in loan_companies:
        result = search_results.get(term, {"count": 0, "pages": set()})
        if result["count"] > 0:
            pages = ", ".join(str(p) for p in sorted(result["pages"]))
            matched_rows.append([term, str(result["count"]), pages])

    lines.append("MATCHED LOAN COMPANIES")
    lines.append("─" * 72)
    if matched_rows:
        lines.append(make_text_table(["Loan Company", "Total Matches", "Page Number(s)"], matched_rows))
    else:
        lines.append("No loan companies matched in the PDF.")
    lines.append("")

    lines.append("LOAN COMPANY CASHFLOW SUMMARY")
    lines.append("─" * 72)
    if cashflow_rows:
        lines.append(make_text_table(
            ["Loan Company", "Loan Received", "Installments Paid", "Net Position", "Matched Txns"],
            [[row["Loan Company"], money(row["Loan Received"]), money(row["Installments Paid"]), money(row["Net Position"]), str(row["Matched Txns"])] for row in cashflow_rows]
        ))
    else:
        lines.append("No matched loan-company cashflows were found.")
    lines.append("")

    month_rows = []
    for month in sorted(monthly.keys()):
        values = monthly[month]
        month_rows.append([month, money(values["paid_in_excluding_matches"]), str(values["counted_paid_in_rows"]), money(values["paid_in_excluded_matched"]), str(values["excluded_paid_in_rows"])])

    lines.append("MONTHLY PAID IN SUMMARY")
    lines.append("─" * 72)
    if month_rows:
        lines.append(make_text_table(["Month", "Paid In Excluding Matches", "Counted Rows", "Paid In Excluded", "Excluded Rows"], month_rows))
    else:
        lines.append("No monthly Paid In rows were found.")
    lines.append("")

    lines.append("ELLEGANT CREDIT LTD PAYMENT SUMMARY")
    lines.append("─" * 72)
    if ellegant_rows:
        by_month = defaultdict(list)
        for row in ellegant_rows:
            by_month[row["Month"]].append(row)
        for month in sorted(by_month.keys()):
            rows = by_month[month]
            total = sum(row["Amount Paid"] for row in rows)
            lines.append("")
            lines.append(f"{month} - Total Paid: {money(total)}")
            lines.append(make_text_table(["Date Paid", "Transaction ID / Receipt No.", "Amount Paid", "Page"], [[row["Date Paid"], row["Transaction ID / Receipt No."], money(row["Amount Paid"]), str(row["Page"])] for row in rows]))
    else:
        lines.append("No ELLEGANT CREDIT LTD transactions found.")
    lines.append("")

    lines.append("CUSTOMER RISK PROFILE")
    lines.append("─" * 72)
    lines.append("Formula: Total installments paid to matched loan companies / normal Paid In total * 100")
    lines.append(make_text_table(["Total Paid In", "Installments Paid", "Loan % of Paid In", "Risk Rating", "Score"], [[money(risk_profile["total_paid_in"]), money(risk_profile["total_loan"]), f'{risk_profile["percentage"]:.2f}%', risk_profile["rating"], str(risk_profile["score"])]],))
    lines.append("")

    if cycle_rows:
        lines.append("INFERRED LOAN CYCLES")
        lines.append("─" * 72)
        lines.append(make_text_table(["Loan Company", "Cycle", "Loan Date", "Loan Received", "Installments Paid", "Installments", "Net Position"], [[row["Loan Company"], str(row["Cycle"]), row["Loan Date"], money(row["Loan Received"]), money(row["Installments Paid"]), str(row["Installment Count"]), money(row["Net Position"])] for row in cycle_rows]))
    return "\n".join(lines)


def analyze_pdf(pdf_bytes, password, loan_companies, filename):
    doc, error = open_pdf_from_bytes(pdf_bytes, password=password)
    if error:
        return None, error

    customer_name = extract_customer_name_from_pdf(doc)
    search_results = find_loan_companies_by_page(doc, loan_companies)
    transactions = parse_all_transactions(doc, loan_companies)
    monthly = monthly_paid_in_summary(transactions)
    ellegant_rows = ellegant_credit_transactions(transactions)
    loan_rows = loan_repayment_transactions(transactions)
    risk_profile = risk_profile_summary(monthly, loan_rows)
    cashflow_rows, cashflow_monthly, company_transactions = loan_company_cashflow_summary(transactions)
    cycle_rows = loan_cycle_summary(company_transactions)

    report = build_report(customer_name, filename, loan_companies, search_results, transactions, monthly, ellegant_rows, loan_rows, risk_profile, cashflow_rows, cycle_rows)
    doc.close()

    return {
        "customer_name": customer_name, "search_results": search_results, "transactions": transactions,
        "monthly": monthly, "ellegant_rows": ellegant_rows, "loan_rows": loan_rows,
        "risk_profile": risk_profile, "cashflow_rows": cashflow_rows,
        "cashflow_monthly": cashflow_monthly, "company_transactions": company_transactions,
        "cycle_rows": cycle_rows, "report": report,
    }, None


def initialize_state():
    defaults = {
        "loan_companies": DEFAULT_LOAN_COMPANIES.copy(), "analysis": None,
        "uploaded_pdf_bytes": None, "uploaded_pdf_name": None, "uploaded_pdf_size": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def add_loan_company():
    value = st.session_state.get("new_loan_company", "").strip()
    if not value:
        return
    existing = [item.lower() for item in st.session_state.loan_companies]
    if value.lower() not in existing:
        st.session_state.loan_companies.append(value)
    st.session_state.new_loan_company = ""


def delete_loan_companies(selected):
    st.session_state.loan_companies = [item for item in st.session_state.loan_companies if item not in selected]


def render_metric_card(title, value, bg, fg):
    st.markdown(f"""
        <div class="metric-card" style="background:{bg}; color:{fg};">
            <div class="metric-title">{html.escape(str(title))}</div>
            <div class="metric-value">{html.escape(str(value))}</div>
        </div>
        """, unsafe_allow_html=True)


def render_loan_company_list(companies):
    items = "".join(f"<div class='loan-item'>{html.escape(str(company))}</div>" for company in companies)
    st.markdown(f"<div class='loan-list-box'>{items}</div>", unsafe_allow_html=True)


def render_uploaded_file_box(filename, size):
    if not filename:
        st.markdown("""
            <div class="upload-placeholder">
                <div class="file-name">No statement selected.</div>
                <div class="file-size">Choose a PDF statement to begin.</div>
            </div>
            """, unsafe_allow_html=True)
        return

    short_name = filename if len(filename) <= 35 else filename[:18] + "..." + filename[-13:]
    st.markdown(f"""
        <div class="uploaded-file-box">
            <div class="file-icon">▣</div>
            <div class="file-meta">
                <div class="file-name">{html.escape(short_name)}</div>
                <div class="file-size">{html.escape(human_file_size(size))}</div>
            </div>
        </div>
        """, unsafe_allow_html=True)


def render_executive_charts(analysis):
    monthly = analysis["cashflow_monthly"]
    cashflow_rows = analysis["cashflow_rows"]

    chart_rows = []
    for month in sorted(monthly.keys()):
        values = monthly[month]
        chart_rows.extend([
            {"Month": month, "Category": "Normal Paid In", "Amount": values.get("paid_in_excluding_matches", 0.0)},
            {"Month": month, "Category": "Loan Received", "Amount": values.get("loan_received", 0.0)},
            {"Month": month, "Category": "Installments Paid", "Amount": values.get("installments_paid", 0.0)},
        ])

    left_chart, right_chart = st.columns(2)
    with left_chart:
        st.markdown('<div class="chart-title">Monthly Cashflow Comparison</div><div class="chart-caption">Compares normal Paid In cashflow, estimated loan receipts, and estimated installments repaid.</div>', unsafe_allow_html=True)
        if chart_rows:
            chart_df = pd.DataFrame(chart_rows)
            spec = {
                "height": 260, "mark": {"type": "bar", "tooltip": True},
                "encoding": {
                    "x": {"field": "Month", "type": "nominal", "axis": {"labelAngle": 0}},
                    "y": {"field": "Amount", "type": "quantitative", "title": "Amount"},
                    "color": {"field": "Category", "type": "nominal", "scale": {"range": ["#16A34A", "#0284C7", "#EA580C"]}},
                    "xOffset": {"field": "Category"},
                    "tooltip": [{"field": "Month", "type": "nominal"}, {"field": "Category", "type": "nominal"}, {"field": "Amount", "type": "quantitative", "format": ",.2f"}],
                },
            }
            st.vega_lite_chart(chart_df, spec, use_container_width=True)
        else:
            st.info("No monthly cashflow data available.")

    with right_chart:
        st.markdown('<div class="chart-title">Installment Distribution by Loan Company</div><div class="chart-caption">Shows how total installment repayments are spread across matched loan companies.</div>', unsafe_allow_html=True)
        pie_rows = [{"Loan Company": row["Loan Company"], "Installments Paid": row["Installments Paid"]} for row in cashflow_rows if row["Installments Paid"] > 0]
        if pie_rows:
            pie_df = pd.DataFrame(pie_rows)
            spec = {
                "height": 260, "mark": {"type": "arc", "innerRadius": 45, "tooltip": True},
                "encoding": {
                    "theta": {"field": "Installments Paid", "type": "quantitative"},
                    "color": {"field": "Loan Company", "type": "nominal", "scale": {"range": ["#16A34A", "#0284C7", "#EA580C", "#7C3AED", "#DC2626", "#0891B2"]}},
                    "tooltip": [{"field": "Loan Company", "type": "nominal"}, {"field": "Installments Paid", "type": "quantitative", "format": ",.2f"}],
                },
            }
            st.vega_lite_chart(pie_df, spec, use_container_width=True)
        else:
            st.info("No installment data available.")


def render_professional_summary(analysis):
    risk = analysis["risk_profile"]
    cashflow_rows = analysis["cashflow_rows"]
    total_received = sum(row["Loan Received"] for row in cashflow_rows)
    total_installments = sum(row["Installments Paid"] for row in cashflow_rows)
    net_position = total_received - total_installments

    top_company = "N/A"
    top_company_paid = 0.0
    if cashflow_rows:
        top = max(cashflow_rows, key=lambda row: row.get("Installments Paid", 0.0))
        top_company = top.get("Loan Company", "N/A")
        top_company_paid = top.get("Installments Paid", 0.0)

    risk_bg, risk_fg = risk_colors(risk["rating"])

    st.markdown(f"""
        <div class="summary-page">
            <h2>MUTEMI M-PESA Statement Analysis Summary</h2>
            <p class="summary-muted">Customer: <b>{html.escape(analysis["customer_name"])}</b></p>

            <div class="summary-grid">
                <div class="summary-mini-card"><span>Transactions Parsed</span><strong>{len(analysis["transactions"])}</strong></div>
                <div class="summary-mini-card"><span>Normal Paid In</span><strong>{money(risk["total_paid_in"])}</strong></div>
                <div class="summary-mini-card"><span>Loan Received</span><strong>{money(total_received)}</strong></div>
                <div class="summary-mini-card"><span>Installments Paid</span><strong>{money(total_installments)}</strong></div>
                <div class="summary-mini-card"><span>Net Position</span><strong>{money(net_position)}</strong></div>
                <div class="summary-mini-card" style="background:{risk_bg}; color:{risk_fg};"><span>Risk Rating</span><strong>{risk["rating"]} ({risk["score"]})</strong></div>
            </div>

            <h3>1. Executive Overview</h3>
            <p>
                The statement shows <b>{money(risk["total_paid_in"])}</b> in normal Paid In cashflow after excluding incoming
                rows that matched the listed loan companies. Matched loan-company activity shows estimated loan receipts of
                <b>{money(total_received)}</b> and installment repayments of <b>{money(total_installments)}</b>.
                This gives a net loan position of <b>{money(net_position)}</b>.
            </p>

            <h3>2. Loan Pressure and Risk Interpretation</h3>
            <p>
                The customer risk rating is <b style="color:{risk_fg};">{risk["rating"]} ({risk["score"]})</b>.
                The loan repayment percentage is <b>{risk["percentage"]:.2f}%</b>. This percentage compares matched-loan
                installment payments against the normal Paid In total. A higher percentage suggests stronger repayment
                pressure relative to regular incoming cashflow.
            </p>

            <h3>3. Main Matched Loan Company</h3>
            <p>
                The highest installment amount detected is linked to <b>{html.escape(top_company)}</b>, with total installments
                of <b>{money(top_company_paid)}</b>. Use the <b>Selected Company Analysis</b> tab to view transaction-level
                movement for each matched lender.
            </p>
        </div>
        """, unsafe_allow_html=True)

    if cashflow_rows:
        st.markdown("#### Loan Company Cashflow Summary")
        df = pd.DataFrame(cashflow_rows)
        for col in ["Loan Received", "Installments Paid", "Net Position"]:
            df[col] = df[col].map(money)
        st.dataframe(df, use_container_width=True, hide_index=True)

    monthly_rows = []
    for month in sorted(analysis["monthly"].keys()):
        values = analysis["monthly"][month]
        monthly_rows.append({
            "Month": month,
            "Paid In Excluding Matches": money(values["paid_in_excluding_matches"]),
            "Counted Rows": values["counted_paid_in_rows"],
            "Paid In Excluded": money(values["paid_in_excluded_matched"]),
            "Excluded Rows": values["excluded_paid_in_rows"],
        })

    if monthly_rows:
        st.markdown("#### Monthly Paid In Summary")
        st.dataframe(pd.DataFrame(monthly_rows), use_container_width=True, hide_index=True)


def render_css():
    st.markdown("""
        <style>
            #MainMenu, footer, header {visibility: hidden;}
            .block-container { padding: 0.75rem 0.85rem 1rem 0.85rem; max-width: 100% !important; }
            .stApp { background: #F1F5F9; color: #0F172A; }
            .app-header { background: #052E16; color: white; padding: 20px 22px 16px 22px; margin: -0.75rem -0.85rem 14px -0.85rem; border-bottom: 1px solid #0B3D1E; }
            .app-header h1 { margin: 0; font-size: 1.75rem; font-weight: 800; line-height: 1.2; }
            .app-header p { margin: 7px 0 0 0; color: #BBF7D0; font-size: 0.92rem; }
            .statement-bar { background: white; border: 1px solid #CBD5E1; min-height: 58px; padding: 13px 16px; display: flex; align-items: center; margin-bottom: 12px; }
            .statement-title { font-weight: 800; margin-right: 18px; white-space: nowrap; }
            .statement-file { color: #334155; font-size: 0.9rem; word-break: break-all; }
            .left-title { font-size: 1.22rem; font-weight: 800; color: #0F2B46; margin: 0 0 10px 0; }
            .left-help { color: #475569; font-size: 0.86rem; line-height: 1.35; margin-bottom: 10px; }
            .uploaded-file-box, .upload-placeholder { background: #F8FAFC; border: 1px solid #D6DEE8; border-radius: 7px; padding: 10px; margin: 8px 0 12px 0; display: flex; align-items: center; gap: 10px; }
            .upload-placeholder { display: block; }
            .file-icon { width: 34px; height: 34px; border-radius: 6px; background: #263447; color: white; display: inline-flex; align-items: center; justify-content: center; font-weight: 800; font-size: 0.85rem; }
            .file-meta { line-height: 1.25; flex: 1; overflow: hidden; }
            .file-name { color: #0F172A; font-size: 0.88rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
            .file-size { color: #64748B; font-size: 0.78rem; }
            .password-box { background: #F8FAFC; border: 1px solid #CBD5E1; padding: 12px; margin-bottom: 16px; }
            .password-title { font-weight: 800; color: #0F172A; margin-bottom: 5px; }
            .password-file { color: #475569; font-size: 0.82rem; word-break: break-all; margin-bottom: 8px; }
            .loan-list-box { border: 1px solid #94A3B8; background: #F8FAFC; height: 305px; overflow-y: auto; margin: 10px 0 10px 0; }
            .loan-item { padding: 3px 7px; color: #0F172A; font-size: 0.9rem; line-height: 1.25; }
            .loan-item:hover { background: #E2E8F0; }
            .metric-card { border: 1px solid #C5CFDD; min-height: 88px; padding: 15px 17px; margin-bottom: 12px; }
            .metric-title { font-size: 0.8rem; font-weight: 800; margin-bottom: 10px; }
            .metric-value { font-size: 1.55rem; font-weight: 900; line-height: 1.15; word-break: break-word; }
            .chart-title { font-size: 1.05rem; font-weight: 900; color: #0F172A; }
            .chart-caption { font-size: 0.82rem; color: #475569; margin-top: 2px; }
            div[data-testid="stDataFrame"] { border: 1px solid #CBD5E1; background: white; }
            button[data-baseweb="tab"] { background: #E2E8F0 !important; border: 1px solid #AEB9C4 !important; border-radius: 0 !important; padding: 8px 13px !important; }
            button[aria-selected="true"][data-baseweb="tab"] { background: white !important; color: #15803D !important; }
            button[data-baseweb="tab"] p { font-weight: 800 !important; color: #0F172A !important; }
            .stButton button[kind="primary"] { background:#16A34A !important; color:white !important; border:1px solid #16A34A !important; border-radius:0 !important; font-weight:800 !important; min-height:42px; }
            .stButton button[kind="primary"]:hover { background:#15803D !important; color:white !important; border-color:#15803D !important; }
            .stButton > button, .stDownloadButton > button { border-radius:0 !important; font-weight:700 !important; min-height:40px; }
            .summary-page { background: white; border: 1px solid #CBD5E1; padding: 28px 30px; line-height: 1.58; margin-bottom: 14px; }
            .summary-page h2 { color: #052E16; margin-top: 0; }
            .summary-page h3 { color: #0F172A; margin-top: 24px; font-weight: 900; }
            .summary-grid { display:grid; grid-template-columns: repeat(3, minmax(160px, 1fr)); gap: 10px; margin: 18px 0 18px 0; }
            .summary-mini-card { border:1px solid #CBD5E1; background:#F8FAFC; padding:12px 14px; }
            .summary-mini-card span { display:block; color:#475569; font-size:0.82rem; font-weight:700; margin-bottom:5px; }
            .summary-mini-card strong { display:block; color:inherit; font-size:1.08rem; font-weight:900; }
            .summary-muted { color: #475569; }
            @media (max-width: 900px) {
                .loan-list-box { height: 240px; }
                .left-panel { min-height: auto; }
                .app-header h1 { font-size: 1.35rem; }
                .metric-value { font-size: 1.2rem; }
                .summary-grid { grid-template-columns: 1fr; }
            }
        </style>
        """, unsafe_allow_html=True)


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="📱", layout="wide", initial_sidebar_state="collapsed")
    initialize_state()
    render_css()

    st.markdown(f"""
        <div class="app-header">
            <h1>{APP_TITLE}</h1>
            <p>Loan company matching, exclusion analysis, monthly Paid In totals, and ELLEGANT CREDIT payment tracking</p>
        </div>
        """, unsafe_allow_html=True)

    file_name = st.session_state.get("uploaded_pdf_name") or "No statement selected."
    file_size = st.session_state.get("uploaded_pdf_size")

    top_file_col, top_btn_col = st.columns([5, 1])
    with top_file_col:
        st.markdown(f"""
            <div class="statement-bar">
                <div class="statement-title">Statement File</div>
                <div class="statement-file">{html.escape(file_name)} {html.escape("• " + human_file_size(file_size) if file_size else "")}</div>
            </div>
            """, unsafe_allow_html=True)
    with top_btn_col:
        st.write("")
        analyze_top = st.button(
            "Re-analyze Statement" if st.session_state.analysis else "Analyze Statement",
            type="primary", use_container_width=True,
            disabled=not st.session_state.get("uploaded_pdf_bytes"),
            key="top_analyze_button",
        )

    left_col, right_col = st.columns([1.05, 4.0], gap="small")

    with left_col:
        st.markdown('<div class="left-title">Statement Upload</div>', unsafe_allow_html=True)
        st.caption("Upload M-PESA PDF statement")
        uploaded = st.file_uploader("Upload M-PESA PDF statement", type=["pdf"], label_visibility="collapsed", key="statement_pdf_uploader")

        if uploaded is not None:
            st.session_state.uploaded_pdf_bytes = uploaded.getvalue()
            st.session_state.uploaded_pdf_name = uploaded.name
            st.session_state.uploaded_pdf_size = uploaded.size
            render_uploaded_file_box(uploaded.name, uploaded.size)
        else:
            render_uploaded_file_box(st.session_state.get("uploaded_pdf_name"), st.session_state.get("uploaded_pdf_size"))

        st.markdown('<div class="password-box">', unsafe_allow_html=True)
        st.markdown('<div class="password-title">Password Protected Statement</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="password-file">File: {html.escape(st.session_state.get("uploaded_pdf_name") or "No statement selected.")}</div>', unsafe_allow_html=True)
        show_password = st.checkbox("Show password", value=False, key="show_password_checkbox")
        password = st.text_input("Enter PDF password:", type="default" if show_password else "password", key="pdf_password_input")
        st.markdown('</div>', unsafe_allow_html=True)

        analyze_left = st.button(
            "Re-analyze Statement" if st.session_state.analysis else "Analyze Statement",
            type="primary", use_container_width=True,
            disabled=not st.session_state.get("uploaded_pdf_bytes"),
            key="left_analyze_button",
        )

        st.markdown("<hr style='margin:18px 0;border:0;border-top:1px solid #CBD5E1;'>", unsafe_allow_html=True)
        st.markdown('<div class="left-title">Loan Companies</div>', unsafe_allow_html=True)
        st.markdown('<div class="left-help">Rows matching these loan companies are excluded from the main Paid In total. Add or delete loan companies, then click Re-analyze.</div>', unsafe_allow_html=True)
        render_loan_company_list(st.session_state.loan_companies)

        st.text_input("Add loan company", key="new_loan_company", label_visibility="collapsed")
        if st.button("Add", type="primary", use_container_width=True, key="add_loan_company_button"):
            add_loan_company()
            st.rerun()

        selected_delete = st.multiselect("Select companies to delete", options=st.session_state.loan_companies, key="delete_company_select")
        del_col, reset_col = st.columns(2)
        with del_col:
            if st.button("Delete Selected", use_container_width=True, key="delete_companies_button"):
                delete_loan_companies(selected_delete)
                st.rerun()
        with reset_col:
            if st.button("Reset", use_container_width=True, key="reset_companies_button"):
                st.session_state.loan_companies = DEFAULT_LOAN_COMPANIES.copy()
                st.rerun()

    should_analyze = analyze_top or analyze_left
    if should_analyze:
        if not st.session_state.get("uploaded_pdf_bytes"):
            st.error("Please upload an M-PESA statement first.")
        else:
            with st.spinner("Analyzing statement..."):
                result, error = analyze_pdf(
                    st.session_state.uploaded_pdf_bytes, password,
                    st.session_state.loan_companies,
                    st.session_state.get("uploaded_pdf_name") or "uploaded_statement.pdf",
                )
            if error == "password_required":
                st.warning("This PDF is password protected. Enter the password and click Analyze Statement again.")
            elif error == "wrong_password":
                st.error("Wrong password or PDF could not be unlocked.")
            else:
                st.session_state.analysis = result
                st.success("Analysis complete.")

    analysis = st.session_state.analysis

    with right_col:
        if not analysis:
            cards = st.columns([1.4, 1, 1, 1, 1, 0.8])
            with cards[0]:
                render_metric_card("Customer Name", "N/A", "#E0F2FE", "#075985")
            with cards[1]:
                render_metric_card("Transactions Parsed", "0", "#DBEAFE", "#1E3A8A")
            with cards[2]:
                render_metric_card("Monthly Paid In Total", "0.00", "#DCFCE7", "#166534")
            with cards[3]:
                render_metric_card("Loan Received", "0.00", "#DFF6FF", "#075985")
            with cards[4]:
                render_metric_card("Installment Paid", "0.00", "#FFEDD5", "#9A3412")
            with cards[5]:
                render_metric_card("Risk Rating", "N/A", "#E2E8F0", "#334155")
            st.info("Upload a statement, enter the PDF password if needed, and click Analyze Statement.")
            return

        risk = analysis["risk_profile"]
        risk_bg, risk_fg = risk_colors(risk["rating"])
        cashflow_rows = analysis["cashflow_rows"]
        total_received = sum(row["Loan Received"] for row in cashflow_rows)
        total_installments = sum(row["Installments Paid"] for row in cashflow_rows)

        cards = st.columns([1.4, 1, 1, 1, 1, 0.8])
        with cards[0]:
            render_metric_card("Customer Name", analysis["customer_name"], "#E0F2FE", "#075985")
        with cards[1]:
            render_metric_card("Transactions Parsed", str(len(analysis["transactions"])), "#DBEAFE", "#1E3A8A")
        with cards[2]:
            render_metric_card("Monthly Paid In Total", money(risk["total_paid_in"]), "#DCFCE7", "#166534")
        with cards[3]:
            render_metric_card("Loan Received", money(total_received), "#DFF6FF", "#075985")
        with cards[4]:
            render_metric_card("Installment Paid", money(total_installments), "#FFEDD5", "#9A3412")
        with cards[5]:
            render_metric_card("Risk Rating", f'{risk["rating"]} ({risk["score"]})', risk_bg, risk_fg)

        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "Executive Dashboard", "Matched Loan Companies", "Monthly Paid In",
            "Selected Company Analysis", "ELLEGANT CREDIT Summary",
            "Customer Risk Profile", "Full Summary",
        ])

        with tab1:
            st.markdown("### Executive Visual Dashboard")
            st.caption("This dashboard compares customer income, loan disbursements received, and installments repaid to matched loan companies.")
            render_executive_charts(analysis)
            if cashflow_rows:
                cf = pd.DataFrame(cashflow_rows)
                for col in ["Loan Received", "Installments Paid", "Net Position"]:
                    cf[col] = cf[col].map(money)
                st.dataframe(cf, use_container_width=True, hide_index=True)
            else:
                st.info("No loan company cashflow rows found.")

        with tab2:
            matched_rows = []
            for term, result in analysis["search_results"].items():
                if result["count"] > 0:
                    matched_rows.append({
                        "Loan Company": term, "Total Matches": result["count"],
                        "Page Number(s)": ", ".join(str(p) for p in sorted(result["pages"])),
                    })
            if matched_rows:
                st.dataframe(pd.DataFrame(matched_rows), use_container_width=True, hide_index=True)
            else:
                st.warning("No loan companies matched in the PDF.")

        with tab3:
            monthly_rows = []
            for month in sorted(analysis["monthly"].keys()):
                values = analysis["monthly"][month]
                monthly_rows.append({
                    "Month": month,
                    "Paid In Excluding Matches": money(values["paid_in_excluding_matches"]),
                    "Counted Rows": values["counted_paid_in_rows"],
                    "Paid In Excluded": money(values["paid_in_excluded_matched"]),
                    "Excluded Rows": values["excluded_paid_in_rows"],
                })
            if monthly_rows:
                st.dataframe(pd.DataFrame(monthly_rows), use_container_width=True, hide_index=True)
            else:
                st.warning("No monthly Paid In rows were found.")

        with tab4:
            companies = list(analysis["company_transactions"].keys())
            if not companies:
                st.info("No matched loan company transactions found.")
            else:
                selected_company = st.selectbox("Select loan company", options=companies, index=companies.index("ELLEGANT CREDIT LTD") if "ELLEGANT CREDIT LTD" in companies else 0, key="selected_company_analysis")
                events = analysis["company_transactions"].get(selected_company, [])
                received_total = sum(e.get("Loan Received", 0.0) for e in events)
                paid_total = sum(e.get("Installment Paid", 0.0) for e in events)

                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    render_metric_card("Loan Received", money(received_total), "#DFF6FF", "#075985")
                with c2:
                    render_metric_card("Installments Paid", money(paid_total), "#FFEDD5", "#9A3412")
                with c3:
                    render_metric_card("Net Position", money(received_total - paid_total), "#E2E8F0", "#334155")
                with c4:
                    render_metric_card("Matched Txns", str(len(events)), "#DBEAFE", "#1E3A8A")

                if events:
                    df = pd.DataFrame(events)
                    for col in ["Loan Received", "Installment Paid"]:
                        df[col] = df[col].map(lambda x: money(x) if x else "")
                    st.dataframe(df, use_container_width=True, hide_index=True)

                cycles = [row for row in analysis["cycle_rows"] if row["Loan Company"] == selected_company]
                if cycles:
                    st.subheader("Inferred Loan Cycles")
                    cycle_df = pd.DataFrame(cycles)
                    for col in ["Loan Received", "Installments Paid", "Net Position"]:
                        cycle_df[col] = cycle_df[col].map(money)
                    st.dataframe(cycle_df, use_container_width=True, hide_index=True)

        with tab5:
            st.markdown('<div class="summary-page"><h3 style="margin-top:0;">ELLEGANT CREDIT LTD Payment Summary</h3><p class="summary-muted">This block lists each ELLEGANT CREDIT transaction by month, including date paid, receipt number, and amount paid.</p></div>', unsafe_allow_html=True)
            if analysis["ellegant_rows"]:
                df = pd.DataFrame(analysis["ellegant_rows"])
                df["Amount Paid"] = df["Amount Paid"].map(money)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.warning("No ELLEGANT CREDIT LTD transactions found.")

        with tab6:
            st.markdown(f"""
                <div class="summary-page">
                    <h3 style="margin-top:0;">Customer Risk Profile</h3>
                    <p><b>Formula:</b> Total installments paid to matched loan companies ÷ normal Paid In total × 100</p>
                    <h2 style="color:{risk_fg};">{risk["rating"]} — Score {risk["score"]}</h2>
                    <p><b>Loan Percentage:</b> {risk["percentage"]:.2f}%</p>
                </div>
                """, unsafe_allow_html=True)
            guide = pd.DataFrame([
                {"Loan Percentage of Total Amount": "0% - 10%", "Risk Rating": "Very Good", "Score": 5},
                {"Loan Percentage of Total Amount": "11% - 25%", "Risk Rating": "Good", "Score": 4},
                {"Loan Percentage of Total Amount": "26% - 50%", "Risk Rating": "Fair", "Score": 3},
                {"Loan Percentage of Total Amount": "51% - 75%", "Risk Rating": "Risky", "Score": 2},
                {"Loan Percentage of Total Amount": "Above 75%", "Risk Rating": "Very Risky", "Score": 1},
            ])
            st.subheader("Risk Rating Guide")
            st.dataframe(guide, use_container_width=True, hide_index=True)
            if analysis["loan_rows"]:
                st.subheader("Loan repayment transactions used")
                df = pd.DataFrame(analysis["loan_rows"])
                df["Amount Paid"] = df["Amount Paid"].map(money)
                st.dataframe(df, use_container_width=True, hide_index=True)

        with tab7:
            st.download_button("Download Summary TXT", data=analysis["report"].encode("utf-8"), file_name="mutemi_mpesa_analysis_summary.txt", mime="text/plain", use_container_width=False, key="download_summary_button")
            render_professional_summary(analysis)


if __name__ == "__main__":
    main()
