# Memory Fabric Architecture Specification

Status: Phase 0 architecture specification only. This document describes the target design for the Neocortex-inspired memory fabric; it does not require runtime behavior changes in this phase.

## 1. Purpose and Design Goals

The memory fabric is the long-lived knowledge layer for Aider Plus. It reconciles the current local memory, conversation buffer, retrieval context, skills, and self-improvement machinery with a scoped, evidence-driven, explainable model.

The design goals are:

- **Scoped memory:** every record declares where it belongs and who may see it.
- **Evidence first:** raw observations become durable memories only when they are tied to evidence and provenance.
- **Explainable recall:** injected memories must carry a reason, ranking signals, and source references.
- **Skill promotion:** repeated successful evidence can become an approval-gated skill, then be reinforced or retired.
- **Backward compatibility:** existing `.aider/project_memory.json`, playbook entries, audit logs, skill proposals, and approved skills remain valid.
- **Local inspectability:** default storage stays file-backed and reviewable, while keeping the repository abstraction open for SQLite or later vector indexes.
- **Privacy by construction:** records are private unless their scope and visibility permit broader retrieval.

## 2. Existing Components Reconciled by the Fabric

| Current component | Current role | Target role in memory fabric |
| --- | --- | --- |
| `ProjectMemory` | Repo-scoped persistent JSON document with defaults for audit log, playbook, skill proposals, and observability. | Becomes the project-scoped repository facade over canonical `MemoryRecord` collections while preserving existing top-level keys during migration. |
| `ConversationMemory` | Rolling in-memory buffer of recent messages. | Feeds short-lived `thread:` records and raw observations; does not automatically persist private conversation content unless a policy converts it to evidence. |
| `ContextBuilder` | Builds task context from requirements, project state, playbook, PRD slices, and skill summaries using retrieval/ranking. | Becomes the recall orchestrator: it resolves allowed scopes, applies department recall order, ranks records, and emits explanations. |
| `CompanySkillManager` | Lists, queries, records usage of, proposes, and approves role-scoped skills under `.aider/skills` and `.aider/skill_proposals`. | Owns approved skill artifacts and skill proposal state; reads `skill_evidence` links from memory records to explain provenance. |
| `SelfImprovementService` | Learns from post-mortems/audit logs and creates approval-gated skill proposals by department. | Becomes the promotion coordinator for the `raw -> evidence -> cluster -> proposal -> approved skill -> reinforced/retired` lifecycle. |
| `MemoryRetriever` | Lightweight TF-IDF scorer for text chunks. | Remains the first-stage local scorer; later phases can add embeddings/vector indexes behind the same recall API. |
| `ProjectMemoryMigrator` / repositories | Forward-only migration and JSON/SQLite persistence boundary. | Adds schema-versioned migration for canonical memory records without destructively rewriting legacy fields. |

## 3. Memory Scopes

A memory scope is the namespace that owns a record. Scope is separate from visibility: scope says *where the memory lives*; visibility says *who may retrieve it*.

### 3.1 Scope names

| Scope | Format | Owner | Typical examples |
| --- | --- | --- | --- |
| Project | `project` or `project:<project_id>` | Current product repository or warehouse product. | Coding standards, deployment gotchas, PRD decisions, approved project playbook patterns. |
| User | `user:<user_id>` | A human operator or local user profile. | Preferred tone, notification preferences, recurring approval preferences that are safe to remember. |
| Department | `department:<name>` | Company role such as `engineering`, `qa`, `delivery`, `ux`, `product`, `reviewer`, `devops`, `coo`. | Department workflow lessons, validation heuristics, role-specific skills. |
| Channel | `channel:<channel_id>` | A shared chat, Discord channel, GUI room, or collaboration surface. | Channel conventions, team-visible decisions, meeting summaries. |
| Thread | `thread:<thread_id>` | A single issue, task, daemon run, chat thread, or agent loop. | Immediate clarifications, task-local assumptions, ephemeral partial context. |
| System | `system` | Aider Plus installation/runtime. | Versioned defaults, global safety policies, built-in department routing notes. |

### 3.2 Scope rules

1. Every canonical record MUST have exactly one primary `scope`.
2. Records MAY include `related_scopes` for secondary references, but authorization is based on the primary scope plus visibility.
3. `thread:` records default to the shortest retention and should be summarized into evidence before promotion.
4. `system` records are read-only by ordinary learning flows; only migrations, bundled defaults, or explicit operator actions can write them.
5. `user:` records are never retrieved for another user unless an explicit export/share action creates a separate record in a shared scope.
6. Department records may be shared across projects only when visibility permits it and the record has enough evidence to avoid overfitting to one project.

## 4. Visibility Rules

Visibility is the retrieval boundary applied after scope resolution. It is intentionally coarse and auditable.

| Visibility | Semantics | Retrieval examples | Non-examples |
| --- | --- | --- | --- |
| `private` | Only the owning scope may retrieve it. For `user:alice`, only Alice's sessions; for `thread:123`, only that thread. | A user's preferred writing style; a thread-only secret-free clarification. | A QA rule that all engineers should see. |
| `channel` | Visible to members/processes of the channel plus records explicitly derived from that channel. | A Discord channel decides to use short release notes; future tasks in that channel can recall it. | A private DM preference copied into a project. |
| `project` | Visible to all project departments and project tasks. | A repo-specific testing command; deployment gotcha; product decision. | A global coding standard unrelated to this repo. |
| `user_visible` | Safe to show to the user and agents acting on their behalf, but not automatically reusable across other users. | COO remembers a user's preferred summary format and can explain it back to the user. | Hidden system routing policy. |
| `system` | Visible to all runtime components subject to precedence and safety rules. Usually read-only. | Built-in instruction precedence, default retention policy, global skill safety note. | Project-specific secrets or temporary task context. |

### 4.1 Visibility examples

- **Private user preference:** `scope="user:alice"`, `visibility="private"`, content `"Alice prefers concise status updates."` Retrieved only in Alice's sessions.
- **Project test command:** `scope="project:billing-api"`, `visibility="project"`, content `"Run pytest tests/billing before release."` Retrieved by Engineering, QA, Reviewer, Delivery, and DevOps for that project.
- **Channel decision:** `scope="channel:discord-prod"`, `visibility="channel"`, content `"Use changelog bullets in release announcements."` Retrieved by agents responding in that channel.
- **Department pattern:** `scope="department:qa"`, `visibility="project"`, content `"For this project, QA should include migration rollback checks."` Owned by QA but visible project-wide because other departments may need it.
- **System policy:** `scope="system"`, `visibility="system"`, content `"Approval gates are required before deployment actions."` Read by all components, never overridden by learned records.

### 4.2 Conflict and precedence

When multiple records conflict, the fabric ranks by instruction authority before relevance:

1. Runtime/system/developer/user instructions from the active prompt.
2. `system` visibility records.
3. `user_visible` or `private` records owned by the active user/thread.
4. `project` records for the active project.
5. `channel` records for the active channel.
6. Department records matching the active role.
7. Lower-confidence related-scope records.

A lower-authority memory can be recalled as context, but it cannot override a higher-authority instruction or approval gate.

## 5. Canonical `MemoryRecord` Schema

Canonical records live in a future `memory.records` collection while legacy fields continue to exist during migration. Records are append-friendly: updates create a new version or append reinforcement events rather than silently replacing provenance.

### 5.1 Field definitions

| Field | Required | Description |
| --- | --- | --- |
| `id` | Yes | Stable unique identifier, preferably `mem_<scope>_<ulid>` or content-addressed ID. |
| `schema_version` | Yes | Canonical memory record schema version. Phase 0 target starts at `1`. |
| `kind` | Yes | `observation`, `preference`, `decision`, `pattern`, `playbook`, `skill_evidence`, `skill`, `audit_summary`, `policy`, or `artifact_summary`. |
| `scope` | Yes | Primary scope from Section 3. |
| `visibility` | Yes | Visibility from Section 4. |
| `department` | No | Department most likely to use the record. |
| `project_id` | No | Project identifier when the memory is project-related. |
| `thread_id` / `channel_id` / `user_id` | No | Optional direct owner identifiers. |
| `content` | Yes | Human-readable concise text to inject or summarize. |
| `summary` | No | Short display summary. |
| `tags` | No | Search and policy tags. |
| `source` | Yes | Provenance: component, event IDs, file paths, task IDs, timestamps. |
| `evidence` | No | Supporting observations, audit entries, test results, or links. |
| `skill_evidence` | No | Structured block used by promotion lifecycle. |
| `ranking` | No | Cached ranking metadata: importance, confidence, recency, usage, decay. |
| `retention` | No | Expiration and archival policy. |
| `redaction` | No | Privacy classification and redaction status. |
| `created_at` / `updated_at` | Yes | UTC ISO-8601 timestamps. |
| `supersedes` | No | Prior record IDs this record replaces. |
| `related_records` | No | Cross-links to supporting or conflicting records. |

### 5.2 Full JSON example

```json
{
  "id": "mem_project_billing-api_01JZ8Z6QV8Z5E9K4F3SB2M8A1C",
  "schema_version": 1,
  "kind": "skill_evidence",
  "scope": "project:billing-api",
  "visibility": "project",
  "department": "qa",
  "project_id": "billing-api",
  "thread_id": "issue-184",
  "channel_id": "discord-prod",
  "user_id": null,
  "content": "QA found that billing release checks must include subscription migration rollback tests before DevOps handoff.",
  "summary": "Billing QA requires migration rollback checks.",
  "tags": [
    "billing",
    "qa",
    "release",
    "migration",
    "rollback"
  ],
  "source": {
    "component": "SelfImprovementService",
    "origin": "post_mortem",
    "task_ids": [
      "task-qa-184",
      "task-devops-184"
    ],
    "audit_event_ids": [
      "audit-2026-05-17T18:01:22Z-qa",
      "audit-2026-05-17T18:42:10Z-devops"
    ],
    "artifact_refs": [
      {
        "type": "proof_of_work",
        "path": ".aider/company/runs/issue-184/proof_of_work.json"
      },
      {
        "type": "test_log",
        "path": ".aider/company/runs/issue-184/qa.log"
      }
    ],
    "created_by": "company-orchestrator",
    "created_at": "2026-05-17T19:05:30Z"
  },
  "evidence": [
    {
      "type": "test_result",
      "status": "failed_then_passed",
      "command": "pytest tests/billing/test_subscription_migrations.py",
      "summary": "Initial QA failed because rollback path was untested; final run passed after adding rollback coverage.",
      "timestamp": "2026-05-17T18:37:44Z"
    },
    {
      "type": "handoff_note",
      "status": "accepted",
      "summary": "Delivery accepted DevOps handoff only after rollback evidence was attached.",
      "timestamp": "2026-05-17T18:49:02Z"
    }
  ],
  "skill_evidence": {
    "candidate_skill": {
      "scope": "qa",
      "name": "billing-release-rollback-checks",
      "title": "Billing release rollback checks"
    },
    "promotion_stage": "evidence",
    "positive_outcomes": 2,
    "negative_outcomes": 1,
    "successful_repetitions": 2,
    "distinct_threads": 2,
    "departments_observed": [
      "qa",
      "delivery",
      "devops"
    ],
    "minimum_thresholds": {
      "successful_repetitions": 2,
      "distinct_threads": 2,
      "human_approval_required": true
    },
    "proposal_id": null,
    "approved_skill_ref": null,
    "reinforcement": {
      "usage_count": 0,
      "last_used_at": null,
      "last_success_at": null,
      "last_failure_at": null
    },
    "retirement": {
      "status": "active",
      "reason": null,
      "retired_at": null
    }
  },
  "ranking": {
    "importance": 0.82,
    "confidence": 0.78,
    "recency_score": 0.94,
    "usage_count": 0,
    "decay_after_days": 180,
    "last_recalled_at": null
  },
  "retention": {
    "policy": "project_lifetime",
    "expires_at": null,
    "archive_after_days": 365
  },
  "redaction": {
    "classification": "internal",
    "contains_secret": false,
    "redacted": false,
    "redaction_notes": []
  },
  "created_at": "2026-05-17T19:05:30Z",
  "updated_at": "2026-05-17T19:05:30Z",
  "supersedes": [],
  "related_records": [
    "mem_project_billing-api_01JZ8Z2F3QG9Y4V5E2F8K9A0BP"
  ]
}
```

## 6. Memory-to-Skill Promotion Lifecycle

The fabric models learning as a pipeline with explicit gates.

```text
raw -> evidence -> cluster -> proposal -> approved skill -> reinforced/retired
```

### 6.1 Stage definitions

1. **Raw**
   - Source: conversation turns, audit events, task payloads, proof-of-work, test logs, handoff notes, reviewer comments.
   - Storage: short-lived `thread:` records or legacy audit/playbook fields.
   - Gate: never injected broadly unless visibility allows it and privacy checks pass.

2. **Evidence**
   - Source: raw observations with provenance and outcomes.
   - Storage: canonical `MemoryRecord` with `kind="skill_evidence"` or `kind="pattern"`.
   - Gate: requires at least one source reference, a confidence score, and a non-private visibility before project/department recall.

3. **Cluster**
   - Source: multiple evidence records that describe the same recurring behavior.
   - Storage: cluster summary record with related record IDs and aggregate ranking.
   - Gate: requires diversity checks: multiple tasks, threads, or departments where possible. Avoid creating skills from one-off incidents unless explicitly approved.

4. **Proposal**
   - Source: cluster crosses configured thresholds such as successful repetitions and tool/test evidence.
   - Storage: existing `.aider/skill_proposals/<scope>/<proposal_id>.json` plus a memory record pointing to evidence IDs.
   - Gate: human approval by default. Auto-create remains opt-in and only for trusted local repos.

5. **Approved skill**
   - Source: approved proposal.
   - Storage: existing `.aider/skills/<scope>/<name>/SKILL.md` and metadata, with provenance back-links to memory evidence.
   - Gate: skill must include when-to-use, procedure, evidence summary, and safety notes. It must not bypass approvals or instruction precedence.

6. **Reinforced**
   - Source: successful recall/use of an approved skill.
   - Storage: existing `skills.recently_used` plus future reinforcement fields in linked memory records.
   - Gate: reinforcement records should include task ID, role, outcome, and whether human/QA validation passed.

7. **Retired**
   - Source: stale, contradicted, harmful, low-confidence, or superseded skill.
   - Storage: skill metadata marks retirement; canonical records retain history and point to replacement records if any.
   - Gate: retirement can be automatic for expired low-use skills, but removal of high-confidence project skills should be reviewable.

### 6.2 Threshold defaults

Initial thresholds should mirror the existing safe defaults:

- `min_successful_repetitions`: 2.
- `min_tool_calls`: 5 where tool traces are available.
- `require_human_approval`: true.
- `auto_create`: false.
- Max proposed/approved skills per role remains bounded to avoid prompt bloat.

### 6.3 Promotion anti-patterns

The learner must not promote:

- Secrets, credentials, personal data, or private DMs.
- One-off workarounds without evidence of recurrence.
- Failed strategies unless they are stored as cautionary evidence and clearly labeled.
- Policies that conflict with system/developer/user instructions.
- Skills that skip tests, approvals, or audit logging because a prior run succeeded without them.

## 7. Recall Policy

Recall is the process that selects memory records for a department task. The policy combines scope permissions, department-specific order, ranking, and explanation generation.

### 7.1 Recall inputs

- Active task: target department, artifact type, payload, context, original request.
- Active project: project ID, phase, PRD/design summaries.
- Actor context: user ID, channel ID, thread ID.
- Requirements: explicit context requirements such as `playbook.*`, `skills.qa`, `project.prd`.
- Safety context: current instruction precedence, approval state, redaction policy.

### 7.2 Department recall order

Each department starts with the most local context, then widens. Records that fail visibility checks are skipped.

| Department | Recall order |
| --- | --- |
| COO | `thread:` -> `user:` -> `channel:` -> `project:` -> `department:coo` -> `system` |
| Product | `thread:` -> `project:` decisions/PRD -> `user:` preferences -> `channel:` -> `department:product` -> `system` |
| UX | `thread:` -> `project:` design/PRD -> `user:` UX preferences -> `channel:` -> `department:ux` -> `system` |
| Engineering | `thread:` -> `project:` code/test/build patterns -> `department:engineering` -> `reviewer`/`qa` related records -> `system` |
| Reviewer | `thread:` -> `project:` coding standards/diffs -> `department:reviewer` -> `engineering` related records -> `system` |
| QA | `thread:` -> `project:` acceptance/test history -> `department:qa` -> `delivery` handoff criteria -> `system` |
| Delivery | `thread:` -> `project:` milestones/blockers/handoffs -> `department:delivery` -> `qa`/`devops` readiness records -> `system` |
| DevOps | `thread:` -> `project:` deployment/release records -> `department:devops` -> `delivery` handoff records -> `system` |
| Security App/Platform | `thread:` -> `project:` security findings -> `department:security_*` -> `devops`/`engineering` related records -> `system` |

### 7.3 Ranking factors

The initial ranker should remain lightweight and deterministic, extending the current TF-IDF, keyword, recency, and usage approach.

Required ranking factors:

- **Text relevance:** TF-IDF cosine similarity between task query and memory content.
- **Keyword/name match:** direct matches against tags, skill names, department names, artifact types, and command names.
- **Scope proximity:** thread > active project/user/channel > matching department > related scopes > system defaults.
- **Visibility authority:** records visible at narrower authorized scopes rank above broadly visible records when relevance is comparable.
- **Evidence confidence:** outcome-backed evidence ranks above unsupported observations.
- **Recency:** recent thread/project facts rank higher, with slower decay for approved project policies and system records.
- **Usage/reinforcement:** records and skills that were used successfully rank higher; failures reduce confidence.
- **Diversity:** avoid injecting many near-duplicates from the same cluster.
- **Token budget:** high-confidence summaries rank above long raw content.

### 7.4 Explanation generation

Every injected memory or skill summary must include a concise explanation. The explanation should be stored in task context and recent-injection telemetry.

Explanation format:

```text
<scope>/<kind>/<id-or-name> — included because <matching terms>, <scope reason>, confidence <score>, last updated <date>, evidence <n> records.
```

Examples:

- `project:billing-api/pattern/mem_... — included because it matches "migration" and "rollback", is project-visible, confidence 0.78, last updated 2026-05-17, evidence 2 records.`
- `qa/billing-release-rollback-checks — included because skill name matches the QA release task, usage count 3, last successful use 2026-05-10.`

### 7.5 Recall output shape

ContextBuilder should eventually emit:

```json
{
  "memory_records": [
    {
      "id": "mem_project_billing-api_01JZ...",
      "scope": "project:billing-api",
      "visibility": "project",
      "kind": "skill_evidence",
      "summary": "Billing QA requires migration rollback checks.",
      "content": "QA found that billing release checks must include subscription migration rollback tests before DevOps handoff.",
      "retrieval_explanation": "project:billing-api/skill_evidence/mem_project_billing-api_01JZ... — included because it matches migration and rollback, is project-visible, confidence 0.78, evidence 2 records."
    }
  ],
  "skill_guidance": [
    "qa/billing-release-rollback-checks: Billing release rollback checks — Why included: matched release and rollback; approved from 2 evidence records."
  ],
  "memory_retrieval_explanations": [
    "..."
  ]
}
```

## 8. Migration Strategy

Migration must preserve existing user data and add canonical structure incrementally.

### 8.1 Current persisted data to preserve

Existing project memory includes:

- `audit_log`: structured or semi-structured events.
- `playbook`: categories such as `coding_standards`, `ux_preferences`, and `deployment_gotchas`.
- `skill_proposals`: compact proposal index.
- `observability`: token usage, QA metrics, and task metrics.
- `knowledge.recently_injected`: recent retrieval explanations created by ContextBuilder.
- `skills.recently_used`: recent approved skill usage.
- Any additional project-specific keys already written by Company Mode.

All of these remain readable and writable through the existing `ProjectMemory.data` contract until their callers are explicitly migrated.

### 8.2 Additive canonical collection

The next schema version should add:

```json
{
  "memory": {
    "records": [],
    "clusters": [],
    "indexes": {
      "by_scope": {},
      "by_kind": {},
      "by_department": {},
      "by_related_skill": {}
    },
    "migration": {
      "legacy_backfill_completed_at": null,
      "legacy_backfill_version": 0
    }
  }
}
```

Legacy fields are not deleted. Backfill creates canonical records that point to legacy source paths, for example `source.legacy_path="playbook.deployment_gotchas[3]"`.

### 8.3 Backfill mapping

| Legacy source | Canonical mapping |
| --- | --- |
| `playbook.coding_standards[]` | `kind="playbook"`, `scope="project:<id>"`, `visibility="project"`, `department="engineering"` or `reviewer`. |
| `playbook.ux_preferences[]` | `kind="preference"`, `scope="project:<id>"` unless clearly user-owned, `visibility="project"` or `user_visible`. |
| `playbook.deployment_gotchas[]` | `kind="pattern"`, `department="devops"`, `visibility="project"`. |
| `audit_log[]` | `kind="audit_summary"` or `observation`, `scope="thread:<task_id>"` when task ID exists, otherwise project. |
| `skill_proposals[]` | `kind="skill_evidence"` or `skill_proposal_ref`, linked to `.aider/skill_proposals`. |
| `skills.recently_used[]` | Reinforcement metadata linked by `scope/name`. |
| `knowledge.recently_injected[]` | Recall telemetry, not promoted unless tied to successful outcome. |

### 8.4 Migration phases

1. **Phase 0: specification**
   - Write this architecture document and README link only.
2. **Phase 1: schema scaffolding**
   - Add dataclasses/types for `MemoryRecord`, `MemoryScope`, `Visibility`, and `SkillEvidence`.
   - Add repository migration that creates empty `memory.records` without changing behavior.
3. **Phase 2: write adapters**
   - Add helper methods that write canonical records alongside existing audit/playbook/skill proposal writes.
   - Add privacy/redaction checks before persistence.
4. **Phase 3: recall integration**
   - Extend ContextBuilder to query canonical records after legacy playbook/skill retrieval or in a feature-flagged path.
   - Emit explanations for canonical records.
5. **Phase 4: promotion integration**
   - Update SelfImprovementService to cluster canonical evidence and create proposals with record links.
   - Update CompanySkillManager approval metadata to include evidence record IDs.
6. **Phase 5: reinforcement and retirement**
   - Record skill outcomes, reinforce useful skills, and mark stale or harmful skills retired.
7. **Phase 6: storage/index optimization**
   - Add optional SQLite tables or vector indexes behind `MemoryRepository` only after behavior is covered by tests.

### 8.5 Rollback plan

- Keep legacy fields as source of truth until canonical read paths are stable.
- Make canonical backfill idempotent using `source.legacy_path` and content digests.
- If canonical recall fails, ContextBuilder falls back to current playbook/skill behavior.
- Schema migrations must be forward-only, but data loss is avoided by retaining legacy payloads.

## 9. Test Plan by Future Phase

### Phase 1: schema scaffolding

- Unit-test scope and visibility validation.
- Unit-test `MemoryRecord` serialization/deserialization with full `skill_evidence` blocks.
- Unit-test repository migration adds empty canonical collections while preserving existing `audit_log`, `playbook`, `skill_proposals`, `observability`, `skills`, and `knowledge` keys.
- Property-style tests for unknown legacy keys: migrations must preserve them.

### Phase 2: write adapters

- Unit-test audit/playbook writes create linked canonical records when the feature flag is enabled.
- Unit-test private/thread records are not promoted to project visibility without explicit policy.
- Unit-test redaction rejects or redacts records marked as containing secrets.
- Integration-test JSON and SQLite repositories produce equivalent canonical memory documents.

### Phase 3: recall integration

- Unit-test per-department recall order and visibility filtering.
- Unit-test ranking factors: relevance, keyword match, scope proximity, confidence, recency, usage, and diversity.
- Unit-test explanation strings include reason, scope, confidence, and evidence count.
- Regression-test existing ContextBuilder behavior for playbook, PRD truncation, and skill guidance.
- Token-budget tests ensure injected memory stays bounded.

### Phase 4: promotion integration

- Unit-test raw observations become evidence only with provenance.
- Unit-test evidence clustering groups related records and avoids unrelated records with similar keywords.
- Unit-test proposal creation includes evidence record IDs and respects `require_human_approval`.
- Integration-test approval creates a skill with metadata back-links to evidence.

### Phase 5: reinforcement and retirement

- Unit-test successful skill usage increments reinforcement and updates confidence/recency.
- Unit-test failed outcomes lower confidence and can trigger review.
- Unit-test retirement hides skills from recall while preserving audit history.
- Integration-test superseded skills point to replacement skills or records.

### Phase 6: storage/index optimization

- Performance tests for projects with thousands of records.
- Consistency tests between JSON, SQLite, and optional vector indexes.
- Migration tests for index rebuilds.
- Concurrency tests for daemon and GUI writes to the same project memory.

## 10. Risks and Trade-offs

### 10.1 Performance

- **Risk:** ranking all records in large projects may be slow.
- **Mitigation:** filter by scope/visibility/kind first, maintain lightweight indexes, cap candidates, and add SQLite/vector indexes only behind the repository boundary.

### 10.2 Token usage

- **Risk:** explainable memory can bloat prompts.
- **Mitigation:** inject summaries by default, cap per-kind and per-department memory counts, deduplicate clusters, and include compact explanations rather than full evidence unless requested.

### 10.3 Privacy

- **Risk:** private conversation data could leak into project or department memory.
- **Mitigation:** default thread/user records to private, require explicit promotion policy, apply redaction metadata, and make visibility checks mandatory before recall.

### 10.4 Incorrect learning

- **Risk:** the system may turn accidental success into a skill.
- **Mitigation:** require repeated evidence, outcome diversity, human approval by default, and retirement paths.

### 10.5 Stale memories

- **Risk:** old decisions or commands may be recalled after they become invalid.
- **Mitigation:** use recency decay, `supersedes`, retirement, and task outcomes to demote stale records.

### 10.6 Complexity

- **Risk:** introducing canonical records while keeping legacy fields increases implementation complexity.
- **Mitigation:** migrate additively, keep wrappers small, preserve current behavior until tests cover each replacement path.

### 10.7 Explainability overhead

- **Risk:** explanations require extra bookkeeping and may expose internal scoring details.
- **Mitigation:** standardize concise explanation format and expose only safe provenance summaries.

## 11. High-Level File Plan

No runtime files are changed in Phase 0. Future implementation phases should add or extend the following files.

### 11.1 New modules/classes

| File | Planned contents |
| --- | --- |
| `aider/memory/fabric.py` | `MemoryFabric` high-level service for writes, recall, promotion hooks, and policy enforcement. |
| `aider/memory/records.py` | `MemoryRecord`, `SkillEvidence`, `MemorySource`, `MemoryRanking`, `MemoryRetention`, `MemoryRedaction` dataclasses or typed dicts. |
| `aider/memory/scopes.py` | `MemoryScope`, `Visibility`, parser/validator helpers, scope authorization checks. |
| `aider/memory/policy.py` | Visibility rules, per-department recall order, promotion thresholds, retention policy. |
| `aider/memory/ranking.py` | Hybrid ranker that combines TF-IDF, keyword, scope proximity, evidence confidence, recency, and usage. |
| `aider/memory/explanations.py` | Explanation generation helpers for recalled records and skills. |
| `aider/memory/promotion.py` | Evidence clustering and lifecycle state helpers shared by SelfImprovementService and CompanySkillManager. |
| `aider/memory/redaction.py` | Secret/privacy classifiers and redaction metadata helpers. |
| `aider/memory/indexes.py` | Optional in-memory/SQLite index maintenance for canonical records. |

### 11.2 Existing files to extend later

| File | Planned extension |
| --- | --- |
| `aider/memory/project.py` | Add convenience methods for canonical record collections while preserving `data`. |
| `aider/memory/repository.py` | Add schema migration for `memory.records`, indexes, and backfill metadata. |
| `aider/memory/retrieval.py` | Keep TF-IDF primitive; optionally expose reusable scoring data for canonical ranking. |
| `aider/memory/conversation.py` | Add optional conversion from rolling messages to private `thread:` observations. |
| `aider/company/context.py` | Use `MemoryFabric.recall()` for canonical records and include explanations in task context. |
| `aider/company/skills.py` | Store approved skill evidence links, reinforcement, and retirement metadata. |
| `aider/company/self_improvement.py` | Promote evidence clusters into proposals rather than reading only raw audit logs. |
| `aider/company/state.py` | Provide stable project/user/channel/thread identifiers to the fabric. |
| `aider/company/audit.py` | Emit provenance-friendly audit IDs suitable for `source.audit_event_ids`. |

### 11.3 Planned tests

| Test file | Coverage |
| --- | --- |
| `tests/memory/test_records.py` | Canonical schema validation and serialization. |
| `tests/memory/test_scopes.py` | Scope parsing, visibility authorization, and conflict precedence. |
| `tests/memory/test_migration.py` | Additive migration and legacy preservation. |
| `tests/memory/test_recall_policy.py` | Department recall order, ranking, token caps, explanations. |
| `tests/memory/test_promotion.py` | Evidence lifecycle, clustering, proposal creation, reinforcement, retirement. |
| `tests/company/test_context_memory_fabric.py` | ContextBuilder integration with canonical recall and legacy fallback. |
| `tests/company/test_skill_evidence.py` | CompanySkillManager and SelfImprovementService evidence links. |

## 12. Phase 0 Acceptance Criteria

Phase 0 is complete when:

- This document exists at `docs/architecture/memory-fabric.md`.
- `README.md` links to this document near the top.
- No runtime behavior changes are included.
- Future implementation work has enough structure to proceed in small, testable phases.
