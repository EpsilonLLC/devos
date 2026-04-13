# Data model

## Conventions (locked — agents must follow exactly)
- All tables: snake_case
- All PKs: `id UUID DEFAULT gen_random_uuid()`
- All tables: `created_at TIMESTAMPTZ DEFAULT NOW()`
- All tables: `updated_at TIMESTAMPTZ DEFAULT NOW()`
- Soft deletes: `deleted_at TIMESTAMPTZ NULL`
- Tenant scoping: every table except `tenants` has `tenant_id UUID NOT NULL`

## Tables

---
### `tenants`
**Purpose:** Represents an isolated account namespace; in this single-user app each registered user maps 1-to-1 with exactly one tenant.

| Column | Type | Nullable | Default | Constraint |
|--------|------|----------|---------|------------|
| id | UUID | NO | gen_random_uuid() | PK |
| created_at | TIMESTAMPTZ | NO | NOW() |  |
| updated_at | TIMESTAMPTZ | NO | NOW() |  |
| deleted_at | TIMESTAMPTZ | YES |  |  |

**Indexes:**
- None beyond primary key

**Relationships:**
- None

**Invariants:**
- In the current single-user deployment model, exactly one tenant record is created per user registration; application code must enforce this 1-to-1 invariant.
- Soft-deleted tenants must cause all associated users, tasks, sessions, and tags to be treated as inaccessible by application code.

**Out of scope:**
- Multi-user or team tenant structures — each tenant corresponds to exactly one individual developer user.
- Tenant-level configuration or settings — no tenant metadata beyond the ID and timestamps is stored.

---

---
### `users`
**Purpose:** Stores the registered developer accounts, including hashed credentials used for email/password authentication.

| Column | Type | Nullable | Default | Constraint |
|--------|------|----------|---------|------------|
| id | UUID | NO | gen_random_uuid() | PK |
| tenant_id | UUID | NO |  | FK → tenants(id) |
| email | TEXT | NO |  | UNIQUE (partial: WHERE deleted_at IS NULL) |
| hashed_password | TEXT | NO |  |  |
| created_at | TIMESTAMPTZ | NO | NOW() |  |
| updated_at | TIMESTAMPTZ | NO | NOW() |  |
| deleted_at | TIMESTAMPTZ | YES |  |  |

**Indexes:**
- CREATE UNIQUE INDEX idx_users_email_active ON users(email) WHERE deleted_at IS NULL
- CREATE INDEX idx_users_tenant_id ON users(tenant_id)

**Relationships:**
- tenant_id REFERENCES tenants(id) ON DELETE CASCADE

**Invariants:**
- Email must be unique among non-deleted users; application code must check for existing active records before insert.
- hashed_password must never store a plaintext password; application code is solely responsible for hashing before write.
- A user record and its corresponding tenant record are created atomically in the same transaction during signup.
- Email format validation (contains '@' and a domain) is enforced by application code before the record is written.

**Out of scope:**
- Password reset tokens or recovery emails — no such mechanism exists.
- OAuth or third-party identity provider fields.
- Email verification status — accounts are active immediately.
- Account lockout counters or rate-limit state.
- Profile fields beyond email (no display name, avatar, bio, etc.).

---

---
### `sessions`
**Purpose:** Stores active authenticated session tokens issued to users after successful login or signup, enabling server-side session validation.

| Column | Type | Nullable | Default | Constraint |
|--------|------|----------|---------|------------|
| id | UUID | NO | gen_random_uuid() | PK |
| tenant_id | UUID | NO |  | FK → tenants(id) |
| user_id | UUID | NO |  | FK → users(id) |
| token_hash | TEXT | NO |  | UNIQUE |
| expires_at | TIMESTAMPTZ | YES |  |  |
| created_at | TIMESTAMPTZ | NO | NOW() |  |
| updated_at | TIMESTAMPTZ | NO | NOW() |  |
| deleted_at | TIMESTAMPTZ | YES |  |  |

**Indexes:**
- CREATE UNIQUE INDEX idx_sessions_token_hash ON sessions(token_hash) WHERE deleted_at IS NULL
- CREATE INDEX idx_sessions_user_id ON sessions(user_id)
- CREATE INDEX idx_sessions_expires_at ON sessions(expires_at)

**Relationships:**
- tenant_id REFERENCES tenants(id) ON DELETE CASCADE
- user_id REFERENCES users(id) ON DELETE CASCADE

**Invariants:**
- The raw session token is stored only in the HTTP-only cookie on the client; only a secure hash of the token is persisted in this table.
- Application code must treat any session with expires_at < NOW() or deleted_at IS NOT NULL as invalid.
- Session expiry policy (duration) is defined by application configuration, not enforced by a DB constraint in this version.

**Out of scope:**
- Session expiry enforcement via DB triggers — expiry is checked at application layer.
- Refresh token mechanics — not in scope per F-001.
- Device or user-agent tracking per session.

---

---
### `tasks`
**Purpose:** Stores all developer task records including title, status, priority, and notes, owned by a single authenticated user.

| Column | Type | Nullable | Default | Constraint |
|--------|------|----------|---------|------------|
| id | UUID | NO | gen_random_uuid() | PK |
| tenant_id | UUID | NO |  | FK → tenants(id) |
| user_id | UUID | NO |  | FK → users(id) |
| title | TEXT | NO |  |  |
| status | TEXT | NO | 'to-do' | CHECK (status IN ('to-do', 'in-progress', 'done')) |
| priority | TEXT | NO | 'medium' | CHECK (priority IN ('low', 'medium', 'high')) |
| notes | TEXT | YES | '' | CHECK (char_length(notes) <= 10000) |
| status_changed_at | TIMESTAMPTZ | YES |  |  |
| created_at | TIMESTAMPTZ | NO | NOW() |  |
| updated_at | TIMESTAMPTZ | NO | NOW() |  |
| deleted_at | TIMESTAMPTZ | YES |  |  |

**Indexes:**
- CREATE INDEX idx_tasks_user_id ON tasks(user_id) WHERE deleted_at IS NULL
- CREATE INDEX idx_tasks_tenant_id ON tasks(tenant_id) WHERE deleted_at IS NULL
- CREATE INDEX idx_tasks_status ON tasks(status) WHERE deleted_at IS NULL
- CREATE INDEX idx_tasks_user_status_priority ON tasks(user_id, status, priority) WHERE deleted_at IS NULL

**Relationships:**
- tenant_id REFERENCES tenants(id) ON DELETE CASCADE
- user_id REFERENCES users(id) ON DELETE CASCADE

**Invariants:**
- title must not be empty or whitespace-only after trimming; enforced in application code before DB write.
- status must be one of exactly {'to-do', 'in-progress', 'done'} — case-sensitive; enforced both by CHECK constraint and application code.
- priority must be one of exactly {'low', 'medium', 'high'} — case-sensitive; enforced both by CHECK constraint and application code.
- notes must not exceed 10,000 characters; enforced by CHECK constraint and application code with a descriptive error.
- status_changed_at must be updated by application code whenever the status column value changes.
- F-004 specifies permanent deletion; application code implements this as setting deleted_at = NOW() (soft delete per schema convention) and must filter deleted_at IS NULL on all user-facing queries, treating soft-deleted tasks as permanently gone with no recovery path exposed.
- A task's user_id and tenant_id must correspond to the same user/tenant pair; application code must enforce this on insert.
- Tags are stored in the task_tags table; deduplication of tags per task is enforced by the UNIQUE constraint on (task_id, tag_value) in task_tags.

**Out of scope:**
- Edit history, version tracking, or audit log of previous field values.
- File or binary attachments.
- Assignment to other users.
- Custom or user-defined status values beyond the three fixed enums.
- Code syntax validation or linting of notes content.
- Task ordering or position fields for manual drag-and-drop reordering.

---

---
### `task_tags`
**Purpose:** Stores the individual tag strings associated with each task, with one row per unique tag per task.

| Column | Type | Nullable | Default | Constraint |
|--------|------|----------|---------|------------|
| id | UUID | NO | gen_random_uuid() | PK |
| tenant_id | UUID | NO |  | FK → tenants(id) |
| task_id | UUID | NO |  | FK → tasks(id) |
| tag_value | TEXT | NO |  | CHECK (char_length(tag_value) >= 1 AND char_length(tag_value) <= 50) |
| created_at | TIMESTAMPTZ | NO | NOW() |  |
| updated_at | TIMESTAMPTZ | NO | NOW() |  |
| deleted_at | TIMESTAMPTZ | YES |  |  |

**Indexes:**
- CREATE UNIQUE INDEX idx_task_tags_task_id_tag_value ON task_tags(task_id, tag_value) WHERE deleted_at IS NULL
- CREATE INDEX idx_task_tags_task_id ON task_tags(task_id) WHERE deleted_at IS NULL
- CREATE INDEX idx_task_tags_tenant_id ON task_tags(tenant_id)

**Relationships:**
- tenant_id REFERENCES tenants(id) ON DELETE CASCADE
- task_id REFERENCES tasks(id) ON DELETE CASCADE

**Invariants:**
- tag_value must not be empty or whitespace-only; application code strips and rejects empty/whitespace tags before insert.
- tag_value max length is 50 characters; enforced by CHECK constraint and application code.
- A task must not have more than 20 active (non-deleted) tags; application code must count existing active tags before insert and reject if the limit would be exceeded.
- Tag deduplication per task is enforced by the partial UNIQUE index on (task_id, tag_value) where deleted_at IS NULL; application code must handle the resulting constraint violation gracefully.
- When tags are cleared (empty array submitted), application code soft-deletes all active task_tags rows for that task_id.
- task_tags.tenant_id must match the parent task's tenant_id; enforced by application code on insert.

**Out of scope:**
- Global or cross-task tag taxonomy — tags have no identity beyond their string value on a specific task.
- Tag renaming across all tasks simultaneously.
- Tag usage counts or analytics.
- Filtering or searching tasks by tag (out of scope per F-006).

---
