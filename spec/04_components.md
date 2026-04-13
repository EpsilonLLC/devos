# Component architecture

## Module ownership (locked — no cross-module direct access)
| Module | Owns | Must not |
|--------|------|----------|
| `auth/` | User records, password hashing, JWT token generation and validation, session cookie management | Never read or write task records directly; never import from tasks/ or dashboard/ |
| `tasks/` | Task records, task validation logic, status enum, priority enum, tags deduplication, notes character limits, updated_at tracking | Never perform authentication logic or token validation; never import from auth/service or auth/security directly — only consume the get_current_user dependency from auth/dependencies |
| `dashboard/` | Dashboard query logic, task aggregation and sort order (status-group then priority), empty-state signaling, notes truncation for preview | Never mutate task records; never contain its own ORM models or database tables; never duplicate task validation logic from tasks/ |
| `core/` | Async SQLAlchemy engine and session factory, environment config parsing, base HTTP exception wrappers, app-wide constants | Never contain any business logic, domain models, or feature-specific code; never import from auth/, tasks/, or dashboard/ |

## Patterns (guided)
- Repository pattern: all SQLAlchemy DB access in {module}/repository.py — never directly in routes or services
- Service layer: all business logic (validation, ownership checks, deduplication, defaults) in {module}/service.py — never in route handlers
- Pydantic v2 for all request/response models and config parsing — no untyped dicts crossing API boundaries
- Explicit error handling — no bare except: — catch specific exception types and re-raise as HTTP exceptions via core/ wrappers
- All DB session handling via core/ async session factory using async with — never instantiate sessions directly in modules

---
### Module: `auth/`
**Responsibility:** Manages user identity, JWT-based authentication, session tokens, and all signup/login flows with per-user tenant isolation.

**Features owned:** F-001

**Internal structure (guided):**
```
auth/
  __init__.py
  routes.py          # FastAPI router: POST /auth/signup, POST /auth/login
  service.py         # AuthService: signup(), login(), validate_token()
  repository.py      # UserRepository: get_by_email(), create_user() — DB queries only
  schemas.py         # Pydantic: SignupRequest, LoginRequest, TokenResponse, UserOut
  security.py        # hash_password(), verify_password(), create_jwt(), decode_jwt()
  dependencies.py    # get_current_user() FastAPI dependency — injected across all protected routes
  exceptions.py      # AuthError, InvalidCredentialsError, DuplicateEmailError
  models.py          # SQLAlchemy User ORM model (id, email, hashed_password, created_at)
```

**Interfaces exposed:**
- `AuthService`
- `get_current_user`
- `UserOut`

**May import:**
- `core/config`
- `core/exceptions`
- `core/database`

**Must never import:**
- `tasks/`
- `dashboard/`
---

---
### Module: `tasks/`
**Responsibility:** Owns the full lifecycle of task records — creation, editing, deletion, status transitions, and developer-specific properties — enforcing per-user ownership (multi-tenant) on every operation.

**Features owned:** F-002, F-003, F-004, F-005, F-007

**Internal structure (guided):**
```
tasks/
  __init__.py
  routes.py          # FastAPI router: POST /tasks, GET /tasks/{id}, PATCH /tasks/{id}, DELETE /tasks/{id}
  service.py         # TaskService: create_task(), update_task(), delete_task(), change_status(), update_dev_properties()
  repository.py      # TaskRepository: get_by_id(), get_all_for_user(), create(), update(), delete() — DB queries only
  schemas.py         # Pydantic: TaskCreateRequest, TaskUpdateRequest, TaskStatusRequest, TaskDevPropsRequest, TaskOut
  models.py          # SQLAlchemy Task ORM model (id, owner_id FK, title, status, priority, tags[], notes, created_at, updated_at)
  enums.py           # TaskStatus enum (to-do, in-progress, done), Priority enum (low, medium, high)
  validators.py      # trim_title(), deduplicate_tags(), strip_empty_tags(), enforce_notes_limit()
  exceptions.py      # TaskNotFoundError, TaskForbiddenError, TaskValidationError
```

**Interfaces exposed:**
- `TaskService`
- `TaskOut`
- `TaskStatus`
- `Priority`

**May import:**
- `core/config`
- `core/exceptions`
- `core/database`
- `auth/dependencies`

**Must never import:**
- `auth/service`
- `auth/security`
- `auth/repository`
- `dashboard/`
---

---
### Module: `dashboard/`
**Responsibility:** Serves the personal task dashboard by aggregating and sorting the authenticated user's tasks for display, with no mutation logic of its own.

**Features owned:** F-006

**Internal structure (guided):**
```
dashboard/
  __init__.py
  routes.py          # FastAPI router: GET /dashboard — returns sorted, grouped task list for current user
  service.py         # DashboardService: get_dashboard_for_user() — fetches via TaskRepository, applies sort/group/truncate
  schemas.py         # Pydantic: DashboardTaskPreview (title, status, priority, tags, notes_preview), DashboardResponse
  sorting.py         # sort_and_group_tasks(): group by status order, secondary sort by priority, handle nulls
  truncation.py      # truncate_notes_preview(): trim notes to display-safe length for card view
```

**Interfaces exposed:**
- `DashboardService`
- `DashboardResponse`

**May import:**
- `core/config`
- `core/exceptions`
- `core/database`
- `auth/dependencies`
- `tasks/repository`
- `tasks/schemas`
- `tasks/enums`

**Must never import:**
- `auth/service`
- `auth/security`
- `tasks/service`
---

---
### Module: `core/`
**Responsibility:** Provides shared infrastructure — database session management, global configuration, base exception classes, and common utilities — consumed by all other modules.

**Internal structure (guided):**
```
core/
  __init__.py
  config.py          # Settings class (pydantic-settings): DATABASE_URL, JWT_SECRET_KEY, JWT_ALGORITHM, NOTES_MAX_LENGTH, TAGS_MAX_COUNT
  database.py        # async_engine, AsyncSessionLocal, get_db() dependency, Base declarative base
  exceptions.py      # AppException base, Http400Error, Http401Error, Http403Error, Http404Error, Http500Error
  middleware.py      # Global exception handler middleware registered on the FastAPI app
  constants.py       # NOTES_MAX_CHARS = 10000, TAG_MAX_LENGTH = 50, TAGS_MAX_COUNT = 20, PASSWORD_MIN_LENGTH = 8
  utils.py           # utcnow() helper, sanitize_string_field()
```

**Interfaces exposed:**
- `Settings`
- `get_db`
- `Base`
- `AppException`
- `Http400Error`
- `Http401Error`
- `Http403Error`
- `Http404Error`
- `Http500Error`
- `NOTES_MAX_CHARS`
- `TAG_MAX_LENGTH`
- `TAGS_MAX_COUNT`
- `PASSWORD_MIN_LENGTH`
- `utcnow`

**May import:**
- _(none)_

**Must never import:**
- `auth/`
- `tasks/`
- `dashboard/`
---
