# Contributing

Thanks for helping out! Bug reports and pull requests are welcome.

- `main` is protected: work on a branch and open a pull request.
- Add or update tests for every change. They run in a real Home Assistant test instance,
  against Home Keeper's test fake and the real Home Keeper (`HOME_KEEPER_REF` in
  `.github/workflows/validate.yml`). The decisions live in `logic.py`, which imports no
  Home Assistant code; test them there first (`tests/test_logic.py`).
- Run `ruff check .`, `ruff format --check .` and `pytest` before opening a pull request.
  For the Home Keeper tests, copy `custom_components/home_keeper` of that release next to
  this integration (the CI step "Fetch Home Keeper" shows how); it is git-ignored.
- Keep the keys that existing installations depend on: the domain, the option keys and
  the keys this integration writes into Home Keeper's `source` (`tests/test_compat.py`
  guards them). Changing one makes the integration lose its own tasks and appliances.
- Home Keeper and IPP stay optional: talk to them only through services, events, the
  registries and entity states, never by importing them.
- New user-facing texts go into both `translations/en.json` and `translations/de.json`
  (`strings.json` mirrors `en.json`), and task texts into `TEXT` in `const.py`.
- The brand icon is generated from `scripts/brand/icon.svg`; change the SVG and re-render
  (`scripts/brand/README.md`), don't edit the PNGs.

Versions: the GitHub release tag is the only source of the version (see "Releases" in the
README). `manifest.json` keeps the placeholder `0.0.0`; don't change it, and don't write the
version anywhere else. Code that needs the version reads it from the loaded integration.

By contributing you agree that your contribution is licensed under the MIT License.
