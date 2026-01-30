# Organization ID vs Client ID — Column Reference

This document explains **which column** in each table is used to scope data by **organization** (so all team members in the same org see the same data) instead of by the legacy **client_id** (per-user tenant).

---

## The column that scopes by organization: `clerk_org_id`

**Column name:** `clerk_org_id`  
**Type:** `TEXT`  
**Meaning:** Clerk’s organization ID (e.g. `org_38mpe7YzuKjtqIRiuiWGCruMwX5`). All users in the same organization share the same `clerk_org_id`.  
**Usage:** Filter/partition data by this column so everything is “per organization,” not “per user” or “per client.”

---

## Tables that use `clerk_org_id` (organization-first)

These tables have a **`clerk_org_id`** column. The app filters by this column to get data by organization instead of by `client_id`.

| Table               | Organization column | Index                         | Notes |
|---------------------|---------------------|-------------------------------|--------|
| **agents**          | `clerk_org_id`      | `idx_agents_clerk_org_id`     | AI agents are shared per org. |
| **calls**           | `clerk_org_id`      | `idx_calls_clerk_org_id`      | Also has `created_by_user_id` for “who started the call.” |
| **voices**          | `clerk_org_id`      | `idx_voices_clerk_org_id`     | Voices (including cloned) are shared per org. |
| **knowledge_bases** | `clerk_org_id`      | `idx_knowledge_bases_clerk_org_id` | RAG/knowledge bases are per org. |
| **tools**           | `clerk_org_id`      | `idx_tools_clerk_org_id`      | Custom tools are per org. |
| **contacts**        | `clerk_org_id`      | `idx_contacts_clerk_org_id`   | Contact records are per org. |
| **contact_folders**  | `clerk_org_id`      | `idx_contact_folders_clerk_org_id` | Contact folders are per org. |
| **campaigns**       | `clerk_org_id`      | `idx_campaigns_clerk_org_id`  | Campaigns are per org. |
| **webhook_endpoints**| `clerk_org_id`      | `idx_webhook_endpoints_clerk_org_id` | Outbound webhooks are per org. |

**Summary:** For every row in these tables, “get data by organization” = **filter by `clerk_org_id`** (not by `client_id`).

---

## Related table: `clients` (org ↔ client mapping)

| Table    | Organization-related column   | Purpose |
|----------|-------------------------------|---------|
| **clients** | `clerk_organization_id` (TEXT) | Links a **client** (tenant) to a **Clerk organization**. One org → one client. Used by `/auth/me` to resolve or create `client_id` from JWT `org_id`. |

- **`clerk_organization_id`** = Clerk org ID.  
- **`id`** = legacy `client_id` (UUID).  
Data is partitioned by **`clerk_org_id`** on the tables above; `clients` is the mapping layer between Clerk org and legacy client.

---

## Tables that do NOT have `clerk_org_id` (still keyed by client or user)

These are not (yet) organization-scoped by a dedicated column; they still rely on `client_id` or user references:

| Table                 | Scoping column(s)     | Notes |
|-----------------------|------------------------|--------|
| **users**             | `client_id`            | Users belong to a client; org is inferred via client → `clerk_organization_id`. |
| **api_keys**          | `client_id` + `settings` | Stored per client; backend can store `clerk_org_id` inside `settings` JSONB for org scoping. |
| **clients**           | —                      | Root tenant; has `clerk_organization_id` to link to org. |
| **audit_log**         | `client_id`            | Legacy; can be extended with org later. |
| **application_logs**  | `client_id`            | Same as above. |
| **idempotency_keys**  | `client_id` (in key)   | Idempotency per client. |
| **credit_transactions** | `client_id`          | Billing/credits per client. |

So: **“get data by organization”** for the main app data is done via **`clerk_org_id`** on the 9 tables in the first section. The others are either mapping (clients) or still client/user-scoped.

---

## Quick rule

- **“By organization”** (shared for the whole team) → use **`clerk_org_id`** on:  
  `agents`, `calls`, `voices`, `knowledge_bases`, `tools`, `contacts`, `contact_folders`, `campaigns`, `webhook_endpoints`.
- **“By tenant / legacy client”** or **“map org ↔ client”** → use **`client_id`** or **`clerk_organization_id`** on `clients` / `users` / `api_keys` / audit and billing tables as above.

Migrations that added **`clerk_org_id`**: `022_add_clerk_org_id_to_tables.sql`, `023_add_clerk_org_id_to_webhook_endpoints.sql`.
