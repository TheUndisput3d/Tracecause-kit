# Infrastructure blueprints

These are target files for the coding assistant to implement and verify, not a runnable completed application. Copy/adapt them to the repository root in the appropriate phase. Required image references, application source, lockfiles, command handlers, initialized volumes and secrets are not supplied as built artifacts. No container startup or deployment has been certified by this planning kit.

Use docs/DOCKER.md as the contract. Do not replace required images with guessed digests. Resolve maintained compatible images, implement referenced modules/commands, validate with Docker Compose, then run the real stack. The local Compose example uses file-backed secrets; ensure their permissions and named-volume ownership work with UID 10001 on the selected host. API artifact reads are read-only; uploads use staging.
