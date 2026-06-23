## Summary

Describe what changed and why.

## Type of change

- [ ] Bug fix
- [ ] Feature
- [ ] Docs
- [ ] Refactor
- [ ] Test-only change
- [ ] Build / CI / release tooling

## Risk area

- [ ] Agent / prompts / tools
- [ ] Designer
- [ ] Memory / knowledge graph
- [ ] Channels / external integrations
- [ ] Installers / auto-update
- [ ] UI only
- [ ] Other

## Testing

- [ ] I ran `uv run python scripts/run_test_matrix.py fast`
- [ ] I ran `uv run python scripts/run_test_matrix.py pr` for shared, release-sensitive, or cross-subsystem changes
- [ ] I added or updated tests
- [ ] I updated the relevant inventory in `tests/helpers/` when changing coverage ownership
- [ ] I manually tested the affected user flow
- [ ] Not applicable, docs-only change

## Release notes

- [ ] User-visible change, release notes needed
- [ ] Internal-only change, no release note needed

## Checklist

- [ ] Branch is based on latest `main`
- [ ] No direct secrets, API keys, local paths, or private data included
- [ ] The change is focused and does not include unrelated cleanup
- [ ] Windows/macOS behavior considered where relevant
