# Smart Inventory Management System — Complete Project Context Transfer

## ⚠️ How to Use This Document

This document is a **complete, self-contained context transfer** for continuing development of this project. Read every section before generating any code. Do NOT ask the user to re-explain requirements — everything is here. Only ask for files when explicitly stated in each phase's "Files to Request" section.

---

## Project Overview

A complete rebuild of an existing inventory management system. The old system used a flat `Bin → Item` model. The new system uses a 3-level hierarchy: `Column → Box → Item`. The codebase is built with Flask + SQLite + Vanilla JS and must stay that way — no framework migrations.

The project is divided into **10 phases**, built incrementally. **Phase 1 is complete.** Continue from Phase 2.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python + Flask |
| Database | SQLite (file: `instance/inventory.db`) |
| Frontend | HTML + CSS + Vanilla JavaScript |
| QR Scanning | jsQR library (local file, no CDN) |
| Authentication | Flask session-based |

**Hard constraints — never change these:**
- No React, Vue, Django, FastAPI, PostgreSQL
- No external CSS frameworks (Bootstrap, Tailwind, etc.)
- All CSS lives in `base.html` as `<style>` block using CSS variables
- jsQR must be loaded from `static/js/jsQR.min.js` (local), never from CDN

---

## User Roles

| Role | Internal Value | Can Do |
|---|---|---|
| Admin | `admin` | Everything: users, columns, boxes, items, QR generate/download, take, return, restock, reports |
| Storekeeper | `storekeeper` | Columns, boxes, items, QR generate/download, take, return, restock |
| Normal User | `employee` | Login, scan QR, take items, return items only |

**Role rules:**
- `session['role']` stores the role value
- `'admin'` and `'storekeeper'` are the privileged roles — most management routes check for both
- `'employee'` is the normal user — cannot generate/download QR, cannot restock, cannot access admin/management pages
- The role value `employee` is kept internally even though the spec calls them "Normal Users" — display label can say "Employee" or "User" in the UI

---

## Inventory Hierarchy

```
Column
 └── Box
      └── Inventory Item
```

**Physical example:**
```
Column A
├── Box 1
│    ├── Screwdriver (qty: 20, min_stock: 5)
│    └── Hammer (qty: 8, min_stock: 3)
└── Box 2
     ├── Wire (qty: 15, min_stock: 5)
     └── Tape (qty: 10, min_stock: 2)

Column B
└── Box 3
     ├── Sensor (qty: 30, min_stock: 10)
     └── Relay (qty: 4, min_stock: 5)
```

**Critical rules:**
- Every item belongs to a box. Every box belongs to a column.
- `min_stock` is stored **per inventory item**, NOT per box.
- QR codes are generated for **columns only** — not boxes, not items.
- Transactions reference `item_id` — not bin_id, not box_id.

---

## Database Schema (Complete)

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'employee',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    column_name TEXT NOT NULL UNIQUE,
    qr_code_path TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    column_id INTEGER NOT NULL,
    box_name TEXT NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(column_id) REFERENCES columns(id) ON DELETE CASCADE
);

CREATE TABLE inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    min_stock INTEGER NOT NULL DEFAULT 5,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(box_id) REFERENCES boxes(id) ON DELETE CASCADE
);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    type TEXT NOT NULL,   -- 'take', 'return', 'restock'
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(item_id) REFERENCES inventory_items(id)
);

CREATE TABLE active_borrowings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    quantity_borrowed INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, item_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(item_id) REFERENCES inventory_items(id)
);
```

**Key difference from old system:** `active_borrowings` and `transactions` both reference `item_id` (inventory_items), NOT `bin_id`.

---

## QR Code System

- QR codes encode the string `COLUMN:{id}` (e.g. `COLUMN:3`)
- QR images are saved to `static/qrcodes/column_{id}.png`
- The `generate_qr_for_column(column_id)` function in `app.py` handles generation
- Generation is **manual** — triggered by a button on each column row
- QR generation/download is allowed for `admin` and `storekeeper` only
- Normal users (`employee`) can **scan** QR codes but cannot generate or download them

---

## QR Scanner Workflow (Critical)

This is the exact flow that must be implemented in the scanner:

```
Step 1: User scans a Column QR code  →  reads "COLUMN:3"
Step 2: System loads all boxes in that column
        User selects a box
Step 3: System loads all items in that box
        User selects an item
Step 4: System shows transaction buttons:
        - employee:             [Take]  [Return]
        - admin/storekeeper:    [Take]  [Return]  [Restock]
Step 5: User enters quantity and confirms
Step 6: Transaction is recorded
```

---

## Design System (Must Preserve)

All CSS lives in `base.html` inside a `<style>` block. Use these CSS variables everywhere — never hardcode colors.

```css
:root {
  --bg: #0f1117;
  --surface: #181c25;
  --surface2: #1e2330;
  --border: #2a303f;
  --border2: #323848;
  --text: #e8eaf0;
  --text-muted: #7b8199;
  --text-dim: #4a5168;
  --accent: #4f8ef7;
  --accent-hover: #6fa3ff;
  --accent-dim: #1a2d52;
  --success: #34c47c;
  --success-dim: #0f3022;
  --warning: #f5a623;
  --warning-dim: #3a2800;
  --danger: #f05a5a;
  --danger-dim: #3a1010;
  --radius: 10px;
  --radius-sm: 6px;
  --sidebar-w: 220px;
  --font: 'DM Sans', sans-serif;
  --mono: 'DM Mono', monospace;
}
```

**Component classes available in base.html:**
- `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-danger`, `.btn-success`, `.btn-warning`, `.btn-sm`
- `.card`, `.card-header`, `.card-title`, `.card-body`
- `.table-wrap`, `table`, `th`, `td`
- `.badge`, `.badge-success`, `.badge-warning`, `.badge-danger`, `.badge-info`, `.badge-muted`
- `.stat-grid`, `.stat-card`, `.stat-label`, `.stat-value`, `.stat-sub`
- `.alert`, `.alert-error`, `.alert-success`, `.alert-warning`
- `.form-group`, `label`, `input`, `select`, `textarea`
- `.search-bar`
- `.empty-state`
- `.breadcrumb`
- `showToast(msg, type)` — global JS function for toast notifications

**Fonts:** DM Sans (body) + DM Mono (code/mono) loaded from Google Fonts.

---

## File Structure

### Current State After Phase 1

```
inventory_system/
├── app.py                          ✅ GENERATED (Phase 1)
├── requirements.txt                ✅ GENERATED (Phase 1)
├── instance/
│   └── inventory.db                (auto-created on first run)
├── static/
│   ├── js/
│   │   └── jsQR.min.js             ✅ COPIED from old project
│   └── qrcodes/                    (QR images saved here at runtime)
└── templates/
    ├── base.html                   ✅ GENERATED (Phase 1)
    ├── login.html                  ✅ COPIED from old project (unchanged)
    ├── dashboard.html              ✅ GENERATED (Phase 1)
    ├── users.html                  ✅ COPIED from old project (unchanged)
    ├── add_user.html               ✅ COPIED from old project (unchanged)
    ├── user_profile.html           ✅ GENERATED (Phase 1) — updated joins
    ├── transactions.html           ✅ GENERATED (Phase 1) — updated joins
    ├── my_items.html               ✅ GENERATED (Phase 1) — updated joins
    ├── scanner.html                ✅ STUB (Phase 1) — full impl in Phase 2
    ├── low_stock.html              ✅ STUB (Phase 1) — full impl in Phase 2
    └── stub.html                   ✅ GENERATED (Phase 1) — placeholder template
```

### Full Target Structure After All Phases

```
inventory_system/
├── app.py                          (grows each phase)
├── requirements.txt
├── instance/
│   └── inventory.db
├── static/
│   ├── js/
│   │   └── jsQR.min.js
│   └── qrcodes/
│       └── column_{id}.png         (generated at runtime)
└── templates/
    ├── base.html                   (updated in Phase 3 for analytics nav)
    ├── login.html
    ├── dashboard.html              (updated Phase 3 for analytics widgets)
    ├── users.html
    ├── add_user.html
    ├── user_profile.html
    ├── transactions.html           (enhanced Phase 3 with filters)
    ├── my_items.html
    ├── scanner.html                (full impl Phase 2)
    ├── low_stock.html              (full impl Phase 2)
    ├── columns.html                (Phase 2)
    ├── add_column.html             (Phase 2)
    ├── edit_column.html            (Phase 2)
    ├── boxes.html                  (Phase 2)
    ├── add_box.html                (Phase 2)  ← renamed from add_bin.html
    ├── edit_box.html               (Phase 2)  ← renamed from edit_bin.html
    ├── items.html                  (Phase 2)
    ├── add_item.html               (Phase 2)
    ├── edit_item.html              (Phase 2)
    ├── analytics.html              (Phase 3)
    ├── reports.html                (Phase 3)
    └── stub.html                   (removed after all stubs are replaced)
```

---

## app.py Route Map (Complete — All Phases)

| Route | Function | Method | Roles | Phase |
|---|---|---|---|---|
| `/` | `index` | GET | all | 1 |
| `/login` | `login` | GET/POST | all | 1 |
| `/logout` | `logout` | GET | all | 1 |
| `/dashboard` | `dashboard` | GET | all | 1 |
| `/users` | `users` | GET | admin | 1 |
| `/users/add` | `add_user` | GET/POST | admin | 1 |
| `/users/delete/<id>` | `delete_user` | POST | admin | 1 |
| `/users/<id>` | `user_profile` | GET | admin | 1 |
| `/api/admin/user/<id>` | `api_edit_user` | POST | admin | 1 |
| `/my-items` | `my_items` | GET | all | 1 |
| `/transactions` | `transactions` | GET | admin, storekeeper | 1 |
| `/columns` | `columns` | GET | admin, storekeeper | 2 |
| `/columns/add` | `add_column` | GET/POST | admin, storekeeper | 2 |
| `/columns/edit/<id>` | `edit_column` | GET/POST | admin, storekeeper | 2 |
| `/columns/delete/<id>` | `delete_column` | POST | admin | 2 |
| `/qr/generate/<col_id>` | `generate_column_qr` | GET | admin, storekeeper | 2 |
| `/qr/download/<col_id>` | `download_column_qr` | GET | admin, storekeeper | 2 |
| `/boxes` | `boxes` | GET | admin, storekeeper | 2 |
| `/boxes/add` | `add_box` | GET/POST | admin, storekeeper | 2 |
| `/boxes/edit/<id>` | `edit_box` | GET/POST | admin, storekeeper | 2 |
| `/boxes/delete/<id>` | `delete_box` | POST | admin | 2 |
| `/items` | `items` | GET | admin, storekeeper | 2 |
| `/items/add` | `add_item` | GET/POST | admin, storekeeper | 2 |
| `/items/edit/<id>` | `edit_item` | GET/POST | admin, storekeeper | 2 |
| `/items/delete/<id>` | `delete_item` | POST | admin | 2 |
| `/items/restock/<id>` | `restock_item` | POST | admin, storekeeper | 2 |
| `/scanner` | `scanner` | GET | all | 2 |
| `/low-stock` | `low_stock` | GET | admin, storekeeper | 2 |
| `/api/column/<id>/boxes` | `api_column_boxes` | GET | all | 2 |
| `/api/box/<id>/items` | `api_box_items` | GET | all | 2 |
| `/api/item/<id>` | `api_item` | GET | all | 2 |
| `/api/transaction` | `api_transaction` | POST | all | 2 |
| `/api/restock` | `api_restock` | POST | admin, storekeeper | 2 |
| `/analytics` | `analytics` | GET | admin | 3 |
| `/reports` | `reports` | GET | admin, storekeeper | 3 |

---

## Seed Data (Established in Phase 1)

**Users:**
| Name | Email | Password | Role |
|---|---|---|---|
| Admin User | admin@edge2.com | admin123 | admin |
| Store Keeper | store@edge2.com | store123 | storekeeper |
| Alice Engineer | alice@edge2.com | alice123 | employee |
| Bob Intern | bob@edge2.com | bob123 | employee |

**Columns → Boxes → Items:**
- Column A → Box 1 (Arduino Uno, ESP32 DevKit), Box 2 (Ultrasonic HC-SR04, DHT22)
- Column B → Box 3 (Breadboard, Jumper Wires), Box 4 (16x2 LCD, OLED SSD1306)
- Column C → Box 5 (L298N Motor Driver, Buck Converter)

---

---

# PHASE BREAKDOWN

---

## ✅ Phase 1 — Authentication + Database + Base Structure
**Status: COMPLETE**

### What Was Built
- Flask app skeleton with `app.secret_key`, `DB_PATH`, `get_db()`, `hash_password()`
- Auth decorators: `login_required`, `role_required(*roles)`
- Full SQLite schema with all 6 tables
- Idempotent `init_db()` with seed data
- `generate_qr_for_column(column_id)` helper function
- Session-based login/logout
- Dashboard (role-aware: admin/storekeeper sees stats + low stock + transactions; employee sees own activity)
- User management: list, add, delete, view profile, edit via API
- My Items page (shows active borrowings)
- Transactions page (last 200, admin/storekeeper only)
- Updated `base.html` with new navigation: Columns / Boxes / Items links in sidebar
- Stub routes for `columns`, `boxes`, `items`, `scanner`, `low_stock` so app runs without errors
- `stub.html` placeholder template

### Files Generated in Phase 1
| File | Status |
|---|---|
| `app.py` | New |
| `requirements.txt` | New |
| `static/js/jsQR.min.js` | Copied from old project |
| `templates/base.html` | New (design reused, nav updated) |
| `templates/login.html` | Copied from old project unchanged |
| `templates/dashboard.html` | New (rebuilt for new schema) |
| `templates/users.html` | Copied from old project unchanged |
| `templates/add_user.html` | Copied from old project unchanged |
| `templates/user_profile.html` | New (updated joins) |
| `templates/transactions.html` | New (updated joins) |
| `templates/my_items.html` | New |
| `templates/scanner.html` | Stub |
| `templates/low_stock.html` | Stub |
| `templates/stub.html` | New (placeholder) |

### Files to Request from User
> **None.** Phase 1 builds from scratch using the old project as reference only.

---

## 🔲 Phase 2 — Core Inventory System + QR + Scanner
**Status: NOT STARTED**

### Overview
This is the largest phase. It implements the complete inventory management CRUD, QR generation, and the full 5-step scanner workflow. Phase 1's stub routes are replaced with real implementations.

### What to Build

#### 2A — Column Management
- **`/columns`** — List all columns. Show: column name, number of boxes inside, QR status (generated or not), action buttons (Edit, Generate QR, Download QR, Delete).
- **`/columns/add`** — Form: `column_name` only. On save, redirect to columns list.
- **`/columns/edit/<id>`** — Form: edit `column_name`. Cannot edit QR path from here.
- **`/columns/delete/<id>`** (POST, admin only) — Deletes column. Cascades to boxes and items (ON DELETE CASCADE in schema).
- **`/qr/generate/<column_id>`** (GET, admin/storekeeper) — Calls `generate_qr_for_column(column_id)`, saves path to `columns.qr_code_path`, returns JSON `{qr_path}`.
- **`/qr/download/<column_id>`** (GET, admin/storekeeper) — Sends the QR PNG file as a download using `send_file()`.

**Column QR encode format:** `COLUMN:{id}` — e.g. `COLUMN:3`

#### 2B — Box Management
- **`/boxes`** — List all boxes grouped or filterable by column. Show: box name, parent column, description, item count, actions.
- **`/boxes/add`** — Form: `column_id` (select dropdown populated from all columns), `box_name`, `description`. Named `add_box.html` (not add_bin.html).
- **`/boxes/edit/<id>`** — Form: edit all fields including column reassignment. Named `edit_box.html` (not edit_bin.html).
- **`/boxes/delete/<id>`** (POST, admin only) — Cascades to inventory_items.

#### 2C — Item Management
- **`/items`** — List all items. Filterable by column, box, low stock status. Show: item name, box, column, quantity, min_stock, status badge, actions.
- **`/items/add`** — Form: `box_id` (select), `item_name`, `quantity`, `min_stock`, `description`. `min_stock` is mandatory — it is stored per item.
- **`/items/edit/<id>`** — Edit all fields including box reassignment.
- **`/items/delete/<id>`** (POST, admin only) — Hard delete.
- **`/items/restock/<id>`** (POST, admin/storekeeper) — Adds quantity to item. Records a `restock` transaction. Returns JSON `{success, new_quantity}`.

#### 2D — QR Code UI
In `columns.html`, each column row has:
- If `qr_code_path` is NULL: **"Generate QR"** button → calls `/qr/generate/<id>` via fetch, updates button inline
- If `qr_code_path` is set: **"View QR"** button → opens modal with QR image, **"Download"** button → links to `/qr/download/<id>`
- QR modal: shows image, column name, download button, close button
- These buttons are only shown to `admin` and `storekeeper`; normal users see nothing

#### 2E — Low Stock Page
- **`/low-stock`** — Full implementation replacing stub. Shows all items where `quantity <= min_stock`. Columns: Item, Column › Box path, Quantity, Min Stock, Status badge (Out of Stock / Low Stock), Restock button (admin/storekeeper). Sort by quantity ASC.

#### 2F — Full Scanner Workflow
Replace the stub `scanner.html` with the complete 5-step flow. This is the most critical UI component.

**Scanner HTML structure:**

```
[Camera View Card]           — Start/Stop camera toggle, video element, scan overlay
[Step 2: Box Selection]      — Hidden until column QR scanned. Shows column name + list of boxes as clickable cards
[Step 3: Item Selection]     — Hidden until box selected. Shows box name + list of items with quantity info
[Step 4: Transaction Panel]  — Hidden until item selected. Shows item info, stock, buttons
[Success Overlay]            — Full-screen success confirmation
```

**Scanner JavaScript logic:**
```javascript
// State variables
let scannedColumnId = null;
let selectedBoxId = null;
let selectedItemId = null;
let selectedAction = null;  // 'take', 'return', 'restock'
let scanning = false;
let videoStream = null;

// QR decode: look for string starting with "COLUMN:"
// On decode: call /api/column/<id>/boxes → show Step 2
// On box click: call /api/box/<id>/items → show Step 3
// On item click: call /api/item/<id> → show Step 4
// On action button: show quantity input + confirm button
// On confirm: POST to /api/transaction or /api/restock
```

**API endpoints needed:**

```python
GET  /api/column/<column_id>/boxes
# Returns: { column_name, boxes: [{id, box_name, description, item_count}] }

GET  /api/box/<box_id>/items
# Returns: { box_name, column_name, items: [{id, item_name, quantity, min_stock, description}] }

GET  /api/item/<item_id>
# Returns: { id, item_name, quantity, min_stock, description, box_name, column_name,
#             quantity_borrowed (by current user) }

POST /api/transaction
# Body: { item_id, quantity, type: 'take'|'return' }
# Take: deducts quantity, updates active_borrowings, records transaction
# Return: adds quantity, updates active_borrowings (cannot return more than borrowed), records transaction
# Returns: { success, new_quantity, message } or { error }

POST /api/restock
# Body: { item_id, quantity }
# Roles: admin, storekeeper only
# Adds quantity, records restock transaction
# Returns: { success, new_quantity }
```

**Transaction rules (enforce server-side):**
- `take`: `quantity` must be <= `inventory_items.quantity` (available stock)
- `return`: `quantity` must be <= `active_borrowings.quantity_borrowed` for that user+item pair
- `restock`: `quantity` must be > 0, no upper limit

**Restock button visibility:**
- Show `[Restock]` button in scanner Step 4 ONLY when `session['role'] in ('admin', 'storekeeper')`
- Normal users (`employee`) see only `[Take]` and `[Return]`
- This check happens both in the HTML template (Jinja `{% if %}`) AND is enforced server-side in `/api/restock`

### Files to Generate in Phase 2

| File | Action |
|---|---|
| `app.py` | **Replace** — add all new routes (columns CRUD, boxes CRUD, items CRUD, QR routes, scanner APIs). Remove stub routes. |
| `templates/columns.html` | **New** |
| `templates/add_column.html` | **New** |
| `templates/edit_column.html` | **New** |
| `templates/boxes.html` | **New** |
| `templates/add_box.html` | **New** (was `add_bin.html` in old project — use as reference for form structure only) |
| `templates/edit_box.html` | **New** (was `edit_bin.html` in old project — use as reference for form structure only) |
| `templates/items.html` | **New** |
| `templates/add_item.html` | **New** |
| `templates/edit_item.html` | **New** |
| `templates/scanner.html` | **Replace stub** — full 5-step scanner implementation |
| `templates/low_stock.html` | **Replace stub** — full low stock page |

### Files to Request from User
> **Request:** The current `app.py` (Phase 1 version).
> That is the only file needed. All templates are generated fresh.

---

## 🔲 Phase 3 — Analytics, Reports & Enhanced Transactions
**Status: NOT STARTED**

### Overview
Analytics and reporting for admin and storekeeper. No new inventory CRUD — only new read-only views built on top of existing data.

### What to Build

#### 3A — Analytics Dashboard Page (`/analytics`)
Admin only. A dedicated analytics page (separate from the main dashboard) with:

- **Usage by Item** — Bar/visual showing which items are taken most frequently (join transactions + inventory_items, count type='take', group by item_id)
- **Usage by User** — Table of users ranked by transaction count
- **Daily Activity** — Table or simple chart of transactions per day (last 30 days)
- **Top Borrowers** — Users with highest current `active_borrowings.quantity_borrowed` total
- **Stock Health Summary** — Count of: Out of Stock / Low Stock / Healthy items
- Rendered entirely with Vanilla JS (no Chart.js, no D3) — use CSS bar charts built with div widths and percentages

#### 3B — Reports Page (`/reports`)
Admin + storekeeper. Filterable transaction history:

- Filter by: date range (from/to), transaction type (take/return/restock/all), user (select), column (select)
- Results table: User, Item, Column › Box, Type, Qty, Timestamp
- "Export CSV" button — generates and downloads a CSV file server-side via `/reports/export` route
- `/reports/export` (GET, admin/storekeeper) — accepts same filter params as query string, returns CSV via `send_file()`

#### 3C — Enhanced Transactions Page
Update existing `transactions.html` and `/transactions` route to add:
- Filter bar: search by item name, filter by type (all/take/return/restock)
- Client-side filtering via JS (no page reload needed — data already loaded)
- Show total count of filtered results

#### 3D — Dashboard Analytics Widgets
Add to the existing `dashboard.html` for admin/storekeeper (below the existing content):
- **Most Active Items This Week** — Top 5 items by transaction count in last 7 days
- **Restock Needed** — Items at 0 quantity (out of stock)

### New API/Routes in Phase 3

```python
GET /analytics         # analytics page (admin only)
GET /reports           # reports page with filters (admin, storekeeper)
GET /reports/export    # CSV download (admin, storekeeper)
                       # Query params: from_date, to_date, type, user_id, column_id

GET /api/analytics/summary       # JSON for analytics page
GET /api/analytics/usage         # JSON: item usage stats
GET /api/analytics/daily         # JSON: daily transaction counts (last 30 days)
```

### Files to Generate in Phase 3

| File | Action |
|---|---|
| `app.py` | **Update** — add analytics and report routes, `/reports/export` CSV endpoint |
| `templates/analytics.html` | **New** |
| `templates/reports.html` | **New** |
| `templates/transactions.html` | **Update** — add client-side filter bar |
| `templates/dashboard.html` | **Update** — add analytics widgets at bottom |
| `templates/base.html` | **Update** — add Analytics and Reports links to sidebar nav (admin/storekeeper section) |

### Files to Request from User
> **Request:** Current `app.py` (Phase 2 version).
> Optionally: `templates/transactions.html` and `templates/dashboard.html` if the user wants to confirm current state.
> All new templates are generated fresh.

---

## 🔲 Phase 4 — Inventory Detail Views & Enhanced Navigation
**Status: NOT STARTED**

### Overview
Drill-down views. Clicking on a column shows its boxes. Clicking on a box shows its items. Breadcrumb navigation throughout.

### What to Build

#### 4A — Column Detail Page (`/columns/<id>`)
- Shows column name, QR status
- Lists all boxes in that column (as cards or table)
- Each box card shows: box name, description, item count, low stock count
- "Add Box" button (pre-filled with this column_id)
- "Generate QR" / "Download QR" buttons (admin/storekeeper)

#### 4B — Box Detail Page (`/boxes/<id>`)
- Shows box name, parent column (with link back)
- Breadcrumb: Columns › Column A › Box 1
- Lists all items in the box (table with quantity, min_stock, status badge)
- "Add Item" button (pre-filled with this box_id)
- Restock button per item row (admin/storekeeper)

#### 4C — Item Detail Page (`/items/<id>`)
- Shows full item info: name, box, column, quantity, min_stock, description, created_at
- Breadcrumb: Columns › Column A › Box 1 › Item Name
- Recent transaction history for this item (last 20)
- Restock button (admin/storekeeper)
- Edit button (admin/storekeeper)

#### 4D — Breadcrumbs
Add breadcrumb component to all inventory pages. The `.breadcrumb` CSS class already exists in `base.html`:
```html
<div class="breadcrumb">
  <a href="/columns">Columns</a>
  <span class="sep">›</span>
  <a href="/columns/3">Column A</a>
  <span class="sep">›</span>
  <span>Box 1</span>
</div>
```

### Files to Generate in Phase 4

| File | Action |
|---|---|
| `app.py` | **Update** — add `/columns/<id>`, `/boxes/<id>`, `/items/<id>` routes |
| `templates/column_detail.html` | **New** |
| `templates/box_detail.html` | **New** |
| `templates/item_detail.html` | **New** |

### Files to Request from User
> **Request:** Current `app.py` (Phase 3 version).

---

## 🔲 Phase 5 — Search & Global Inventory View
**Status: NOT STARTED**

### Overview
A single unified inventory view showing everything across all columns/boxes, with powerful filtering and search. Also adds a global search bar.

### What to Build

#### 5A — Unified Inventory Table (`/inventory`)
New route `/inventory` as a global all-items view:
- Shows every item across all columns/boxes in one flat table
- Columns: Item Name, Column, Box, Quantity, Min Stock, Status, Actions
- Client-side filters: search (item name), filter by column (dropdown), filter by box (dropdown, updates based on selected column), filter by status (all/low/ok/out)
- Result count shown live as filters change
- For admin/storekeeper: Edit and Restock buttons per row

#### 5B — Add Inventory link to sidebar
Update `base.html` to add `/inventory` to the nav under "Inventory" section.

#### 5C — Global Search (Optional Enhancement)
If time permits: a search input in the topbar that queries items, boxes, and columns simultaneously and shows grouped results in a dropdown.

### Files to Generate in Phase 5

| File | Action |
|---|---|
| `app.py` | **Update** — add `/inventory` route |
| `templates/inventory.html` | **New** — unified all-items table |
| `templates/base.html` | **Update** — add Inventory nav link |

### Files to Request from User
> **Request:** Current `app.py` (Phase 4 version).

---

## 🔲 Phase 6 — Bulk Operations & Import/Export
**Status: NOT STARTED**

### Overview
Productivity features for admin/storekeeper: bulk actions on items, CSV import for items, CSV export.

### What to Build

#### 6A — Bulk Restock
In `items.html`, add checkboxes per row. A "Bulk Restock" bar appears when items are checked:
- Input: quantity to add to ALL selected items
- Confirm button
- Backend: `POST /items/bulk-restock` — accepts `{item_ids: [], quantity: int}`

#### 6B — CSV Export of Inventory
`GET /inventory/export` — exports all items (or filtered) as CSV:
```
Column,Box,Item,Quantity,Min Stock,Status
Column A,Box 1,Arduino Uno,24,5,Healthy
```

#### 6C — CSV Import of Items
`GET/POST /inventory/import` — upload a CSV file to batch-create items:
- Required columns: `box_name`, `column_name`, `item_name`, `quantity`, `min_stock`
- Validation: skip rows with missing fields, report errors
- If box/column doesn't exist: create them automatically
- Preview step before final import

### Files to Generate in Phase 6

| File | Action |
|---|---|
| `app.py` | **Update** — add bulk-restock, export, import routes |
| `templates/items.html` | **Update** — add checkboxes, bulk action bar |
| `templates/import_items.html` | **New** — CSV import form with preview |

### Files to Request from User
> **Request:** Current `app.py` + current `templates/items.html`.

---

## 🔲 Phase 7 — User Self-Service & Profile
**Status: NOT STARTED**

### Overview
Normal users currently can only scan QR and take/return. This phase gives them a richer personal experience.

### What to Build

#### 7A — User Self Profile Page (`/profile`)
Available to all logged-in users (not just admin):
- View own name, email, role
- Change own password (requires current password confirmation)
- View own transaction history (all time)
- View currently borrowed items

#### 7B — Borrow History Page (`/my-history`)
Dedicated page showing all past transactions for the current user:
- Table: Item, Column › Box, Type, Qty, Timestamp
- Filter by type (take/return)

#### 7C — Update `/my-items`
Enhance the existing my_items.html:
- Add a "Return" button per row that opens a quantity input and calls `/api/transaction` with type `return`
- No need to re-scan the QR — can return directly from this page
- This is a convenience feature on top of the existing scanner flow

### Files to Generate in Phase 7

| File | Action |
|---|---|
| `app.py` | **Update** — add `/profile`, `/my-history`, `/api/quick-return` routes |
| `templates/profile.html` | **New** |
| `templates/my_history.html` | **New** |
| `templates/my_items.html` | **Update** — add inline return buttons |
| `templates/base.html` | **Update** — add Profile link to sidebar |

### Files to Request from User
> **Request:** Current `app.py` + current `templates/my_items.html`.

---

## 🔲 Phase 8 — Notifications & Stock Alerts
**Status: NOT STARTED**

### Overview
Proactive alerts for low stock. Admins/storekeepers see alerts across the app; no email — all in-app.

### What to Build

#### 8A — Alert Badge on Sidebar
The "Low Stock" nav link in `base.html` already has a `.nav-badge` class. Populate it with the count of low-stock items.

In `base.html`, update the Low Stock nav link to fetch the count and display it. Pass `low_stock_count` from all routes via a context processor.

```python
@app.context_processor
def inject_low_stock_count():
    if 'user_id' in session and session.get('role') in ('admin', 'storekeeper'):
        conn = get_db()
        count = conn.execute(
            "SELECT COUNT(*) FROM inventory_items WHERE quantity <= min_stock"
        ).fetchone()[0]
        conn.close()
        return {'low_stock_alert_count': count}
    return {'low_stock_alert_count': 0}
```

#### 8B — Dashboard Alert Banner
If any items are at 0 quantity (out of stock), show a red alert banner at the top of the dashboard:
```
⚠ 3 items are completely out of stock. View Low Stock →
```

#### 8C — Item Page Inline Warning
In `items.html` and `item_detail.html`, highlight rows where `quantity <= min_stock` with a left border color (use `--warning` or `--danger` CSS var). Already possible with the existing table styles.

#### 8D — Restock Reminder on Low Stock Page
On `low_stock.html`, add a one-click "Restock All Low Items" button (admin/storekeeper) that opens a modal to set a quantity and restocks all items currently below min_stock to `min_stock` level.

### Files to Generate in Phase 8

| File | Action |
|---|---|
| `app.py` | **Update** — add context processor, `/items/restock-all` route |
| `templates/base.html` | **Update** — inject badge count into Low Stock nav link |
| `templates/dashboard.html` | **Update** — add out-of-stock banner |
| `templates/low_stock.html` | **Update** — add restock-all button + modal |

### Files to Request from User
> **Request:** Current `app.py` + current `templates/base.html` + current `templates/dashboard.html` + current `templates/low_stock.html`.

---

## 🔲 Phase 9 — Polish, UX & Mobile Improvements
**Status: NOT STARTED**

### Overview
No new features — pure UX improvement pass. Responsive design, loading states, confirm modals, empty states, keyboard shortcuts.

### What to Build

#### 9A — Mobile Responsive Fixes
- All tables get horizontal scroll on mobile (`overflow-x: auto` already exists via `.table-wrap`)
- Multi-column form grids collapse to single column on mobile
- Scanner page: video takes full viewport height on mobile
- Box/item selection cards in scanner become large tap targets (min 60px height)

#### 9B — Confirm Modals (Replace browser `confirm()`)
Replace all `onsubmit="return confirm(...)"` with a custom modal using existing card/button styles:
```html
<div id="confirmModal">
  <div class="card">
    <div class="card-body">
      <p id="confirmMsg"></p>
      <div style="display:flex;gap:8px">
        <button id="confirmYes" class="btn btn-danger">Delete</button>
        <button onclick="closeConfirm()" class="btn btn-secondary">Cancel</button>
      </div>
    </div>
  </div>
</div>
```
Add to `base.html`. All delete buttons use `openConfirm(msg, formId)`.

#### 9C — Loading States
- Add `.btn-loading` CSS state: spinner via CSS animation, disable button, change text to "..."
- Apply to all async fetch buttons (Generate QR, scanner confirm, restock submit)

#### 9D — Empty State Consistency
Audit all pages and ensure every table/list has a proper `.empty-state` block when no data exists (using existing component).

#### 9E — Keyboard Shortcuts
In scanner: press `Enter` to confirm transaction when quantity input is focused. Press `Escape` to close modals.

### Files to Generate in Phase 9

| File | Action |
|---|---|
| `templates/base.html` | **Update** — add confirm modal, loading CSS, keyboard shortcut JS |
| All inventory templates | **Minor updates** — replace `confirm()` calls, add loading states |

### Files to Request from User
> **Request:** Current `templates/base.html` + any templates that use `confirm()` for delete actions.

---

## 🔲 Phase 10 — Final Hardening & Production Readiness
**Status: NOT STARTED**

### Overview
Security hardening, error handling, edge case fixes, and documentation. Makes the app production-deployable.

### What to Build

#### 10A — Error Handling
- Custom 404 page (`templates/404.html`) using base.html styles
- Custom 500 page (`templates/500.html`)
- All routes that accept `<int:id>` parameters: return proper 404 if record not found (currently some redirect silently)
- API endpoints: return consistent JSON error format `{error: string, code: int}`

#### 10B — Security Hardening
- CSRF protection: add a CSRF token to all POST forms
  - Generate token in session on login, validate in all POST routes
  - Add `<input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">` to all forms
- Rate limiting on `/login`: after 5 failed attempts, lock for 60 seconds (track in session or simple in-memory dict)
- Sanitize all text inputs (strip HTML characters) — use `markupsafe.escape()` or manual strip

#### 10C — Session Security
- Set `SESSION_COOKIE_HTTPONLY = True`
- Set `SESSION_COOKIE_SAMESITE = 'Lax'`
- Set `PERMANENT_SESSION_LIFETIME` to 8 hours
- Add these to `app.config` block at top of `app.py`

#### 10D — Graceful DB Error Handling
Wrap all DB operations in try/except. If a DB error occurs in an API route, return `{error: "Database error"}` with 500 status instead of crashing.

#### 10E — README + Setup Docs
Update/create `README.md`:
```markdown
# Edge2 Smart Inventory System

## Setup
pip install -r requirements.txt
python app.py

## Default Accounts
admin@edge2.com / admin123
store@edge2.com / store123
alice@edge2.com / alice123

## Architecture
Column → Box → Item hierarchy
QR codes on Columns only
...
```

### Files to Generate in Phase 10

| File | Action |
|---|---|
| `app.py` | **Update** — CSRF, rate limiting, session config, error handlers, try/except |
| `templates/404.html` | **New** |
| `templates/500.html` | **New** |
| `templates/base.html` | **Update** — CSRF token injection in all forms |
| `README.md` | **New/Update** |

### Files to Request from User
> **Request:** Current `app.py` + current `templates/base.html`.

---

## Critical Non-Negotiable Rules (For Every Phase)

1. **Stack:** Flask + SQLite + HTML/CSS/Vanilla JS only. No exceptions.
2. **Auth:** Session-based. `login_required` and `role_required` decorators on every protected route.
3. **Hierarchy:** Column → Box → Item. Every item must belong to a box. Every box must belong to a column.
4. **QR:** Generated for Columns only. Format: `COLUMN:{id}`. File: `static/qrcodes/column_{id}.png`.
5. **Transactions and active_borrowings:** Both reference `item_id` — never `bin_id`, never `box_id`.
6. **min_stock:** Stored per inventory item, not per box.
7. **Restock:** Available to admin and storekeeper only — both in inventory management pages and in the scanner workflow.
8. **Normal user (employee):** Can take and return only. Cannot generate QR, download QR, restock, or access management pages.
9. **Template naming:** `add_box.html` and `edit_box.html` — never `add_bin.html` or `edit_bin.html`.
10. **Design system:** Preserve all CSS variables and component classes from `base.html`. Never hardcode colors.
11. **jsQR:** Always load from `static/js/jsQR.min.js` — never from a CDN.
12. **Analytics:** Do NOT start until Phase 3. Phases 1 and 2 have no analytics.

---

## How the AI Should Work Per Phase

When asked to generate a phase:

1. **Read this document fully first.** Do not start generating until you understand the full context.
2. **Only ask for files listed in "Files to Request" for that phase.** Do not ask for files you already have context on.
3. **Generate ALL files for the phase at once** — the user has requested all files per phase in one delivery.
4. **Never regress existing functionality.** Each phase's `app.py` must include all routes from all previous phases.
5. **Stub routes** from Phase 1 (`columns`, `boxes`, `items`) are replaced in Phase 2 — never leave stubs after Phase 2.
6. **Test mentally before generating:** trace through the scanner flow, check all foreign key references use `item_id`, verify all Jinja role checks match the `session['role']` values.
