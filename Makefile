# indieclaw release automation
# Usage: make release V=0.8.0

CURRENT_VERSION := $(shell python3 -c "import re; print(re.search(r'version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read()).group(1))")

.PHONY: release version

version:
	@echo $(CURRENT_VERSION)

release:
ifndef V
	$(error Usage: make release V=x.y.z (current: $(CURRENT_VERSION)))
endif
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
	@# Stage, commit, tag
	git add pyproject.toml CHANGELOG.md uv.lock
	git commit -m "release: v$(V)"
	git tag -a "v$(V)" -m "v$(V)"
	@echo ""
	@echo "Done. Tagged v$(V). Push with: git push && git push --tags"
