# Security policy

## Reporting a vulnerability

Please do not disclose exploitable security issues in a public issue.

Use GitHub's private vulnerability reporting flow for this repository:

https://github.com/nayana3333/InsightRAG-Multi-Document-Research-Assistant/security/advisories/new

Include the affected endpoint or component, reproduction steps, expected impact, and any suggested mitigation. Reports will be acknowledged after they can be reproduced and scoped.

## Secrets

Never commit `Backend/.env`, provider keys, authentication secrets, local databases, uploaded documents, or vector indexes. The repository ignore and Docker context rules exclude these files, but contributors remain responsible for checking staged changes before every commit.

If a key is exposed, revoke it at the provider and replace it immediately. Removing a secret from the latest commit does not remove it from Git history.

## Production considerations

The default configuration is intended for local development and single-instance demonstrations. Production deployments should use managed secret storage, TLS at the ingress, an approved model-provider retention policy, Qdrant authentication, Redis-backed distributed rate limits, centralized logging, backups, and a managed relational database.
