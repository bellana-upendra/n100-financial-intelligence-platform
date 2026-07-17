PYTHON ?= python

.PHONY: load ratios test report dashboard api verify clean

load:
	$(PYTHON) -m src.etl.run_pipeline

ratios:
	$(PYTHON) -m src.ratios

test:
	$(PYTHON) -m pytest -q

report:
	$(PYTHON) -m src.report

dashboard:
	$(PYTHON) -m src.dashboard

api:
	$(PYTHON) -m src.api

verify:
	$(PYTHON) scripts/verify_database.py

clean:
	$(PYTHON) scripts/clean.py
