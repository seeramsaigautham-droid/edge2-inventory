# ⬡ Edge2 Inventory System

A smart, QR-code-based inventory management system built with Python Flask. Designed for physical storage environments where items are organised into columns and boxes, tracked in real time via QR scanning.

---

## Features

### Core Inventory
- Hierarchical storage structure — Columns → Boxes → Items
- Real-time stock tracking with quantity and minimum stock thresholds
- Low stock alerts with sidebar badge counter
- Out-of-stock detection and dashboard banners
- Bulk restock and individual item restock

### QR Scanner
- Camera-based QR code scanning (mobile-optimised)
- 5-step workflow: Scan Column → Select Box → Select Item → Choose Action → Confirm
- Manual column ID entry as fallback
- Take, Return, and Restock actions via scanner

### User Management
- Role-based access control: Admin, Storekeeper, Employee
- Admin: full access including user management, analytics, delete operations
- Storekeeper: inventory management, restocking, reports
- Employee: scanner, personal borrowing history, profile

### Reporting & Analytics
- Transaction log with search and filter
- Advanced reports — filter by date, user, column, transaction type
- CSV export for transactions and inventory
- Analytics dashboard — stock health, daily activity chart, top items, usage by user

### Other
- CSV import for bulk inventory upload
- Profile page with password change and borrowing overview
- Active borrowings tracker per user
- Mobile responsive UI with sidebar toggle
- Confirm modals for all destructive actions
- Toast notifications for all actions

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Database | SQLite (via sqlite3) |
| Frontend | Jinja2 templates, vanilla JS |
| QR Generation | qrcode, Pillow |
| QR Scanning | jsQR.js |
| Production Server | Gunicorn |
| Hosting | Railway |

---

## Getting Started (Local)

### Prerequisites
- Python 3.10+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/edge2-inventory.git
cd edge2-inventory

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

The app will be available at `http://localhost:5000`

---

## Default Login Credentials

| Role | Email | Password |
|---|---|---|
| Admin | admin@edge2.com | admin123 |
| Storekeeper | store@edge2.com | store123 |
| Employee | alice@edge2.com | alice123 |
| Employee | bob@edge2.com | bob123 |

> **Note:** Change these credentials before any production use.

---

## Project Structure

```
inventory_system/
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── Procfile                # Gunicorn start command for Railway
├── static/
│   ├── js/
│   │   └── jsQR.min.js     # QR code scanning library
│   └── qrcodes/            # Generated QR code images (runtime)
└── templates/
    ├── base.html            # Base layout with sidebar
    ├── dashboard.html       # Role-aware dashboard
    ├── scanner.html         # QR scanner workflow
    ├── columns.html         # Column management
    ├── boxes.html           # Box management
    ├── items.html           # Item management with bulk restock
    ├── inventory.html       # Full inventory overview
    ├── low_stock.html       # Low stock page
    ├── transactions.html    # Transaction log
    ├── reports.html         # Advanced reports with filters
    ├── analytics.html       # Admin analytics dashboard
    ├── profile.html         # User profile and password
    ├── my_items.html        # Active borrowings
    ├── history.html         # Personal transaction history
    └── users.html           # User management (admin)
```

---

## Roles & Permissions

| Feature | Admin | Storekeeper | Employee |
|---|:---:|:---:|:---:|
| Dashboard (full stats) | ✓ | ✓ | — |
| Scanner | ✓ | ✓ | ✓ |
| My Items / History | ✓ | ✓ | ✓ |
| Inventory / Items / Boxes / Columns | ✓ | ✓ | — |
| Restock | ✓ | ✓ | — |
| Transactions | ✓ | ✓ | — |
| Reports & Export | ✓ | ✓ | — |
| Analytics | ✓ | — | — |
| User Management | ✓ | — | — |
| Delete Operations | ✓ | — | — |

---

