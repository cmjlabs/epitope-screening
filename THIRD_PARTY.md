# Third-party provenance

`epitope_screening/vendor/population_coverage/` contains the local IEDB Population Coverage tool, identified by its bundled README as version 3.0.2 (2020-05-12). Sources were taken from the existing `lab-tools-v2/population_coverage` installation. The original README is retained.

Portability changes: package-relative imports; load trusted bundled frequency data relative to `__file__` instead of deprecated `pkg_resources`; gzip-compress the unchanged pickle snapshot; replace process exits on invalid requests with exceptions. Plotting code remains upstream code and is not invoked or included in the frozen application. The adapter supplies input rows in memory and reuses the original coverage calculation.

The allele-specific cutoff CSV was copied from the existing iedb-pipeline data folder. Its SHA-256 is `7b1703ec3fcb37d0ee487f5770848912d9c234f1e16bbaf925ce979d8b067b15`.

Upstream references: https://tools.iedb.org/population/ and the local bundled README. A separate redistribution license was not present in the supplied installation. This project does not assign a new license to third-party code or data. Before public redistribution, retain the upstream terms and establish the applicable redistribution permissions. The initial repository is private.

Pandas, NumPy, openpyxl, Python/Tcl/Tk, and PyInstaller retain their respective upstream licenses. No IEDB prediction executables or model weights are included.
