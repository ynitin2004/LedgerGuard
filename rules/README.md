# rules/

**Planned (M2).** Versioned, YAML-defined risk rules (`v1.yaml`): code, description, inputs, predicate, severity (`hard`|`soft`), weight. MVP rule codes: `VELOCITY_5M`, `VELOCITY_1H`, `AMOUNT_ZSCORE`, `NEW_MERCHANT`, `CATEGORY_SHIFT`, `REPEAT_FAIL_24H`, `HIGH_AMOUNT_ABS`. A rules file is immutable once a decision references its version; changes ship as a new version. See docs/ARCHITECTURE.md (Risk decisioning).
