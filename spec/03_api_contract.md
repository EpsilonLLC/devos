# API contract

## Conventions (locked)
- Base path: `/api/v1`
- Auth: Bearer token in Authorization header (all unless marked [public])
- All requests: Content-Type: application/json
- Timestamps: ISO 8601 UTC
- IDs: UUID strings

## Error envelope (locked — every error response)
```json
{
  "error": "human_readable_message",
  "code": "MACHINE_READABLE_CODE",
  "detail": {}
}
```

## Standard error codes
| HTTP | Code | Meaning |
|------|------|---------|
| 400 | VALIDATION_ERROR | Request body invalid |
| 401 | UNAUTHORIZED | Missing or invalid token |
| 403 | FORBIDDEN | Insufficient permission |
| 404 | NOT_FOUND | Resource does not exist |
| 409 | CONFLICT | State conflict |
| 422 | UNPROCESSABLE | Valid syntax, invalid semantics |
| 429 | RATE_LIMITED | Too many requests |
| 500 | INTERNAL_ERROR | Server fault |

---
### `POST /api/v1/auth/signup` [public]
**Purpose:** Register a new user account, create an associated tenant record, issue a session token, and return the new user object.
**Feature:** F-001

**Request body:**
```json
{
  "email": "string (required, must contain '@' and a domain)",
  "password": "string (required, minimum 8 characters)"
}
```

**Response 200:**
```json
{
  "user": {
    "id": "uuid",
    "email": "string",
    "created_at": "ISO 8601 UTC string"
  },
  "token": "string (opaque session token; also set as HTTP-only cookie)"
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| Email already registered by an active account | 409 | EMAIL_ALREADY_EXISTS |
| Email field is empty or missing | 400 | VALIDATION_ERROR |
| Password field is empty or missing | 400 | VALIDATION_ERROR |
| Email does not match valid email format | 400 | VALIDATION_ERROR |
| Password is shorter than 8 characters | 400 | VALIDATION_ERROR |
| Database is unreachable or write fails | 500 | INTERNAL_ERROR |

**Side effects:**
- Creates a new record in the tenants table
- Creates a new record in the users table with hashed password, linked to the new tenant, within the same atomic transaction
- Creates a new record in the sessions table with a hashed token and expiry
- Sets a secure HTTP-only cookie containing the raw session token on the response

**Out of scope:**
- Does not send a verification email — account is active immediately
- Does not support OAuth or third-party login providers
- Does not enforce account lockout or rate limiting

---

---
### `POST /api/v1/auth/login` [public]
**Purpose:** Authenticate an existing user with email and password, issue a session token, and return the user object.
**Feature:** F-001

**Request body:**
```json
{
  "email": "string (required)",
  "password": "string (required)"
}
```

**Response 200:**
```json
{
  "user": {
    "id": "uuid",
    "email": "string",
    "created_at": "ISO 8601 UTC string"
  },
  "token": "string (opaque session token; also set as HTTP-only cookie)"
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| Email field is empty or missing | 400 | VALIDATION_ERROR |
| Password field is empty or missing | 400 | VALIDATION_ERROR |
| No account found with the submitted email | 401 | INVALID_CREDENTIALS |
| Account found but password does not match | 401 | INVALID_CREDENTIALS |
| Database is unreachable | 500 | INTERNAL_ERROR |

**Side effects:**
- Creates a new record in the sessions table with a hashed token and expiry
- Sets a secure HTTP-only cookie containing the raw session token on the response

**Out of scope:**
- Does not differentiate between 'no account' and 'wrong password' in the error message exposed to the client (both return INVALID_CREDENTIALS) — note: F-001 specifies distinct UI messages; the API code is INVALID_CREDENTIALS in both cases
- Does not implement account lockout after repeated failures
- Does not support OAuth or SSO
- Does not handle password reset

---

---
### `GET /api/v1/auth/session`
**Purpose:** Validate the current session token and return the authenticated user object. Used by the web client to check session state on navigation.
**Feature:** F-001

**Request body:** None

**Response 200:**
```json
{
  "user": {
    "id": "uuid",
    "email": "string",
    "created_at": "ISO 8601 UTC string"
  }
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No session token present or token is invalid/expired | 401 | UNAUTHORIZED |

**Side effects:**
- None

**Out of scope:**
- Does not refresh or extend the session token
- Does not return any task data

---

---
### `POST /api/v1/tasks`
**Purpose:** Create a new task record owned by the authenticated user and return the full created task object.
**Feature:** F-002

**Request body:**
```json
{
  "title": "string (required, non-empty after trimming whitespace)",
  "status": "string (optional, enum: 'to-do' | 'in-progress' | 'done', default: 'to-do', case-sensitive)",
  "priority": "string (optional, enum: 'low' | 'medium' | 'high', default: 'medium', case-sensitive)",
  "tags": "array of strings (optional, default: [], each tag max 50 chars, max 20 tags, empty strings stripped, duplicates deduplicated)",
  "notes": "string (optional, default: '', max 10,000 characters, stored verbatim)"
}
```

**Response 200:**
```json
{
  "task": {
    "id": "uuid",
    "user_id": "uuid",
    "tenant_id": "uuid",
    "title": "string",
    "status": "string ('to-do' | 'in-progress' | 'done')",
    "priority": "string ('low' | 'medium' | 'high')",
    "tags": "array of strings",
    "notes": "string",
    "status_changed_at": "ISO 8601 UTC string or null",
    "created_at": "ISO 8601 UTC string",
    "updated_at": "ISO 8601 UTC string"
  }
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Title field is missing, empty, or whitespace-only | 400 | VALIDATION_ERROR |
| Status value is not one of the allowed enum values (case-sensitive) | 400 | VALIDATION_ERROR |
| Priority value is not one of the allowed enum values (case-sensitive) | 400 | VALIDATION_ERROR |
| Notes field exceeds 10,000 characters | 400 | VALIDATION_ERROR |
| A tag in the tags array exceeds 50 characters | 400 | VALIDATION_ERROR |
| More than 20 unique tags are submitted | 400 | VALIDATION_ERROR |
| Database write fails or connection timeout | 500 | INTERNAL_ERROR |

**Side effects:**
- Creates a new record in the tasks table with user_id and tenant_id set to the authenticated user's values, status defaulting to 'to-do', priority defaulting to 'medium', created_at and updated_at set to current UTC time
- Creates one record per unique non-empty tag in the task_tags table linked to the new task
- Empty-string tags are stripped before persistence; duplicate tags are deduplicated before persistence

**Out of scope:**
- Does not support bulk task creation
- Does not send any notification on creation
- Does not import tasks from external tools
- Does not support file attachments
- Does not validate or lint code content in the notes field

---

---
### `GET /api/v1/tasks`
**Purpose:** Retrieve all non-deleted tasks belonging to the authenticated user, sorted by status (to-do first, in-progress second, done third) then by priority (high first, medium second, low third) for the personal task dashboard.
**Feature:** F-006

**Request body:** None

**Response 200:**
```json
{
  "tasks": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "tenant_id": "uuid",
      "title": "string",
      "status": "string ('to-do' | 'in-progress' | 'done')",
      "priority": "string ('low' | 'medium' | 'high')",
      "tags": "array of strings",
      "notes_preview": "string (truncated to first 200 characters of notes; empty string if notes is empty)",
      "status_changed_at": "ISO 8601 UTC string or null",
      "created_at": "ISO 8601 UTC string",
      "updated_at": "ISO 8601 UTC string"
    }
  ],
  "total": "integer (count of tasks returned)"
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Database query fails or times out | 500 | INTERNAL_ERROR |

**Side effects:**
- None

**Out of scope:**
- Does not support filtering by status, priority, or tag
- Does not support search
- Does not return tasks belonging to other users
- Does not return full notes content — only a truncated preview; use GET /api/v1/tasks/{id} for full content
- Does not support pagination in this version

---

---
### `GET /api/v1/tasks/{id}`
**Purpose:** Retrieve the full detail of a single task owned by the authenticated user, including the complete notes field.
**Feature:** F-006

**Request body:** None

**Response 200:**
```json
{
  "task": {
    "id": "uuid",
    "user_id": "uuid",
    "tenant_id": "uuid",
    "title": "string",
    "status": "string ('to-do' | 'in-progress' | 'done')",
    "priority": "string ('low' | 'medium' | 'high')",
    "tags": "array of strings",
    "notes": "string (full content, up to 10,000 characters)",
    "status_changed_at": "ISO 8601 UTC string or null",
    "created_at": "ISO 8601 UTC string",
    "updated_at": "ISO 8601 UTC string"
  }
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Task ID format is invalid (not a UUID) | 400 | VALIDATION_ERROR |
| Task does not exist or has been soft-deleted | 404 | NOT_FOUND |
| Task exists but belongs to a different user | 403 | FORBIDDEN |
| Database query fails | 500 | INTERNAL_ERROR |

**Side effects:**
- None

**Out of scope:**
- Does not return edit history or audit log
- Does not expose tasks owned by other users

---

---
### `PATCH /api/v1/tasks/{id}`
**Purpose:** Update one or more fields on an existing task owned by the authenticated user. Supports updating title, status, priority, tags, and notes. Fields not included in the request body are left unchanged.
**Feature:** F-003

**Request body:**
```json
{
  "title": "string (optional, non-empty after trimming whitespace)",
  "status": "string (optional, enum: 'to-do' | 'in-progress' | 'done', case-sensitive)",
  "priority": "string (optional, enum: 'low' | 'medium' | 'high', case-sensitive)",
  "tags": "array of strings (optional, each tag max 50 chars, max 20 tags total on task, empty strings stripped, duplicates deduplicated; empty array clears all tags)",
  "notes": "string (optional, max 10,000 characters, stored verbatim including special characters and code blocks)"
}
```

**Response 200:**
```json
{
  "task": {
    "id": "uuid",
    "user_id": "uuid",
    "tenant_id": "uuid",
    "title": "string",
    "status": "string ('to-do' | 'in-progress' | 'done')",
    "priority": "string ('low' | 'medium' | 'high')",
    "tags": "array of strings",
    "notes": "string",
    "status_changed_at": "ISO 8601 UTC string or null",
    "created_at": "ISO 8601 UTC string",
    "updated_at": "ISO 8601 UTC string"
  }
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Task ID format is invalid (not a UUID) | 400 | VALIDATION_ERROR |
| Task does not exist or has been soft-deleted | 404 | NOT_FOUND |
| Task exists but belongs to a different user | 403 | FORBIDDEN |
| Title is provided but is empty string or whitespace-only | 400 | VALIDATION_ERROR |
| Status value is not one of the allowed enum values (case-sensitive) | 400 | VALIDATION_ERROR |
| Priority value is not one of the allowed enum values (case-sensitive) | 400 | VALIDATION_ERROR |
| Notes field exceeds 10,000 characters | 400 | VALIDATION_ERROR |
| A tag in the tags array exceeds 50 characters | 400 | VALIDATION_ERROR |
| A tag in the tags array is an empty string or whitespace-only | 400 | VALIDATION_ERROR |
| Tags array would cause task to exceed 20 unique tags | 400 | VALIDATION_ERROR |
| Request body contains no recognized editable fields | 400 | VALIDATION_ERROR |
| Database write fails | 500 | INTERNAL_ERROR |

**Side effects:**
- Updates the matching tasks table record; updated_at is always set to current UTC time
- If status value changes, status_changed_at is updated to current UTC time
- If tags are included in the request: existing task_tags rows for the task are soft-deleted and replaced with the new set of unique non-empty tags; if an empty array is submitted, all task_tags rows for the task are soft-deleted
- Fields not included in the request body remain unchanged in the database

**Out of scope:**
- Does not create a new task — only modifies an existing one
- Does not support bulk editing of multiple tasks
- Does not maintain edit history or version tracking
- Does not reassign the task to another user
- Does not integrate with external tools

---

---
### `DELETE /api/v1/tasks/{id}`
**Purpose:** Permanently delete (soft-delete) a task owned by the authenticated user. The task is immediately inaccessible and treated as gone with no recovery path.
**Feature:** F-004

**Request body:** None

**Response 200:**
```json
{
  "message": "string ('Task deleted successfully')",
  "id": "uuid (the ID of the deleted task)"
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Task ID is missing from the path | 400 | VALIDATION_ERROR |
| Task ID format is invalid (not a UUID) | 400 | VALIDATION_ERROR |
| Task does not exist or has already been deleted | 404 | NOT_FOUND |
| Task exists but belongs to a different user | 403 | FORBIDDEN |
| Database write fails during deletion | 500 | INTERNAL_ERROR |

**Side effects:**
- Sets deleted_at = NOW() on the tasks record (soft delete); the task is excluded from all user-facing queries thereafter
- All associated task_tags rows for the task become inaccessible via cascade or application-level filter

**Out of scope:**
- Does not provide soft-delete recovery or undo — deletion is final from the user's perspective
- Does not support bulk deletion of multiple tasks
- Does not send any notification or confirmation outside the API response
- Does not allow deletion of tasks owned by other users

---

---
### `PATCH /api/v1/tasks/{id}/status`
**Purpose:** Update only the status field of an existing task owned by the authenticated user. Provides a dedicated, lightweight endpoint for status-only changes driven by status selectors on the dashboard and task detail views.
**Feature:** F-005

**Request body:**
```json
{
  "status": "string (required, enum: 'to-do' | 'in-progress' | 'done', case-sensitive)"
}
```

**Response 200:**
```json
{
  "task": {
    "id": "uuid",
    "user_id": "uuid",
    "tenant_id": "uuid",
    "title": "string",
    "status": "string ('to-do' | 'in-progress' | 'done')",
    "priority": "string ('low' | 'medium' | 'high')",
    "tags": "array of strings",
    "notes": "string",
    "status_changed_at": "ISO 8601 UTC string or null",
    "created_at": "ISO 8601 UTC string",
    "updated_at": "ISO 8601 UTC string"
  }
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Task ID format is invalid (not a UUID) | 400 | VALIDATION_ERROR |
| Status field is missing from the request body | 400 | VALIDATION_ERROR |
| Status value is not one of the allowed enum values (case-sensitive) | 400 | VALIDATION_ERROR |
| Task does not exist or has been soft-deleted | 404 | NOT_FOUND |
| Task exists but belongs to a different user | 403 | FORBIDDEN |
| Database write fails | 500 | INTERNAL_ERROR |

**Side effects:**
- If the submitted status differs from the current value: updates the status column and sets status_changed_at = NOW() and updated_at = NOW() on the tasks record
- If the submitted status is identical to the current value: no database write is performed; current task state is returned as-is

**Out of scope:**
- Does not enforce status transition order (e.g., 'done' can move back to 'to-do')
- Does not support custom or user-defined status values
- Does not log a status change history or expose an audit trail
- Does not send notifications when status changes
- Does not support bulk status updates across multiple tasks

---

---
### `PATCH /api/v1/tasks/{id}/properties`
**Purpose:** Update one or more developer-specific properties (priority, tags, notes/code snippets) on an existing task without touching title or status. Provides a dedicated endpoint for the developer-friendly task properties feature.
**Feature:** F-007

**Request body:**
```json
{
  "priority": "string (optional, enum: 'low' | 'medium' | 'high', case-sensitive)",
  "tags": "array of strings (optional, each tag 1\u201350 chars, max 20 tags total, no empty/whitespace-only entries, empty array clears all tags)",
  "notes": "string (optional, max 10,000 characters, stored verbatim including special characters, backticks, and multi-line code blocks)"
}
```

**Response 200:**
```json
{
  "task": {
    "id": "uuid",
    "user_id": "uuid",
    "tenant_id": "uuid",
    "title": "string",
    "status": "string ('to-do' | 'in-progress' | 'done')",
    "priority": "string ('low' | 'medium' | 'high')",
    "tags": "array of strings",
    "notes": "string",
    "status_changed_at": "ISO 8601 UTC string or null",
    "created_at": "ISO 8601 UTC string",
    "updated_at": "ISO 8601 UTC string"
  }
}
```

**Response errors:**
| Scenario | HTTP | Code |
|----------|------|------|
| No valid session token / session expired | 401 | UNAUTHORIZED |
| Task ID format is invalid (not a UUID) | 400 | VALIDATION_ERROR |
| Request body contains none of the recognized developer-specific properties (priority, tags, notes) | 400 | VALIDATION_ERROR |
| Task does not exist, has been soft-deleted, or belongs to a different user | 404 | NOT_FOUND |
| Priority value is not one of the allowed enum values (case-sensitive) | 400 | VALIDATION_ERROR |
| A tag in the tags array is an empty string or whitespace-only | 400 | VALIDATION_ERROR |
| A tag in the tags array exceeds 50 characters | 400 | VALIDATION_ERROR |
| Tags array would cause task to exceed 20 unique tags | 400 | VALIDATION_ERROR |
| Notes field exceeds 10,000 characters | 400 | VALIDATION_ERROR |
| Any validation failure: no partial updates are applied | 400 | VALIDATION_ERROR |
| Database write fails | 500 | INTERNAL_ERROR |

**Side effects:**
- Updates priority and/or notes columns on the tasks record for submitted fields; updated_at set to current UTC time
- If tags are submitted: soft-deletes existing task_tags rows for the task and inserts new rows for each unique non-empty tag value
- If empty tags array is submitted: soft-deletes all existing task_tags rows for the task (clears all tags)
- Fields not included in the request body are left unchanged
- Notes content is stored exactly as submitted without character escaping or stripping

**Out of scope:**
- Does not create new tasks
- Does not update title or status — those are handled by PATCH /api/v1/tasks/{id} and PATCH /api/v1/tasks/{id}/status respectively
- Does not perform syntax highlighting or code execution on notes content
- Does not support file or binary attachments
- Does not expose task properties to other users
- Does not define or allow configuration of valid priority levels — those are system-defined constants

---
