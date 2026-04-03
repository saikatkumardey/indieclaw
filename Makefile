# indieclaw release automation
# Usage:
#   make release          — bump patch (0.1.7 → 0.1.8), commit, tag, push
#   make release V=1.2.3  — release a specific version

CURRENT_VERSION := $(shell python3 -c "import re; print(re.search(r'version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read()).group(1))")
NEXT_PATCH      := $(shell python3 -c "v='$(CURRENT_VERSION)'.split('.'); v[2]=str(int(v[2])+1); print('.'.join(v))")
V               ?= $(NEXT_PATCH)

.PHONY: release version

version:
	@echo $(CURRENT_VERSION)

release:
	@echo "Releasing indieclaw $(CURRENT_VERSION) → $(V)"
	@# Update version in pyproject.toml
	sed -i 's/^version = "$(CURRENT_VERSION)"/version = "$(V)"/' pyproject.toml
	@echo "  pyproject.toml updated"
	@# Insert new version entry after the header in CHANGELOG.md
	@DATE=$$(date +%Y-%m-%d); \
	PREV_TAG=$$(git describe --tags --abbrev=0 2>/dev/null || echo ""); \
	if [ -n "$$PREV_TAG" ]; then \
		COMMITS=$$(git log $$PREV_TAG..HEAD --oneline --no-merges | grep -v "bump version\|Bump version"); \
	else \
		COMMITS=$$(git log --oneline --no-merges -20); \
	fi; \
	BODY=$$(echo "$$COMMITS" | sed 's/^[a-f0-9]* /- /'); \
	{ head -3 CHANGELOG.md; \
	  echo ""; \
	  echo "## [$(V)] - $$DATE"; \
	  echo ""; \
	  echo "$$BODY"; \
	  echo ""; \
	  tail -n +4 CHANGELOG.md; \
	} > CHANGELOG.md.tmp && mv CHANGELOG.md.tmp CHANGELOG.md
	@echo "  CHANGELOG.md updated"
	@# Lock file
	uv lock 2>/dev/null || true
	@# Stage, commit, tag, push
	git add pyproject.toml CHANGELOG.md uv.lock
	git commit -m "release: v$(V)"
	git tag -a "v$(V)" -m "v$(V)"
	git push && git push --tags
	@echo ""
	@echo "Released v$(V)"
