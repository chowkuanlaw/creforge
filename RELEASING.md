# Releasing creforge

Releases publish to PyPI automatically through
[trusted publishing](https://docs.pypi.org/trusted-publishers/): no tokens are stored
anywhere. Anyone with maintainer rights on the GitHub repository can release.

## Checklist

1. **Decide the version** (see [docs/stability.md](docs/stability.md)):
   - patch (x.y.**Z**): bug fixes only; output stays byte-identical unless the bug was in
     the data itself;
   - minor (x.**Y**.0): new features; data for a seed may change; schema only grows;
   - major (**X**.0.0): anything that breaks the schema, the public API or the CLI.
2. **If the schema changed**, bump `SCHEMA_VERSION` in `src/creforge/generator.py` and
   update [docs/data-dictionary.md](docs/data-dictionary.md). The docs tests fail until
   both agree.
3. **Update the version** in `pyproject.toml` and `src/creforge/__init__.py`.
4. **Update `CHANGELOG.md`**: move items from *Unreleased* to the new version, with
   today's date, and add the compare link at the bottom.
5. **Check locally**:
   ```console
   $ ruff check .
   $ pytest            # includes the slow calibration suite
   $ python -m build   # the wheel must include src/creforge/profiles/*.yaml
   ```
6. **Push to `main`** and wait for CI to pass (every OS, Python version, and the
   min-deps job).
7. **Publish the GitHub release**: *Releases → Draft a new release*, create the tag
   `vX.Y.Z` on `main`, title `creforge X.Y.Z`, *Generate release notes*, *Publish*.
   The `Release` workflow builds and uploads to PyPI.
8. **Verify**: in a fresh virtual environment, `pip install creforge==X.Y.Z`, then run
   `creforge generate -n 2000 -o out && creforge validate out`.

## If the release workflow fails

- *"trusted publishing exchange failure"*: the PyPI trusted publisher settings
  (owner, repository, workflow `release.yml`, environment `pypi`) must match exactly.
- *"file already exists"*: that version is already on PyPI. PyPI never allows reusing
  a version number, so bump to the next patch version and release again.
