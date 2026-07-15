# Contributing

Contributions should be small, testable, and tied to a clear retrieval, security, product, or operational outcome.

## Development workflow

1. Create a focused branch from `main`.
2. Copy `Backend/.env.example` to `Backend/.env` and keep all credentials local.
3. Make the smallest change that solves the problem.
4. Add or update regression coverage.
5. Run the checks below before opening a pull request.

```powershell
.\.venv\Scripts\python.exe -m pytest Backend\tests -q

cd Frontend
npm run lint
npm run build
npm run test:e2e

cd ..
docker compose config --quiet
```

## Pull requests

A useful pull request explains:

- the problem and why it matters;
- the chosen design and important trade-offs;
- how the behavior was verified;
- any configuration, migration, privacy, or deployment impact.

Do not include uploaded PDFs, databases, generated reports, build output, environment files, or screenshots containing private user data.
