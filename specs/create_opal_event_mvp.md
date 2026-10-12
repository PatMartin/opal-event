# Implement Opal Event

Implement the existing `opal_event.yaml` OpenAPI 3.1 contract as a complete, production-quality application consisting of a REST server, CLI, and React SPA.

**The OpenAPI contract is authoritative.** Do not modify it without explicit approval. All three interfaces must provide functional parity for applicable operations.

## Technology Stack

- **REST:** Python, FastAPI, Pydantic, Uvicorn
- **Messaging:** PyZMQ with XSUB/XPUB broker
- **Persistence:** SQLite
- **CLI:** Typer, using nested commands such as `opal event`, `opal event rules`, and `opal event actions`
- **SPA:** React, TypeScript, Vite
- **Configuration:** YAML, with live reloading
- **Packaging:** `pyproject.toml`

## Requirements

1. Implement every operation, schema, validation rule, and response defined in `opal_event.yaml`.
2. Implement the ZeroMQ wire protocol exactly as documented in `zeromq_protocol.md`.
3. Support event publishing, topic subscriptions, lightweight rule matching, and action execution.
4. Implement shell, HTTP, and Python plugin actions, including subprocess isolation, timeouts, templating, and security restrictions.
5. Execute actions concurrently using a configurable bounded worker pool. Record failures and dropped executions without retries.
6. Persist events and execution history in SQLite with configurable retention.
7. Support YAML-backed settings, rules, and actions with atomic updates and hot reloading.
8. Implement configured REST authentication, ZeroMQ CURVE security, and HTTP destination allowlists.
9. Provide a complete CLI exposing all applicable REST operations, with hyphenated CLI options and snake_case Python identifiers.
10. Provide a clean, responsive React SPA for managing settings, rules, actions, events, executions, and service health.
11. Keep business logic centralized in the backend. Both CLI and SPA must consume the REST API rather than duplicate backend logic.
12. Include comprehensive unit, integration, contract, security, and end-to-end tests. Use mocks where external services or hardware are unavailable.

## Delivery

- Deliver complete, working source code, not scaffolding or pseudocode.
- Include example YAML configuration, sample events, rules, and actions.
- Include installation instructions, development commands, and production startup instructions for Windows and Linux.
- Provide a README documenting architecture, REST, CLI, SPA, ZeroMQ endpoints, and configuration.
- Run the test suite, fix failures, and report any remaining limitations.

**Implementation principles:** API-first, modular, implementation-neutral, minimal dependencies, straightforward architecture, and no unnecessary abstractions.

Do not add functionality beyond the approved contract. Make reasonable implementation decisions independently and complete the implementation without unnecessary questions.

Add explicit repository inputsSpecify action security defaults