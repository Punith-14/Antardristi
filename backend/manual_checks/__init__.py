"""
End-to-end checks a person runs and reads, as opposed to the pytest suite.

These were named test_*.py at the backend root, which made them look like the
test suite - they never were. pytest.ini sets `testpaths = tests`, so they had
never once been collected. Renamed so the distinction is visible.

Most need a running server, real Earth Engine quota, or a Groq key, which is
exactly why they are not in the automatic suite.

    python -m manual_checks.api_smoke           # needs uvicorn on :8000
    python -m manual_checks.sar_flood_kerala    # needs Earth Engine
    python -m manual_checks.surface_analyses    # needs uvicorn
    python -m manual_checks.report_pipeline     # LLM step needs GROQ_API_KEY
"""
