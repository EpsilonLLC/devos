# Constraints
# Injected at TOP of every agent context. Never summarized away.

## Stack
- Backend: FastAPI + SQLAlchemy async
- Database: PostgreSQL
- JWT authentication
- multi-tenant isolation

## Hard rules — violation fails validation
- All DB queries must include a user_id filter — no task record is read, written, or deleted without scoping to the authenticated user's ID
- No raw SQL outside {module}/repository.py — all DB access via SQLAlchemy ORM through repository classes only
- All endpoints return standard error envelope: {"error": "<message>", "code": <http_status>, "detail": "<field_or_context>"}
- No secrets in code — all credentials, secret keys, and DB URLs via environment variables only
- JWT validation is required on every request except POST /auth/signup and POST /auth/login — no unauthenticated access to any other route

## Naming
- Files and directories: snake_case (e.g., task_repository.py)
- Classes: PascalCase (e.g., TaskRepository, UserService)
- Functions and variables: snake_case (e.g., get_task_by_id, current_user)
- Constants: UPPER_SNAKE_CASE (e.g., MAX_NOTES_LENGTH, ALLOWED_STATUS_VALUES)
- Pydantic request/response models: PascalCase suffixed with Request or Response (e.g., TaskCreateRequest, TaskResponse)

## Always used
- Repository pattern: all SQLAlchemy DB access in {module}/repository.py — never directly in routes or services
- Service layer: all business logic (validation, ownership checks, deduplication, defaults) in {module}/service.py — never in route handlers
- Pydantic v2 for all request/response models and config parsing — no untyped dicts crossing API boundaries
- Explicit error handling — no bare except: — catch specific exception types and re-raise as HTTP exceptions via core/ wrappers
- All DB session handling via core/ async session factory using async with — never instantiate sessions directly in modules

## Non-functional
- Notes field enforced max 10,000 characters; tags max 20 entries each max 50 characters — validated in service layer before any DB write
- Passwords must be hashed with a secure one-way algorithm (e.g., bcrypt) — plaintext passwords must never be stored or logged
- Session cookies must be set as HTTP-only and Secure — JWT must not be exposed to JavaScript
