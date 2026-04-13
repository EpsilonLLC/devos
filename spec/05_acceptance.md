# Acceptance criteria

---
### AC-F001: User Authentication
**Feature:** F-001
**Status:** required

**Done criteria:**
- [ ] POST /api/v1/auth/signup with valid unique email and password ≥8 chars returns 200 with user.id (UUID), user.email, user.created_at (ISO 8601), and non-empty token string
- [ ] POST /api/v1/auth/login with correct email and password returns 200 with user object and non-empty token string
- [ ] GET /api/v1/auth/session with valid Bearer token returns 200 with user.id, user.email, user.created_at
- [ ] POST /api/v1/auth/signup with already-registered email returns 409 with code EMAIL_ALREADY_EXISTS
- [ ] GET /api/v1/auth/session with missing or invalid Authorization header returns 401 with code UNAUTHORIZED

**Behavioral tests:**
```python
def test_signup_valid_credentials_returns_user_and_token():
    """POST /api/v1/auth/signup with a unique email and password ≥8 chars returns 200 with user.id UUID, user.email matching input, and a non-empty token string."""
    ...

def test_signup_duplicate_email_returns_409_conflict():
    """POST /api/v1/auth/signup with an already-registered email returns 409 with code EMAIL_ALREADY_EXISTS and no new user record created."""
    ...

def test_login_correct_credentials_returns_token():
    """POST /api/v1/auth/login with a known email and correct password returns 200 with a non-empty token string and valid user object."""
    ...

def test_session_endpoint_rejects_missing_auth_header():
    """GET /api/v1/auth/session without an Authorization header returns 401 with code UNAUTHORIZED."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| User submits the login form with a correctly formatted email that exists in the database but enters the wrong password: display 'Incorrect password.' error inline, do not redirect, do not lock the account. | `test_login_wrong_password_returns_401_invalid_credentials` |
| User submits the signup form with an email that is already registered: display 'An account with this email already exists.' error inline, do not create a duplicate record. | `test_signup_duplicate_email_returns_409_conflict` |
| User submits either form with the email field empty or the password field empty: display an inline field-level error ('This field is required.') adjacent to the empty field before any network request is made. | `test_signup_and_login_empty_fields_return_400_validation_error` |
| User submits a signup password shorter than 8 characters: display an inline error 'Password must be at least 8 characters.' before any network request is made. | `test_signup_short_password_returns_400_validation_error` |
| User submits a login or signup form but the database is unreachable at the moment of the request: display a page-level error message 'Something went wrong. Please try again.' and keep the form visible with the email field pre-filled. | `test_signup_db_unreachable_returns_500_internal_error` |
| User navigates directly to the task dashboard URL while holding a valid session token: system allows access and renders the dashboard (no re-authentication required). | `test_session_valid_token_allows_dashboard_access` |
| User navigates directly to the task dashboard URL without a valid session token: system redirects the user to the login page. | `test_session_missing_token_redirects_to_login` |

---

---
### AC-F002: Task Creation
**Feature:** F-002
**Status:** required

**Done criteria:**
- [ ] POST /api/v1/tasks with valid authenticated request and non-empty title returns 201 with task.id (UUID), task.status='to-do', task.priority='medium', task.created_at (ISO 8601), and task.user_id matching the authenticated user
- [ ] POST /api/v1/tasks with optional status='in-progress', priority='high', tags=['bug'], and notes='text' returns 201 with all submitted fields reflected in the task object
- [ ] POST /api/v1/tasks with duplicate tags ['bug','bug'] returns 201 with tags=['bug'] (deduplicated) in the persisted task
- [ ] POST /api/v1/tasks with title containing only whitespace returns 400 with code VALIDATION_ERROR referencing the title field
- [ ] POST /api/v1/tasks without a valid session token returns 401 with code UNAUTHORIZED and no task record is created

**Behavioral tests:**
```python
def test_create_task_minimal_title_returns_201_with_defaults():
    """POST /api/v1/tasks with only a title returns 201 with task.id UUID, status='to-do', priority='medium', tags=[], notes='', and a valid created_at timestamp."""
    ...

def test_create_task_rejects_whitespace_only_title_with_400():
    """POST /api/v1/tasks with title=' ' (whitespace only) returns 400 VALIDATION_ERROR and no task is written to the database."""
    ...

def test_create_task_deduplicates_tags_and_strips_empty_strings():
    """POST /api/v1/tasks with tags=['bug','bug',''] returns 201 with persisted tags=['bug'] — duplicates removed and empty strings stripped."""
    ...

def test_create_task_unauthenticated_returns_401():
    """POST /api/v1/tasks with no Authorization header returns 401 UNAUTHORIZED and no task record is persisted."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| Title is submitted as only whitespace characters (e.g., spaces or tabs): the system must trim the value, treat it as empty, and return a 400 error — it must not persist a whitespace-only title. | `test_create_task_rejects_whitespace_only_title_with_400` |
| Status or priority field is submitted with a value that differs only in letter casing (e.g., 'TO-DO', 'High'): the system must treat this as invalid and return a 400 error; it must not silently coerce casing. | `test_create_task_rejects_wrong_case_status_and_priority` |
| Tags array contains duplicate string values (e.g., ['bug', 'bug']): the system must deduplicate tags before persisting, storing only unique values. | `test_create_task_deduplicates_tags_and_strips_empty_strings` |
| Tags array contains an empty string as one of its elements (e.g., ['bug', '']): the system must strip empty-string entries from the array before persisting. | `test_create_task_deduplicates_tags_and_strips_empty_strings` |
| Notes field contains a very large string (e.g., a pasted code file exceeding a reasonable limit): the system must enforce a maximum character length on notes (defined by the implementation limit) and return a 400 error specifying that the notes field exceeds the limit if breached. | `test_create_task_rejects_notes_exceeding_10000_chars` |
| The database write fails (e.g., connection timeout or constraint violation): the system must return a 500 Internal Server Error, must not return a 201 or render a task in the UI, and must not leave a partial record in the database. | `test_create_task_db_failure_returns_500_no_partial_record` |
| The user's session expires between loading the form and submitting it: the system must return a 401 Unauthorized response, must not persist the task, and the UI must redirect the user to the login page. | `test_create_task_expired_session_returns_401` |

---

---
### AC-F003: Task Editing
**Feature:** F-003
**Status:** required

**Done criteria:**
- [ ] PATCH /api/v1/tasks/{id} with valid authenticated request and updated title returns 200 with full task object including updated title and a new updated_at timestamp
- [ ] PATCH /api/v1/tasks/{id} with only unmodified field values still returns 200 with updated_at set to current UTC time and all current field values
- [ ] PATCH /api/v1/tasks/{id} with tags=[] returns 200 with task.tags=[] confirming all tags cleared
- [ ] PATCH /api/v1/tasks/{id} with title='' or whitespace-only title returns 400 with code VALIDATION_ERROR referencing the title field
- [ ] PATCH /api/v1/tasks/{id} where the task ID belongs to a different user returns 403 with code FORBIDDEN and no fields are modified

**Behavioral tests:**
```python
def test_edit_task_updates_title_and_returns_full_task_object():
    """PATCH /api/v1/tasks/{id} with a new non-empty title returns 200 with the updated title reflected in the task object and updated_at advanced."""
    ...

def test_edit_task_rejects_empty_title_with_400_validation_error():
    """PATCH /api/v1/tasks/{id} with title='' returns 400 VALIDATION_ERROR and the task title remains unchanged in the database."""
    ...

def test_edit_task_forbidden_when_task_owned_by_different_user():
    """PATCH /api/v1/tasks/{id} where the task belongs to a different user returns 403 FORBIDDEN without revealing task details."""
    ...

def test_edit_task_no_changes_still_updates_updated_at_timestamp():
    """PATCH /api/v1/tasks/{id} with all field values identical to current stored values returns 200 with updated_at set to current UTC time."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| User attempts to set the task title to an empty string or whitespace only — system rejects with a 400 error specifying that title cannot be blank. | `test_edit_task_rejects_empty_title_with_400_validation_error` |
| User submits a status value not in the allowed set (e.g., 'blocked') — system rejects with a 400 error listing the valid status options. | `test_edit_task_rejects_invalid_status_value_with_400` |
| User attempts to edit a task that has already been deleted — system returns a 404 Not Found error. | `test_edit_task_returns_404_for_deleted_task` |
| User submits an edit for a task ID that belongs to a different user — system returns a 403 Forbidden error and does not reveal that the task exists. | `test_edit_task_forbidden_when_task_owned_by_different_user` |
| User submits an edit with no fields changed (all submitted values are identical to current stored values) — system still writes the updated_at timestamp and returns a 200 OK with the current task state. | `test_edit_task_no_changes_still_updates_updated_at_timestamp` |
| Two concurrent edit requests arrive for the same task from the same user — the system processes them sequentially; the last write wins and is persisted. | `test_edit_task_concurrent_requests_last_write_wins` |
| The tags field is submitted as an empty array — the system clears all existing tags on the task and persists the empty state. | `test_edit_task_empty_tags_array_clears_all_tags` |

---

---
### AC-F004: Task Deletion
**Feature:** F-004
**Status:** required

**Done criteria:**
- [ ] DELETE /api/v1/tasks/{id} with valid authenticated request and owned task ID returns 200 with message='Task deleted successfully' and id matching the deleted task UUID
- [ ] DELETE /api/v1/tasks/{id} succeeds and subsequent GET /api/v1/tasks/{id} returns 404 NOT_FOUND confirming permanent inaccessibility
- [ ] DELETE /api/v1/tasks/{id} with no Authorization header returns 401 with code UNAUTHORIZED and the task record remains intact
- [ ] DELETE /api/v1/tasks/{id} where the task belongs to a different user returns 403 with code FORBIDDEN and the task record is not deleted
- [ ] DELETE /api/v1/tasks/{id} with a non-UUID malformed task ID returns 400 with code VALIDATION_ERROR

**Behavioral tests:**
```python
def test_delete_task_owned_by_user_returns_200_and_task_inaccessible():
    """DELETE /api/v1/tasks/{id} for a task owned by the authenticated user returns 200 with the deleted task id; subsequent GET returns 404."""
    ...

def test_delete_task_unauthenticated_returns_401():
    """DELETE /api/v1/tasks/{id} without a valid Authorization header returns 401 UNAUTHORIZED and the task remains in the database."""
    ...

def test_delete_task_forbidden_for_another_users_task():
    """DELETE /api/v1/tasks/{id} targeting a task owned by a different user returns 403 FORBIDDEN and the task record is not modified."""
    ...

def test_delete_task_malformed_or_missing_id_returns_400():
    """DELETE /api/v1/tasks/{id} with a non-UUID path segment returns 400 VALIDATION_ERROR; DELETE without a task ID path segment returns 400 VALIDATION_ERROR."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| If the task ID is not provided in the request, the system returns a 400 Bad Request error and does not attempt any deletion. | `test_delete_task_malformed_or_missing_id_returns_400` |
| If the task ID is provided but references a task that has already been deleted (double-delete), the system returns a 404 Not Found error. | `test_delete_task_already_deleted_returns_404` |
| If the task belongs to another user, the system returns a 403 Forbidden error and does not delete the task, even if the requesting user is otherwise authenticated. | `test_delete_task_forbidden_for_another_users_task` |
| If the database write fails during deletion, the system returns a 500 Internal Server Error, does not update the UI, and leaves the task record intact. | `test_delete_task_db_failure_returns_500_task_record_intact` |
| If the API request is made with a malformed task ID (e.g., wrong type or format), the system returns a 400 Bad Request error. | `test_delete_task_malformed_or_missing_id_returns_400` |

---

---
### AC-F005: Task Status Tracking
**Feature:** F-005
**Status:** required

**Done criteria:**
- [ ] PATCH /api/v1/tasks/{id}/status with valid authenticated request and status='done' returns 200 with task.status='done' and status_changed_at set to current UTC ISO 8601 timestamp
- [ ] PATCH /api/v1/tasks/{id}/status with a status value identical to the current stored status returns 200 with the current task state and does not perform a database write
- [ ] PATCH /api/v1/tasks/{id}/status for an owned task where status transitions from 'done' back to 'to-do' returns 200 confirming no transition-order enforcement
- [ ] PATCH /api/v1/tasks/{id}/status with status='DONE' (wrong casing) returns 400 with code VALIDATION_ERROR listing valid status options
- [ ] PATCH /api/v1/tasks/{id}/status where the task belongs to a different user returns 403 with code FORBIDDEN and status is not modified

**Behavioral tests:**
```python
def test_status_update_valid_transition_updates_status_changed_at():
    """PATCH /api/v1/tasks/{id}/status with a valid new status returns 200 with task.status reflecting the new value and status_changed_at updated to current UTC time."""
    ...

def test_status_update_same_value_returns_200_no_db_write():
    """PATCH /api/v1/tasks/{id}/status with the same status as currently stored returns 200 with unchanged task state and no database write performed."""
    ...

def test_status_update_invalid_enum_value_returns_400():
    """PATCH /api/v1/tasks/{id}/status with status='blocked' or status='DONE' returns 400 VALIDATION_ERROR with valid enum options listed."""
    ...

def test_status_update_forbidden_for_another_users_task():
    """PATCH /api/v1/tasks/{id}/status for a task owned by a different user returns 403 FORBIDDEN and task status is not changed."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| If the submitted status value is not one of 'to-do', 'in-progress', or 'done', the system rejects the request with a 400 error and returns a message listing the valid status options; no database write occurs. | `test_status_update_invalid_enum_value_returns_400` |
| If the task ID does not exist in the system, the system returns a 404 error and no update is performed. | `test_status_update_nonexistent_task_returns_404` |
| If the authenticated user does not own the task matching the provided task ID, the system returns a 403 error and no update is performed. | `test_status_update_forbidden_for_another_users_task` |
| If the user submits a status value that is identical to the task's current status, the system accepts the request, performs no database write, and returns the current task data as a success response. | `test_status_update_same_value_returns_200_no_db_write` |
| If the database write fails (e.g., connection error), the system returns a 500 error, does not update the UI, and the task retains its previous status value. | `test_status_update_db_failure_returns_500_status_unchanged` |
| If the user's session expires between loading the UI and submitting the status change, the system rejects the request with a 401 error and redirects the user to the login page. | `test_status_update_expired_session_returns_401` |

---

---
### AC-F006: Personal Task Dashboard
**Feature:** F-006
**Status:** required

**Done criteria:**
- [ ] GET /api/v1/tasks with valid authenticated session returns 200 with tasks array sorted by status (to-do → in-progress → done) then by priority (high → medium → low), with notes_preview truncated to 200 chars and total integer matching array length
- [ ] GET /api/v1/tasks for a user with zero tasks returns 200 with tasks=[] and total=0 (empty-state scenario)
- [ ] GET /api/v1/tasks/{id} returns 200 with full task object including complete notes string up to 10,000 characters and all fields (id, user_id, tenant_id, title, status, priority, tags, notes, status_changed_at, created_at, updated_at)
- [ ] GET /api/v1/tasks with no valid session token returns 401 with code UNAUTHORIZED
- [ ] GET /api/v1/tasks/{id} for a task belonging to a different user returns 403 with code FORBIDDEN

**Behavioral tests:**
```python
def test_dashboard_tasks_sorted_by_status_then_priority():
    """GET /api/v1/tasks returns 200 with tasks ordered to-do before in-progress before done, and within each group high priority before medium before low."""
    ...

def test_dashboard_empty_state_returns_empty_tasks_array():
    """GET /api/v1/tasks for a user with no tasks returns 200 with tasks=[] and total=0."""
    ...

def test_dashboard_task_detail_returns_full_notes_not_truncated():
    """GET /api/v1/tasks/{id} returns 200 with full notes string content whereas GET /api/v1/tasks returns only a notes_preview truncated at 200 chars."""
    ...

def test_dashboard_unauthenticated_request_returns_401():
    """GET /api/v1/tasks without a valid Authorization header returns 401 UNAUTHORIZED."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| If the authenticated user has no tasks yet, the dashboard renders an empty-state view (step 3) rather than an empty list or error. | `test_dashboard_empty_state_returns_empty_tasks_array` |
| If the data store query fails or times out, the dashboard displays an inline error message (e.g., 'Could not load tasks. Please try again.') and does not render a blank or broken layout. | `test_dashboard_db_failure_returns_500_internal_error` |
| If a task has no priority value set, it is sorted after all tasks with an explicit priority within its status group. | `test_dashboard_task_without_priority_sorted_last_within_status_group` |
| If a task has no tags, the tags area for that entry is omitted or left blank — no placeholder label is shown. | `test_dashboard_task_with_no_tags_returns_empty_tags_array` |
| If the user's session expires between page load and a subsequent navigation action on the dashboard, the system redirects to the login page rather than displaying a partial or broken dashboard. | `test_dashboard_expired_session_returns_401_on_navigation` |
| If a task's notes/code snippet field is very long, only a truncated preview is shown on the dashboard card; the full content is accessible only in the task detail view. | `test_dashboard_task_detail_returns_full_notes_not_truncated` |

---

---
### AC-F007: Developer-Friendly Task Properties
**Feature:** F-007
**Status:** required

**Done criteria:**
- [ ] PATCH /api/v1/tasks/{id}/properties with valid authenticated request containing priority='high', tags=['backend','perf'], and notes='code block' returns 200 with full task object reflecting all three updated fields and updated_at advanced
- [ ] PATCH /api/v1/tasks/{id}/properties with tags=[] returns 200 with task.tags=[] confirming all tags cleared and no other fields modified
- [ ] PATCH /api/v1/tasks/{id}/properties with notes containing backticks, special characters, and multi-line code blocks returns 200 with notes stored byte-for-byte as submitted
- [ ] PATCH /api/v1/tasks/{id}/properties with priority='urgent' (not in allowed enum) returns 400 with code VALIDATION_ERROR naming the invalid value and listing accepted values; task is not modified
- [ ] PATCH /api/v1/tasks/{id}/properties targeting a task owned by a different user returns 404 with code NOT_FOUND (not 403) to avoid disclosing task existence

**Behavioral tests:**
```python
def test_properties_update_priority_tags_notes_returns_full_task():
    """PATCH /api/v1/tasks/{id}/properties with all three developer properties returns 200 with the full updated task object and updated_at set to current UTC time."""
    ...

def test_properties_update_rejects_invalid_priority_with_400():
    """PATCH /api/v1/tasks/{id}/properties with priority='urgent' returns 400 VALIDATION_ERROR identifying the invalid value; the task record is unchanged."""
    ...

def test_properties_update_notes_stored_verbatim_with_special_chars():
    """PATCH /api/v1/tasks/{id}/properties with notes containing backticks, newlines, and special characters returns 200 with notes stored exactly as submitted."""
    ...

def test_properties_update_another_users_task_returns_404_not_403():
    """PATCH /api/v1/tasks/{id}/properties for a task belonging to a different user returns 404 NOT_FOUND, not 403, to avoid disclosing task existence."""
    ...

```

**Edge case coverage required:**
| Edge case | Test name |
|-----------|-----------|
| User submits a priority value that is not one of the defined levels (e.g., sends 'urgent' when only low/medium/high/critical are valid): system returns a validation error naming the invalid value and listing accepted values; task is not updated. | `test_properties_update_rejects_invalid_priority_with_400` |
| User submits an empty tags array: system interprets this as clearing all tags on the task and persists an empty tag list. | `test_properties_update_empty_tags_array_clears_all_tags` |
| User submits a tag string that is an empty string or contains only whitespace: system rejects that individual tag with a validation error and does not save any part of the update. | `test_properties_update_whitespace_only_tag_returns_400_no_partial_save` |
| User submits a notes/code snippet that exceeds 10,000 characters: system returns a validation error specifying the character limit; task is not updated. | `test_properties_update_notes_exceeding_10000_chars_returns_400` |
| User submits more than 20 tags: system returns a validation error specifying the maximum tag count; task is not updated. | `test_properties_update_more_than_20_tags_returns_400` |
| User submits a request with no developer-specific property fields at all (empty body or body with only unrecognized fields): system returns a validation error indicating at least one valid property must be provided. | `test_properties_update_empty_body_returns_400_validation_error` |
| User submits a request for a task that exists but belongs to a different user: system returns a not-found error (not a forbidden error) to avoid disclosing task existence to unauthorized users. | `test_properties_update_another_users_task_returns_404_not_403` |
| Two concurrent requests update different properties on the same task simultaneously: the system applies both updates without data loss — the last write per individual field wins, but neither request silently drops the other field's change. | `test_properties_update_concurrent_requests_no_data_loss` |
| User submits a notes/code snippet containing special characters, backticks, or multi-line code blocks: the system stores the content exactly as submitted without stripping or escaping characters. | `test_properties_update_notes_stored_verbatim_with_special_chars` |

---
