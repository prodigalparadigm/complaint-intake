PY := PYTHONPATH=src python3

.PHONY: all demo data run evals test console calibrate simulate clean

all: data run evals test console ## full build: data, pipeline, evidence, tests, console

demo: all ## everything, then print where the console is
	@echo ""
	@echo "Review console: open out/console.html"

data: ## regenerate the synthetic corpus
	python3 data/generate.py

run: ## run the pipeline over the corpus
	$(PY) -m mhb run --fresh

evals: ## measure behaviour, write out/evidence.json, enforce the hard gates
	$(PY) evals/run_evals.py

test: ## run the test suite (stdlib unittest, no dependencies)
	cd tests && PYTHONPATH=../src:. python3 -m unittest discover -p 'test_*.py'

console: ## build the self-contained HTML review console
	$(PY) -m mhb console

calibrate: ## where the two definitions of "serious" diverge
	$(PY) -m mhb calibrate

simulate: ## backlog arithmetic
	$(PY) -m mhb simulate

clean: ## clear generated cases, vault and audit log
	$(PY) -m mhb reset
