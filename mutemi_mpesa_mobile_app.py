# MUTEMI M-PESA Statement Analyzer - Professional Desktop App
#
# What this app does:
# 1. Opens an M-PESA PDF statement using a file picker.
# 2. Requests the PDF password only if the PDF is protected.
# 3. Searches for selected loan company names/phrases.
# 4. Lets you add or delete loan companies inside the app.
# 5. Shows matched loan companies with page numbers.
# 6. Shows monthly Paid In totals excluding matched loan companies.
# 7. Shows ELLEGANT CREDIT LTD payment summary by month.
# 8. Shows customer risk profile based on loan repayments versus Paid In amount.
# 9. Extracts and displays customer name from the statement.
# 10. Adds executive dashboard charts, company-by-company loan analysis, and loan-cycle inference.
# 11. Lets you save the summary as a TXT file.
#
# Required package:
#   pip install pymupdf
#
# Run:
#   python mutemi_mpesa_statement_app.py

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from collections import defaultdict
import re
import os

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

APP_TITLE = "MUTEMI M-PESA Statement Analyzer"

DEFAULT_SEARCH_TERMS = [
    "CITYFIED CAPITAL",
    "GOLDSTEP CAPITAL",
    "fourth generation",
    "newark",
    "ELLEGANT CREDIT LTD",
    "OCL BUSINESS CREDIT LIMITED",
    "ASA LIMITED -GITHUNGURI",
    "UMOJA UFANISI",
    "INSPIRE CREDIT",
    "PEMBENI VENTURES",
    "SAMAWATI",
    "SIMPLEPAY",
    "OYA CREDIT",
    "TICK CREDIT",
    "PALLA",
    "CHEREHANI",
    "MWENYEJI INVESTMENT",
    "EDENBRIDGE",
    "BUSINESS CASH ADVANCE",
    "SASA PAY",
    "BIDII CREDIT",
    "INUKA",
    "ECLOF",
    "THIKA FAHALI EDEN INVESTMENT LTD",
    "PREMIER KENYA",
    "Premier SuperKwik",
    

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

def format_month_label(month_str):
    try:
        return datetime.strptime(month_str, "%Y-%m").strftime("%b %Y")
    except Exception:
        return month_str



def transaction_sort_key(tx):
    """Safe chronological sort key for statement transactions."""
    return (tx.get("date", ""), tx.get("time", ""), tx.get("receipt", ""))


def parse_amount(text):
    """Extract the last amount-like value from a table cell."""
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
    """Convert withdrawn values like -9,030.00 into positive amount paid."""
    if value < 0:
        return abs(value)
    return value


def keyword_pattern(term):
    """Flexible case-insensitive pattern. Spaces and hyphens can vary."""
    escaped = re.escape(term.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace(r"\-", r"\s*-\s*")
    return re.compile(escaped, re.IGNORECASE)


def build_term_patterns(search_terms):
    return {term: keyword_pattern(term) for term in search_terms if term.strip()}


def term_matches_text(text, patterns):
    return [term for term, pattern in patterns.items() if pattern.search(text or "")]


def is_ellegant_credit_text(text):
    """
    Robust ELLEGANT CREDIT matcher.

    Some M-PESA PDF rows are wrapped across two lines. In those rows, PyMuPDF can
    read the text in this order:
        ELLEGANT Completed -9,030.00 CREDIT LTD
    instead of:
        ELLEGANT CREDIT LTD

    This function prevents such rows from being skipped.
    """
    text = (text or "").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    words = set(text.split())

    has_name = "ELLEGANT" in words or "ELEGANT" in words
    return has_name and "CREDIT" in words and "LTD" in words


def make_text_table(headers, rows):
    """Create a clean text table with solid separator lines for the report page."""
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
    """
    Customer risk grading based on loan repayment percentage of total Paid In.

    0% - 10%   = Very Good, score 5
    11% - 25%  = Good, score 4
    26% - 50%  = Fair, score 3
    51% - 75%  = Risky, score 2
    Above 75%  = Very Risky, score 1
    """
    if percentage <= 10:
        return "Very Good", 5
    if percentage <= 25:
        return "Good", 4
    if percentage <= 50:
        return "Fair", 3
    if percentage <= 75:
        return "Risky", 2
    return "Very Risky", 1


def extract_customer_name_from_pdf(doc):
    """Extract customer name from the first page of an M-PESA statement."""
    if doc is None or doc.page_count == 0:
        return "N/A"

    page = doc[0]

    # First try normal text extraction. This usually returns:
    # Customer Name: Mutemi Muusya Mobile Number: ...
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

    # Fallback: use word positions. Find the Customer Name label, then read words
    # on the same row to the right of the label.
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


class PasswordDialog(tk.Toplevel):
    def __init__(self, parent, filename):
        super().__init__(parent)
        self.title("PDF Password Required")
        self.resizable(False, False)
        self.configure(bg="#F8FAFC")
        self.password = None

        self.transient(parent)
        self.grab_set()

        frame = tk.Frame(self, bg="#F8FAFC", padx=18, pady=18)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="Password Protected Statement",
            bg="#F8FAFC",
            fg="#0F172A",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")

        tk.Label(
            frame,
            text=f"File: {Path(filename).name}",
            bg="#F8FAFC",
            fg="#475569",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(6, 14))

        tk.Label(
            frame,
            text="Enter PDF password:",
            bg="#F8FAFC",
            fg="#0F172A",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.password_var = tk.StringVar()
        self.entry = ttk.Entry(frame, textvariable=self.password_var, width=48, show="*")
        self.entry.pack(fill="x", pady=(5, 8))
        self.entry.focus()

        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Show password",
            variable=self.show_var,
            command=self.toggle_password,
        ).pack(anchor="w")

        button_frame = tk.Frame(frame, bg="#F8FAFC")
        button_frame.pack(fill="x", pady=(16, 0))

        ttk.Button(button_frame, text="Cancel", command=self.cancel, style="Secondary.TButton").pack(side="right", padx=(8, 0))
        ttk.Button(button_frame, text="Open PDF", command=self.ok, style="Primary.TButton").pack(side="right")

        self.bind("<Return>", lambda event: self.ok())
        self.bind("<Escape>", lambda event: self.cancel())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

    def toggle_password(self):
        self.entry.config(show="" if self.show_var.get() else "*")

    def ok(self):
        self.password = self.password_var.get()
        self.destroy()

    def cancel(self):
        self.password = None
        self.destroy()


class MpesaStatementApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x780")
        self.root.minsize(1080, 680)
        self.root.configure(bg="#F1F5F9")

        self.pdf_path = None
        self.customer_name = "N/A"
        self.last_report = ""

        self.search_terms_var = tk.StringVar(value=DEFAULT_SEARCH_TERMS)

        self.configure_styles()
        self.create_widgets()
        self.set_risk_rating_display("N/A", "N/A")

        if fitz is None:
            messagebox.showerror(
                "Missing package",
                "PyMuPDF is not installed.\n\nOpen PowerShell and run:\npython -m pip install pymupdf",
            )

    def configure_styles(self):
        self.style = ttk.Style()

        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.colors = {
            "bg": "#F1F5F9",
            "panel": "#FFFFFF",
            "primary": "#16A34A",
            "primary_dark": "#15803D",
            "accent": "#0F766E",
            "text": "#0F172A",
            "muted": "#64748B",
            "border": "#CBD5E1",
            "header": "#052E16",
            "soft_green": "#DCFCE7",
            "soft_blue": "#DBEAFE",
            "soft_orange": "#FFEDD5",
        }

        self.style.configure("TFrame", background=self.colors["bg"])
        self.style.configure("Panel.TFrame", background=self.colors["panel"])
        self.style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=("Segoe UI", 10))
        self.style.configure("Panel.TLabel", background=self.colors["panel"], foreground=self.colors["text"], font=("Segoe UI", 10))

        self.style.configure(
            "Primary.TButton",
            background=self.colors["primary"],
            foreground="white",
            font=("Segoe UI", 10, "bold"),
            padding=(12, 7),
            borderwidth=0,
        )
        self.style.map("Primary.TButton", background=[("active", self.colors["primary_dark"])])

        self.style.configure(
            "Secondary.TButton",
            background="#E2E8F0",
            foreground=self.colors["text"],
            font=("Segoe UI", 10),
            padding=(10, 7),
            borderwidth=0,
        )
        self.style.map("Secondary.TButton", background=[("active", "#CBD5E1")])

        self.style.configure(
            "Danger.TButton",
            background="#FEE2E2",
            foreground="#991B1B",
            font=("Segoe UI", 10),
            padding=(10, 7),
            borderwidth=0,
        )
        self.style.map("Danger.TButton", background=[("active", "#FECACA")])

        self.style.configure(
            "Treeview",
            background="white",
            fieldbackground="white",
            foreground=self.colors["text"],
            rowheight=30,
            bordercolor=self.colors["border"],
            borderwidth=1,
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Treeview.Heading",
            background="#E2E8F0",
            foreground=self.colors["text"],
            font=("Segoe UI", 9, "bold"),
            padding=(6, 6),
        )
        self.style.map("Treeview", background=[("selected", "#BBF7D0")], foreground=[("selected", "#052E16")])

        self.style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            background="#E2E8F0",
            foreground=self.colors["text"],
            padding=(13, 8),
            font=("Segoe UI", 9, "bold"),
        )
        self.style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", self.colors["primary_dark"])])

    def create_widgets(self):
        self.create_header()

        main = tk.Frame(self.root, bg=self.colors["bg"], padx=14, pady=14)
        main.pack(fill="both", expand=True)

        self.create_file_bar(main)

        body = ttk.PanedWindow(main, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = tk.Frame(body, bg="white", padx=12, pady=12, highlightbackground=self.colors["border"], highlightthickness=1)
        right = ttk.Frame(body, padding=(12, 0, 0, 0))

        body.add(left, weight=1)
        body.add(right, weight=4)

        self.create_search_words_panel(left)
        self.create_results_panel(right)
        self.create_bottom_bar(main)

    def create_header(self):
        header = tk.Frame(self.root, bg=self.colors["header"], padx=18, pady=14)
        header.pack(fill="x")

        title_frame = tk.Frame(header, bg=self.colors["header"])
        title_frame.pack(side="left", fill="x", expand=True)

        tk.Label(
            title_frame,
            text=APP_TITLE,
            bg=self.colors["header"],
            fg="white",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")

        tk.Label(
            title_frame,
            text="Loan company matching, exclusion analysis, monthly Paid In totals, and ELLEGANT CREDIT payment tracking",
            bg=self.colors["header"],
            fg="#BBF7D0",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(3, 0))

        ttk.Button(
            header,
            text="Select Statement PDF",
            command=self.select_pdf,
            style="Primary.TButton",
        ).pack(side="right")

        ttk.Button(
            header,
            text="Re-analyze",
            command=self.analyze_pdf,
            style="Secondary.TButton",
        ).pack(side="right", padx=(0, 10))

    def create_file_bar(self, parent):
        bar = tk.Frame(parent, bg="white", padx=14, pady=12, highlightbackground=self.colors["border"], highlightthickness=1)
        bar.pack(fill="x")

        tk.Label(
            bar,
            text="Statement File",
            bg="white",
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        self.file_label = tk.Label(
            bar,
            text="No statement selected.",
            bg="white",
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.file_label.pack(side="left", fill="x", expand=True, padx=(12, 0))

        ttk.Button(
            bar,
            text="Re-analyze Statement",
            command=self.analyze_pdf,
            style="Primary.TButton",
        ).pack(side="right", padx=(10, 0))

    def create_search_words_panel(self, parent):
        tk.Label(parent, text="Loan Companies", bg="white", fg=self.colors["text"], font=("Segoe UI", 13, "bold")).pack(anchor="w")

        tk.Label(
            parent,
            text="Rows matching these loan companies are excluded from the main Paid In total. Add or delete loan companies, then click Re-analyze.",
            bg="white",
            fg=self.colors["muted"],
            wraplength=260,
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 12))

        list_frame = tk.Frame(parent, bg="white")
        list_frame.pack(fill="both", expand=True)

        self.terms_listbox = tk.Listbox(
            list_frame,
            listvariable=self.search_terms_var,
            height=18,
            selectmode="extended",
            relief="flat",
            bg="#F8FAFC",
            fg=self.colors["text"],
            selectbackground="#BBF7D0",
            selectforeground="#052E16",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            font=("Segoe UI", 10),
        )
        self.terms_listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.terms_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.terms_listbox.config(yscrollcommand=scrollbar.set)

        entry_frame = tk.Frame(parent, bg="white")
        entry_frame.pack(fill="x", pady=(12, 0))

        self.new_term_var = tk.StringVar()
        self.new_term_entry = ttk.Entry(entry_frame, textvariable=self.new_term_var)
        self.new_term_entry.pack(side="left", fill="x", expand=True)
        self.new_term_entry.bind("<Return>", lambda event: self.add_search_term())

        ttk.Button(entry_frame, text="Add", command=self.add_search_term, style="Primary.TButton").pack(side="left", padx=(8, 0))

        action_frame = tk.Frame(parent, bg="white")
        action_frame.pack(fill="x", pady=(10, 0))

        ttk.Button(action_frame, text="Delete Selected", command=self.delete_selected_terms, style="Danger.TButton").pack(side="left")
        ttk.Button(action_frame, text="Reset", command=self.reset_default_terms, style="Secondary.TButton").pack(side="left", padx=(8, 0))

    def create_results_panel(self, parent):
        self.cards_frame = tk.Frame(parent, bg=self.colors["bg"])
        self.cards_frame.pack(fill="x", pady=(0, 12))

        self.card_values = {}
        self.create_card("Customer Name", "N/A", "#E0F2FE", "#075985")
        self.create_card("Transactions Parsed", "0", self.colors["soft_blue"], "#1E3A8A")
        self.create_card("Monthly Paid In Total", "0.00", self.colors["soft_green"], "#166534")
        self.create_card("Loan Received", "0.00", "#DFF6FF", "#075985")
        self.create_card("Installment Paid", "0.00", self.colors["soft_orange"], "#9A3412")
        self.create_card("Risk Rating", "N/A", "#FEE2E2", "#991B1B")

        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)

        dashboard_tab = ttk.Frame(notebook, padding=8)
        matched_tab = ttk.Frame(notebook, padding=8)
        monthly_tab = ttk.Frame(notebook, padding=8)
        selected_tab = ttk.Frame(notebook, padding=8)
        ellegant_tab = ttk.Frame(notebook, padding=8)
        risk_tab = ttk.Frame(notebook, padding=8)
        report_tab = ttk.Frame(notebook, padding=8)

        notebook.add(dashboard_tab, text="Executive Dashboard")
        notebook.add(matched_tab, text="Matched Loan Companies")
        notebook.add(monthly_tab, text="Monthly Paid In")
        notebook.add(selected_tab, text="Selected Company Analysis")
        notebook.add(ellegant_tab, text="ELLEGANT CREDIT Summary")
        notebook.add(risk_tab, text="Customer Risk Profile")
        notebook.add(report_tab, text="Full Summary")

        self.create_dashboard_panel(dashboard_tab)
        self.create_matched_tree(matched_tab)
        self.create_monthly_tree(monthly_tab)
        self.create_selected_company_panel(selected_tab)
        self.create_ellegant_tree(ellegant_tab)
        self.create_risk_profile_panel(risk_tab)
        self.create_report_text(report_tab)

    def create_card(self, title, value, bg, fg):
        card = tk.Frame(self.cards_frame, bg=bg, padx=16, pady=12, highlightbackground=self.colors["border"], highlightthickness=1)
        card.pack(side="left", fill="x", expand=True, padx=(0, 10))

        tk.Label(card, text=title, bg=bg, fg=fg, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        value_label = tk.Label(card, text=value, bg=bg, fg=fg, font=("Segoe UI", 18, "bold"))
        value_label.pack(anchor="w", pady=(4, 0))

        self.card_values[title] = value_label

    def create_dashboard_panel(self, parent):
        container = tk.Frame(parent, bg="white", padx=12, pady=10)
        container.pack(fill="both", expand=True)

        tk.Label(container, text="Executive Visual Dashboard", bg="white", fg=self.colors["text"], font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(
            container,
            text="This dashboard compares customer income, loan disbursements received, and installments repaid to matched loan companies.",
            bg="white", fg=self.colors["muted"], font=("Segoe UI", 9), wraplength=980, justify="left"
        ).pack(anchor="w", pady=(3, 10))

        chart_frame = tk.Frame(container, bg="white")
        chart_frame.pack(fill="both", expand=True)

        left = tk.Frame(chart_frame, bg="white")
        right = tk.Frame(chart_frame, bg="white")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self.monthly_chart_canvas = tk.Canvas(left, height=340, bg="#FFFFFF", highlightbackground=self.colors["border"], highlightthickness=1)
        self.monthly_chart_canvas.pack(fill="both", expand=True)

        self.company_chart_canvas = tk.Canvas(right, height=340, bg="#FFFFFF", highlightbackground=self.colors["border"], highlightthickness=1)
        self.company_chart_canvas.pack(fill="both", expand=True)

        summary_frame = tk.Frame(container, bg="white")
        summary_frame.pack(fill="both", expand=True, pady=(12, 0))

        self.company_summary_tree = ttk.Treeview(summary_frame, columns=("company", "received", "installments", "net", "txns"), show="headings", height=8)
        self.company_summary_tree.heading("company", text="Loan Company")
        self.company_summary_tree.heading("received", text="Loan Received")
        self.company_summary_tree.heading("installments", text="Installments Paid")
        self.company_summary_tree.heading("net", text="Net Position")
        self.company_summary_tree.heading("txns", text="Matched Txns")
        self.company_summary_tree.column("company", width=280, anchor="w")
        self.company_summary_tree.column("received", width=140, anchor="e")
        self.company_summary_tree.column("installments", width=150, anchor="e")
        self.company_summary_tree.column("net", width=140, anchor="e")
        self.company_summary_tree.column("txns", width=110, anchor="center")
        self.company_summary_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(summary_frame, orient="vertical", command=self.company_summary_tree.yview)
        scroll.pack(side="right", fill="y")
        self.company_summary_tree.configure(yscrollcommand=scroll.set)

    def create_selected_company_panel(self, parent):
        container = tk.Frame(parent, bg="white", padx=12, pady=10)
        container.pack(fill="both", expand=True)

        header = tk.Frame(container, bg="white")
        header.pack(fill="x", pady=(0, 10))
        tk.Label(header, text="Selected Loan Company Summary", bg="white", fg=self.colors["text"], font=("Segoe UI", 13, "bold")).pack(side="left")

        self.selected_company_var = tk.StringVar(value="")
        self.selected_company_combo = ttk.Combobox(header, textvariable=self.selected_company_var, state="readonly", width=36)
        self.selected_company_combo.pack(side="left", padx=(16, 8))
        self.selected_company_combo.bind("<<ComboboxSelected>>", lambda event: self.update_selected_company_view())
        ttk.Button(header, text="View Summary", command=self.update_selected_company_view, style="Primary.TButton").pack(side="left")

        self.selected_company_totals = tk.Frame(container, bg="#F8FAFC", padx=12, pady=10, highlightbackground=self.colors["border"], highlightthickness=1)
        self.selected_company_totals.pack(fill="x", pady=(0, 10))
        self.selected_received_var = tk.StringVar(value="0.00")
        self.selected_paid_var = tk.StringVar(value="0.00")
        self.selected_net_var = tk.StringVar(value="0.00")
        self.selected_count_var = tk.StringVar(value="0")

        for label, var in [("Loan Received", self.selected_received_var), ("Installments Paid", self.selected_paid_var), ("Net Position", self.selected_net_var), ("Matched Transactions", self.selected_count_var)]:
            block = tk.Frame(self.selected_company_totals, bg="#F8FAFC")
            block.pack(side="left", fill="x", expand=True, padx=(0, 10))
            tk.Label(block, text=label, bg="#F8FAFC", fg=self.colors["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(block, textvariable=var, bg="#F8FAFC", fg=self.colors["text"], font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(3, 0))

        paned = ttk.PanedWindow(container, orient="vertical")
        paned.pack(fill="both", expand=True)
        tx_frame = tk.Frame(paned, bg="white")
        cycle_frame = tk.Frame(paned, bg="white")
        paned.add(tx_frame, weight=2)
        paned.add(cycle_frame, weight=1)

        tk.Label(tx_frame, text="Transactions for Selected Company", bg="white", fg=self.colors["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 5))
        self.selected_company_tree = ttk.Treeview(tx_frame, columns=("date", "type", "receipt", "received", "paid", "page"), show="headings", height=8)
        for col, title, width, anchor in [("date", "Date", 120, "center"), ("type", "Type", 140, "center"), ("receipt", "Receipt No.", 220, "w"), ("received", "Loan Received", 140, "e"), ("paid", "Installment Paid", 140, "e"), ("page", "Page", 70, "center")]:
            self.selected_company_tree.heading(col, text=title)
            self.selected_company_tree.column(col, width=width, anchor=anchor)
        self.selected_company_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(tx_frame, orient="vertical", command=self.selected_company_tree.yview)
        scroll.pack(side="right", fill="y")
        self.selected_company_tree.configure(yscrollcommand=scroll.set)

        tk.Label(cycle_frame, text="Inferred Loan Cycles", bg="white", fg=self.colors["text"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 5))
        self.loan_cycle_tree = ttk.Treeview(cycle_frame, columns=("company", "cycle", "loan_date", "received", "installments", "count", "net"), show="headings", height=5)
        for col, title, width, anchor in [("company", "Loan Company", 220, "w"), ("cycle", "Cycle", 70, "center"), ("loan_date", "Loan Date", 130, "center"), ("received", "Loan Received", 130, "e"), ("installments", "Installments Paid", 140, "e"), ("count", "No. of Installments", 130, "center"), ("net", "Net Position", 130, "e")]:
            self.loan_cycle_tree.heading(col, text=title)
            self.loan_cycle_tree.column(col, width=width, anchor=anchor)
        self.loan_cycle_tree.pack(side="left", fill="both", expand=True)
        scroll2 = ttk.Scrollbar(cycle_frame, orient="vertical", command=self.loan_cycle_tree.yview)
        scroll2.pack(side="right", fill="y")
        self.loan_cycle_tree.configure(yscrollcommand=scroll2.set)

    def create_matched_tree(self, parent):
        self.matched_tree = ttk.Treeview(parent, columns=("term", "count", "pages"), show="headings")
        self.matched_tree.heading("term", text="Loan Company")
        self.matched_tree.heading("count", text="Total Matches")
        self.matched_tree.heading("pages", text="Page Number(s)")

        self.matched_tree.column("term", width=320, anchor="w")
        self.matched_tree.column("count", width=120, anchor="center")
        self.matched_tree.column("pages", width=320, anchor="w")

        self.matched_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.matched_tree.yview)
        scroll.pack(side="right", fill="y")
        self.matched_tree.configure(yscrollcommand=scroll.set)

    def create_monthly_tree(self, parent):
        self.monthly_tree = ttk.Treeview(
            parent,
            columns=("month", "paid_excluding", "counted_rows", "paid_excluded", "excluded_rows"),
            show="headings",
        )
        self.monthly_tree.heading("month", text="Month")
        self.monthly_tree.heading("paid_excluding", text="Paid In Excluding Matches")
        self.monthly_tree.heading("counted_rows", text="Counted Rows")
        self.monthly_tree.heading("paid_excluded", text="Paid In Excluded")
        self.monthly_tree.heading("excluded_rows", text="Excluded Rows")

        self.monthly_tree.column("month", width=100, anchor="center")
        self.monthly_tree.column("paid_excluding", width=210, anchor="e")
        self.monthly_tree.column("counted_rows", width=120, anchor="center")
        self.monthly_tree.column("paid_excluded", width=170, anchor="e")
        self.monthly_tree.column("excluded_rows", width=120, anchor="center")

        self.monthly_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.monthly_tree.yview)
        scroll.pack(side="right", fill="y")
        self.monthly_tree.configure(yscrollcommand=scroll.set)

    def create_ellegant_tree(self, parent):
        intro = tk.Frame(parent, bg="white", padx=10, pady=8)
        intro.pack(fill="x", pady=(0, 8))

        tk.Label(
            intro,
            text="ELLEGANT CREDIT LTD Payment Summary",
            bg="white",
            fg=self.colors["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        tk.Label(
            intro,
            text="This block lists each ELLEGANT CREDIT transaction by month, including date paid, receipt number, and amount paid.",
            bg="white",
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        self.ellegant_tree = ttk.Treeview(
            parent,
            columns=("month", "date", "receipt", "amount", "page"),
            show="headings",
        )
        self.ellegant_tree.heading("month", text="Month")
        self.ellegant_tree.heading("date", text="Date Paid")
        self.ellegant_tree.heading("receipt", text="Transaction ID / Receipt No.")
        self.ellegant_tree.heading("amount", text="Amount Paid")
        self.ellegant_tree.heading("page", text="Page")

        self.ellegant_tree.column("month", width=90, anchor="center")
        self.ellegant_tree.column("date", width=120, anchor="center")
        self.ellegant_tree.column("receipt", width=230, anchor="w")
        self.ellegant_tree.column("amount", width=140, anchor="e")
        self.ellegant_tree.column("page", width=70, anchor="center")

        self.ellegant_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.ellegant_tree.yview)
        scroll.pack(side="right", fill="y")
        self.ellegant_tree.configure(yscrollcommand=scroll.set)

    def create_risk_profile_panel(self, parent):
        container = tk.Frame(parent, bg="white", padx=14, pady=12)
        container.pack(fill="both", expand=True)

        tk.Label(
            container,
            text="Customer Risk Profile",
            bg="white",
            fg=self.colors["text"],
            font=("Segoe UI", 13, "bold")
        ).pack(anchor="w")

        tk.Label(
            container,
            text="Risk is calculated as: Total repayment to matched loan companies ÷ Total Paid In amount × 100.",
            bg="white",
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            wraplength=820,
            justify="left"
        ).pack(anchor="w", pady=(3, 12))

        self.risk_summary_frame = tk.Frame(container, bg="#F8FAFC", padx=12, pady=10, highlightbackground=self.colors["border"], highlightthickness=1)
        self.risk_summary_frame.pack(fill="x", pady=(0, 12))

        self.risk_total_paid_in_var = tk.StringVar(value="0.00")
        self.risk_total_loan_var = tk.StringVar(value="0.00")
        self.risk_percentage_var = tk.StringVar(value="0.00%")
        self.risk_rating_var = tk.StringVar(value="N/A")
        self.risk_score_var = tk.StringVar(value="N/A")

        fields = [
            ("Total Paid In", self.risk_total_paid_in_var),
            ("Total Loan Repayment", self.risk_total_loan_var),
            ("Loan % of Paid In", self.risk_percentage_var),
            ("Risk Rating", self.risk_rating_var),
            ("Score", self.risk_score_var),
        ]

        for label, variable in fields:
            block = tk.Frame(self.risk_summary_frame, bg="#F8FAFC")
            block.pack(side="left", fill="x", expand=True, padx=(0, 8))

            tk.Label(block, text=label, bg="#F8FAFC", fg=self.colors["muted"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
            value_label = tk.Label(block, textvariable=variable, bg="#F8FAFC", fg=self.colors["text"], font=("Segoe UI", 14, "bold"))
            value_label.pack(anchor="w", pady=(3, 0))

            if label == "Risk Rating":
                self.risk_rating_value_label = value_label
            elif label == "Score":
                self.risk_score_value_label = value_label

        rating_frame = tk.Frame(container, bg="white")
        rating_frame.pack(fill="x", pady=(0, 12))

        tk.Label(
            rating_frame,
            text="Risk Rating Guide",
            bg="white",
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0, 5))

        self.risk_guide_tree = ttk.Treeview(
            rating_frame,
            columns=("range", "rating", "score"),
            show="headings",
            height=5
        )
        self.risk_guide_tree.heading("range", text="Loan Percentage of Total Amount")
        self.risk_guide_tree.heading("rating", text="Risk Rating")
        self.risk_guide_tree.heading("score", text="Score")

        self.risk_guide_tree.column("range", width=280, anchor="center")
        self.risk_guide_tree.column("rating", width=180, anchor="center")
        self.risk_guide_tree.column("score", width=100, anchor="center")

        self.risk_guide_tree.pack(fill="x")

        guide_rows = [
            ("0% - 10%", "Very Good", "5"),
            ("11% - 25%", "Good", "4"),
            ("26% - 50%", "Fair", "3"),
            ("51% - 75%", "Risky", "2"),
            ("Above 75%", "Very Risky", "1"),
        ]

        for row in guide_rows:
            self.risk_guide_tree.insert("", "end", values=row)

        tk.Label(
            container,
            text="Loan Repayments Detected from Matched Loan Companies",
            bg="white",
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(8, 5))

        repayment_frame = tk.Frame(container, bg="white")
        repayment_frame.pack(fill="both", expand=True)

        self.loan_repayment_tree = ttk.Treeview(
            repayment_frame,
            columns=("month", "date", "company", "receipt", "amount", "page"),
            show="headings"
        )

        self.loan_repayment_tree.heading("month", text="Month")
        self.loan_repayment_tree.heading("date", text="Date Paid")
        self.loan_repayment_tree.heading("company", text="Loan Company")
        self.loan_repayment_tree.heading("receipt", text="Transaction ID / Receipt No.")
        self.loan_repayment_tree.heading("amount", text="Amount Paid")
        self.loan_repayment_tree.heading("page", text="Page")

        self.loan_repayment_tree.column("month", width=90, anchor="center")
        self.loan_repayment_tree.column("date", width=110, anchor="center")
        self.loan_repayment_tree.column("company", width=240, anchor="w")
        self.loan_repayment_tree.column("receipt", width=210, anchor="w")
        self.loan_repayment_tree.column("amount", width=130, anchor="e")
        self.loan_repayment_tree.column("page", width=70, anchor="center")

        self.loan_repayment_tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(repayment_frame, orient="vertical", command=self.loan_repayment_tree.yview)
        scroll.pack(side="right", fill="y")
        self.loan_repayment_tree.configure(yscrollcommand=scroll.set)

    def create_report_text(self, parent):
        wrapper = tk.Frame(parent, bg=self.colors["bg"])
        wrapper.pack(fill="both", expand=True)

        header = tk.Frame(wrapper, bg="#F8FAFC", highlightbackground=self.colors["border"], highlightthickness=1, padx=16, pady=14)
        header.pack(fill="x", padx=8, pady=(8, 0))

        tk.Label(header, text="Professional Summary Page", bg="#F8FAFC", fg=self.colors["text"], font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(
            header,
            text="A cleaner executive report with section headings, decision-ready highlights, and a readable narrative summary.",
            bg="#F8FAFC", fg=self.colors["muted"], font=("Segoe UI", 9), justify="left"
        ).pack(anchor="w", pady=(4, 0))

        text_holder = tk.Frame(wrapper, bg=self.colors["bg"])
        text_holder.pack(fill="both", expand=True, padx=8, pady=(8, 8))

        self.report_text = tk.Text(
            text_holder,
            wrap="word",
            font=("Segoe UI", 10),
            bg="white",
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            padx=18,
            pady=18,
            spacing1=3,
            spacing2=2,
            spacing3=5,
        )
        self.report_text.pack(side="left", fill="both", expand=True)

        yscroll = ttk.Scrollbar(text_holder, orient="vertical", command=self.report_text.yview)
        yscroll.pack(side="right", fill="y")
        self.report_text.config(yscrollcommand=yscroll.set)

        # Text styles for professional summary display
        self.report_text.tag_configure("title", font=("Segoe UI", 18, "bold"), foreground="#052E16", spacing3=10)
        self.report_text.tag_configure("subtitle", font=("Segoe UI", 10), foreground="#64748B", spacing3=14)
        self.report_text.tag_configure("section", font=("Segoe UI", 13, "bold"), foreground="#0F172A", spacing1=10, spacing3=7)
        self.report_text.tag_configure("subsection", font=("Segoe UI", 12, "bold"), foreground="#166534", spacing1=8, spacing3=5)
        self.report_text.tag_configure("body", font=("Segoe UI", 10), foreground="#0F172A", lmargin1=2, lmargin2=2)
        self.report_text.tag_configure("muted", font=("Segoe UI", 10), foreground="#475569")
        self.report_text.tag_configure("bullet", font=("Segoe UI", 10), foreground="#0F172A", lmargin1=18, lmargin2=32, spacing1=2, spacing3=2)
        self.report_text.tag_configure("metric", font=("Segoe UI", 10, "bold"), foreground="#9A3412")
        self.report_text.tag_configure("good", font=("Segoe UI", 10, "bold"), foreground="#166534")
        self.report_text.tag_configure("warning", font=("Segoe UI", 10, "bold"), foreground="#92400E")
        self.report_text.tag_configure("bad", font=("Segoe UI", 10, "bold"), foreground="#B91C1C")
        self.report_text.tag_configure("tablehead", font=("Consolas", 10, "bold"), foreground="#0F172A")
        self.report_text.tag_configure("table", font=("Consolas", 10), foreground="#1E293B")

    def create_bottom_bar(self, parent):
        bottom = tk.Frame(parent, bg=self.colors["bg"])
        bottom.pack(fill="x", pady=(12, 0))

        ttk.Button(bottom, text="Analyze / Re-analyze", command=self.analyze_pdf, style="Primary.TButton").pack(side="left")
        ttk.Button(bottom, text="Save Summary TXT", command=self.save_summary, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="Clear Results", command=self.clear_results, style="Secondary.TButton").pack(side="left", padx=(8, 0))

        self.status_label = tk.Label(bottom, text="Ready.", bg=self.colors["bg"], fg=self.colors["muted"], font=("Segoe UI", 9))
        self.status_label.pack(side="right")

    def get_search_terms(self):
        terms = list(self.terms_listbox.get(0, tk.END))
        return [term.strip() for term in terms if term.strip()]

    def add_search_term(self):
        term = self.new_term_var.get().strip()

        if not term:
            return

        existing = [t.lower() for t in self.get_search_terms()]
        if term.lower() in existing:
            messagebox.showinfo("Duplicate", "This loan company already exists.")
            return

        self.terms_listbox.insert(tk.END, term)
        self.new_term_var.set("")

    def delete_selected_terms(self):
        selected = list(self.terms_listbox.curselection())

        if not selected:
            messagebox.showinfo("No selection", "Select one or more loan companies to delete.")
            return

        for index in reversed(selected):
            self.terms_listbox.delete(index)

    def reset_default_terms(self):
        self.search_terms_var.set(DEFAULT_SEARCH_TERMS)

    def select_pdf(self):
        path = filedialog.askopenfilename(
            title="Select M-PESA Statement PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )

        if not path:
            return

        self.pdf_path = path
        self.file_label.config(text=path, fg=self.colors["text"])

        self.analyze_pdf()

    def open_pdf(self, pdf_path):
        if fitz is None:
            raise RuntimeError("PyMuPDF is not installed. Run: python -m pip install pymupdf")

        doc = fitz.open(pdf_path)

        if doc.needs_pass:
            for _ in range(3):
                dialog = PasswordDialog(self.root, pdf_path)
                self.root.wait_window(dialog)

                if dialog.password is None:
                    doc.close()
                    return None

                if doc.authenticate(dialog.password):
                    return doc

                messagebox.showerror("Wrong password", "Wrong password or PDF could not be unlocked.")

            doc.close()
            messagebox.showerror("Failed", "Failed after 3 password attempts.")
            return None

        return doc

    def find_search_terms_by_page(self, doc, search_terms):
        patterns = build_term_patterns(search_terms)
        results = {term: {"count": 0, "pages": set()} for term in search_terms}

        for page_number, page in enumerate(doc, start=1):
            text = clean_text(page.get_text("text"))

            for term, pattern in patterns.items():
                matches = list(pattern.finditer(text))
                if matches:
                    results[term]["count"] += len(matches)
                    results[term]["pages"].add(page_number)

        return results

    def detect_column_ranges(self, words, page_width, page_height):
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

        boundaries = {}
        boundaries["receipt"] = (0, (x[0] + x[1]) / 2)
        boundaries["completion"] = ((x[0] + x[1]) / 2, (x[1] + x[2]) / 2)
        boundaries["details"] = ((x[1] + x[2]) / 2, (x[2] + x[3]) / 2)
        boundaries["status"] = ((x[2] + x[3]) / 2, (x[3] + x[4]) / 2)
        boundaries["paid"] = ((x[3] + x[4]) / 2, (x[4] + x[5]) / 2)
        boundaries["withdrawn"] = ((x[4] + x[5]) / 2, (x[5] + x[6]) / 2)
        boundaries["balance"] = ((x[5] + x[6]) / 2, page_width + 20)

        return boundaries

    def cell_text(self, row_words, x_min, x_max):
        cell = [w for w in row_words if x_min <= w[0] < x_max]
        cell.sort(key=lambda w: (round(w[1], 1), w[0]))
        return clean_text(" ".join(str(w[4]) for w in cell))

    def parse_transactions_from_page(self, page, page_number, patterns):
        words = page.get_text("words")

        if not words:
            return []

        page_width = page.rect.width
        page_height = page.rect.height
        columns = self.detect_column_ranges(words, page_width, page_height)

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
            paid_text = self.cell_text(row_words, *columns["paid"])
            withdrawn_text = self.cell_text(row_words, *columns["withdrawn"])
            details_text = self.cell_text(row_words, *columns["details"])
            receipt_text = self.cell_text(row_words, *columns["receipt"])

            paid_in = parse_amount(paid_text)
            withdrawn = parse_amount(withdrawn_text)

            # Search using both the clean Details cell and the full row.
            # The Details cell is important because wrapped PDF text can be read out of order.
            search_blob = clean_text(f"{details_text} {row_text}")
            matched_terms = term_matches_text(search_blob, patterns)

            # Extra protection for ELLEGANT CREDIT LTD rows where PDF extraction separates words.
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

    def parse_all_transactions(self, doc, search_terms):
        patterns = build_term_patterns(search_terms)
        transactions = []

        for page_number, page in enumerate(doc, start=1):
            transactions.extend(self.parse_transactions_from_page(page, page_number, patterns))

        return transactions

    def monthly_paid_in_summary(self, transactions):
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

    def ellegant_credit_transactions(self, transactions):
        patterns = build_term_patterns(ELLEGANT_TERMS)
        rows = []

        for tx in transactions:
            search_blob = clean_text(f"{tx.get('details', '')} {tx.get('row_text', '')} {tx.get('search_blob', '')}")

            if term_matches_text(search_blob, patterns) or is_ellegant_credit_text(search_blob):
                amount = tx.get("amount_paid", 0.0)
                if amount <= 0:
                    amount = amount_paid_value(tx.get("withdrawn", 0.0))

                rows.append({
                    "month": tx["month"],
                    "date": tx["date"],
                    "receipt": tx["receipt"],
                    "amount": amount,
                    "page": tx["page"],
                    "details": tx["details"],
                })

        rows.sort(key=lambda r: (r["month"], r["date"], r["receipt"]))
        return rows

    def loan_repayment_transactions(self, transactions):
        """
        Loan repayment total is based on amount paid out to rows matching the loan companies.
        These matched loan companies are treated as loan companies.
        """
        rows = []

        for tx in transactions:
            amount = tx.get("amount_paid", 0.0)

            if amount <= 0:
                continue

            matched_terms = tx.get("matched_terms", [])

            if not matched_terms:
                continue

            rows.append({
                "month": tx["month"],
                "date": tx["date"],
                "company": ", ".join(matched_terms),
                "receipt": tx["receipt"],
                "amount": amount,
                "page": tx["page"],
            })

        rows.sort(key=lambda r: (r["month"], r["date"], r["company"], r["receipt"]))
        return rows

    def risk_profile_summary(self, monthly, loan_rows):
        total_paid_in = sum(values["paid_in_excluding_matches"] for values in monthly.values())
        total_loan = sum(row["amount"] for row in loan_rows)

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

    def loan_company_cashflow_summary(self, transactions):
        """Group matched loan company transactions into received loans and installments paid."""
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
                selected_transactions[company].append({"date": tx.get("date", ""), "time": tx.get("time", ""), "type": tx_type, "receipt": tx.get("receipt", ""), "received": received, "paid": paid, "page": tx.get("page", ""), "details": tx.get("details", "")})

        rows = []
        for company, values in summary.items():
            received = values["loan_received"]
            paid = values["installments_paid"]
            rows.append({"company": company, "loan_received": received, "installments_paid": paid, "net_position": received - paid, "matched_transactions": values["matched_transactions"]})
        rows.sort(key=lambda r: r["installments_paid"], reverse=True)
        self.company_transactions = dict(selected_transactions)
        return rows, monthly

    def loan_cycle_summary(self):
        """Infer loan cycles: each matched paid-in starts a new cycle; paid-out rows until the next paid-in are installments."""
        cycles = []
        for company, events in getattr(self, "company_transactions", {}).items():
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
                    current = {"company": company, "cycle": cycle_no, "loan_date": event.get("date", ""), "loan_received": received, "installments_paid": 0.0, "installment_count": 0}
                if paid > 0:
                    if current is None:
                        cycle_no += 1
                        current = {"company": company, "cycle": cycle_no, "loan_date": "Before first matched loan", "loan_received": 0.0, "installments_paid": 0.0, "installment_count": 0}
                    current["installments_paid"] += paid
                    current["installment_count"] += 1
            if current is not None:
                cycles.append(current)
        for cycle in cycles:
            cycle["net_position"] = cycle["loan_received"] - cycle["installments_paid"]
        self.loan_cycles = cycles
        return cycles

    def build_report(self, search_terms, search_results, transactions, monthly, ellegant_rows, loan_rows, risk_profile, customer_name, cashflow_rows=None, cycle_rows=None):
        cashflow_rows = cashflow_rows or []
        cycle_rows = cycle_rows or []
        lines = []
        total_loan_received = sum(row["loan_received"] for row in cashflow_rows)
        total_installments_paid = sum(row["installments_paid"] for row in cashflow_rows)
        net_position = total_loan_received - total_installments_paid

        lines.append("MUTEMI M-PESA STATEMENT ANALYSIS SUMMARY")
        lines.append("=" * 70)
        lines.append(f"PDF file: {self.pdf_path}")
        lines.append(f"Customer Name: {customer_name}")
        lines.append("")
        lines.append("EXECUTIVE SUMMARY")
        lines.append("-" * 70)
        lines.append(f"Transactions parsed: {len(transactions)}")
        lines.append(f"Normal Paid In Total, excluding matched loan companies: {money(risk_profile['total_paid_in'])}")
        lines.append(f"Loan received from matched loan companies: {money(total_loan_received)}")
        lines.append(f"Installments paid to matched loan companies: {money(total_installments_paid)}")
        lines.append(f"Net loan position, received less repaid: {money(net_position)}")
        lines.append(f"Risk rating: {risk_profile['rating']} | Score: {risk_profile['score']} | Loan repayment percentage: {risk_profile['percentage']:.2f}%")
        lines.append("")

        matched_rows = []
        for term in search_terms:
            result = search_results.get(term, {"count": 0, "pages": set()})
            if result["count"] > 0:
                pages = ", ".join(str(p) for p in sorted(result["pages"]))
                matched_rows.append([term, str(result["count"]), pages])
        lines.append("MATCHED LOAN COMPANIES")
        lines.append("-" * 70)
        if matched_rows:
            lines.append(make_text_table(["Loan Company", "Total Matches", "Page Number(s)"], matched_rows))
        else:
            lines.append("No loan companies matched in the PDF.")
        lines.append("")

        lines.append("LOAN COMPANY CASHFLOW SUMMARY")
        lines.append("-" * 70)
        if cashflow_rows:
            lines.append(make_text_table(["Loan Company", "Loan Received", "Installments Paid", "Net Position", "Matched Txns"], [[row["company"], money(row["loan_received"]), money(row["installments_paid"]), money(row["net_position"]), str(row["matched_transactions"])] for row in cashflow_rows]))
        else:
            lines.append("No matched loan-company cashflows were found.")
        lines.append("")

        monthly_rows = []
        for month in sorted(monthly.keys()):
            values = monthly[month]
            monthly_rows.append([month, money(values["paid_in_excluding_matches"]), str(values["counted_paid_in_rows"]), money(values["paid_in_excluded_matched"]), str(values["excluded_paid_in_rows"])])
        lines.append("MONTHLY PAID IN SUMMARY")
        lines.append("-" * 70)
        lines.append("Main total excludes Paid In rows whose details matched loan companies.")
        lines.append("")
        if monthly_rows:
            lines.append(make_text_table(["Month", "Paid In Excluding Matches", "Counted Rows", "Paid In Excluded", "Excluded Rows"], monthly_rows))
        else:
            lines.append("No Paid In transactions were parsed. The PDF may be scanned or the table layout may be different.")
        lines.append("")

        lines.append("ELLEGANT CREDIT LTD PAYMENT SUMMARY")
        lines.append("-" * 70)
        if ellegant_rows:
            by_month = defaultdict(list)
            for row in ellegant_rows:
                by_month[row["month"]].append(row)
            for month in sorted(by_month.keys()):
                month_rows = by_month[month]
                total = sum(row["amount"] for row in month_rows)
                lines.append("")
                lines.append(f"{month} - Total Paid: {money(total)}")
                lines.append(make_text_table(["Date Paid", "Transaction ID / Receipt No.", "Amount Paid", "Page"], [[row["date"], row["receipt"], money(row["amount"]), str(row["page"])] for row in month_rows]))
        else:
            lines.append("No ELLEGANT CREDIT LTD transactions found.")
        lines.append("")

        lines.append("CUSTOMER RISK PROFILE")
        lines.append("-" * 70)
        lines.append("Formula: Total installments paid to matched loan companies / normal Paid In total * 100")
        lines.append(make_text_table(["Total Paid In", "Installments Paid", "Loan % of Paid In", "Risk Rating", "Score"], [[money(risk_profile["total_paid_in"]), money(risk_profile["total_loan"]), f'{risk_profile["percentage"]:.2f}%', risk_profile["rating"], str(risk_profile["score"])]],))
        lines.append("")

        if cycle_rows:
            lines.append("INFERRED LOAN CYCLES")
            lines.append("-" * 70)
            lines.append("Each matched Paid In starts a new loan cycle; matched Paid Out rows before the next Paid In are treated as installments.")
            lines.append(make_text_table(["Loan Company", "Cycle", "Loan Date", "Loan Received", "Installments Paid", "Installments", "Net Position"], [[row["company"], str(row["cycle"]), row["loan_date"], money(row["loan_received"]), money(row["installments_paid"]), str(row["installment_count"]), money(row["net_position"])] for row in cycle_rows]))
            lines.append("")

        if loan_rows:
            lines.append("INSTALLMENT TRANSACTIONS USED IN RISK CALCULATION")
            lines.append("-" * 70)
            lines.append(make_text_table(["Month", "Date Paid", "Loan Company", "Receipt No.", "Amount Paid", "Page"], [[row["month"], row["date"], row["company"], row["receipt"], money(row["amount"]), str(row["page"])] for row in loan_rows],))
        else:
            lines.append("No matched loan repayment transactions were found.")

        lines.append("")
        lines.append("Loan companies used:")
        for term in search_terms:
            lines.append(f"- {term}")
        return "\n".join(lines)

    def get_risk_colors(self, rating):
        """Return card colours based on the risk rating."""
        rating = (rating or "").lower()

        if rating == "very good":
            return "#DCFCE7", "#166534"  # green
        if rating == "good":
            return "#ECFDF5", "#047857"  # soft green
        if rating == "fair":
            return "#FEF9C3", "#854D0E"  # yellow
        if rating == "risky":
            return "#FFEDD5", "#9A3412"  # orange
        if rating == "very risky":
            return "#FEE2E2", "#991B1B"  # red

        return "#E2E8F0", "#334155"

    def set_risk_rating_display(self, rating, score=None):
        """Update the risk card and risk profile text colours dynamically."""
        bg, fg = self.get_risk_colors(rating)

        display_text = "N/A" if not rating or rating == "N/A" else rating
        if score not in (None, "N/A") and display_text != "N/A":
            display_text = f"{rating} ({score})"

        risk_card = self.card_values.get("Risk Rating")
        if risk_card is not None:
            risk_card.config(text=display_text, bg=bg, fg=fg)
            parent = risk_card.master
            parent.config(bg=bg)
            for child in parent.winfo_children():
                child.config(bg=bg, fg=fg)

        if hasattr(self, "risk_rating_value_label"):
            self.risk_rating_value_label.config(fg=fg)
        if hasattr(self, "risk_score_value_label"):
            self.risk_score_value_label.config(fg=fg)

    def clear_results(self):
        for tree in [self.matched_tree, self.monthly_tree, self.ellegant_tree, self.loan_repayment_tree, self.company_summary_tree, self.selected_company_tree, self.loan_cycle_tree]:
            for item in tree.get_children():
                tree.delete(item)

        self.report_text.config(state="normal")
        self.report_text.delete("1.0", tk.END)
        self.report_text.config(state="disabled")
        self.last_report = ""

        self.customer_name = "N/A"
        self.card_values["Customer Name"].config(text="N/A")
        self.card_values["Transactions Parsed"].config(text="0")
        self.card_values["Monthly Paid In Total"].config(text="0.00")
        self.card_values["Loan Received"].config(text="0.00")
        self.card_values["Installment Paid"].config(text="0.00")
        self.set_risk_rating_display("N/A", "N/A")

        self.risk_total_paid_in_var.set("0.00")
        self.risk_total_loan_var.set("0.00")
        self.risk_percentage_var.set("0.00%")
        self.risk_rating_var.set("N/A")
        self.risk_score_var.set("N/A")
        self.selected_received_var.set("0.00")
        self.selected_paid_var.set("0.00")
        self.selected_net_var.set("0.00")
        self.selected_count_var.set("0")
        self.selected_company_var.set("")
        self.selected_company_combo["values"] = []
        self.company_transactions = {}
        self.loan_cycles = []
        self.draw_empty_charts()

        self.status_label.config(text="Results cleared.")

    def analyze_pdf(self):
        if not self.pdf_path:
            messagebox.showinfo("No PDF selected", "Please select an M-PESA statement PDF first.")
            return

        if not os.path.exists(self.pdf_path):
            messagebox.showerror("File missing", "The selected PDF file does not exist.")
            return

        search_terms = self.get_search_terms()

        if not search_terms:
            messagebox.showinfo("No loan companies", "Please add at least one loan company.")
            return

        self.status_label.config(text="Analyzing statement...")
        self.root.update_idletasks()

        try:
            doc = self.open_pdf(self.pdf_path)

            if doc is None:
                self.status_label.config(text="Analysis cancelled.")
                return

            customer_name = extract_customer_name_from_pdf(doc)
            self.customer_name = customer_name

            search_results = self.find_search_terms_by_page(doc, search_terms)
            transactions = self.parse_all_transactions(doc, search_terms)
            monthly = self.monthly_paid_in_summary(transactions)
            ellegant_rows = self.ellegant_credit_transactions(transactions)
            loan_rows = self.loan_repayment_transactions(transactions)
            risk_profile = self.risk_profile_summary(monthly, loan_rows)
            cashflow_rows, cashflow_monthly = self.loan_company_cashflow_summary(transactions)
            cycle_rows = self.loan_cycle_summary()

            report = self.build_report(search_terms, search_results, transactions, monthly, ellegant_rows, loan_rows, risk_profile, customer_name, cashflow_rows, cycle_rows)
            self.last_report = report

            doc.close()

            self.update_results_tables(search_terms, search_results, monthly, ellegant_rows, transactions, loan_rows, risk_profile, customer_name, cashflow_rows, cashflow_monthly, cycle_rows)
            self.render_professional_summary(search_terms, search_results, monthly, ellegant_rows, risk_profile, customer_name, cashflow_rows, cycle_rows)

            self.status_label.config(text=f"Done. Transactions parsed: {len(transactions)}")

        except Exception as e:
            self.status_label.config(text="Error.")
            messagebox.showerror("Analysis error", str(e))

    def update_results_tables(self, search_terms, search_results, monthly, ellegant_rows, transactions, loan_rows, risk_profile, customer_name, cashflow_rows=None, cashflow_monthly=None, cycle_rows=None):
        for item in self.matched_tree.get_children():
            self.matched_tree.delete(item)

        for term in search_terms:
            result = search_results.get(term, {"count": 0, "pages": set()})
            if result["count"] > 0:
                pages = ", ".join(str(p) for p in sorted(result["pages"]))
                self.matched_tree.insert("", "end", values=(term, result["count"], pages))

        for item in self.monthly_tree.get_children():
            self.monthly_tree.delete(item)

        total_paid_in_excluding = 0.0
        for month in sorted(monthly.keys()):
            values = monthly[month]
            total_paid_in_excluding += values["paid_in_excluding_matches"]

            self.monthly_tree.insert(
                "",
                "end",
                values=(
                    month,
                    money(values["paid_in_excluding_matches"]),
                    values["counted_paid_in_rows"],
                    money(values["paid_in_excluded_matched"]),
                    values["excluded_paid_in_rows"],
                ),
            )

        cashflow_rows = cashflow_rows or []
        cashflow_monthly = cashflow_monthly or {}
        cycle_rows = cycle_rows or []

        for item in self.company_summary_tree.get_children():
            self.company_summary_tree.delete(item)
        for row in cashflow_rows:
            self.company_summary_tree.insert("", "end", values=(row["company"], money(row["loan_received"]), money(row["installments_paid"]), money(row["net_position"]), row["matched_transactions"]))

        self.draw_dashboard_charts(cashflow_monthly, cashflow_rows)
        self.update_selected_company_options(cashflow_rows, cycle_rows)

        for item in self.ellegant_tree.get_children():
            self.ellegant_tree.delete(item)

        ellegant_total = 0.0
        current_month = None

        for row in ellegant_rows:
            if row["month"] != current_month:
                current_month = row["month"]
                month_total = sum(r["amount"] for r in ellegant_rows if r["month"] == current_month)
                self.ellegant_tree.insert(
                    "",
                    "end",
                    values=(current_month, "", "MONTH TOTAL", money(month_total), ""),
                    tags=("month_total",),
                )

            ellegant_total += row["amount"]
            self.ellegant_tree.insert(
                "",
                "end",
                values=(
                    row["month"],
                    row["date"],
                    row["receipt"],
                    money(row["amount"]),
                    row["page"],
                ),
            )

        self.ellegant_tree.tag_configure("month_total", background="#DCFCE7", foreground="#14532D")

        for item in self.loan_repayment_tree.get_children():
            self.loan_repayment_tree.delete(item)

        current_month = None
        for row in loan_rows:
            if row["month"] != current_month:
                current_month = row["month"]
                month_total = sum(r["amount"] for r in loan_rows if r["month"] == current_month)
                self.loan_repayment_tree.insert(
                    "",
                    "end",
                    values=(current_month, "", "MONTH TOTAL", "", money(month_total), ""),
                    tags=("loan_month_total",)
                )

            self.loan_repayment_tree.insert(
                "",
                "end",
                values=(
                    row["month"],
                    row["date"],
                    row["company"],
                    row["receipt"],
                    money(row["amount"]),
                    row["page"],
                )
            )

        self.loan_repayment_tree.tag_configure("loan_month_total", background="#DBEAFE", foreground="#1E3A8A")

        self.risk_total_paid_in_var.set(money(risk_profile["total_paid_in"]))
        self.risk_total_loan_var.set(money(risk_profile["total_loan"]))
        self.risk_percentage_var.set(f'{risk_profile["percentage"]:.2f}%')
        self.risk_rating_var.set(risk_profile["rating"])
        self.risk_score_var.set(str(risk_profile["score"]))

        self.card_values["Customer Name"].config(text=customer_name)
        self.card_values["Transactions Parsed"].config(text=str(len(transactions)))
        self.card_values["Monthly Paid In Total"].config(text=money(total_paid_in_excluding))
        total_received = sum(row["loan_received"] for row in (cashflow_rows or []))
        self.card_values["Loan Received"].config(text=money(total_received))
        self.card_values["Installment Paid"].config(text=money(risk_profile["total_loan"]))
        self.set_risk_rating_display(risk_profile["rating"], risk_profile["score"])

    def draw_empty_charts(self):
        if hasattr(self, "monthly_chart_canvas"):
            self.monthly_chart_canvas.delete("all")
            self.monthly_chart_canvas.create_text(20, 20, anchor="nw", text="Monthly cashflow chart will appear after analysis.", fill="#64748B", font=("Segoe UI", 10))
        if hasattr(self, "company_chart_canvas"):
            self.company_chart_canvas.delete("all")
            self.company_chart_canvas.create_text(20, 20, anchor="nw", text="Loan company chart will appear after analysis.", fill="#64748B", font=("Segoe UI", 10))

    def draw_dashboard_charts(self, monthly_data, company_rows):
        self.draw_monthly_bar_chart(monthly_data)
        self.draw_company_pie_chart(company_rows)

    def draw_monthly_bar_chart(self, monthly_data):
        c = self.monthly_chart_canvas
        c.delete("all")
        width = max(c.winfo_width(), 560)
        height = max(c.winfo_height(), 320)

        c.create_text(18, 16, anchor="nw", text="Monthly Cashflow Comparison", fill="#0F172A", font=("Segoe UI", 12, "bold"))
        c.create_text(18, 36, anchor="nw", text="Compares normal Paid In cashflow, estimated loan receipts, and estimated installments repaid.", fill="#64748B", font=("Segoe UI", 9))

        months = sorted(monthly_data.keys())
        if not months:
            c.create_text(18, 72, anchor="nw", text="No data available.", fill="#64748B", font=("Segoe UI", 10))
            return

        colors = [
            ("paid_in_excluding_matches", "#16A34A", "Normal Paid In"),
            ("loan_received", "#0284C7", "Loan Received"),
            ("installments_paid", "#EA580C", "Installments Paid"),
        ]

        # legend on its own row to avoid collisions
        legend_y = 62
        legend_x = 18
        for _, color, label in colors:
            c.create_rectangle(legend_x, legend_y, legend_x + 12, legend_y + 12, fill=color, outline="")
            c.create_text(legend_x + 18, legend_y + 6, anchor="w", text=label, fill="#334155", font=("Segoe UI", 8, "bold"))
            legend_x += 120

        left, top, bottom = 58, 92, height - 48
        chart_w = width - 95
        chart_h = bottom - top
        max_val = max(max(v.get("paid_in_excluding_matches", 0), v.get("loan_received", 0), v.get("installments_paid", 0)) for v in monthly_data.values()) or 1

        # axes and y ticks
        c.create_line(left, bottom, left + chart_w, bottom, fill="#94A3B8", width=1)
        c.create_line(left, top, left, bottom, fill="#94A3B8", width=1)
        for step in range(5):
            y = bottom - step * (chart_h / 4)
            val = max_val * step / 4
            c.create_line(left, y, left + chart_w, y, fill="#E2E8F0")
            c.create_text(left - 6, y, anchor="e", text=money(val), fill="#64748B", font=("Segoe UI", 7))

        group_w = chart_w / max(len(months), 1)
        bar_w = max(10, min(22, group_w / 5))

        for i, month in enumerate(months):
            x_center = left + i * group_w + group_w / 2
            for j, (key, color, _label) in enumerate(colors):
                val = monthly_data[month].get(key, 0)
                bar_h = (val / max_val) * (chart_h - 10)
                x0 = x_center + (j - 1) * (bar_w + 5) - bar_w / 2
                x1 = x0 + bar_w
                y0 = bottom - bar_h
                c.create_rectangle(x0, y0, x1, bottom, fill=color, outline="")
            c.create_text(x_center, bottom + 16, text=format_month_label(month).replace(' ', '\n', 1), fill="#334155", font=("Segoe UI", 8), justify="center")

    def draw_company_pie_chart(self, company_rows):
        c = self.company_chart_canvas
        c.delete("all")
        width = max(c.winfo_width(), 560)
        height = max(c.winfo_height(), 320)
        c.create_text(18, 16, anchor="nw", text="Installment Distribution by Loan Company", fill="#0F172A", font=("Segoe UI", 12, "bold"))
        c.create_text(18, 36, anchor="nw", text="Shows how total installment repayments are spread across matched loan companies.", fill="#64748B", font=("Segoe UI", 9))

        rows = [r for r in company_rows if r.get("installments_paid", 0) > 0]
        if not rows:
            c.create_text(18, 72, anchor="nw", text="No installment data available.", fill="#64748B", font=("Segoe UI", 10))
            return

        rows = sorted(rows, key=lambda r: r.get("installments_paid", 0), reverse=True)
        if len(rows) > 5:
            other_total = sum(r["installments_paid"] for r in rows[5:])
            rows = rows[:5] + [{"company": "Other Companies", "installments_paid": other_total}]

        total = sum(r["installments_paid"] for r in rows) or 1
        colors = ["#16A34A", "#0284C7", "#EA580C", "#7C3AED", "#DC2626", "#0891B2"]

        x0, y0, size = 30, 78, 180
        start = 0
        for i, row in enumerate(rows):
            extent = row["installments_paid"] / total * 360
            c.create_arc(x0, y0, x0 + size, y0 + size, start=start, extent=extent, fill=colors[i % len(colors)], outline="white", width=2)
            start += extent

        legend_x = x0 + size + 32
        legend_y = y0 + 6
        for i, row in enumerate(rows):
            color = colors[i % len(colors)]
            pct = row["installments_paid"] / total * 100
            c.create_rectangle(legend_x, legend_y + i * 36, legend_x + 14, legend_y + i * 36 + 14, fill=color, outline="")
            name = row['company'] if len(row['company']) <= 24 else row['company'][:21] + '...'
            c.create_text(legend_x + 20, legend_y + i * 36 + 2, anchor="nw", text=name, fill="#0F172A", font=("Segoe UI", 8, "bold"))
            c.create_text(legend_x + 20, legend_y + i * 36 + 17, anchor="nw", text=f"{money(row['installments_paid'])}  ({pct:.1f}%)", fill="#475569", font=("Segoe UI", 8))

    def update_selected_company_options(self, cashflow_rows, cycle_rows):
        companies = [row["company"] for row in cashflow_rows]
        self.selected_company_combo["values"] = companies
        if companies:
            current = self.selected_company_var.get()
            preferred = "ELLEGANT CREDIT LTD" if "ELLEGANT CREDIT LTD" in companies else companies[0]
            self.selected_company_var.set(current if current in companies else preferred)
            self.update_selected_company_view()
        else:
            self.selected_company_var.set("")
            self.update_selected_company_view()

    def update_selected_company_view(self):
        company = self.selected_company_var.get()
        for tree in [self.selected_company_tree, self.loan_cycle_tree]:
            for item in tree.get_children():
                tree.delete(item)
        if not company:
            self.selected_received_var.set("0.00")
            self.selected_paid_var.set("0.00")
            self.selected_net_var.set("0.00")
            self.selected_count_var.set("0")
            return
        events = getattr(self, "company_transactions", {}).get(company, [])
        received_total = sum(e.get("received", 0.0) for e in events)
        paid_total = sum(e.get("paid", 0.0) for e in events)
        self.selected_received_var.set(money(received_total))
        self.selected_paid_var.set(money(paid_total))
        self.selected_net_var.set(money(received_total - paid_total))
        self.selected_count_var.set(str(len(events)))
        for event in events:
            self.selected_company_tree.insert("", "end", values=(event.get("date", ""), event.get("type", ""), event.get("receipt", ""), money(event.get("received", 0.0)) if event.get("received", 0.0) else "", money(event.get("paid", 0.0)) if event.get("paid", 0.0) else "", event.get("page", "")))
        for row in getattr(self, "loan_cycles", []):
            if row["company"] != company:
                continue
            self.loan_cycle_tree.insert("", "end", values=(row["company"], row["cycle"], row["loan_date"], money(row["loan_received"]), money(row["installments_paid"]), row["installment_count"], money(row["net_position"])))

    def render_professional_summary(self, search_terms, search_results, monthly, ellegant_rows, risk_profile, customer_name, cashflow_rows=None, cycle_rows=None):
        cashflow_rows = cashflow_rows or []
        cycle_rows = cycle_rows or []
        total_paid_in = risk_profile.get("total_paid_in", 0.0)
        total_installments = risk_profile.get("total_loan", 0.0)
        total_received = sum(row.get("loan_received", 0.0) for row in cashflow_rows)
        net_position = total_received - total_installments

        self.report_text.config(state="normal")
        self.report_text.delete("1.0", tk.END)

        self.report_text.insert(tk.END, "MUTEMI M-PESA Statement Analysis Summary\n", "title")
        self.report_text.insert(tk.END, f"Customer: {customer_name}    •    Statement file: {Path(self.pdf_path).name if self.pdf_path else 'N/A'}\n", "subtitle")
        self.report_text.insert(tk.END, f"Generated from the current statement analysis. The summary below highlights overall customer cashflow, matched microfinance activity, repayment behavior, and risk interpretation.\n\n", "muted")

        self.report_text.insert(tk.END, "1. Executive Overview\n", "section")
        overview = [
            f"Transactions parsed: {self.card_values['Transactions Parsed'].cget('text')}",
            f"Normal Paid In total (excluding matched loan companies): {money(total_paid_in)}",
            f"Estimated loan receipts from matched loan companies: {money(total_received)}",
            f"Estimated installments repaid to matched loan companies: {money(total_installments)}",
            f"Net position (loan received less installments paid): {money(net_position)}",
            f"Customer risk rating: {risk_profile.get('rating', 'N/A')} (Score {risk_profile.get('score', 'N/A')}) with a loan repayment percentage of {risk_profile.get('percentage', 0.0):.2f}%",
        ]
        for line in overview:
            self.report_text.insert(tk.END, f"• {line}\n", "bullet")
        self.report_text.insert(tk.END, "\n")

        self.report_text.insert(tk.END, "2. Matched Loan Companies\n", "section")
        matched = []
        for term in search_terms:
            result = search_results.get(term, {"count": 0, "pages": set()})
            if result["count"] > 0:
                pages = ", ".join(str(p) for p in sorted(result["pages"]))
                matched.append([term, str(result["count"]), pages])
        if matched:
            self.report_text.insert(tk.END, make_text_table(["Loan Company", "Matches", "Page Number(s)"], matched) + "\n\n", "table")
        else:
            self.report_text.insert(tk.END, "No matched loan companies were identified in this statement.\n\n", "body")

        self.report_text.insert(tk.END, "3. Loan Company Cashflow Summary\n", "section")
        if cashflow_rows:
            self.report_text.insert(tk.END, "This section estimates how much the customer appears to have received as loans and how much has been repaid as installments for each matched company.\n", "body")
            cash_table = []
            for row in cashflow_rows:
                cash_table.append([row["company"], money(row["loan_received"]), money(row["installments_paid"]), money(row["net_position"]), str(row["matched_transactions"])])
            self.report_text.insert(tk.END, make_text_table(["Loan Company", "Loan Received", "Installments Paid", "Net Position", "Matched Txns"], cash_table) + "\n\n", "table")
        else:
            self.report_text.insert(tk.END, "No matched loan-company cashflow rows were available.\n\n", "body")

        self.report_text.insert(tk.END, "4. Monthly Paid In Trend\n", "section")
        self.report_text.insert(tk.END, "The monthly summary below focuses on normal Paid In cashflow after excluding incoming rows linked to matched loan companies.\n", "body")
        month_rows = []
        for month in sorted(monthly.keys()):
            vals = monthly[month]
            month_rows.append([format_month_label(month), money(vals["paid_in_excluding_matches"]), str(vals["counted_paid_in_rows"]), money(vals["paid_in_excluded_matched"]), str(vals["excluded_paid_in_rows"])])
        if month_rows:
            self.report_text.insert(tk.END, make_text_table(["Month", "Paid In Excluding Matches", "Counted Rows", "Paid In Excluded", "Excluded Rows"], month_rows) + "\n\n", "table")
        else:
            self.report_text.insert(tk.END, "No monthly paid-in trend could be generated.\n\n", "body")

        self.report_text.insert(tk.END, "5. ELLEGANT CREDIT LTD Summary\n", "section")
        if ellegant_rows:
            monthly_totals = {}
            for row in ellegant_rows:
                monthly_totals.setdefault(row['month'], 0.0)
                monthly_totals[row['month']] += row['amount']
            for month in sorted(monthly_totals.keys()):
                self.report_text.insert(tk.END, f"• {format_month_label(month)} total paid to ELLEGANT CREDIT LTD: {money(monthly_totals[month])}\n", "bullet")
            self.report_text.insert(tk.END, "\n")
        else:
            self.report_text.insert(tk.END, "No ELLEGANT CREDIT LTD transactions were found in this statement.\n\n", "body")

        self.report_text.insert(tk.END, "6. Risk Interpretation\n", "section")
        rating = risk_profile.get('rating', 'N/A')
        tag = 'good' if rating in ('Very Good', 'Good') else 'warning' if rating in ('Fair', 'Risky') else 'bad'
        self.report_text.insert(tk.END, f"Current rating: {rating} (Score {risk_profile.get('score', 'N/A')})\n", tag)
        self.report_text.insert(tk.END, f"Repayment percentage: {risk_profile.get('percentage', 0.0):.2f}%\n", 'body')
        self.report_text.insert(tk.END, "Interpretation: the risk score is based on the ratio of matched-loan installment payments to the main Paid In total. A higher percentage indicates greater loan pressure relative to regular incoming cashflow.\n\n", 'body')

        self.report_text.insert(tk.END, "7. Inferred Loan Cycles\n", "section")
        if cycle_rows:
            cycle_table = []
            for row in cycle_rows:
                cycle_table.append([row['company'], str(row['cycle']), row['loan_date'], money(row['loan_received']), money(row['installments_paid']), str(row['installment_count']), money(row['net_position'])])
            self.report_text.insert(tk.END, make_text_table(["Company", "Cycle", "Loan Date", "Loan Received", "Installments Paid", "Installment Count", "Net Position"], cycle_table) + "\n", "table")
        else:
            self.report_text.insert(tk.END, "No loan cycles could be inferred from the matched transactions.\n", "body")

        self.report_text.config(state="disabled")

    def save_summary(self):
        if not self.last_report:
            messagebox.showinfo("No summary", "There is no summary to save yet. Analyze a PDF first.")
            return

        default_name = "mutemi_mpesa_analysis_summary.txt"
        if self.pdf_path:
            default_name = Path(self.pdf_path).stem + "_mutemi_mpesa_analysis_summary.txt"

        path = filedialog.asksaveasfilename(
            title="Save Summary",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )

        if not path:
            return

        Path(path).write_text(self.last_report, encoding="utf-8")
        messagebox.showinfo("Saved", f"Summary saved to:\n{path}")


def main():
    root = tk.Tk()
    MpesaStatementApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
