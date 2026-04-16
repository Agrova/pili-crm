# Module: api

**Zone:** HTTP API layer.

## Responsibilities
- Expose endpoints for AnythingLLM tool calls
- Expose endpoints for future admin panel
- Routing, serialization, request validation
- No business logic — delegates to module public interfaces

## Dependencies
- Depends on: all modules (`catalog`, `orders`, `procurement`, `warehouse`, `pricing`, `communications`, `finance`, `shared`)
- Depended on by: _none_ (top of the dependency graph)
