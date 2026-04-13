# Functional specification

## Feature schema
Every feature uses this exact structure.

---
### F-001: User Authentication
**Status:** included
**Zone:** locked

**Trigger:** User submits a signup or login form via the web UI, or sends a POST request to the authentication API endpoint.

**Preconditions:**
- The user is not currently holding a valid authenticated session (i.e., no active session token exists in the browser for this app).
- The authentication API endpoint is reachable and the database is available to read/write user records.
- The submitted request contains a non-empty email field and a non-empty password field.

**Behavior:**
1. User navigates to the login/signup page. If a valid session token already exists, the system immediately redirects the user to the personal task dashboard and skips all remaining steps.
2. The system renders two clearly labeled entry points on the page: 'Log In' and 'Sign Up', each with an email input field and a password input field.
3. SIGNUP PATH — User fills in email and password and submits the Sign Up form. The system validates that: (a) the email matches standard email format (contains '@' and a domain), and (b) the password is at least 8 characters long.
4. SIGNUP PATH — If validation passes, the system checks the database for an existing account with the submitted email address. If a match is found, the system displays an inline error message: 'An account with this email already exists. Please log in.' No new record is created.
5. SIGNUP PATH — If no existing account is found, the system hashes the password using a secure one-way hashing algorithm and writes a new user record (email + hashed password) to the database.
6. SIGNUP PATH — Upon successful record creation, the system generates a session token, stores it as a secure HTTP-only cookie in the user's browser, and redirects the user to the personal task dashboard.
7. LOGIN PATH — User fills in email and password and submits the Log In form. The system looks up the user record by the submitted email address.
8. LOGIN PATH — If no user record exists for that email, the system displays an inline error message: 'No account found with that email address.' The form remains visible and no session is created.
9. LOGIN PATH — If a user record is found, the system hashes the submitted password and compares it to the stored hashed password. If they do not match, the system displays an inline error message: 'Incorrect password.' The form remains visible and no session is created.
10. LOGIN PATH — If the password comparison succeeds, the system generates a session token, stores it as a secure HTTP-only cookie in the user's browser, and redirects the user to the personal task dashboard.
11. The personal task dashboard displayed after successful login or signup must immediately show the user's existing tasks (or an empty state prompt if no tasks exist), with no additional onboarding screens or interstitial steps between authentication and the dashboard.

**Edge cases:**
- User submits the login form with a correctly formatted email that exists in the database but enters the wrong password: display 'Incorrect password.' error inline, do not redirect, do not lock the account.
- User submits the signup form with an email that is already registered: display 'An account with this email already exists.' error inline, do not create a duplicate record.
- User submits either form with the email field empty or the password field empty: display an inline field-level error ('This field is required.') adjacent to the empty field before any network request is made.
- User submits a signup password shorter than 8 characters: display an inline error 'Password must be at least 8 characters.' before any network request is made.
- User submits a login or signup form but the database is unreachable at the moment of the request: display a page-level error message 'Something went wrong. Please try again.' and keep the form visible with the email field pre-filled.
- User navigates directly to the task dashboard URL while holding a valid session token: system allows access and renders the dashboard (no re-authentication required).
- User navigates directly to the task dashboard URL without a valid session token: system redirects the user to the login page.

**Out of scope for this feature:**
- Password reset or 'forgot password' flows — no email recovery mechanism is implemented in this feature.
- OAuth or third-party login providers (e.g., GitHub SSO, Google) — only email/password authentication is supported.
- Email verification after signup — accounts are active immediately upon creation without any email confirmation step.
- Account lockout or rate-limiting after repeated failed login attempts.
- Session expiry, logout functionality, or session token refresh — these are separate concerns not covered by this feature.
- Any multi-user or team account structures — authentication is strictly per individual user.
- Mobile or non-web client authentication flows.
---

---
### F-002: Task Creation
**Status:** included
**Zone:** locked

**Trigger:** An authenticated user submits a task creation form via the web UI or sends a POST request to the task creation API endpoint.

**Preconditions:**
- The user has an active authenticated session (valid session token or auth credential is present and not expired).
- The incoming request includes at minimum a non-empty task title string.
- The user's account exists and is in good standing in the database.

**Behavior:**
1. The system receives the task creation request (form submission or API POST) and validates that the user's session is authenticated; if not, reject with a 401 Unauthorized response and do not proceed.
2. The system extracts the following fields from the request payload: title (required, string), status (optional, enum: 'to-do' | 'in-progress' | 'done', defaults to 'to-do' if omitted), priority (optional, enum: 'low' | 'medium' | 'high', defaults to 'medium' if omitted), tags (optional, array of strings, defaults to empty array if omitted), and notes (optional, string for free-text or code snippet content, defaults to empty string if omitted).
3. The system validates that the title field is present and is a non-empty string after trimming whitespace; if validation fails, return a 400 Bad Request response with a specific error message identifying the title field as missing or empty, and do not persist the record.
4. The system validates that status, if provided, matches one of the allowed enum values ('to-do', 'in-progress', 'done'); if not, return a 400 Bad Request response identifying the invalid status value.
5. The system validates that priority, if provided, matches one of the allowed enum values ('low', 'medium', 'high'); if not, return a 400 Bad Request response identifying the invalid priority value.
6. The system creates a new task record in the database with all validated fields, the authenticated user's ID as the owner, a system-generated unique task ID, and a created_at timestamp set to the current UTC time.
7. On successful database write, the system returns a 201 Created response containing the full task record (including the generated task ID and created_at timestamp).
8. The web UI immediately appends the newly created task to the user's task dashboard list without requiring a full page reload, reflecting the current status ('to-do' by default) and all submitted properties.
9. The task creation form is reset to its empty/default state after successful submission so the user can create another task immediately.

**Edge cases:**
- Title is submitted as only whitespace characters (e.g., spaces or tabs): the system must trim the value, treat it as empty, and return a 400 error — it must not persist a whitespace-only title.
- Status or priority field is submitted with a value that differs only in letter casing (e.g., 'TO-DO', 'High'): the system must treat this as invalid and return a 400 error; it must not silently coerce casing.
- Tags array contains duplicate string values (e.g., ['bug', 'bug']): the system must deduplicate tags before persisting, storing only unique values.
- Tags array contains an empty string as one of its elements (e.g., ['bug', '']): the system must strip empty-string entries from the array before persisting.
- Notes field contains a very large string (e.g., a pasted code file exceeding a reasonable limit): the system must enforce a maximum character length on notes (defined by the implementation limit) and return a 400 error specifying that the notes field exceeds the limit if breached.
- The database write fails (e.g., connection timeout or constraint violation): the system must return a 500 Internal Server Error, must not return a 201 or render a task in the UI, and must not leave a partial record in the database.
- The user's session expires between loading the form and submitting it: the system must return a 401 Unauthorized response, must not persist the task, and the UI must redirect the user to the login page.

**Out of scope for this feature:**
- This feature does not handle editing or updating an existing task — that is covered by the task editing feature.
- This feature does not assign tasks to other users or support any multi-user or collaboration context.
- This feature does not send any notification or reminder upon task creation.
- This feature does not create tasks by importing or syncing from external tools (GitHub issues, Jira tickets, etc.).
- This feature does not support bulk creation of multiple tasks in a single request.
- This feature does not support file attachments — only plain text or code snippet text in the notes field.
- This feature does not validate or lint code content entered into the notes field.
---

---
### F-003: Task Editing
**Status:** included
**Zone:** locked

**Trigger:** An authenticated user submits a task edit via the web form UI (e.g., clicking a Save/Update button on an open task) or via a direct API PATCH/PUT request to the task endpoint.

**Preconditions:**
- The user is authenticated — a valid session token or auth credential is present on the request.
- The task being edited exists in the system (task ID resolves to a record in the database).
- The task being edited belongs to the authenticated user.
- The request body contains at least one editable field with a valid non-empty value.

**Behavior:**
1. The system receives the edit submission (form POST or API request) containing the task ID and one or more updated fields: title (string, required if present — cannot be set to empty), status (one of: to-do, in-progress, done), priority (e.g., low, medium, high), tags (array of strings), or notes/code snippets (freeform text).
2. The system validates that the authenticated user owns the task matching the provided task ID; if not, it returns a 403 Forbidden error and halts processing.
3. The system validates that any provided field values conform to their type and allowed values (e.g., status must be one of the defined enum values, title must not be blank); if validation fails, it returns a 400 Bad Request error with a field-level error message and halts processing.
4. The system applies the provided field updates to the existing task record, leaving all unmentioned fields unchanged.
5. The system persists the updated task record to the database with a new `updated_at` timestamp.
6. The system returns the full updated task object (all fields) in the response (HTTP 200 OK for API; updated task state for web form).
7. The UI immediately reflects the updated field values in-place on the task view/dashboard without requiring a full page reload.

**Edge cases:**
- User attempts to set the task title to an empty string or whitespace only — system rejects with a 400 error specifying that title cannot be blank.
- User submits a status value not in the allowed set (e.g., 'blocked') — system rejects with a 400 error listing the valid status options.
- User attempts to edit a task that has already been deleted — system returns a 404 Not Found error.
- User submits an edit for a task ID that belongs to a different user — system returns a 403 Forbidden error and does not reveal that the task exists.
- User submits an edit with no fields changed (all submitted values are identical to current stored values) — system still writes the updated_at timestamp and returns a 200 OK with the current task state.
- Two concurrent edit requests arrive for the same task from the same user — the system processes them sequentially; the last write wins and is persisted.
- The tags field is submitted as an empty array — the system clears all existing tags on the task and persists the empty state.

**Out of scope for this feature:**
- Task creation — F-003 only modifies an existing task record; creating a new task is handled by a separate feature.
- Task deletion — removing a task entirely is not part of this feature.
- Editing tasks belonging to other users — no admin override or shared editing capability exists.
- Bulk editing multiple tasks in a single request.
- Edit history, version tracking, or undo functionality — no audit log of previous field values is maintained by this feature.
- Reassigning a task to another user — the app does not support multi-user collaboration.
- Integration with external tools (e.g., syncing edits to GitHub or Jira).
---

---
### F-004: Task Deletion
**Status:** included
**Zone:** locked

**Trigger:** An authenticated user submits a delete request for a specific task via a web form action (e.g., clicking a delete button on the task) or a direct API DELETE request targeting a task by its ID.

**Preconditions:**
- The user must be authenticated (valid session or auth token is present).
- The task identified by the provided task ID must exist in the database.
- The task identified by the provided task ID must belong to the authenticated user.

**Behavior:**
1. The system receives a delete request containing the task ID, either from a web form submission or an API call.
2. The system verifies the user is authenticated; if not, it rejects the request with an unauthorized error (401).
3. The system looks up the task by the provided task ID; if the task does not exist, it returns a not-found error (404).
4. The system verifies the authenticated user is the owner of the task; if not, it returns a forbidden error (403).
5. The system permanently deletes the task record (including its properties: status, priority, tags, notes/code snippets) from the database.
6. The system returns a success response (e.g., 200 OK or 204 No Content) confirming the deletion.
7. The UI removes the deleted task from the task dashboard view immediately, without requiring a full page reload, so the remaining task list reflects the current state.

**Edge cases:**
- If the task ID is not provided in the request, the system returns a 400 Bad Request error and does not attempt any deletion.
- If the task ID is provided but references a task that has already been deleted (double-delete), the system returns a 404 Not Found error.
- If the task belongs to another user, the system returns a 403 Forbidden error and does not delete the task, even if the requesting user is otherwise authenticated.
- If the database write fails during deletion, the system returns a 500 Internal Server Error, does not update the UI, and leaves the task record intact.
- If the API request is made with a malformed task ID (e.g., wrong type or format), the system returns a 400 Bad Request error.

**Out of scope for this feature:**
- Soft deletion or archiving — this feature permanently removes the task record with no recovery mechanism.
- Bulk deletion of multiple tasks in a single request.
- Undo or restore functionality after a task has been deleted.
- Deletion of tasks owned by other users (no admin override path exists in this feature).
- Any notifications or confirmations sent outside the UI (e.g., email receipts) upon deletion.
- Cascading effects on external tools or integrations (no such integrations exist in scope).
---

---
### F-005: Task Status Tracking
**Status:** included
**Zone:** locked

**Trigger:** An authenticated user submits a status change for an existing task via a web form interaction (e.g., clicking a status selector on the task card or task detail view) or a direct API request with a valid status value.

**Preconditions:**
- The user is authenticated (a valid session token or equivalent auth credential is present in the request).
- The task identified by the provided task ID exists in the system.
- The task identified by the provided task ID belongs to the authenticated user.
- The submitted status value is one of the defined allowed values: 'to-do', 'in-progress', or 'done'.

**Behavior:**
1. The system receives the status update request, containing the task ID and the new status value, via web form submission or API request.
2. The system verifies all preconditions: user authentication, task existence, task ownership, and validity of the submitted status value.
3. The system writes the new status value to the task record in the database, replacing the previous status value.
4. The system records the timestamp of the status change on the task record.
5. The system returns a success response (HTTP 200 with updated task data for API requests).
6. The UI immediately reflects the updated status on the task — the status indicator on both the task card (dashboard view) and the task detail view updates to display the new status without requiring a full page reload.

**Edge cases:**
- If the submitted status value is not one of 'to-do', 'in-progress', or 'done', the system rejects the request with a 400 error and returns a message listing the valid status options; no database write occurs.
- If the task ID does not exist in the system, the system returns a 404 error and no update is performed.
- If the authenticated user does not own the task matching the provided task ID, the system returns a 403 error and no update is performed.
- If the user submits a status value that is identical to the task's current status, the system accepts the request, performs no database write, and returns the current task data as a success response.
- If the database write fails (e.g., connection error), the system returns a 500 error, does not update the UI, and the task retains its previous status value.
- If the user's session expires between loading the UI and submitting the status change, the system rejects the request with a 401 error and redirects the user to the login page.

**Out of scope for this feature:**
- This feature does not define custom or user-created status values — only the three fixed statuses ('to-do', 'in-progress', 'done') are supported.
- This feature does not enforce or validate status transition order (e.g., it does not prevent moving a task from 'done' directly back to 'to-do').
- This feature does not send notifications or reminders when a status changes.
- This feature does not log or expose a full status change history or audit trail to the user.
- This feature does not support bulk status updates across multiple tasks in a single request.
- This feature does not sync status changes to any external tool (GitHub, Jira, Slack, etc.).
---

---
### F-006: Personal Task Dashboard
**Status:** included
**Zone:** locked

**Trigger:** An authenticated user successfully completes login or navigates to the root/home route of the web application

**Preconditions:**
- The user has an active authenticated session (valid session token or cookie is present)
- The user's account exists in the system and is not in a deleted or suspended state
- The task data store is reachable and able to return records for the authenticated user

**Behavior:**
1. On trigger, the system queries the data store for all tasks belonging to the authenticated user, filtering exclusively to that user's records.
2. The system retrieves each task's core properties: title, status (to-do / in-progress / done), priority, tags, and a truncated preview of notes/code snippets (if present).
3. The system renders a dashboard page displaying the retrieved tasks. If zero tasks exist, the dashboard displays an empty-state message (e.g., 'No tasks yet — create your first one') alongside a prominent 'New Task' action.
4. Tasks are grouped or sorted by status by default, presenting to-do tasks first, in-progress second, and done third within the rendered view.
5. Within each status group, tasks are ordered by priority (highest priority first) as the secondary sort key.
6. Each task entry in the dashboard displays at minimum: title, status badge, priority indicator, and any associated tags.
7. Each task entry is interactive — clicking or selecting a task navigates the user to that task's detail/edit view (as handled by the relevant task editing feature).
8. A 'New Task' creation affordance (button or equivalent control) is persistently visible on the dashboard without requiring any scrolling on initial load, satisfying the no-onboarding-friction requirement.
9. The dashboard reflects the current persisted state of all tasks; if the user returns to the dashboard after creating, editing, or deleting a task, the updated state is shown without requiring a manual page refresh.

**Edge cases:**
- If the authenticated user has no tasks yet, the dashboard renders an empty-state view (step 3) rather than an empty list or error.
- If the data store query fails or times out, the dashboard displays an inline error message (e.g., 'Could not load tasks. Please try again.') and does not render a blank or broken layout.
- If a task has no priority value set, it is sorted after all tasks with an explicit priority within its status group.
- If a task has no tags, the tags area for that entry is omitted or left blank — no placeholder label is shown.
- If the user's session expires between page load and a subsequent navigation action on the dashboard, the system redirects to the login page rather than displaying a partial or broken dashboard.
- If a task's notes/code snippet field is very long, only a truncated preview is shown on the dashboard card; the full content is accessible only in the task detail view.

**Out of scope for this feature:**
- This feature does not handle task creation, editing, or deletion — those are covered by their respective CRUD features.
- This feature does not provide filtering or searching tasks by tag, status, or priority — the dashboard displays all tasks with a fixed default sort only.
- This feature does not support multi-user or shared task views — the dashboard is strictly scoped to the authenticated user's own tasks.
- This feature does not send notifications or reminders about tasks shown on the dashboard.
- This feature does not render project-level views such as Gantt charts, roadmaps, or kanban boards.
- This feature does not integrate with or display data from any external tools (GitHub, Jira, Slack, etc.).
- This feature does not handle the authentication flow itself — it only activates after authentication is already complete.
---

---
### F-007: Developer-Friendly Task Properties
**Status:** included
**Zone:** locked

**Trigger:** An authenticated user submits a web form or API request to set or update one or more developer-specific properties (priority, tags, notes/code snippets) on an existing task.

**Preconditions:**
- The user is authenticated — a valid session or auth token exists for the request.
- The target task exists in the system and belongs to the authenticated user.
- The request body contains at least one of the three developer-specific properties: priority, tags, or notes/code snippets.

**Behavior:**
1. The system receives the request (web form submission or API call) containing one or more of: priority, tags, or notes/code snippet content, along with the target task identifier.
2. The system verifies the user is authenticated; if not, it rejects the request with an unauthorized error and stops processing.
3. The system looks up the task by its identifier and confirms it belongs to the authenticated user; if not found or not owned by the user, it returns a not-found error and stops processing.
4. The system validates each submitted property: priority must be one of the defined levels (e.g., low, medium, high, critical); tags must be non-empty strings, each with a maximum length of 50 characters, with a maximum of 20 tags per task; notes/code snippet content must be a string with a maximum length of 10,000 characters.
5. If any field fails validation, the system returns a descriptive error identifying which field failed and why, and makes no changes to the task.
6. The system applies all valid submitted property values to the task record, overwriting the previous values for those specific fields. Fields not included in the request are left unchanged.
7. The system persists the updated task record to the data store.
8. The system returns a success response containing the full updated task object, including all current property values.
9. The UI reflects the updated property values on the task immediately upon receiving the success response, without requiring a full page reload.

**Edge cases:**
- User submits a priority value that is not one of the defined levels (e.g., sends 'urgent' when only low/medium/high/critical are valid): system returns a validation error naming the invalid value and listing accepted values; task is not updated.
- User submits an empty tags array: system interprets this as clearing all tags on the task and persists an empty tag list.
- User submits a tag string that is an empty string or contains only whitespace: system rejects that individual tag with a validation error and does not save any part of the update.
- User submits a notes/code snippet that exceeds 10,000 characters: system returns a validation error specifying the character limit; task is not updated.
- User submits more than 20 tags: system returns a validation error specifying the maximum tag count; task is not updated.
- User submits a request with no developer-specific property fields at all (empty body or body with only unrecognized fields): system returns a validation error indicating at least one valid property must be provided.
- User submits a request for a task that exists but belongs to a different user: system returns a not-found error (not a forbidden error) to avoid disclosing task existence to unauthorized users.
- Two concurrent requests update different properties on the same task simultaneously: the system applies both updates without data loss — the last write per individual field wins, but neither request silently drops the other field's change.
- User submits a notes/code snippet containing special characters, backticks, or multi-line code blocks: the system stores the content exactly as submitted without stripping or escaping characters.

**Out of scope for this feature:**
- Creating a new task — this feature only sets or updates properties on an already-existing task.
- Updating core task fields such as title or status — those are handled by the task creation and status tracking features respectively.
- Syntax highlighting or code execution of content in the notes/code snippet field — the field stores raw text only.
- Defining or managing the list of valid priority levels — those are system-defined constants, not configurable by the user through this feature.
- Attaching files or binary assets to a task.
- Sharing or exposing task properties to other users — this is a single-user app with no collaboration features.
- External tool integration — property values are not synced to GitHub, Jira, or any other external system.
---
