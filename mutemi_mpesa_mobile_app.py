# MUTEMI M-PESA Mobile Statement Analyzer
#
# Mobile-friendly version of the desktop app.
#
# Install:
#   python -m pip install streamlit pymupdf pandas
#
# Run:
#   streamlit run mutemi_mpesa_mobile_app.py --server.address 0.0.0.0
#
# Then open on your phone browser:
#   http://YOUR-COMPUTER-IP:8501

from collections import defaultdict
import re

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st


APP_TITLE = "MUTEMI M-PESA Statement Analyzer"

DEFAULT_LOAN_COMPANIES = [
    "premier",
    "cityfield",
    "goldstep",
    "fourth generation",
    "newark",
    "ELLEGANT CREDIT LTD",
    "OCL BUSINESS CREDIT LIMITED",
    "ASA LIMITED -GITHUNGURI",
]

ELLEGANT_TERMS = [
    "ELLEGANT CREDIT LTD",
    "ELEGANT CREDIT LTD",
]

AMOUNT_RE = re.compile(r"[+-]?\d{1,3}(?:,\d{3})*(?:\.\d{2})|[+-]?\d+(?:\.\d{2})")
DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
DATETIME_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def money(value):
    return f"{value:,.2f}"


def parse_amount(text):
    if not text:
        return 0.0

    text = text.replace("−", "-")
    matches = AMOUNT_RE.findall(text)

    if not matches:
        return 0.0

    value = matches[-1].replace(",", "")

    try:
        return float(value)
    except ValueError:
        return 0.0


def amount_paid_value(value):
    if value < 0:
        return abs(value)
    return value


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


def make_text_table(headers, rows):
    """Create a clean text table with solid separator lines."""
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


def extract_customer_name(doc):
    if doc.page_count == 0:
        return "Not found"

    first_page_text = clean_text(doc[0].get_text("text"))

    patterns = [
        r"Customer\s+Name\s*[:\-]?\s*([A-Za-z][A-Za-z\s.'-]{2,80}?)(?=\s+Mobile\s+Number|\s+Email\s+Address|\s+Statement\s+Period|\s+Request\s+Date|$)",
        r"Customer\s*[:\-]?\s*([A-Za-z][A-Za-z\s.'-]{2,80}?)(?=\s+Mobile|\s+Email|\s+Statement|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, first_page_text, re.IGNORECASE)
        if match:
            name = clean_text(match.group(1))
            if name:
                return name

    return "Not found"


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


def detect_column_ranges(words, page_width, page_height):
    top_words = [w for w in words if w[1] < page_height * 0.35]

    header_targets = {
        "receipt": ["receipt"],
        "completion": ["completion"],
        "details": ["details"],
        "status": ["transaction"],
        "paid": ["paid"],
        "withdrawn": ["withdrawn"],
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
        "receipt": page_width * 0.03,
        "completion": page_width * 0.16,
        "details": page_width * 0.31,
        "status": page_width * 0.49,
        "paid": page_width * 0.63,
        "withdrawn": page_width * 0.75,
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

        row_text = clean_text(
            " ".join(str(w[4]) for w in sorted(row_words, key=lambda w: (round(w[1], 1), w[0])))
        )

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
                if "ELLEGANT" in term.upper() and "CREDIT" in term.upper():
                    if term not in matched_terms:
                        matched_terms.append(term)

        transactions.append({
            "page": page_number,
            "receipt": receipt_text,
            "date": date_str,
            "time": time_str,
            "month": month,
            "details": details_text,
            "paid_in": paid_in,
            "withdrawn": withdrawn,
            "amount_paid": amount_paid_value(withdrawn),
            "matched_terms": matched_terms,
            "row_text": row_text,
            "search_blob": search_blob,
        })

    return transactions


def parse_all_transactions(doc, loan_companies):
    patterns = build_term_patterns(loan_companies)
    transactions = []

    for page_number, page in enumerate(doc, start=1):
        transactions.extend(parse_transactions_from_page(page, page_number, patterns))

    return transactions


def monthly_paid_in_summary(transactions):
    monthly = defaultdict(lambda: {
        "paid_in_excluding_matches": 0.0,
        "paid_in_excluded_matched": 0.0,
        "counted_paid_in_rows": 0,
        "excluded_paid_in_rows": 0,
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
                "Month": tx["month"],
                "Date Paid": tx["date"],
                "Transaction ID / Receipt No.": tx["receipt"],
                "Amount Paid": amount,
                "Page": tx["page"],
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
            "Month": tx["month"],
            "Date Paid": tx["date"],
            "Loan Company": ", ".join(matched_terms),
            "Transaction ID / Receipt No.": tx["receipt"],
            "Amount Paid": amount,
            "Page": tx["page"],
        })

    rows.sort(key=lambda r: (r["Month"], r["Date Paid"], r["Loan Company"], r["Transaction ID / Receipt No."]))
    return rows


def risk_profile_summary(monthly, loan_rows):
    total_paid_in = sum(values["paid_in_excluding_matches"] for values in monthly.values())
    total_loan = sum(row["Amount Paid"] for row in loan_rows)

    if total_paid_in > 0:
        percentage = (total_loan / total_paid_in) * 100
    else:
        percentage = 0.0

    rating, score = risk_rating_from_percentage(percentage)

    return {
        "total_paid_in": total_paid_in,
        "total_loan": total_loan,
        "percentage": percentage,
        "rating": rating,
        "score": score,
    }


def loan_company_cashflow_summary(transactions):
    """Summarize matched loan-company cashflows into loans received and installments paid."""
    summary = defaultdict(lambda: {"loan_received": 0.0, "installments_paid": 0.0, "matched_transactions": 0})
    monthly = defaultdict(lambda: {"paid_in_excluding_matches": 0.0, "loan_received": 0.0, "installments_paid": 0.0})
    company_transactions = defaultdict(list)

    for tx in transactions:
        if tx.get("paid_in", 0.0) > 0 and not tx.get("matched_terms"):
            monthly[tx["month"]]["paid_in_excluding_matches"] += tx["paid_in"]

    for tx in sorted(transactions, key=lambda x: (x.get("date", ""), x.get("time", ""), x.get("receipt", ""))):
        matched_terms = list(dict.fromkeys(tx.get("matched_terms", [])))
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

            if received > 0 and paid <= 0:
                tx_type = "Loan Received"
            elif paid > 0 and received <= 0:
                tx_type = "Installment Paid"
            else:
                tx_type = "Received/Paid"

            company_transactions[company].append({
                "date": tx.get("date", ""),
                "time": tx.get("time", ""),
                "type": tx_type,
                "receipt": tx.get("receipt", ""),
                "received": received,
                "paid": paid,
                "page": tx.get("page", ""),
                "details": tx.get("details", ""),
            })

    rows = []
    for company, values in summary.items():
        received = values["loan_received"]
        paid = values["installments_paid"]
        rows.append({
            "Loan Company": company,
            "Loan Received": received,
            "Installments Paid": paid,
            "Net Position": received - paid,
            "Matched Txns": values["matched_transactions"],
        })

    rows.sort(key=lambda r: r["Installments Paid"], reverse=True)
    return rows, monthly, dict(company_transactions)


def loan_cycle_summary(company_transactions):
    """Infer loan cycles: every matched paid-in starts a new cycle; paid-out rows until next paid-in are installments."""
    cycles = []

    for company, events in company_transactions.items():
        ordered = sorted(events, key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("receipt", "")))
        current = None
        cycle_no = 0

        for event in ordered:
            received = event.get("received", 0.0)
            paid = event.get("paid", 0.0)

            if received > 0:
                if current is not None:
                    cycles.append(current)
                cycle_no += 1
                current = {
                    "Loan Company": company,
                    "Cycle": cycle_no,
                    "Loan Date": event.get("date", ""),
                    "Loan Received": received,
                    "Installments Paid": 0.0,
                    "Installment Count": 0,
                }

            if paid > 0:
                if current is None:
                    cycle_no += 1
                    current = {
                        "Loan Company": company,
                        "Cycle": cycle_no,
                        "Loan Date": "Before first matched loan",
                        "Loan Received": 0.0,
                        "Installments Paid": 0.0,
                        "Installment Count": 0,
                    }
                current["Installments Paid"] += paid
                current["Installment Count"] += 1

        if current is not None:
            cycles.append(current)

    for cycle in cycles:
        cycle["Net Position"] = cycle["Loan Received"] - cycle["Installments Paid"]

    return cycles


def build_report_v10(customer_name, loan_companies, search_results, transactions, monthly, ellegant_rows, loan_rows, risk_profile, cashflow_rows, cycle_rows):
    lines = []
    total_loan_received = sum(row["Loan Received"] for row in cashflow_rows)
    total_installments_paid = sum(row["Installments Paid"] for row in cashflow_rows)
    net_position = total_loan_received - total_installments_paid

    lines.append("MUTEMI M-PESA STATEMENT ANALYSIS SUMMARY")
    lines.append("═" * 70)
    lines.append(f"Customer Name: {customer_name}")
    lines.append("")
    lines.append("EXECUTIVE SUMMARY")
    lines.append("─" * 70)
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
    lines.append("─" * 70)
    if matched_rows:
        lines.append(make_text_table(["Loan Company", "Total Matches", "Page Number(s)"], matched_rows))
    else:
        lines.append("No loan companies matched in the PDF.")
    lines.append("")

    lines.append("LOAN COMPANY CASHFLOW SUMMARY")
    lines.append("─" * 70)
    if cashflow_rows:
        lines.append(make_text_table(
            ["Loan Company", "Loan Received", "Installments Paid", "Net Position", "Matched Txns"],
            [[row["Loan Company"], money(row["Loan Received"]), money(row["Installments Paid"]), money(row["Net Position"]), str(row["Matched Txns"])] for row in cashflow_rows]
        ))
    else:
        lines.append("No matched loan-company cashflows were found.")
    lines.append("")

    monthly_rows = []
    for month in sorted(monthly.keys()):
        values = monthly[month]
        monthly_rows.append([
            month,
            money(values["paid_in_excluding_matches"]),
            str(values["counted_paid_in_rows"]),
            money(values["paid_in_excluded_matched"]),
            str(values["excluded_paid_in_rows"]),
        ])

    lines.append("MONTHLY PAID IN SUMMARY")
    lines.append("─" * 70)
    lines.append("Main total excludes Paid In rows whose details matched loan companies.")
    if monthly_rows:
        lines.append(make_text_table(["Month", "Paid In Excluding Matches", "Counted Rows", "Paid In Excluded", "Excluded Rows"], monthly_rows))
    else:
        lines.append("No monthly paid-in rows were found.")
    lines.append("")

    lines.append("CUSTOMER RISK PROFILE")
    lines.append("─" * 70)
    lines.append("Formula: Total loan repayment to matched loan companies / Total Paid In amount * 100")
    lines.append(make_text_table(
        ["Total Paid In", "Total Installments Paid", "Loan % of Paid In", "Risk Rating", "Score"],
        [[money(risk_profile["total_paid_in"]), money(risk_profile["total_loan"]), f'{risk_profile["percentage"]:.2f}%', risk_profile["rating"], str(risk_profile["score"])]],
    ))
    lines.append("")

    lines.append("Loan companies used:")
    for term in loan_companies:
        lines.append(f"- {term}")

    return "\n".join(lines)


def build_report(customer_name, loan_companies, search_results, transactions, monthly, ellegant_rows, loan_rows, risk_profile):
    lines = []

    lines.append("MUTEMI M-PESA STATEMENT ANALYSIS SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Customer Name: {customer_name}")
    lines.append("")

    matched_rows = []
    for term in loan_companies:
        result = search_results.get(term, {"count": 0, "pages": set()})
        if result["count"] > 0:
            pages = ", ".join(str(p) for p in sorted(result["pages"]))
            matched_rows.append([term, str(result["count"]), pages])

    lines.append("MATCHED LOAN COMPANIES")
    lines.append("-" * 60)

    if matched_rows:
        lines.append(make_text_table(["Loan Company", "Total Matches", "Page Number(s)"], matched_rows))
    else:
        lines.append("No loan companies matched in the PDF.")

    lines.append("")

    monthly_rows = []
    for month in sorted(monthly.keys()):
        values = monthly[month]
        monthly_rows.append([
            month,
            money(values["paid_in_excluding_matches"]),
            str(values["counted_paid_in_rows"]),
            money(values["paid_in_excluded_matched"]),
            str(values["excluded_paid_in_rows"]),
        ])

    lines.append("MONTHLY PAID IN SUMMARY")
    lines.append("-" * 60)

    if monthly_rows:
        lines.append(make_text_table(
            ["Month", "Paid In Excluding Matches", "Counted Rows", "Paid In Excluded", "Excluded Rows"],
            monthly_rows
        ))
    else:
        lines.append("No Paid In transactions were parsed.")

    lines.append("")
    lines.append("ELLEGANT CREDIT LTD PAYMENT SUMMARY")
    lines.append("-" * 60)

    if ellegant_rows:
        by_month = defaultdict(list)
        for row in ellegant_rows:
            by_month[row["Month"]].append(row)

        for month in sorted(by_month.keys()):
            month_rows = by_month[month]
            total = sum(row["Amount Paid"] for row in month_rows)

            lines.append("")
            lines.append(f"{month} - Total Paid: {money(total)}")
            lines.append(make_text_table(
                ["Date Paid", "Transaction ID / Receipt No.", "Amount Paid", "Page"],
                [
                    [row["Date Paid"], row["Transaction ID / Receipt No."], money(row["Amount Paid"]), str(row["Page"])]
                    for row in month_rows
                ]
            ))
    else:
        lines.append("No ELLEGANT CREDIT LTD transactions found.")

    lines.append("")
    lines.append("CUSTOMER RISK PROFILE")
    lines.append("-" * 60)
    lines.append("Formula: Total loan repayment to matched loan companies / Total Paid In amount * 100")
    lines.append("")
    lines.append(make_text_table(
        ["Total Paid In", "Total Loan Repayment", "Loan % of Paid In", "Risk Rating", "Score"],
        [[
            money(risk_profile["total_paid_in"]),
            money(risk_profile["total_loan"]),
            f'{risk_profile["percentage"]:.2f}%',
            risk_profile["rating"],
            str(risk_profile["score"]),
        ]]
    ))

    lines.append("")
    lines.append(f"Transactions parsed: {len(transactions)}")
    lines.append("")
    lines.append("Loan companies used:")

    for term in loan_companies:
        lines.append(f"- {term}")

    return "\n".join(lines)


def style_metric_card(title, value, bg, fg):
    st.markdown(
        f"""
        <div class="metric-card" style="background:{bg}; color:{fg};">
            <div class="metric-title">{title}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True
    )


def initialize_state():
    if "loan_companies" not in st.session_state:
        st.session_state.loan_companies = DEFAULT_LOAN_COMPANIES.copy()

    if "analysis" not in st.session_state:
        st.session_state.analysis = None


def add_loan_company():
    value = st.session_state.get("new_loan_company", "").strip()

    if not value:
        return

    existing = [item.lower() for item in st.session_state.loan_companies]

    if value.lower() not in existing:
        st.session_state.loan_companies.append(value)

    st.session_state.new_loan_company = ""


def delete_loan_companies(selected):
    st.session_state.loan_companies = [
        item for item in st.session_state.loan_companies if item not in selected
    ]


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


def render_loan_company_list(companies):
    if not companies:
        st.caption("No loan companies added yet.")
        return

    html_items = ''.join(f'<div class="loan-item">{c}</div>' for c in companies)
    st.markdown(f'<div class="loan-list-box">{html_items}</div>', unsafe_allow_html=True)


def analyze_pdf(pdf_bytes, password, loan_companies):
    doc, error = open_pdf_from_bytes(pdf_bytes, password=password)

    if error:
        return None, error

    customer_name = extract_customer_name(doc)
    search_results = find_loan_companies_by_page(doc, loan_companies)
    transactions = parse_all_transactions(doc, loan_companies)
    monthly = monthly_paid_in_summary(transactions)
    ellegant_rows = ellegant_credit_transactions(transactions)
    loan_rows = loan_repayment_transactions(transactions)
    risk_profile = risk_profile_summary(monthly, loan_rows)
    cashflow_rows, cashflow_monthly, company_transactions = loan_company_cashflow_summary(transactions)
    cycle_rows = loan_cycle_summary(company_transactions)
    report = build_report_v10(
        customer_name,
        loan_companies,
        search_results,
        transactions,
        monthly,
        ellegant_rows,
        loan_rows,
        risk_profile,
        cashflow_rows,
        cycle_rows,
    )

    doc.close()

    return {
        "customer_name": customer_name,
        "search_results": search_results,
        "transactions": transactions,
        "monthly": monthly,
        "ellegant_rows": ellegant_rows,
        "loan_rows": loan_rows,
        "risk_profile": risk_profile,
        "cashflow_rows": cashflow_rows,
        "cashflow_monthly": cashflow_monthly,
        "company_transactions": company_transactions,
        "cycle_rows": cycle_rows,
        "report": report,
    }, None



def main():
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="📱",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    initialize_state()

    st.markdown(
        """
        <style>
            .stApp { background: #F1F5F9; }
            .block-container { padding: 0.35rem 0.85rem 1rem 0.85rem; max-width: 100% !important; }
            [data-testid="stSidebar"] { display: none; }
            .offline-header {
                background:#052E16;
                color:white;
                padding: 20px 22px 16px 22px;
                margin: -0.6rem -0.85rem 14px -0.85rem;
                min-height: 96px;
                border-bottom: 1px solid #0B4A20;
            }
            .offline-header h1 { margin:0; font-size: 1.95rem; font-weight:800; }
            .offline-header p { margin: 8px 0 0 0; font-size: 0.92rem; color:#DCFCE7; }
            .file-bar {
                background: white;
                border: 1px solid #CBD5E1;
                padding: 13px 16px;
                min-height: 58px;
                margin-bottom: 12px;
                display:flex;
                align-items:center;
                gap: 14px;
            }
            .file-label { font-weight:800; color:#0F172A; min-width: 100px; }
            .file-name { color:#475569; font-size:0.90rem; word-break:break-all; }
            .left-panel {
                background:white;
                border:1px solid #CBD5E1;
                padding: 18px 14px 12px 14px;
                min-height: 640px;
            }
            .left-title { font-size:1.35rem; font-weight:800; color:#0F2B46; margin-bottom:8px; }
            .left-help { font-size:0.86rem; color:#475569; line-height:1.25; margin-bottom:12px; }
            .uploaded-file-box {
                background: #F8FAFC;
                border: 1px solid #D6DEE8;
                border-radius: 7px;
                padding: 9px 10px;
                margin: 8px 0 12px 0;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            .file-icon {
                width: 34px;
                height: 34px;
                border-radius: 6px;
                background: #263447;
                color: white;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                font-weight: 800;
                font-size: 0.8rem;
            }
            .file-meta { line-height:1.25; flex:1; overflow:hidden; }
            .upload-name { color:#0F172A; font-size:0.88rem; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
            .upload-size { color:#64748B; font-size:0.78rem; }
            .password-card { background:#F8FAFC; border:1px solid #D6DEE8; padding:10px; margin:10px 0 15px 0; }
            .password-title { font-size:0.95rem; font-weight:800; color:#0F172A; margin-bottom:4px; }
            .password-file { font-size:0.78rem; color:#475569; word-break:break-all; margin-bottom:8px; }
            .loan-list-box { border:1px solid #8D99A8; background:#FFFFFF; height: 300px; overflow-y:auto; margin:10px 0 12px 0; padding:4px 0; }
            .loan-item { padding:3px 6px; color:#111827; font-size:0.90rem; line-height:1.3; }
            .metric-card {
                border:1px solid #C0CCD9;
                padding: 15px 18px;
                min-height:88px;
                margin-bottom:12px;
            }
            .metric-title { font-size:0.84rem; font-weight:800; margin-bottom:11px; }
            .metric-value { font-size:1.45rem; font-weight:900; line-height:1.1; word-break:break-word; }
            .right-shell { background:white; border:1px solid #AEB9C4; padding:10px; min-height:630px; }
            div[data-baseweb="tab-list"] { gap:0px; border-bottom:1px solid #9AA6B2; }
            button[data-baseweb="tab"] {
                background:#ECEFF3 !important;
                border:1px solid #AEB9C4 !important;
                border-bottom:0 !important;
                border-radius:0 !important;
                padding: 9px 13px !important;
            }
            button[data-baseweb="tab"] p { font-weight:800 !important; font-size:0.85rem !important; color:#0F172A !important; }
            button[aria-selected="true"][data-baseweb="tab"] { background:#FFFFFF !important; border-bottom:2px solid #FFFFFF !important; }
            .section-card { background:white; border:1px solid #CBD5E1; padding:14px 16px; margin: 8px 0 12px 0; }
            .section-title { margin:0; font-size:1.15rem; font-weight:800; color:#0F172A; }
            .section-subtitle { color:#475569; font-size:0.88rem; margin-top:6px; }
            .stButton button[kind="primary"] { background:#16A34A !important; color:white !important; border:1px solid #16A34A !important; }
            .stButton button[kind="primary"]:hover { background:#15803D !important; color:white !important; border-color:#15803D !important; }
            .stButton > button { border-radius:0 !important; font-weight:800 !important; min-height:42px; }
            .stDownloadButton > button { border-radius:0 !important; font-weight:800 !important; }
            div[data-testid="stDataFrame"] { border:1px solid #CBD5E1; border-radius:0; background:white; }
            .stTextArea textarea { font-family: Consolas, monospace !important; font-size:0.88rem !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="offline-header">
            <h1>{APP_TITLE}</h1>
            <p>Loan company matching, exclusion analysis, monthly Paid In totals, and ELLEGANT CREDIT payment tracking</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Defaults for widgets / file state
    uploaded_file = None
    password = ""

    # Top statement bar and analyze button
    top_button_label = "Re-analyze Statement" if st.session_state.get("analysis") else "Analyze Statement"
    left_button_label = "Re-analyze Statement" if st.session_state.get("analysis") else "Analyze Statement"

    top_file_col, top_btn_col = st.columns([5, 1])
    with top_file_col:
        current_file = st.session_state.get("current_file_name", "No statement selected.")
        current_size = st.session_state.get("current_file_size", "")
        st.markdown(
            f"""
            <div class="file-bar">
                <div class="file-label">Statement File</div>
                <div class="file-name">{current_file}{' • ' + current_size if current_size else ''}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with top_btn_col:
        st.write("")
        analyze_clicked_top = st.button(top_button_label, type="primary", use_container_width=True, disabled=not st.session_state.get("uploaded_pdf_bytes"), key="top_analyze_statement_button")

    left_col, right_col = st.columns([1.05, 4.0], gap="small")

    with left_col:
        st.markdown('<div class="left-panel">', unsafe_allow_html=True)
        st.markdown('<div class="left-title">Statement Upload</div>', unsafe_allow_html=True)
        st.caption("Upload M-PESA PDF statement")
        uploaded_file = st.file_uploader("Upload M-PESA PDF statement", type=["pdf"], label_visibility="collapsed", key="statement_pdf_uploader")

        if uploaded_file is not None:
            st.session_state.uploaded_pdf_bytes = uploaded_file.getvalue()
            st.session_state.current_file_name = uploaded_file.name
            st.session_state.current_file_size = human_file_size(uploaded_file.size)
            st.markdown(
                f"""
                <div class="uploaded-file-box">
                    <div class="file-icon">PDF</div>
                    <div class="file-meta">
                        <div class="upload-name">{uploaded_file.name}</div>
                        <div class="upload-size">{human_file_size(uploaded_file.size)}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif st.session_state.get("current_file_name"):
            st.markdown(
                f"""
                <div class="uploaded-file-box">
                    <div class="file-icon">PDF</div>
                    <div class="file-meta">
                        <div class="upload-name">{st.session_state.current_file_name}</div>
                        <div class="upload-size">{st.session_state.get('current_file_size','')}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown('<div class="password-card">', unsafe_allow_html=True)
        st.markdown('<div class="password-title">Password Protected Statement</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="password-file">File: {st.session_state.get("current_file_name", "No statement selected.")}</div>', unsafe_allow_html=True)
        show_password = st.checkbox("Show password", value=False, key="show_pdf_password_checkbox")
        password = st.text_input("Enter PDF password:", type="default" if show_password else "password", key="pdf_password_input")
        st.markdown('</div>', unsafe_allow_html=True)

        analyze_clicked_left = st.button(left_button_label, type="primary", use_container_width=True, disabled=not st.session_state.get("uploaded_pdf_bytes"), key="left_analyze_statement_button")

        st.markdown('<hr style="border:0;border-top:1px solid #D0D7E2;margin:18px 0;">', unsafe_allow_html=True)
        st.markdown('<div class="left-title" style="font-size:1.25rem;">Loan Companies</div>', unsafe_allow_html=True)
        st.markdown('<div class="left-help">Rows matching these loan companies are excluded from the main Paid In total. Add or delete loan companies, then click Re-analyze.</div>', unsafe_allow_html=True)
        render_loan_company_list(st.session_state.loan_companies)
        st.text_input("Add loan company", key="new_loan_company", label_visibility="collapsed")
        if st.button("Add", use_container_width=True, key="add_loan_company_button"):
            add_loan_company()
            st.rerun()
        selected_delete = st.multiselect("Select companies to delete", options=st.session_state.loan_companies, key="companies_to_delete_multiselect")
        del_col, reset_col = st.columns(2)
        with del_col:
            if st.button("Delete Selected", use_container_width=True, key="delete_selected_companies_button"):
                delete_loan_companies(selected_delete)
                st.rerun()
        with reset_col:
            if st.button("Reset", use_container_width=True, key="reset_loan_companies_button"):
                st.session_state.loan_companies = DEFAULT_LOAN_COMPANIES.copy()
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    do_analyze = analyze_clicked_top or analyze_clicked_left
    if do_analyze:
        with st.spinner("Analyzing statement..."):
            result, error = analyze_pdf(st.session_state.uploaded_pdf_bytes, password, st.session_state.loan_companies)
        if error == "password_required":
            st.warning("This PDF is password protected. Enter the password and click Analyze Statement again.")
        elif error == "wrong_password":
            st.error("Wrong password or PDF could not be unlocked.")
        else:
            st.session_state.analysis = result
            st.success("Analysis complete.")

    analysis = st.session_state.get("analysis")

    with right_col:
        # metrics row
        if analysis:
            risk = analysis["risk_profile"]
            risk_bg, risk_fg = risk_colors(risk["rating"])
            total_paid_in = risk["total_paid_in"]
            total_installments = risk["total_loan"]
            total_received = sum(row["Loan Received"] for row in analysis.get("cashflow_rows", []))
            customer_name = analysis["customer_name"]
            tx_count = len(analysis["transactions"])
            risk_text = f'{risk["rating"]} ({risk["score"]})'
        else:
            total_paid_in = total_installments = total_received = 0.0
            customer_name = "N/A"
            tx_count = 0
            risk_text = "N/A"
            risk_bg, risk_fg = "#E2E8F0", "#0F172A"

        m1, m2, m3, m4, m5, m6 = st.columns([1.2, 1.2, 1.35, 1.1, 1.1, 1.0])
        with m1:
            style_metric_card("Customer Name", customer_name, "#D9EAF7", "#0F2B46")
        with m2:
            style_metric_card("Transactions Parsed", str(tx_count), "#D9E4F7", "#1E3A8A")
        with m3:
            style_metric_card("Monthly Paid In Total", money(total_paid_in), "#D9FBE7", "#166534")
        with m4:
            style_metric_card("Loan Received", money(total_received), "#D9F3FB", "#075985")
        with m5:
            style_metric_card("Installment Paid", money(total_installments), "#FFEBD1", "#9A3412")
        with m6:
            style_metric_card("Risk Rating", risk_text, risk_bg, risk_fg)

        st.markdown('<div class="right-shell">', unsafe_allow_html=True)
        tab0, tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
            "Executive Dashboard",
            "Matched Loan Companies",
            "Monthly Paid In",
            "Selected Company Analysis",
            "ELLEGANT CREDIT Summary",
            "Customer Risk Profile",
            "Full Summary",
        ])

        if not analysis:
            with tab0:
                st.markdown('<div class="section-card"><h3 class="section-title">Executive Visual Dashboard</h3><div class="section-subtitle">Upload a statement and click Analyze Statement to generate the dashboard.</div></div>', unsafe_allow_html=True)
            with tab1:
                st.info("No statement analyzed yet.")
            with tab2:
                st.info("No statement analyzed yet.")
            with tab3:
                st.info("No statement analyzed yet.")
            with tab4:
                st.info("No statement analyzed yet.")
            with tab5:
                st.info("No statement analyzed yet.")
            with tab6:
                st.info("No statement analyzed yet.")
            st.markdown('</div>', unsafe_allow_html=True)
            return

        with tab0:
            st.markdown('<div class="section-card"><h3 class="section-title">Executive Visual Dashboard</h3><div class="section-subtitle">This dashboard compares customer income, loan disbursements received, and installments repaid to matched loan companies.</div></div>', unsafe_allow_html=True)
            chart_left, chart_right = st.columns(2)
            monthly_chart_rows = []
            for month, vals in sorted(analysis["cashflow_monthly"].items()):
                monthly_chart_rows.append({
                    "Month": month,
                    "Normal Paid In": vals.get("paid_in_excluding_matches", 0.0),
                    "Loan Received": vals.get("loan_received", 0.0),
                    "Installments Paid": vals.get("installments_paid", 0.0),
                })
            with chart_left:
                st.subheader("Monthly Cashflow Comparison")
                if monthly_chart_rows:
                    chart_df = pd.DataFrame(monthly_chart_rows).set_index("Month")
                    st.bar_chart(chart_df, use_container_width=True)
                else:
                    st.info("No monthly cashflow data available.")
            with chart_right:
                st.subheader("Installments by Loan Company")
                company_chart_rows = [
                    {"Loan Company": row["Loan Company"], "Installments Paid": row["Installments Paid"]}
                    for row in analysis.get("cashflow_rows", []) if row["Installments Paid"] > 0
                ]
                if company_chart_rows:
                    company_df = pd.DataFrame(company_chart_rows).set_index("Loan Company")
                    st.bar_chart(company_df, use_container_width=True)
                else:
                    st.info("No installment data available.")
            st.subheader("Loan Company Cashflow Summary")
            if analysis.get("cashflow_rows"):
                df = pd.DataFrame(analysis["cashflow_rows"])
                for col in ["Loan Received", "Installments Paid", "Net Position"]:
                    df[col] = df[col].map(money)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No matched loan-company cashflows were found.")

        with tab1:
            matched_rows = []
            for term, result in analysis["search_results"].items():
                if result["count"] > 0:
                    matched_rows.append({
                        "Loan Company": term,
                        "Total Matches": result["count"],
                        "Page Number(s)": ", ".join(str(p) for p in sorted(result["pages"])),
                    })
            if matched_rows:
                st.dataframe(pd.DataFrame(matched_rows), use_container_width=True, hide_index=True)
            else:
                st.warning("No loan companies matched in the PDF.")

        with tab2:
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

        with tab3:
            st.markdown('<div class="section-card"><h3 class="section-title">Selected Company Analysis</h3><div class="section-subtitle">Select a matched company to view its loan receipts, installment payments, and inferred loan cycles.</div></div>', unsafe_allow_html=True)
            companies = [row["Loan Company"] for row in analysis.get("cashflow_rows", [])]
            if companies:
                selected_company = st.selectbox("Select loan company", companies)
                events = analysis.get("company_transactions", {}).get(selected_company, [])
                received_total = sum(e.get("received", 0.0) for e in events)
                paid_total = sum(e.get("paid", 0.0) for e in events)
                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1: style_metric_card("Loan Received", money(received_total), "#D9F3FB", "#075985")
                with sc2: style_metric_card("Installments Paid", money(paid_total), "#FFEBD1", "#9A3412")
                with sc3: style_metric_card("Net Position", money(received_total - paid_total), "#E2E8F0", "#0F172A")
                with sc4: style_metric_card("Transactions", str(len(events)), "#D9E4F7", "#1E3A8A")
                event_rows = []
                for event in events:
                    event_rows.append({
                        "Date": event.get("date", ""),
                        "Type": event.get("type", ""),
                        "Receipt No.": event.get("receipt", ""),
                        "Loan Received": money(event.get("received", 0.0)) if event.get("received", 0.0) else "",
                        "Installment Paid": money(event.get("paid", 0.0)) if event.get("paid", 0.0) else "",
                        "Page": event.get("page", ""),
                    })
                st.subheader("Transactions")
                st.dataframe(pd.DataFrame(event_rows), use_container_width=True, hide_index=True)
                cycle_rows = [row for row in analysis.get("cycle_rows", []) if row["Loan Company"] == selected_company]
                if cycle_rows:
                    df = pd.DataFrame(cycle_rows)
                    for col in ["Loan Received", "Installments Paid", "Net Position"]:
                        df[col] = df[col].map(money)
                    st.subheader("Inferred Loan Cycles")
                    st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No matched company analysis is available.")

        with tab4:
            st.markdown('<div class="section-card"><h3 class="section-title">ELLEGANT CREDIT LTD Payment Summary</h3><div class="section-subtitle">This block lists each ELLEGANT CREDIT transaction by month, including date paid, receipt number, and amount paid.</div></div>', unsafe_allow_html=True)
            if analysis["ellegant_rows"]:
                df = pd.DataFrame(analysis["ellegant_rows"])
                df["Amount Paid"] = df["Amount Paid"].map(money)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.warning("No ELLEGANT CREDIT LTD transactions found.")

        with tab5:
            st.markdown(
                f"""
                <div class="section-card">
                    <h3 class="section-title">Customer Risk Profile</h3>
                    <div class="section-subtitle">Formula: Total loan repayment to matched loan companies ÷ Total Paid In amount × 100</div>
                    <h2 style="color:{risk_fg}; margin-bottom:0;">{risk['rating']} — Score {risk['score']}</h2>
                    <p style="font-size:1.05rem;"><b>Loan Percentage:</b> {risk['percentage']:.2f}%</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            guide = pd.DataFrame([
                {"Loan Percentage of Total Amount": "0% - 10%", "Risk Rating": "Very Good", "Score": 5},
                {"Loan Percentage of Total Amount": "11% - 25%", "Risk Rating": "Good", "Score": 4},
                {"Loan Percentage of Total Amount": "26% - 50%", "Risk Rating": "Fair", "Score": 3},
                {"Loan Percentage of Total Amount": "51% - 75%", "Risk Rating": "Risky", "Score": 2},
                {"Loan Percentage of Total Amount": "Above 75%", "Risk Rating": "Very Risky", "Score": 1},
            ])
            st.dataframe(guide, use_container_width=True, hide_index=True)
            if analysis["loan_rows"]:
                df = pd.DataFrame(analysis["loan_rows"])
                df["Amount Paid"] = df["Amount Paid"].map(money)
                st.subheader("Loan repayment transactions used")
                st.dataframe(df, use_container_width=True, hide_index=True)

        with tab6:
            st.download_button(
                "Download Summary TXT",
                data=analysis["report"].encode("utf-8"),
                file_name="mutemi_mpesa_mobile_summary.txt",
                mime="text/plain",
                use_container_width=False,
            )
            st.text_area("Full Summary", analysis["report"], height=520)

        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
