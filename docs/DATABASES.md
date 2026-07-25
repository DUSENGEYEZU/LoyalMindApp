---
noteId: "ded629c0884e11f1a39b93143153b659"
tags: []

---

# Database reference: PostgreSQL, MongoDB, Redis

How to connect to each datastore in the KoboToolbox stack, and what's actually stored in each — verified against a real running instance of this project (table lists, row/key counts, and sample documents were pulled live, not copied from generic docs).

All credentials referenced below live in `.env` (see the root of this repo) — never hardcode them elsewhere.

---

## PostgreSQL

Two logical databases share one Postgres server: **`koboform`** (KPI — the main app) and **`kobocat`** (KoboCat — submission handling). Both use PostGIS (the image is `postgis/postgis`), which is why you'll see a long list of geocoding reference tables (`tabblock`, `tract`, `addr`, `zip_lookup`, `county`, `state`, `faces`, `pagc_*`, ...) — these are standard PostGIS/TIGER geocoder tables bundled with the extension. They aren't populated or used by anything KoboToolbox does in this project; ignore them unless you're doing geocoding work.

### Connect

```bash
psql "postgresql://$(grep '^POSTGRES_USER=' .env | cut -d= -f2-):$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)@localhost:$(grep '^POSTGRES_PORT=' .env | cut -d= -f2-)/koboform"
```

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `POSTGRES_PORT` in `.env` (default `5432`) |
| Username | `POSTGRES_USER` in `.env` (default `kobo`) |
| Password | `POSTGRES_PASSWORD` in `.env` |
| Databases | `koboform` (KPI), `kobocat` (KoboCat) |

Any GUI client (TablePlus, pgAdmin, DBeaver, Postico) works with the same values. Requires the `ports:` mapping added for `postgres` in `docker-compose.yml` — if you removed it, Postgres is only reachable from inside the Docker network.

### `koboform` — KPI's database (users, forms, projects, permissions)

| Table | Contains |
|---|---|
| `kpi_asset` | Every asset: surveys, questions, blocks, templates. The core "what forms exist" table — includes the form's XLSForm-derived content as JSON. |
| `kpi_assetversion` | Version history for each asset (every time a form is edited/redeployed). |
| `kpi_assetsnapshot` | Rendered XForm XML snapshots (what actually gets deployed/served to Enketo). |
| `kpi_assetfile` | Uploaded files attached to an asset (e.g. media files for a form). |
| `kpi_assetexportsettings` | Saved export configurations (column selection, language, etc.) per asset. |
| `kpi_objectpermission` | Who can view/edit/manage each asset — the permission system. |
| `kpi_assetuserpartialpermission` | Partial/restricted permissions (e.g. "can only see their own submissions"). |
| `kpi_userassetsubscription` | Users subscribed to public/discoverable assets. |
| `kpi_importtask` | Background jobs importing a form (e.g. from an uploaded XLSForm file). |
| `kpi_submissionexporttask` / `kpi_submissionsynchronousexport` | Background/foreground jobs exporting submission data to CSV/XLS/SPSS. |
| `kpi_accesslogexporttask` / `kpi_projecthistorylogexporttask` / `kpi_projectviewexporttask` | Export jobs for audit/history views. |
| `kpi_taguid` | Tags applied to assets. |
| `kpi_authorizedapplication` | OAuth-style external apps authorized against the API. |
| `auth_user`, `auth_group`, `auth_permission`, `auth_user_groups`, `auth_user_user_permissions`, `auth_group_permissions` | Django's built-in user/group/permission system — this is where `super_admin` and every other login lives. |
| `authtoken_token` | API auth tokens (used by KoboCollect, external integrations). |
| `oauth2_provider_*` | OAuth2 provider tables (access tokens, applications, grants) for third-party API access. |
| `socialaccount_*` | Social login (Google/GitHub-style SSO) accounts, if configured. |
| `accounts_mfa_*`, `mfa_authenticator`, `trench_mfamethod` | Multi-factor authentication enrollment/state. |
| `organizations_organization`, `organizations_organizationuser`, `organizations_organizationowner`, `organizations_organizationinvitation` | Multi-user organizations (team accounts, billing grouping). |
| `project_views_projectview`, `project_views_assignmentprojectviewm2m` | Saved custom "project views" (filtered/curated lists of projects for admins). |
| `project_ownership_transfer`, `project_ownership_invite` | Transferring a project's ownership between users. |
| `trash_bin_projecttrash`, `trash_bin_accounttrash`, `trash_bin_attachmenttrash` | Soft-deleted projects/accounts/attachments pending permanent deletion. |
| `hook_hook`, `hook_hooklog` | REST Services (webhooks) configured per form, and their call logs. |
| `django_celery_beat_*` | Scheduled/periodic task definitions (Celery Beat) — e.g. recurring exports, cleanup jobs. |
| `constance_constance` | Dynamic, admin-editable app settings (feature flags, limits) stored in DB instead of static config. |
| `taggit_tag`, `taggit_taggeditem` | Generic tagging system (used by assets and elsewhere). |
| `languages_language`, `languages_languageregion`, `languages_translationservice*`, `languages_transcriptionservice*` | Supported languages + configured translation/transcription service integrations (e.g. for automated transcription of audio submissions). |
| `help_inappmessage*` | In-app announcement/help messages shown to users. |
| `mass_emails_*` | Bulk email campaign configuration and send records. |
| `user_reports_billingandusagesnapshot*` | Usage/billing snapshots (submission counts, storage used) per user/org. |
| `hub_configurationfile`, `hub_extrauserdetail`, `hub_perusersetting`, `hub_sitewidemessage` | Misc. site configuration and per-user extra settings. |
| `audit_log_auditlog` | Audit trail of significant actions (who did what, when). |
| `django_admin_log`, `django_session`, `django_migrations`, `django_content_type` | Standard Django framework tables (admin history, login sessions, schema migration state, content-type registry). |
| `subsequences_*` | "Advanced features" pipeline data — e.g. AI-assisted transcription/translation/qualitative analysis attached to submissions. |
| `data_collectors_datacollector*` | Enumerator/data-collector registry (used by some collection workflows). |
| `form_disclaimer_formdisclaimer` | Custom disclaimer text shown before a form loads. |
| `kobo_scim_*` | SCIM provisioning (enterprise identity-provider user sync), if enabled. |
| `long_running_migrations_longrunningmigration` | Tracks large data migrations that run in the background instead of at deploy time. |

### `kobocat` — submission handling database

| Table | Contains |
|---|---|
| `logger_xform` | One row per deployed form (KoboCat's own copy of form metadata, separate from `kpi_asset`). |
| **`logger_instance`** | **One row per submission.** Links to `xform_id`, holds `uuid`, `date_created`, `status` (e.g. `submitted_via_web`), geolocation, XML hash. This is the record we found earlier for the test submission — it's the index/metadata row; the actual answer content lives in MongoDB (see below). |
| `logger_instancehistory` | Edit history when a submission is corrected/resubmitted. |
| `logger_attachment` | Files attached to a submission (photos, audio, signatures) — filename/path/media-type metadata, not the file bytes themselves (those sit on disk/media storage). |
| `logger_note` | Notes/comments attached to a submission (used in data cleaning workflows). |
| `logger_surveytype` | Legacy survey-type classification. |
| `logger_dailyxformsubmissioncounter`, `logger_monthlyxformsubmissioncounter` | Rolling submission counts per form, per day/month (used for quota and usage stats). |
| `viewer_parsedinstance` | Denormalized/flattened view of each submission, used to generate CSV/XLS exports efficiently. |
| `viewer_export` | Records of generated export files. |
| `viewer_columnrename` | User-defined renaming of export columns. |
| `viewer_instancemodification` | Tracks modifications made to a submission's data after the fact. |
| `main_userprofile` | KoboCat-side extension of the user profile (separate from KPI's `auth_user`). |
| `main_metadata` | Form-level metadata (supporting docs, media files linked to a form rather than a submission). |
| `auth_user`, `auth_group`, ... | KoboCat keeps its own copy of Django's auth tables (kept in sync with KPI's). |

---

## MongoDB

Stores the **actual submission answers** — the content Postgres's `logger_instance` merely indexes.

### Connect

```bash
docker exec -it loyalmind-kobo-mongo-1 mongosh -u "$(grep '^MONGO_USER_USERNAME=' .env | cut -d= -f2-)" -p "$(grep '^MONGO_USER_PASSWORD=' .env | cut -d= -f2-)" --authenticationDatabase formhub
```

Mongo's port (`MONGO_PORT` in `.env`, default `27017`) isn't published to the host in this project by default — connect via `docker exec` as above, or add a `ports:` mapping to `docker-compose.yml` (same pattern used for Postgres) if you want a GUI client (Compass, Studio 3T) to reach it directly from the Mac.

| Database | Collection | Contains |
|---|---|---|
| `formhub` | **`instances`** | One document per submission. Flat key-value pairs keyed by the question's full group path (e.g. `grp_section_b/grp_q22_attributes/q22_accuracy`), plus metadata fields: `_uuid`, `_xform_id_string`, `_userform_id`, `_submission_time`, `_submitted_by`, `_status`, `_geolocation`, `_attachments`, `_validation_status`, `meta/instanceID`. This is the document we inspected earlier for the test submission — same `_uuid` as the matching `logger_instance` row in Postgres. |

There's normally also a `formhub.userdata` and per-form indexing collections in larger deployments; only `instances` is populated so far in this project (one test submission).

---

## Redis

Two separate Redis instances, each serving a different purpose — they are **not interchangeable**, don't point one at the other's port.

### Connect

```bash
# redis_main - port 6379 internally; not published to host in this project
docker exec -it loyalmind-kobo-redis_main-1 redis-cli -a "$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2-)" --no-auth-warning

# redis_cache - port 6380 internally
docker exec -it loyalmind-kobo-redis_cache-1 redis-cli -p 6380 -a "$(grep '^REDIS_PASSWORD=' .env | cut -d= -f2-)" --no-auth-warning
```

Both share `REDIS_PASSWORD` from `.env`. Neither is published to the host by default (same as Mongo) — add a `ports:` entry in `docker-compose.yml` if you want to reach them from a GUI client (e.g. RedisInsight) directly.

### `redis_main` (internal port 6379)

Redis uses numbered logical DBs (`0`-`15` by default) within one instance — `SELECT <n>` or `-n <n>` on the CLI.

| DB | Used for | What's in it |
|---|---|---|
| `0` | Enketo Express session/security state | Keys like `id:<token>` (survey instance ids), `su:<token>` (survey/user session), `or:<domain>/<user>,<form>` (origin allow-list mapping, so Enketo knows which KPI domain+form a given web-form session is allowed to submit to). |
| `1` | Celery broker + result backend | `celery-task-meta-<task-uuid>` keys — background task queue and results for the `worker`/`worker_kobocat`/`worker_low_priority`/`worker_long_running_tasks`/`beat` containers (exports, webhooks, scheduled jobs, etc.). This is the busiest DB — 262 keys / 256 with TTLs in this project as of writing. |
| `2`, `3` | Reserved/unused | Empty in this project; available for future use (e.g. multi-tenant separation) without conflicting with 0/1. |

### `redis_cache` (internal port 6380)

| DB | Used for | What's in it |
|---|---|---|
| `5` | Django's cache framework (`CACHE_URL`) | `constance_4x:1:<SETTING_NAME>` — cached values of the dynamic settings from Postgres's `constance_constance` table (avoids a DB hit on every request); `:1:attachment_xpaths:...` / `:1:all_attachment_xpaths:...` — cached per-form attachment field lookups; `:1:mass_emails_...` — daily email-send counters; `organization-org...` — cached organization lookups. |
| `0`, `2` | Misc. framework-internal cache entries | A handful of keys (1-2) not tied to a specific app feature — normal Django cache framework bookkeeping. |

---

## Quick mental model

```
Postgres (koboform)  →  who can do what, and what forms exist
Postgres (kobocat)    →  submission INDEX (one row per submission, metadata only)
MongoDB (formhub)     →  submission CONTENT (the actual answers)
Redis (main, db1)     →  background job queue (Celery)
Redis (main, db0)     →  Enketo web-form session security
Redis (cache, db5)    →  Django's application-level cache
```
