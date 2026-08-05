# Operator dashboard example

Copy a valid runtime root containing the existing console evidence and export
it to a separate safe directory:

```text
python scripts/export_operator_dashboard.py --runtime-root <runtime-root> --as-of 2026-08-05T12:00:00Z --output-root <output-root> --include-json
```

The output is static HTML and can be opened locally. It contains no external
resources and grants no operational authorization.
