# Phase 8 — Final: Build, Test, Commit, Merge, Tag, Push

## Tasks
- [ ] `docker build -t librarian .` — full image build
- [ ] `docker run --rm librarian pytest -v` — all tests must pass (target: 90+ tests)
- [ ] Fix any remaining test failures
- [ ] `git add -A; git commit -m "Merge MadTracked tracker into Librarian (v0.1.0)"`
- [ ] `git checkout master; git merge feature/madtracked-merge`
- [ ] `git tag v0.1.0`
- [ ] `git push; git push origin v0.1.0`
- [ ] `docker tag librarian ghcr.io/beejeex/librarian:v0.1.0`
- [ ] `docker tag librarian ghcr.io/beejeex/librarian:latest`
- [ ] `docker push ghcr.io/beejeex/librarian:v0.1.0`
- [ ] `docker push ghcr.io/beejeex/librarian:latest`
