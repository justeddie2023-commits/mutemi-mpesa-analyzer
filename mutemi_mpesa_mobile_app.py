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
    if not rows:
        return ""

    all_rows = [headers] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]

    def fmt(row):
        return " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers)))

    line = "-+-".join("-" * width for width in widths)
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
    report = build_report(
        customer_name,
        loan_companies,
        search_results,
        transactions,
        monthly,
        ellegant_rows,
        loan_rows,
        risk_profile,
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
        "report": report,
    }, None


def main():
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="📱",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    initialize_state()

    st.markdown(
        """
        <style>
            .stApp {
                background: #F3F5F7;
            }
            [data-testid="stSidebar"] {
                background: #EEF1F4;
                border-right: 1px solid #C9D2DC;
            }
            [data-testid="stSidebar"] .block-container {
                padding-top: 1rem;
                padding-left: 1rem;
                padding-right: 1rem;
            }
            .main-title {
                background: linear-gradient(90deg, #033312 0%, #044d1f 100%);
                color: white;
                padding: 18px 18px;
                border-radius: 0px;
                margin-bottom: 14px;
                border: 1px solid #0E5A2A;
            }
            .main-title h2 {
                font-size: 2.1rem;
                font-weight: 800;
            }
            .subtitle {
                color: #E4F7E8;
                font-size: 0.95rem;
                margin-top: 4px;
            }
            .metric-card {
                border-radius: 0px;
                padding: 14px 16px;
                border: 1px solid #BCC8D6;
                min-height: 110px;
                margin-bottom: 12px;
                box-shadow: none;
            }
            .metric-title {
                font-size: 0.86rem;
                font-weight: 700;
                opacity: 0.95;
            }
            .metric-value {
                font-size: 1.2rem;
                font-weight: 800;
                margin-top: 12px;
                word-break: break-word;
                line-height: 1.2;
            }
            .section-card {
                background: white;
                padding: 12px;
                border-radius: 0px;
                border: 1px solid #BCC8D6;
                margin-bottom: 14px;
            }
            .sidebar-heading {
                font-size: 2rem;
                font-weight: 800;
                color: #15304B;
                margin-bottom: 8px;
            }
            .sidebar-subtext {
                color: #54657A;
                font-size: 0.93rem;
                margin-bottom: 10px;
            }
            .loan-list-box {
                border: 1px solid #909CAB;
                background: white;
                max-height: 240px;
                overflow-y: auto;
                padding: 4px 0;
                margin-top: 8px;
                margin-bottom: 10px;
            }
            .loan-item {
                padding: 3px 8px;
                font-size: 0.95rem;
                line-height: 1.35;
                border-bottom: 1px solid #EEF2F6;
                color: #111827;
            }
            .loan-item:last-child {
                border-bottom: none;
            }
            .block-label {
                font-weight: 700;
                color: #243B53;
                margin-bottom: 6px;
            }
            div[data-baseweb="tab-list"] {
                gap: 2px;
            }
            button[data-baseweb="tab"] {
                background: #ECEFF3 !important;
                border: 1px solid #AEB9C4 !important;
                border-bottom: none !important;
                border-radius: 0 !important;
                padding: 10px 14px !important;
            }
            button[data-baseweb="tab"] p {
                font-weight: 700 !important;
                font-size: 0.95rem !important;
                color: #20344D !important;
            }
            button[aria-selected="true"][data-baseweb="tab"] {
                background: white !important;
                border-bottom: 2px solid white !important;
            }
            div[data-testid="stDataFrame"] {
                border: 1px solid #BCC8D6;
                border-radius: 0px;
                background: white;
            }
            div[data-testid="stAlert"] {
                border-radius: 0px;
            }
            .stButton > button {
                border-radius: 0px !important;
                font-weight: 700 !important;
            }
            .stDownloadButton > button {
                border-radius: 0px !important;
                font-weight: 700 !important;
            }
            @media only screen and (max-width: 900px) {
                .metric-value {
                    font-size: 1.05rem;
                }
                .main-title h2 {
                    font-size: 1.5rem;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown('<div class="sidebar-heading">Statement Upload</div>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader("Upload M-PESA PDF statement", type=["pdf"])
        password = st.text_input(
            "PDF password",
            type="password",
            help="Leave blank if the PDF is not password protected.",
        )
        analyze_clicked = st.button("Analyze Statement", type="primary", use_container_width=True)

        st.markdown("<hr style='margin:18px 0;border:0;border-top:1px solid #C7D0DA;'>", unsafe_allow_html=True)
        st.markdown('<div class="sidebar-heading" style="font-size:1.75rem;">Loan Companies</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-subtext">Rows matching these loan companies are excluded from the main Paid In total. Add or delete loan companies, then click Analyze.</div>', unsafe_allow_html=True)

        st.markdown('<div class="block-label">Current loan companies</div>', unsafe_allow_html=True)
        render_loan_company_list(st.session_state.loan_companies)

        st.text_input("Add loan company", key="new_loan_company")
        if st.button("Add", use_container_width=True):
            add_loan_company()
            st.rerun()

        selected_delete = st.multiselect(
            "Select companies to delete",
            options=st.session_state.loan_companies,
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Delete", use_container_width=True):
                delete_loan_companies(selected_delete)
                st.rerun()
        with col_b:
            if st.button("Reset", use_container_width=True):
                st.session_state.loan_companies = DEFAULT_LOAN_COMPANIES.copy()
                st.rerun()

    st.markdown(
        f"""
        <div class="main-title">
            <h2 style="margin:0;">{APP_TITLE}</h2>
            <div class="subtitle">Loan company matching, exclusion analysis, monthly Paid In totals, and ELLEGANT CREDIT payment tracking</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if uploaded_file is None:
        st.info("Upload an M-PESA PDF statement from the left panel to begin.")
        return

    pdf_bytes = uploaded_file.getvalue()

    if analyze_clicked:
        with st.spinner("Analyzing statement..."):
            result, error = analyze_pdf(pdf_bytes, password, st.session_state.loan_companies)

        if error == "password_required":
            st.warning("This PDF is password protected. Enter the password in the left panel and click Analyze Statement again.")
        elif error == "wrong_password":
            st.error("Wrong password or PDF could not be unlocked.")
        else:
            st.session_state.analysis = result
            st.success("Analysis complete.")

    analysis = st.session_state.analysis

    if not analysis:
        st.info("Click **Analyze Statement** after uploading the PDF.")
        return

    risk = analysis["risk_profile"]
    risk_bg, risk_fg = risk_colors(risk["rating"])
    total_paid_in = risk["total_paid_in"]
    total_loan = risk["total_loan"]

    c1, c2, c3, c4, c5 = st.columns([1.3, 1.1, 1.1, 1.1, 0.9])
    with c1:
        style_metric_card("Customer Name", analysis["customer_name"], "#D9E4EF", "#0F2B46")
    with c2:
        style_metric_card("Transactions Parsed", str(len(analysis["transactions"])), "#D9E4EF", "#1E3A8A")
    with c3:
        style_metric_card("Monthly Paid In Total", money(total_paid_in), "#CFE9D7", "#166534")
    with c4:
        style_metric_card("Loan Repayment Total", money(total_loan), "#F0DEC2", "#9A3412")
    with c5:
        style_metric_card("Risk Rating", f'{risk["rating"]} ({risk["score"]})', risk_bg, risk_fg)

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Matched Loan Companies",
        "Monthly Paid In",
        "ELLEGANT CREDIT Summary",
        "Customer Risk Profile",
        "Full Summary",
    ])

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
        st.markdown('<div class="section-card"><h4 style="margin-top:0;">ELLEGANT CREDIT LTD Payment Summary</h4><div style="color:#667085;">This block lists each ELLEGANT CREDIT transaction by month, including date paid, receipt number, and amount paid.</div></div>', unsafe_allow_html=True)
        if analysis["ellegant_rows"]:
            df = pd.DataFrame(analysis["ellegant_rows"])
            df["Amount Paid"] = df["Amount Paid"].map(money)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("No ELLEGANT CREDIT LTD transactions found.")

    with tab4:
        st.markdown(
            f"""
            <div class="section-card">
                <h4 style="margin-top:0;">Customer Risk Profile</h4>
                <p><b>Formula:</b> Total loan repayment to matched loan companies ÷ Total Paid In amount × 100</p>
                <h3 style="color:{risk_fg}; margin-bottom:0;">{risk["rating"]} — Score {risk["score"]}</h3>
                <p style="font-size:1.1rem;"><b>Loan Percentage:</b> {risk["percentage"]:.2f}%</p>
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
        st.subheader("Risk Rating Guide")
        st.dataframe(guide, use_container_width=True, hide_index=True)

        st.subheader("Loan repayment transactions used")
        if analysis["loan_rows"]:
            df = pd.DataFrame(analysis["loan_rows"])
            df["Amount Paid"] = df["Amount Paid"].map(money)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning("No matched loan repayment transactions found.")

    with tab5:
        st.download_button(
            "Download Summary TXT",
            data=analysis["report"].encode("utf-8"),
            file_name="mutemi_mpesa_mobile_summary.txt",
            mime="text/plain",
            use_container_width=False,
        )
        st.text_area("Full Summary", analysis["report"], height=500)


if __name__ == "__main__":
    main()
